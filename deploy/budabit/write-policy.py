#!/usr/bin/env python3
"""strfry write-policy plugin for the Budabit relay deployment.

Pipeline (first rejection wins):

1. storage guard   - database size / free space
2. budabit         - Communikeys V2 community write control (when branches
                     or auto-host are configured); no writes pass until
                     the live plugin's initial authority load completes
3. rate limits     - per pubkey, per source, global token buckets

Usage:
    write-policy.py                  # plugin mode (JSONL on stdin/stdout)
    write-policy.py --check-storage  # exit 1 when the storage guard rejects
    write-policy.py --check-policy   # load/check a fresh snapshot, not live ingestion readiness
    write-policy.py --check-read-policy [EPOCH SEQ]
                                     # inspect a local snapshot, not proof of live C++ enforcement
    write-policy.py --status         # print branch state as JSON
    write-policy.py --nip11-extra    # print the relay.info.extra JSON advertising enforcement
    write-policy.py --replay FILE [lmdb+export|export]
                                     # evaluate a JSONL export against the community
                                     # policy only (no rate limits or storage guard)
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from policy.budabit.config import BudabitConfig  # noqa: E402
from policy.budabit.metrics import Metrics  # noqa: E402
from policy.budabit import rules  # noqa: E402
from policy.budabit.stage import BudabitWriteControl  # noqa: E402
from policy.budabit.readers import ReadProjection, CONTROL_TYPES, check_read_snapshot  # noqa: E402
from policy.pipeline import Pipeline, Request  # noqa: E402
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
        self.readers = None
        if self.budabit.config.read_control == "members":
            self.readers = ReadProjection(self.budabit.config, self.metrics, auto_start=start_loader)

    def handle(self, request):
        if isinstance(request, dict) and request.get("type") in CONTROL_TYPES:
            if self.readers is not None:
                try:
                    self.readers.control(request)
                except Exception:
                    self.readers.fail_control()
                    self.metrics.log("reader_control_failed")
            return None  # controls NEVER produce a JSONL write response
        return self.pipeline.handle(request)

    def stop(self):
        if self.readers is not None:
            self.readers.stop()
        if self.budabit.loader is not None:
            self.budabit.loader.stop()

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
            print(f"policy request failed: {type(error).__name__}", file=sys.stderr, flush=True)
            if isinstance(request, dict) and request.get("type") in CONTROL_TYPES:
                continue
            event_id = ""
            try:
                event_id = (request or {}).get("event", {}).get("id", "")
            except Exception:  # noqa: BLE001
                pass
            response = _fail_response(event_id, "blocked: policy failure")
        if response is not None:
            print(json.dumps(response, separators=(",", ":")), flush=True)


def replay(policy, path, authority="lmdb+export"):
    """Offline evaluation of a JSONL export against the community policy only.

    Rate limits and the storage guard are not applied; they depend on live
    traffic and would dominate any sizeable export. Authority state is built
    first and completely -- from the relay's LMDB when the loader is enabled
    and ``authority="lmdb+export"``, then from every authority event in the
    export -- and only then is each non-authority event evaluated against
    that final state. This matches the client's current-grant model and
    audit.py: a shard published before the definition that references it
    still counts. With ``authority="export"`` LMDB is not consulted.

    Prints one JSON line per event that would be rejected and a summary of
    decisions by reason on stderr.
    """
    stage = policy.budabit
    if authority == "lmdb+export" and stage.loader is not None:
        stage.loader.warm_up()
    else:
        for branch in stage.state.branches.values():
            branch.warm = True

    with open(path, "r", encoding="utf-8") as handle:
        events = [json.loads(line) for line in handle if line.strip()]
    events.sort(key=lambda e: (e.get("created_at", 0), e.get("id", "")))

    # Two passes: definitions first so that shard coordinates exist, then all
    # authority kinds in order so deletions and replacements resolve.
    for event in events:
        if event.get("kind") == rules.P.COMMUNITY_DEFINITION_KIND:
            stage.state.apply(event)
    for event in events:
        if event.get("kind") in rules.AUTHORITY_KINDS:
            stage.state.apply(event)

    counts = {}
    for event in events:
        if event.get("kind") in rules.AUTHORITY_KINDS:
            counts["skipped:authority"] = counts.get("skipped:authority", 0) + 1
            continue
        raw = {
            "type": "new",
            "event": event,
            "receivedAt": event.get("created_at", 0),
            "sourceType": "Import",
            "sourceInfo": "replay",
        }
        request = Request.parse(raw)
        outcome = rules.evaluate(request.event, stage.state, stage.config)
        key = f"{outcome.decision.action}:{outcome.scope}:{outcome.reason}"
        counts[key] = counts.get(key, 0) + 1
        if not outcome.accepted:
            print(
                json.dumps(
                    {
                        "id": event.get("id"),
                        "kind": event.get("kind"),
                        "pubkey": event.get("pubkey"),
                        "community": outcome.community_id[:8],
                        "reason": outcome.reason,
                        "msg": outcome.decision.msg,
                    }
                )
            )
    print(json.dumps(dict(sorted(counts.items())), indent=2), file=sys.stderr)


def main(argv):
    args = argv[1:]

    if args and args[0] == "--check-read-policy" and len(args) in (1, 3):
        config = BudabitConfig.from_env(os.environ)
        ok, message = check_read_snapshot(
            config, expected_epoch=args[1] if len(args) == 3 else None,
            expected_seq=int(args[2]) if len(args) == 3 else None,
        )
        if not ok:
            print(message, file=sys.stderr)
            raise SystemExit(1)
        return

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

    if args == ["--nip11-extra"]:
        policy = WritePolicy(start_loader=False)
        if policy.budabit.loader is not None:
            policy.budabit.loader.warm_up()
        print(json.dumps(policy.budabit.nip11_extra(), separators=(",", ":")))
        return

    if args and args[0] == "--replay" and len(args) in (2, 3):
        authority = args[2] if len(args) == 3 else "lmdb+export"
        if authority not in ("lmdb+export", "export"):
            print("usage: write-policy.py --replay FILE [lmdb+export|export]", file=sys.stderr)
            raise SystemExit(2)
        policy = WritePolicy(start_loader=False)
        replay(policy, args[1], authority)
        return

    if args:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)

    policy = WritePolicy()
    try:
        serve(policy)
    finally:
        policy.stop()


if __name__ == "__main__":
    try:
        main(sys.argv)
    except ValueError as error:
        print(f"policy configuration error: {error}", file=sys.stderr)
        raise SystemExit(2)
