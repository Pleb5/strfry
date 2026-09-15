"""Eventually consistent membership cache, private to the read plugin.

Requests never scan storage. No persisted reader snapshots, speculative write
state, core policy revision, or database commit coordination is involved.
"""
import threading
import time

from . import protocol as P
from .loader import Loader
from .read_scanner import BoundedReadScanner, ReadScanError
from .state import Branch, can_read_community


class QuietMetrics:
    def count(self, *args, **kwargs):
        pass

    def log(self, *args, **kwargs):
        pass


class ReadAdmission:
    def __init__(self, config, *, scanner_factory=None, clock=time.monotonic, start=True):
        config.validate_read_control()
        if config.read_control != "members":
            raise ValueError("read plugin requires BUDABIT_READ_CONTROL=members")
        self.config, self.clock = config, clock
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.scanner_factory = scanner_factory or (lambda: BoundedReadScanner(config, self.stop_event))
        self.readers = frozenset()
        self.loaded_at = None
        self.last_error = "initializing"
        self.thread = None
        if start:
            self.thread = threading.Thread(target=self.run, name="read-membership", daemon=True)
            self.thread.start()

    def refresh(self):
        started = self.clock()
        try:
            branch = Branch(*P.parse_definition_address(self.config.branches[0]))
            Loader(None, self.scanner_factory(), QuietMetrics()).load_branch(branch)
            branch.warm = True
            derived = branch.derived()
            candidates = {branch.owner} | derived.structural_members | derived.current_moderators
            for grants in derived.section_grants.values():
                candidates.update(grants)
            readers = frozenset(key for key in candidates if can_read_community(branch, key, ready=True))
            if len(readers) > self.config.read_max_pubkeys:
                raise ReadScanError("reader count exceeds bound")
            if self.clock() - started >= self.config.read_max_age_seconds:
                raise ReadScanError("completed view is already stale")
            with self.lock:
                if self.stop_event.is_set():
                    return False
                self.readers, self.loaded_at, self.last_error = readers, started, ""
            return True
        except Exception as error:
            # A running/answering process is not proof its policy is fresh.
            with self.lock:
                self.last_error = type(error).__name__
            return False

    def decide(self, keys):
        with self.lock:
            if (self.stop_event.is_set() or self.loaded_at is None
                    or not 0 <= self.clock() - self.loaded_at < self.config.read_max_age_seconds):
                return "unavailable"
            return "allow" if any(key in self.readers for key in keys) else "deny"

    def handle(self, request):
        if not isinstance(request, dict) or request.get("type") != "read-admission":
            raise ValueError("invalid read admission request")
        token, keys = request.get("request_id"), request.get("authenticated_pubkeys")
        if not isinstance(token, str) or not 1 <= len(token) <= 128:
            raise ValueError("invalid read admission token")
        if not isinstance(keys, list) or len(keys) > 32 or any(not P.is_hex64(key) for key in keys):
            raise ValueError("invalid authenticated keys")
        return {"request_id": token, "decision": self.decide(keys)}

    def run(self):
        while not self.stop_event.is_set():
            self.refresh()
            self.stop_event.wait(self.config.read_refresh_seconds)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=self.config.read_scan_timeout_seconds + 1)
