#!/usr/bin/env python3
"""Persistent REQ admission plugin. --check-policy checks an independent fresh
scan, not the running relay's enforcement. No roster is printed or persisted.
"""
import json
import os
import sys

from policy.budabit.config import BudabitConfig
from policy.budabit.read_admission import ReadAdmission


def serve(policy, source, output):
    while True:
        line = source.readline(8193)
        if not line:
            return
        if len(line) > 8192 or not line.endswith("\n"):
            raise ValueError("read admission input exceeds bound")
        response = policy.handle(json.loads(line))
        output.write(json.dumps(response, separators=(",", ":")) + "\n")
        output.flush()


def main():
    check = sys.argv[1:] == ["--check-policy"]
    if sys.argv[1:] and not check:
        raise ValueError("unknown read policy argument")
    policy = ReadAdmission(BudabitConfig.from_env(os.environ), start=not check)
    try:
        if check:
            if not policy.refresh():
                return 1
            return 0
        serve(policy, sys.stdin, sys.stdout)
        return 0
    finally:
        policy.stop()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, KeyError):
        print("read policy unavailable: check configuration and local storage", file=sys.stderr)
        sys.exit(1)
