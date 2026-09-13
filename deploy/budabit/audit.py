#!/usr/bin/env python3
"""Audit stored community content against the current write-control rules.

Read-only. For every hosted branch (``BUDABIT_BRANCHES``) the current
authority state is loaded from LMDB exactly as the plugin does, then every
stored event scoped to the community (``#h=<communityId>``) is evaluated
with the same decision table. Events the plugin would reject *today* are
reported; nothing is deleted. Feed the report to ``sweep.py`` to act on it.

Usage:
    audit.py [--strfry-bin PATH] [--config PATH] [--limit N] > audit.json

Environment: the same BUDABIT_* variables as the plugin (see RUNBOOK.md).
Inside the container:
    docker compose -f deploy/budabit/compose.yaml exec -T relay \\
        python3 /usr/local/lib/strfry/audit.py > audit.json
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from policy.budabit import protocol as P  # noqa: E402
from policy.budabit import rules  # noqa: E402
from policy.budabit.config import BudabitConfig  # noqa: E402
from policy.budabit.loader import Loader, StrfryScanner  # noqa: E402
from policy.budabit.metrics import Metrics  # noqa: E402
from policy.budabit.state import CommunityState  # noqa: E402


def audit(config, scanner, limit=0, metrics=None):
    metrics = metrics or Metrics(stream=open(os.devnull, "w"))
    state = CommunityState(config.branches)
    loader = Loader(state, scanner, metrics, reconcile_seconds=config.reconcile_seconds)
    loader.warm_up()

    rejections = []
    counts = Counter()
    seen = set()
    for branch in state.branches.values():
        community_id = branch.community_id
        candidates = scanner.scan({"#h": [community_id]})
        for event in sorted(candidates, key=lambda e: (e.get("created_at", 0), e.get("id", ""))):
            if event.get("id") in seen:
                continue
            seen.add(event.get("id"))
            if event.get("kind") in rules.AUTHORITY_KINDS:
                # Authority state is what we audit *with*, not what we audit.
                counts["skipped_authority"] += 1
                continue
            outcome = rules.evaluate(event, state, config)
            counts[f"{outcome.decision.action}:{outcome.reason}"] += 1
            if not outcome.accepted:
                rejections.append(
                    {
                        "id": event.get("id"),
                        "kind": event.get("kind"),
                        "pubkey": event.get("pubkey"),
                        "created_at": event.get("created_at"),
                        "community": outcome.community_id,
                        "reason": outcome.reason,
                        "msg": outcome.decision.msg,
                    }
                )
                if limit and len(rejections) >= limit:
                    break

    branches = []
    for branch in state.branches.values():
        derived = branch.derived()
        branches.append(
            {
                "address": branch.address,
                "definition": derived.available,
                "shards_loaded": sum(1 for c in branch.shards.values() if c.current is not None),
                "shards_referenced": len(branch.shards),
                "person_bans": sorted(derived.person_bans),
                "reports": len(branch.reports),
            }
        )
    return {
        "generated_at": int(time.time()),
        "mode": "strict" if config.strict else "passthrough",
        "policy_version": config.policy_version,
        "branches": branches,
        "counts": dict(sorted(counts.items())),
        "rejections": rejections,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strfry-bin", default=os.environ.get("BUDABIT_STRFRY_BIN", "/usr/local/bin/strfry"))
    parser.add_argument("--config", default=os.environ.get("STRFRY_CONFIG", "/etc/strfry.conf"))
    parser.add_argument("--limit", type=int, default=0, help="stop after N rejections (0 = all)")
    args = parser.parse_args()

    env = dict(os.environ)
    env["BUDABIT_STRFRY_BIN"] = args.strfry_bin
    env["STRFRY_CONFIG"] = args.config
    config = BudabitConfig.from_env(env)
    if not config.enabled:
        print("BUDABIT_BRANCHES is empty; nothing to audit", file=sys.stderr)
        raise SystemExit(2)

    report = audit(config, StrfryScanner(config), limit=args.limit)
    json.dump(report, sys.stdout, indent=2)
    print()
    print(
        f"audited branches={len(report['branches'])} rejections={len(report['rejections'])}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
