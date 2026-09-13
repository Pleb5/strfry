#!/usr/bin/env python3
"""Delete stored events listed in an audit.py report.

Off by default and deliberately narrow. The write-control plugin gates new
writes only; this tool is the separate sweep the plan (§7) describes. By
default it removes only content by effectively person-banned authors, which
is the one case where Budabit will never show the events again. Removing
revoked-author content diverges from Budabit's "regrant refetches history"
behaviour, and removing censored events removes the client's "Moderated
event" placeholder shape; both need ``--reasons`` to be named explicitly.

Usage:
    sweep.py audit.json                      # dry run: print what would be deleted
    sweep.py audit.json --apply              # delete via `strfry delete`
    sweep.py audit.json --reasons person_banned,no_grant --apply
    sweep.py audit.json --apply --require-recent-backup /mnt/.../backups/strfry/daily --max-backup-age-hours 30

Runs `strfry delete` through the same docker compose service as
retention.py, or directly with --strfry-bin.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_REASONS = ("person_banned",)


def newest_mtime(directory):
    newest = 0.0
    for path in Path(directory).glob("*.jsonl.zst"):
        newest = max(newest, path.stat().st_mtime)
    return newest


def strfry_command(args):
    if args.strfry_bin:
        return [args.strfry_bin, "--config", args.config]
    deploy_dir = Path(__file__).resolve().parent
    return [
        "docker",
        "compose",
        "--project-directory",
        str(deploy_dir),
        "-f",
        str(deploy_dir / "compose.yaml"),
        "exec",
        "-T",
        "relay",
        "/usr/local/bin/strfry",
        "--config",
        "/etc/strfry.conf",
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report", help="audit.py JSON report")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reasons", default=",".join(DEFAULT_REASONS), help="comma-separated reason codes to sweep")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--strfry-bin", default="", help="run strfry directly instead of via docker compose")
    parser.add_argument("--config", default=os.environ.get("STRFRY_CONFIG", "/etc/strfry.conf"))
    parser.add_argument("--require-recent-backup", default="", help="directory that must contain a recent *.jsonl.zst")
    parser.add_argument("--max-backup-age-hours", type=float, default=30.0)
    args = parser.parse_args()

    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    reasons = {item.strip() for item in args.reasons.split(",") if item.strip()}
    if not reasons:
        parser.error("--reasons must name at least one reason code")

    with open(args.report, "r", encoding="utf-8") as handle:
        report = json.load(handle)

    selected = [item for item in report.get("rejections", []) if item.get("reason") in reasons]
    by_reason = {}
    for item in selected:
        by_reason[item["reason"]] = by_reason.get(item["reason"], 0) + 1

    print(f"report generated_at={report.get('generated_at')} mode={report.get('mode')}", file=sys.stderr)
    print(f"selected {len(selected)} events by reason: {json.dumps(by_reason)}", file=sys.stderr)

    if not args.apply:
        for item in selected:
            print(json.dumps({k: item[k] for k in ("id", "kind", "pubkey", "reason")}))
        print("dry run; pass --apply to delete", file=sys.stderr)
        return

    if args.require_recent_backup:
        age_hours = (time.time() - newest_mtime(args.require_recent_backup)) / 3600.0
        if age_hours > args.max_backup_age_hours:
            print(
                f"refusing to sweep: newest backup in {args.require_recent_backup} is {age_hours:.1f}h old",
                file=sys.stderr,
            )
            raise SystemExit(1)

    strfry = strfry_command(args)
    ids = [item["id"] for item in selected if item.get("id")]
    deleted = 0
    for start in range(0, len(ids), args.batch_size):
        batch = ids[start : start + args.batch_size]
        delete_filter = json.dumps({"ids": batch}, separators=(",", ":"))
        subprocess.run([*strfry, "delete", f"--filter={delete_filter}"], check=True)
        deleted += len(batch)
    print(f"deleted {deleted} events", file=sys.stderr)


if __name__ == "__main__":
    main()
