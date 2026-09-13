#!/usr/bin/env python3
"""strfry write-policy plugin for the Budabit relay deployment.

Pipeline (first rejection wins):

1. storage guard   - database size / free space
2. budabit         - Communikeys V2 community write control (when
                     BUDABIT_BRANCHES is set)
3. rate limits     - per pubkey, per source, global token buckets

Usage:
    write-policy.py                  # plugin mode (JSONL on stdin/stdout)
    write-policy.py --check-storage  # exit 1 when the storage guard rejects
    write-policy.py --check-policy   # exit 1 when a hosted branch is not usable
    write-policy.py --status         # print branch state as JSON
    write-policy.py --replay FILE    # evaluate a JSONL event file offline
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from policy.budabit.config import BudabitConfig  # noqa: E402
from policy.budabit.metrics import Metrics  # noqa: E402
from policy.budabit.stage import BudabitWriteControl  # noqa: E402
from policy.pipeline import Pipeline  # noqa: E402
from policy.ratelimit import RateLimiter  # noqa: E402
from policy.storage import StorageGuard  # noqa: E402


class WritePolicy:
    """Composes the stages. Kept as a class for the health CLI and tests."""

    def __init__(self, env=None, clock=None, start_loader=True, scanner=None):
        env = os.environ if env is None else env
        self.clock = time.monotonic if clock is None else clock
        self.metrics = Metrics()
        self.storage = StorageGuard(env, self.clock)
        self.budabit = BudabitWriteControl(
            BudabitConfig.from_env(env),
            metrics=self.metrics,
            scanner=scanner,
            start_loader=start_loader,
        )
        self.ratelimit = RateLimiter(env, self.clock)
        self.pipeline = Pipeline([self.storage, self.budabit, self.ratelimit], self.clock)

    def handle(self, request):
        return self.pipeline.handle(request)

    def _check_storage(self, now=None):
        return self.storage.check(now)


def _fail_response(event_id, msg):
    return {"id": event_id, "action": "reject", "msg": msg}


def serve(policy):
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            response = policy.handle(request)
        except Exception as error:  # noqa: BLE001 - never let the plugin die
            print(f"policy request failed: {error}", file=sys.stderr, flush=True)
            event_id = ""
            try:
                event_id = (request or {}).get("event", {}).get("id", "")
            except Exception:  # noqa: BLE001
                pass
            response = _fail_response(event_id, "blocked: policy failure")
        print(json.dumps(response, separators=(",", ":")), flush=True)


def replay(policy, path):
    """Offline evaluation of a JSONL export; prints one line per rejected event."""
    if policy.budabit.loader is not None:
        policy.budabit.loader.warm_up()
    counts = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            request = {
                "type": "new",
                "event": event,
                "receivedAt": int(time.time()),
                "sourceType": "Import",
                "sourceInfo": "replay",
            }
            _, decision, stage = policy.pipeline.decide(request)
            key = f"{decision.action}:{stage or ''}:{decision.reason}"
            counts[key] = counts.get(key, 0) + 1
            if not decision.accepted:
                print(
                    json.dumps(
                        {
                            "id": event.get("id"),
                            "kind": event.get("kind"),
                            "pubkey": event.get("pubkey"),
                            "action": decision.action,
                            "msg": decision.msg,
                        }
                    )
                )
    print(json.dumps(counts, indent=2), file=sys.stderr)


def main(argv):
    args = argv[1:]

    if args == ["--check-storage"]:
        policy = WritePolicy(start_loader=False)
        ok, message = policy._check_storage()
        if not ok:
            print(message, file=sys.stderr)
            raise SystemExit(1)
        return

    if args == ["--check-policy"]:
        policy = WritePolicy(start_loader=False)
        if policy.budabit.loader is not None:
            policy.budabit.loader.warm_up()
        ok, message = policy.budabit.health()
        if not ok:
            print(message, file=sys.stderr)
            raise SystemExit(1)
        return

    if args == ["--status"]:
        policy = WritePolicy(start_loader=False)
        if policy.budabit.loader is not None:
            policy.budabit.loader.warm_up()
        print(json.dumps(policy.budabit.status(), indent=2))
        return

    if len(args) == 2 and args[0] == "--replay":
        policy = WritePolicy(start_loader=False)
        replay(policy, args[1])
        return

    if args:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)

    serve(WritePolicy())


if __name__ == "__main__":
    main(sys.argv)
