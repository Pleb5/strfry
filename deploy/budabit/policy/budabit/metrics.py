"""Structured stderr logging and in-memory counters."""

import json
import sys
import threading
import time
from collections import Counter


class Metrics:
    def __init__(self, stream=None, clock=None):
        self.stream = stream or sys.stderr
        self.clock = clock or time.time
        self.counters = Counter()
        self.lock = threading.Lock()

    def log(self, event, **fields):
        record = {"ts": int(self.clock()), "event": event}
        record.update(fields)
        line = json.dumps(record, separators=(",", ":"), sort_keys=True)
        with self.lock:
            print(line, file=self.stream, flush=True)

    def count(self, name, **labels):
        key = (name, tuple(sorted(labels.items())))
        with self.lock:
            self.counters[key] += 1

    def decision(self, request, outcome, dry_run=False):
        labels = {"kind": request.kind, "reason": outcome.reason, "scope": outcome.scope}
        if outcome.community_id:
            labels["community"] = outcome.community_id[:8]
        self.count("would_reject" if dry_run and not outcome.accepted else "decision", **labels)
        if not outcome.accepted:
            self.log(
                "would_reject" if dry_run else "reject",
                kind=request.kind,
                pubkey=request.pubkey[:8],
                community=outcome.community_id[:8],
                reason=outcome.reason,
                msg=outcome.decision.msg,
            )

    def state_change(self, branch, change, event):
        self.count("state_change", change=change, community=branch.community_id[:8])
        self.log(
            change,
            community=branch.community_id[:8],
            kind=event.get("kind"),
            pubkey=(event.get("pubkey") or "")[:8],
            id=(event.get("id") or "")[:8],
        )

    def snapshot(self):
        with self.lock:
            return {
                f"{name}{{{','.join(f'{k}={v}' for k, v in labels)}}}": value
                for (name, labels), value in self.counters.items()
            }
