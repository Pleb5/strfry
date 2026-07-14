#!/usr/bin/env python3

import json
import os
import sys
import time
from collections import OrderedDict
from pathlib import Path


class TokenBucket:
    def __init__(self, capacity, refill_per_second, now):
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.tokens = capacity
        self.updated_at = now
        self.last_seen = now

    def refill(self, now):
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(
            self.capacity,
            self.tokens + elapsed * self.refill_per_second,
        )
        self.updated_at = now
        self.last_seen = now


class WritePolicy:
    def __init__(self, env=None, clock=None):
        env = os.environ if env is None else env
        self.clock = time.monotonic if clock is None else clock

        self.db_file = Path(
            env.get("STRFRY_POLICY_DB_FILE", "/var/lib/strfry/db/data.mdb")
        )
        self.max_db_bytes = int(
            env.get("STRFRY_POLICY_MAX_DB_BYTES", str(10 * 1024**3))
        )
        self.min_free_bytes = int(
            env.get("STRFRY_POLICY_MIN_FREE_BYTES", str(25 * 1024**3))
        )
        self.storage_check_seconds = float(
            env.get("STRFRY_POLICY_STORAGE_CHECK_SECONDS", "5")
        )

        self.pubkey_capacity = float(
            env.get("STRFRY_POLICY_PUBKEY_CAPACITY", "30")
        )
        self.pubkey_rate = float(
            env.get("STRFRY_POLICY_PUBKEY_RATE_PER_MINUTE", "2")
        ) / 60.0
        self.ip_capacity = float(env.get("STRFRY_POLICY_IP_CAPACITY", "100"))
        self.ip_rate = float(
            env.get("STRFRY_POLICY_IP_RATE_PER_MINUTE", "10")
        ) / 60.0
        self.global_capacity = float(
            env.get("STRFRY_POLICY_GLOBAL_CAPACITY", "200")
        )
        self.global_rate = float(
            env.get("STRFRY_POLICY_GLOBAL_RATE_PER_MINUTE", "20")
        ) / 60.0
        self.max_tracked_pubkeys = int(
            env.get("STRFRY_POLICY_MAX_TRACKED_PUBKEYS", "10000")
        )
        self.max_tracked_sources = int(
            env.get("STRFRY_POLICY_MAX_TRACKED_SOURCES", "4096")
        )

        now = self.clock()
        self.global_bucket = TokenBucket(
            self.global_capacity, self.global_rate, now
        )
        self.pubkey_buckets = OrderedDict()
        self.ip_buckets = OrderedDict()
        self.storage_result = (True, "")
        self.storage_checked_at = float("-inf")

    def _bucket(self, buckets, key, capacity, rate, max_entries, now):
        bucket = buckets.get(key)
        if bucket is None:
            if len(buckets) >= max_entries:
                _, oldest = next(iter(buckets.items()))
                if now - oldest.last_seen < 3600:
                    return None
                buckets.popitem(last=False)
            bucket = TokenBucket(capacity, rate, now)
            buckets[key] = bucket
        else:
            buckets.move_to_end(key)
        bucket.refill(now)
        return bucket

    def _check_storage(self, now):
        if now - self.storage_checked_at < self.storage_check_seconds:
            return self.storage_result

        try:
            db_size = self.db_file.stat().st_size if self.db_file.exists() else 0
            if db_size >= self.max_db_bytes:
                result = (False, "blocked: relay storage budget reached")
            else:
                if not os.access(self.db_file.parent, os.W_OK):
                    result = (False, "blocked: relay database is not writable")
                else:
                    stat = os.statvfs(self.db_file.parent)
                    free_bytes = stat.f_bavail * stat.f_frsize
                    if free_bytes < self.min_free_bytes:
                        result = (
                            False,
                            "blocked: relay filesystem is low on space",
                        )
                    else:
                        result = (True, "")
        except OSError as error:
            print(f"storage check failed: {error}", file=sys.stderr, flush=True)
            result = (False, "blocked: relay storage check failed")

        self.storage_checked_at = now
        self.storage_result = result
        return result

    def handle(self, request):
        event = request.get("event", {})
        event_id = event.get("id", "")

        if request.get("type") != "new" or not event_id:
            return {
                "id": event_id,
                "action": "reject",
                "msg": "blocked: invalid policy request",
            }

        now = self.clock()
        storage_ok, storage_message = self._check_storage(now)
        if not storage_ok:
            return {
                "id": event_id,
                "action": "reject",
                "msg": storage_message,
            }

        self.global_bucket.refill(now)
        if self.global_bucket.tokens < 1:
            return {
                "id": event_id,
                "action": "reject",
                "msg": "rate-limited: relay write limit exceeded",
            }

        source = (
            f"{request.get('sourceType', 'unknown')}:"
            f"{request.get('sourceInfo', 'unknown')}"
        )
        source_bucket = self._bucket(
            self.ip_buckets,
            source,
            self.ip_capacity,
            self.ip_rate,
            self.max_tracked_sources,
            now,
        )
        if source_bucket is None:
            return {
                "id": event_id,
                "action": "reject",
                "msg": "rate-limited: source tracking limit reached",
            }
        if source_bucket.tokens < 1:
            return {
                "id": event_id,
                "action": "reject",
                "msg": "rate-limited: source write limit exceeded",
            }

        pubkey = event.get("pubkey", "unknown")
        pubkey_bucket = self._bucket(
            self.pubkey_buckets,
            pubkey,
            self.pubkey_capacity,
            self.pubkey_rate,
            self.max_tracked_pubkeys,
            now,
        )
        if pubkey_bucket is None:
            return {
                "id": event_id,
                "action": "reject",
                "msg": "rate-limited: pubkey tracking limit reached",
            }
        if pubkey_bucket.tokens < 1:
            return {
                "id": event_id,
                "action": "reject",
                "msg": "rate-limited: pubkey write limit exceeded",
            }

        for bucket in (pubkey_bucket, source_bucket, self.global_bucket):
            bucket.tokens -= 1

        return {"id": event_id, "action": "accept"}


def main():
    policy = WritePolicy()

    if sys.argv[1:] == ["--check-storage"]:
        storage_ok, message = policy._check_storage(policy.clock())
        if not storage_ok:
            print(message, file=sys.stderr)
            raise SystemExit(1)
        return

    if len(sys.argv) > 1:
        print("usage: write-policy.py [--check-storage]", file=sys.stderr)
        raise SystemExit(2)

    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = policy.handle(request)
        except Exception as error:
            print(f"policy request failed: {error}", file=sys.stderr, flush=True)
            event_id = ""
            try:
                event_id = request.get("event", {}).get("id", "")
            except Exception:
                pass
            response = {
                "id": event_id,
                "action": "reject",
                "msg": "blocked: policy failure",
            }

        print(json.dumps(response, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
