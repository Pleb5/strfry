"""Storage guard: refuse writes when the LMDB file or filesystem is at risk."""

import os
import sys
from pathlib import Path

from .pipeline import Decision, Stage


class StorageGuard(Stage):
    name = "storage"

    def __init__(self, env, clock):
        self.clock = clock
        self.db_file = Path(
            env.get("STRFRY_POLICY_DB_FILE", "/var/lib/strfry/db/data.mdb")
        )
        self.max_db_bytes = int(
            env.get("STRFRY_POLICY_MAX_DB_BYTES", str(10 * 1024**3))
        )
        self.min_free_bytes = int(
            env.get("STRFRY_POLICY_MIN_FREE_BYTES", str(25 * 1024**3))
        )
        self.check_seconds = float(
            env.get("STRFRY_POLICY_STORAGE_CHECK_SECONDS", "5")
        )
        self.result = (True, "")
        self.checked_at = float("-inf")

    def check(self, now=None):
        now = self.clock() if now is None else now
        if now - self.checked_at < self.check_seconds:
            return self.result

        try:
            db_size = self.db_file.stat().st_size if self.db_file.exists() else 0
            if db_size >= self.max_db_bytes:
                result = (False, "blocked: relay storage budget reached")
            elif not os.access(self.db_file.parent, os.W_OK):
                result = (False, "blocked: relay database is not writable")
            else:
                stat = os.statvfs(self.db_file.parent)
                free_bytes = stat.f_bavail * stat.f_frsize
                if free_bytes < self.min_free_bytes:
                    result = (False, "blocked: relay filesystem is low on space")
                else:
                    result = (True, "")
        except OSError as error:
            print(f"storage check failed: {error}", file=sys.stderr, flush=True)
            result = (False, "blocked: relay storage check failed")

        self.checked_at = now
        self.result = result
        return result

    def evaluate(self, request, now):
        ok, message = self.check(now)
        if not ok:
            return Decision.reject(message, "storage")
        return None

    def health(self):
        return self.check()
