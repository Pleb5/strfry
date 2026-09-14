"""Warm-up and reconcile branch state from the relay's own LMDB.

Runs in a background thread so the request loop never blocks. Uses logical
``strfry scan`` queries from a second process rather than a WebSocket client,
which keeps the plugin dependency-free. Events found in storage are fed through
the same ``Branch.apply`` as inline writes. Ingestion stays fail-closed until
this loader completes its initial pass; an empty undiscovered map is not ready.
"""

import json
import subprocess
import threading
import time

from . import protocol as P


class StrfryScanner:
    def __init__(self, config):
        self.config = config

    def scan(self, filter_obj):
        command = [
            self.config.strfry_bin,
            "--config",
            self.config.strfry_config,
            "scan",
            json.dumps(filter_obj, separators=(",", ":")),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.config.scan_timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"strfry scan failed ({result.returncode}): {result.stderr.strip()[:500]}"
            )
        events = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events


class Loader:
    def __init__(self, state, scanner, metrics, reconcile_seconds=300.0, clock=None, sleep=None, auto_host_url=""):
        self.state = state
        self.scanner = scanner
        self.metrics = metrics
        self.reconcile_seconds = reconcile_seconds
        self.auto_host_url = auto_host_url
        self.clock = clock or time.monotonic
        self.stop_event = threading.Event()
        self.sleep = sleep or self.stop_event.wait
        self.thread = None
        self.last_error = ""
        self.last_reconcile_at = None
        # An empty map before discovery is not evidence that nothing is hosted.
        # Latch only after a complete successful initial load; ordinary refresh
        # failures must not discard the initialized view or reopen passthrough.
        self.initialized = False

    # --- lifecycle ----------------------------------------------------------

    def start(self):
        self.thread = threading.Thread(target=self.run, name="budabit-loader", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def run(self):
        retry_seconds = 1.0
        while not self.stop_event.is_set():
            try:
                self.warm_up()
                break
            except Exception as error:  # noqa: BLE001 - keep the relay alive
                self.last_error = str(error)
                self.metrics.log("loader_error", phase="warm_up", error=str(error)[:300])
            # Retry initialization without waiting a full reconcile interval,
            # but never let incoming events trigger scans or a busy retry loop.
            self.sleep(retry_seconds)
            retry_seconds = min(retry_seconds * 2, 30.0)
        next_reconcile = self.clock() + self.reconcile_seconds
        while not self.stop_event.is_set():
            self.sleep(1.0)
            if self.stop_event.is_set():
                break
            now = self.clock()
            due = now >= next_reconcile or any(
                branch.needs_reconcile for branch in self.state.branches.values()
            )
            if not due:
                continue
            try:
                self.reconcile()
                self.last_error = ""
            except Exception as error:  # noqa: BLE001
                self.last_error = str(error)
                self.metrics.log("loader_error", phase="reconcile", error=str(error)[:300])
            next_reconcile = self.clock() + self.reconcile_seconds

    # --- work ---------------------------------------------------------------

    def discover(self):
        """Auto-host: add branches whose current valid definition names this relay."""
        url = self.auto_host_url
        if not url:
            return
        definitions = self.scanner.scan({"kinds": [P.COMMUNITY_DEFINITION_KIND]})
        for event in sorted(definitions, key=lambda e: (e.get("created_at", 0), e.get("id", ""))):
            try:
                parsed = P.parse_definition(event)
            except P.InvalidEvent:
                continue
            if url in parsed.relays and self.state.branch(parsed.address) is None:
                self.state.add_branch(parsed.address, auto=True)
                self.metrics.log("branch_auto_hosted", community=parsed.community_id[:8], address=parsed.address)

    def unhost_stale(self):
        """Auto-host: drop branches whose current definition no longer names this relay."""
        url = self.auto_host_url
        if not url:
            return
        for branch in list(self.state.branches.values()):
            if not self.state.is_auto(branch.address):
                continue
            definition = branch.definition
            if definition is None or url not in definition.relays:
                if self.state.remove_branch(branch.address):
                    self.metrics.log("branch_unhosted", community=branch.community_id[:8], address=branch.address)

    def warm_up(self):
        self.discover()
        for branch in list(self.state.branches.values()):
            self.load_branch(branch)
            branch.warm = True
            derived = branch.derived()
            self.metrics.log(
                "branch_warm",
                community=branch.community_id[:8],
                definition=bool(derived.available),
                shards=len(branch.shards),
                grants=sum(len(g) for g in derived.section_grants.values()),
                bans=len(derived.person_bans),
                reports=len(branch.reports),
            )
        self.unhost_stale()
        self.last_reconcile_at = self.clock()
        self.last_error = ""
        if not self.initialized:
            self.initialized = True
            self.metrics.log("initial_load_complete", branches=len(self.state.branches))

    def reconcile(self):
        self.discover()
        for branch in list(self.state.branches.values()):
            branch.needs_reconcile = False
            self.load_branch(branch)
            branch.warm = True
        self.unhost_stale()
        self.last_reconcile_at = self.clock()

    def _apply_all(self, branch, events):
        for event in sorted(events, key=lambda e: (e.get("created_at", 0), e.get("id", ""))):
            change = branch.apply(event)
            if change:
                self.metrics.count("loader_apply", change=change)

    def _retain(self, branch, address, events):
        change = branch.retain_only(address, {event.get("id") for event in events})
        if change:
            self.metrics.count("loader_apply", change=change)
            self.metrics.log(change, community=branch.community_id[:8], address=address)

    def load_branch(self, branch):
        owner = branch.owner
        community_id = branch.community_id

        # Definition and its owner-authored tombstones.
        self._apply_all(
            branch,
            self.scanner.scan(
                {"kinds": [P.DELETE_KIND], "authors": [owner], "#a": [branch.address]}
            ),
        )
        definitions = self.scanner.scan(
            {"kinds": [P.COMMUNITY_DEFINITION_KIND], "authors": [owner], "#d": [community_id]}
        )
        self._apply_all(branch, definitions)
        self._retain(branch, branch.address, definitions)

        # Referenced shards, iterating until the referenced set is stable.
        seen = set()
        for _ in range(3):
            pending = [address for address in branch.shards if address not in seen]
            if not pending:
                break
            for address in pending:
                seen.add(address)
                parsed = P.parse_address(address, P.PROFILE_LIST_KIND)
                if not parsed:
                    continue
                self._apply_all(
                    branch,
                    self.scanner.scan(
                        {"kinds": [P.DELETE_KIND], "authors": [parsed.pubkey], "#a": [address]}
                    ),
                )
                shard_events = self.scanner.scan(
                    {
                        "kinds": [P.PROFILE_LIST_KIND],
                        "authors": [parsed.pubkey],
                        "#d": [parsed.identifier],
                    }
                )
                if not shard_events:
                    self.metrics.log("shard_missing", community=community_id[:8], address=address)
                self._apply_all(branch, shard_events)
                self._retain(branch, address, shard_events)

        # Reports and community-scoped deletes. Deletes first so a report and
        # its retraction are both known before derivation.
        self._apply_all(
            branch, self.scanner.scan({"kinds": [P.DELETE_KIND], "#h": [community_id]})
        )
        reports = self.scanner.scan({"kinds": [P.REPORT_KIND], "#h": [community_id]})
        self._apply_all(branch, reports)
        removed = branch.retain_reports({event.get("id") for event in reports})
        if removed:
            self.metrics.count("loader_apply", change="report_removed")
            self.metrics.log("report_removed", community=community_id[:8], count=removed)
        branch.needs_reconcile = False
