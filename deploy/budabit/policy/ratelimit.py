"""Token-bucket rate limits keyed by author pubkey, source, and globally."""

from collections import OrderedDict

from .pipeline import Decision, Stage


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


class RateLimiter(Stage):
    name = "ratelimit"

    def __init__(self, env, clock):
        self.pubkey_capacity = float(env.get("STRFRY_POLICY_PUBKEY_CAPACITY", "30"))
        self.pubkey_rate = (
            float(env.get("STRFRY_POLICY_PUBKEY_RATE_PER_MINUTE", "2")) / 60.0
        )
        self.ip_capacity = float(env.get("STRFRY_POLICY_IP_CAPACITY", "100"))
        self.ip_rate = float(env.get("STRFRY_POLICY_IP_RATE_PER_MINUTE", "10")) / 60.0
        self.global_capacity = float(env.get("STRFRY_POLICY_GLOBAL_CAPACITY", "200"))
        self.global_rate = (
            float(env.get("STRFRY_POLICY_GLOBAL_RATE_PER_MINUTE", "20")) / 60.0
        )
        self.max_tracked_pubkeys = int(
            env.get("STRFRY_POLICY_MAX_TRACKED_PUBKEYS", "10000")
        )
        self.max_tracked_sources = int(
            env.get("STRFRY_POLICY_MAX_TRACKED_SOURCES", "4096")
        )

        now = clock()
        self.global_bucket = TokenBucket(self.global_capacity, self.global_rate, now)
        self.pubkey_buckets = OrderedDict()
        self.ip_buckets = OrderedDict()

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

    def evaluate(self, request, now):
        self.global_bucket.refill(now)
        if self.global_bucket.tokens < 1:
            return Decision.reject(
                "rate-limited: relay write limit exceeded", "rate_global"
            )

        source_bucket = self._bucket(
            self.ip_buckets,
            request.source,
            self.ip_capacity,
            self.ip_rate,
            self.max_tracked_sources,
            now,
        )
        if source_bucket is None:
            return Decision.reject(
                "rate-limited: source tracking limit reached", "rate_source_tracking"
            )
        if source_bucket.tokens < 1:
            return Decision.reject(
                "rate-limited: source write limit exceeded", "rate_source"
            )

        pubkey_bucket = self._bucket(
            self.pubkey_buckets,
            request.pubkey or "unknown",
            self.pubkey_capacity,
            self.pubkey_rate,
            self.max_tracked_pubkeys,
            now,
        )
        if pubkey_bucket is None:
            return Decision.reject(
                "rate-limited: pubkey tracking limit reached", "rate_pubkey_tracking"
            )
        if pubkey_bucket.tokens < 1:
            return Decision.reject(
                "rate-limited: pubkey write limit exceeded", "rate_pubkey"
            )

        request.annotations["ratelimit"] = (pubkey_bucket, source_bucket)
        return None

    def commit(self, request, now):
        buckets = request.annotations.get("ratelimit")
        if not buckets:
            return
        pubkey_bucket, source_bucket = buckets
        for bucket in (pubkey_bucket, source_bucket, self.global_bucket):
            bucket.tokens -= 1
