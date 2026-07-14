import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("write-policy.py")
SPEC = importlib.util.spec_from_file_location("write_policy", MODULE_PATH)
write_policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(write_policy)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class WritePolicyTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.tempdir.name) / "data.mdb"
        self.clock = Clock()
        self.env = {
            "STRFRY_POLICY_DB_FILE": str(self.db_file),
            "STRFRY_POLICY_MAX_DB_BYTES": "1000000",
            "STRFRY_POLICY_MIN_FREE_BYTES": "0",
            "STRFRY_POLICY_STORAGE_CHECK_SECONDS": "0",
            "STRFRY_POLICY_PUBKEY_CAPACITY": "100",
            "STRFRY_POLICY_PUBKEY_RATE_PER_MINUTE": "0",
            "STRFRY_POLICY_IP_CAPACITY": "100",
            "STRFRY_POLICY_IP_RATE_PER_MINUTE": "0",
            "STRFRY_POLICY_GLOBAL_CAPACITY": "100",
            "STRFRY_POLICY_GLOBAL_RATE_PER_MINUTE": "0",
            "STRFRY_POLICY_MAX_TRACKED_PUBKEYS": "100",
            "STRFRY_POLICY_MAX_TRACKED_SOURCES": "100",
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def request(self, event_id="01", pubkey="02", source="192.0.2.1"):
        return {
            "type": "new",
            "event": {"id": event_id, "pubkey": pubkey},
            "sourceType": "IP4",
            "sourceInfo": source,
        }

    def policy(self, **overrides):
        env = dict(self.env)
        env.update(overrides)
        return write_policy.WritePolicy(env=env, clock=self.clock)

    def test_accepts_event_within_limits(self):
        response = self.policy().handle(self.request())
        self.assertEqual(response, {"id": "01", "action": "accept"})

    def test_rejects_pubkey_over_limit(self):
        policy = self.policy(STRFRY_POLICY_PUBKEY_CAPACITY="1")
        self.assertEqual(policy.handle(self.request())["action"], "accept")
        response = policy.handle(self.request(event_id="03"))
        self.assertEqual(response["action"], "reject")
        self.assertTrue(response["msg"].startswith("rate-limited: pubkey"))

    def test_rejects_source_over_limit(self):
        policy = self.policy(STRFRY_POLICY_IP_CAPACITY="1")
        self.assertEqual(policy.handle(self.request())["action"], "accept")
        response = policy.handle(
            self.request(event_id="03", pubkey="04")
        )
        self.assertEqual(response["action"], "reject")
        self.assertTrue(response["msg"].startswith("rate-limited: source"))

    def test_rejects_global_over_limit(self):
        policy = self.policy(STRFRY_POLICY_GLOBAL_CAPACITY="1")
        self.assertEqual(policy.handle(self.request())["action"], "accept")
        response = policy.handle(
            self.request(event_id="03", pubkey="04", source="192.0.2.2")
        )
        self.assertEqual(response["action"], "reject")
        self.assertTrue(response["msg"].startswith("rate-limited: relay"))

    def test_rejects_when_database_reaches_budget(self):
        self.db_file.write_bytes(b"x" * 101)
        policy = self.policy(STRFRY_POLICY_MAX_DB_BYTES="100")
        response = policy.handle(self.request())
        self.assertEqual(response["action"], "reject")
        self.assertEqual(response["msg"], "blocked: relay storage budget reached")

    def test_pubkey_tracking_is_bounded(self):
        policy = self.policy(STRFRY_POLICY_MAX_TRACKED_PUBKEYS="1")
        self.assertEqual(policy.handle(self.request())["action"], "accept")
        response = policy.handle(self.request(event_id="03", pubkey="04"))
        self.assertEqual(response["action"], "reject")
        self.assertEqual(
            response["msg"], "rate-limited: pubkey tracking limit reached"
        )


if __name__ == "__main__":
    unittest.main()
