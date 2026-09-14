"""Committed reader snapshots. No speculative write state and no stdout IPC.

C++ owns the epoch and invalidates reads BEFORE policy commits. A rebuild is
labelled with the committed sequence captured before scans; C++ must still check
active epoch/exact pending sequence/lease at installation and final send.
"""

import json
import os
import stat
from pathlib import Path
import tempfile
import threading
import time

from . import protocol as P
from .loader import Loader
from .metrics import Metrics
from .read_scanner import BoundedReadScanner, ReadScanError
from .state import Branch, can_read_community

CONTROL_TYPES = frozenset(("read-control-init", "committed"))
HEARTBEAT_SECONDS = 1.0
SNAPSHOT_MAX_AGE_SECONDS = 10.0
MAX_SEQUENCE = 2**64 - 1


class _QuietScanMetrics:
    # A read rebuild should not log private shard addresses/member details.
    def count(self, *args, **kwargs):
        pass

    def log(self, *args, **kwargs):
        pass


def atomic_json(path, value, maximum):
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    if len(encoded) > maximum:
        raise ValueError("read snapshot byte bound exceeded")
    # NamedTemporaryFile creates mode 0600. close + rename is atomic; snapshots
    # aren't durable credentials and cannot survive an epoch restart as valid.
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            name = handle.name
            handle.write(encoded)
        os.replace(name, path)
        name = None
    finally:
        if name is not None:
            os.unlink(name)


class ReadProjection:
    def __init__(self, config, metrics=None, scanner_factory=None, auto_start=True,
                 clock=time.monotonic, wall_clock=time.time):
        config.validate_read_control()
        if config.read_control != "members":
            raise ValueError("read projection requires members mode")
        self.config = config
        self.metrics = metrics or Metrics()
        self.path = Path(config.read_snapshot_path)
        self.status_path = self.path.with_suffix(".status.json")
        self.clock, self.wall_clock = clock, wall_clock
        self.stop_event = threading.Event()
        self.condition = threading.Condition()
        self.scanner_factory = scanner_factory or (lambda: BoundedReadScanner(config, self.stop_event))
        self.auto_start = auto_start
        self.thread = None
        self.epoch = None
        self.seq = 0
        self.dirty = False
        self.invalid = False
        self.snapshot = None
        self.heartbeat = 0
        self.next_rebuild = float("inf")
        self.next_heartbeat = float("inf")
        self.last_success = None
        self.last_error = ""

    def control(self, message):
        with self.condition:
            epoch, seq = message.get("epoch"), message.get("seq")
            if not P.is_hex64(epoch) or type(seq) is not int or not 0 <= seq <= MAX_SEQUENCE:
                raise ValueError("invalid reader epoch/sequence")
            if self.invalid or self.stop_event.is_set():
                raise ValueError("reader process requires restart")
            if message.get("type") == "read-control-init":
                if self.epoch is not None or message.get("branch_address") != self.config.branches[0]:
                    raise ValueError("reader init mismatch")
                self.epoch, self.seq = epoch, seq
                self.dirty = True
                if self.auto_start:
                    self.thread = threading.Thread(target=self.run, name="budabit-readers", daemon=True)
                    self.thread.start()
            elif message.get("type") == "committed":
                if self.epoch != epoch or seq < self.seq or seq > self.seq + 1:
                    raise ValueError("reader commit sequence mismatch")
                if seq > self.seq:
                    self.seq = seq
                    self.dirty = True
            else:
                raise ValueError("invalid reader control type")
            self.condition.notify_all()

    def fail_control(self):
        with self.condition:
            self.invalid = True
            self.last_error = "invalid reader control; restart required"
            if self.epoch is not None:
                self._publish_locked(False, [])
            self.condition.notify_all()

    def _publish_locked(self, ready, readers):
        self.heartbeat += 1
        snapshot = {
            "version": 1, "epoch": self.epoch, "seq": self.seq,
            "branch_address": self.config.branches[0],
            "owner": self.config.branches[0].split(":")[1],
            "write_enforcement": True, "ready": ready,
            "heartbeat": self.heartbeat, "updated_at": self.wall_clock(),
            "eligible_pubkeys": readers,
        }
        atomic_json(self.path, snapshot, self.config.read_max_snapshot_bytes)
        self.snapshot = snapshot
        self.next_heartbeat = self.clock() + HEARTBEAT_SECONDS
        atomic_json(self.status_path, {
            key: value for key, value in {
                **snapshot, "eligible_pubkeys": None,
                "reader_count": len(readers), "last_success": self.last_success,
                "last_error": self.last_error,
            }.items() if key != "eligible_pubkeys"
        }, self.config.read_max_snapshot_bytes)

    def rebuild(self):
        """One controlled rebuild; called only by the worker (or isolated tests)."""
        with self.condition:
            if self.epoch is None or self.invalid or self.stop_event.is_set():
                return False
            token = self.epoch, self.seq  # NEVER label a finished scan with a newer notice
            self.dirty = False
            self._publish_locked(False, [])
        try:
            branch = Branch(*P.parse_definition_address(self.config.branches[0]))
            Loader(None, self.scanner_factory(), _QuietScanMetrics()).load_branch(branch)
            branch.warm = True
            derived = branch.derived()
            candidates = {branch.owner} | derived.structural_members | derived.current_moderators
            for grants in derived.section_grants.values():
                candidates.update(grants)
            readers = sorted(key for key in candidates if can_read_community(branch, key, ready=True))
            if len(readers) > self.config.read_max_pubkeys:
                raise ReadScanError("reader count bound exceeded")
            with self.condition:
                if token != (self.epoch, self.seq) or self.invalid or self.stop_event.is_set():
                    return False
                self.last_error = ""
                self.last_success = self.wall_clock()
                self._publish_locked(True, readers)
                self.next_rebuild = self.clock() + self.config.reconcile_seconds
            return True
        except Exception as error:  # fail closed; no exception text with private payloads
            with self.condition:
                self.last_error = str(error) if isinstance(error, ReadScanError) else type(error).__name__
                self.next_rebuild = self.clock() + min(5.0, self.config.reconcile_seconds)
                self._publish_locked(False, [])
            self.metrics.log("reader_rebuild_failed", reason=self.last_error)
            return False

    def run(self):
        while not self.stop_event.is_set():
            try:
                with self.condition:
                    if self.invalid:
                        return  # no heartbeats; C++ must restart this process
                    now = self.clock()
                    rebuild = self.dirty or now >= self.next_rebuild
                    if not rebuild:
                        if (self.snapshot and self.snapshot["ready"]
                                and self.snapshot["seq"] == self.seq and now >= self.next_heartbeat):
                            self._publish_locked(True, self.snapshot["eligible_pubkeys"])
                        self.condition.wait(HEARTBEAT_SECONDS)
                if rebuild:
                    self.rebuild()
            except Exception as error:
                # File failures cannot leave a live heartbeat behind. No retry
                # here: expire the lease and let the parent restart the process.
                self.metrics.log("reader_worker_failed", reason=type(error).__name__)
                return

    def stop(self):
        self.stop_event.set()
        with self.condition:
            if self.epoch is not None:
                try:
                    self._publish_locked(False, [])
                except OSError:
                    pass  # failed writes cannot renew the old file's lease
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=2)


def check_read_snapshot(config, *, expected_epoch=None, expected_seq=None, now=None):
    """Local artifact health only. Serving-state validation belongs to C++."""
    config.validate_read_control()
    if config.read_control == "off":
        return True, ""
    now = time.time() if now is None else now
    try:
        fd = os.open(config.read_snapshot_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return False, "reader snapshot is not a regular file"
            raw = handle.read(config.read_max_snapshot_bytes + 1)
        if len(raw) > config.read_max_snapshot_bytes:
            return False, "reader snapshot oversized"
        data = json.loads(raw)
        if (type(data["version"]) is not int or data["version"] != 1 or data["ready"] is not True
                or data["write_enforcement"] is not True
                or data["branch_address"] != config.branches[0]
                or data["owner"] != config.branches[0].split(":")[1]
                or not P.is_hex64(data["epoch"])
                or type(data["seq"]) is not int or not 0 <= data["seq"] <= MAX_SEQUENCE
                or type(data["heartbeat"]) is not int or not 1 <= data["heartbeat"] <= MAX_SEQUENCE):
            return False, "reader snapshot unavailable or invalid"
        if expected_epoch is not None and data["epoch"] != expected_epoch:
            return False, "reader snapshot epoch mismatch"
        if expected_seq is not None and data["seq"] != expected_seq:
            return False, "reader snapshot sequence mismatch"
        if not now - SNAPSHOT_MAX_AGE_SECONDS <= data["updated_at"] <= now + 1:
            return False, "reader snapshot stale"
        readers = data["eligible_pubkeys"]
        if (not isinstance(readers, list) or len(readers) > config.read_max_pubkeys
                or any(not P.is_hex64(key) for key in readers)
                or len(set(readers)) != len(readers) or data["owner"] not in readers):
            return False, "reader snapshot invalid keys"
        return True, ""
    except (OSError, ValueError, TypeError, KeyError):
        return False, "reader snapshot missing or invalid"
