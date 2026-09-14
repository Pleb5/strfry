#!/usr/bin/env python3

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path


# Reports and their retractions can remain effective indefinitely. Retain all
# events of these kinds; reconstructing authority during pruning is unsafe.
POLICY_EVIDENCE_KINDS = frozenset((5, 1984, 30000, 32222))


def is_replaceable_kind(kind):
    return kind in (0, 3, 41) or 10000 <= kind < 20000 or 30000 <= kind < 40000


def is_preserved_kind(kind):
    return kind in POLICY_EVIDENCE_KINDS or is_replaceable_kind(kind)


def main():
    parser = argparse.ArgumentParser(
        description="Delete old regular events while preserving replaceable events and moderation evidence"
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    if args.days < 1:
        parser.error("--days must be positive")
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")

    deploy_dir = Path(__file__).resolve().parent
    compose = [
        "docker",
        "compose",
        "--project-directory",
        str(deploy_dir),
        "-f",
        str(deploy_dir / "compose.yaml"),
    ]
    strfry = [
        *compose,
        "exec",
        "-T",
        "relay",
        "/usr/local/bin/strfry",
        "--config",
        "/etc/strfry.conf",
    ]

    cutoff = int(time.time()) - args.days * 86400
    scan_filter = json.dumps({"until": cutoff}, separators=(",", ":"))
    scan = subprocess.Popen(
        [*strfry, "scan", scan_filter],
        stdout=subprocess.PIPE,
        text=True,
    )

    with tempfile.TemporaryFile(mode="w+") as selected_ids:
        selected = 0
        preserved = 0
        try:
            for line in scan.stdout:
                event = json.loads(line)
                kind = int(event["kind"])
                if is_preserved_kind(kind):
                    preserved += 1
                    continue

                selected += 1
                if args.apply:
                    selected_ids.write(f"{event['id']}\n")

            return_code = scan.wait()
            if return_code != 0:
                raise subprocess.CalledProcessError(return_code, scan.args)
        except Exception:
            scan.terminate()
            scan.wait()
            raise

        if not args.apply:
            print(
                f"Dry run: would delete {selected} events older than "
                f"{args.days} days; preserved {preserved} replaceable/policy events"
            )
            return

        selected_ids.seek(0)
        deleted = 0
        batch = []
        for event_id in selected_ids:
            batch.append(event_id.rstrip("\n"))
            if len(batch) < args.batch_size:
                continue

            delete_filter = json.dumps({"ids": batch}, separators=(",", ":"))
            subprocess.run(
                [*strfry, "delete", f"--filter={delete_filter}"],
                check=True,
            )
            deleted += len(batch)
            batch.clear()

        if batch:
            delete_filter = json.dumps({"ids": batch}, separators=(",", ":"))
            subprocess.run(
                [*strfry, "delete", f"--filter={delete_filter}"],
                check=True,
            )
            deleted += len(batch)

        print(f"Deleted {deleted} events older than {args.days} days; "
              f"preserved {preserved} replaceable/policy events")


if __name__ == "__main__":
    main()
