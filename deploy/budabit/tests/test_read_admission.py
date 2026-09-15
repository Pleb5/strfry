import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

from fixtures import ADDRESS, MEMBER, OUTSIDER, OWNER, definition, person_report, request, shard, standard_events
from policy.budabit.config import BudabitConfig
from policy.budabit.read_admission import ReadAdmission
from policy.budabit.read_scanner import BoundedReadScanner, ReadScanError
from test_loader import FakeScanner
from test_write_policy import write_policy


class ReadAdmissionTests(unittest.TestCase):
    def config(self, **overrides):
        return BudabitConfig.from_env({"BUDABIT_READ_CONTROL": "members", "BUDABIT_BRANCHES": ADDRESS, **overrides})

    def policy(self, events=(), **kwargs):
        scanner = FakeScanner(events)
        policy = ReadAdmission(self.config(), scanner_factory=lambda: scanner, start=False, **kwargs)
        self.addCleanup(policy.stop)
        return policy, scanner

    def test_initial_load_and_owner_bootstrap(self):
        policy, _ = self.policy()
        self.assertEqual(policy.decide([OWNER]), "unavailable")
        self.assertTrue(policy.refresh())
        self.assertEqual(policy.decide([OWNER]), "allow")
        self.assertEqual(policy.decide([OUTSIDER]), "deny")
        policy.stop()
        self.assertEqual(policy.decide([OWNER]), "unavailable")

    def test_membership_multiple_keys_and_eventual_revocation(self):
        policy, scanner = self.policy(standard_events())
        policy.refresh()
        self.assertEqual(policy.decide([MEMBER, OUTSIDER]), "allow")
        scanner.events.append(person_report(OWNER, MEMBER))
        self.assertEqual(policy.decide([MEMBER]), "allow")  # expressly eventual
        policy.refresh()
        self.assertEqual(policy.decide([MEMBER]), "deny")
        self.assertEqual(policy.decide([MEMBER, OWNER]), "allow")

    def test_requests_are_lookups_not_scans(self):
        policy, scanner = self.policy(standard_events())
        policy.refresh()
        scans = len(scanner.calls)
        for i in range(10000):
            self.assertEqual(policy.handle({"type": "read-admission", "request_id": str(i),
                                           "authenticated_pubkeys": [MEMBER]}),
                             {"request_id": str(i), "decision": "allow"})
        self.assertEqual(len(scanner.calls), scans)

    def test_failed_refresh_cannot_renew_policy_age(self):
        now = [0.0]
        policy, scanner = self.policy(standard_events(), clock=lambda: now[0])
        self.assertTrue(policy.refresh())
        scanner.scan = lambda _: (_ for _ in ()).throw(RuntimeError("private detail"))
        now[0] = 9
        self.assertFalse(policy.refresh())
        self.assertEqual(policy.decide([MEMBER]), "allow")
        self.assertNotIn("private detail", policy.last_error)
        now[0] = 10
        self.assertEqual(policy.decide([MEMBER]), "unavailable")
        self.assertEqual(policy.decide([OWNER]), "unavailable")

    def test_partial_or_oversized_first_scan_never_bootstraps(self):
        policy, scanner = self.policy(standard_events())
        policy.config.read_max_pubkeys = 1
        self.assertFalse(policy.refresh())
        self.assertEqual(policy.decide([OWNER]), "unavailable")
        scanner.scan = lambda _: (_ for _ in ()).throw(ReadScanError("partial"))
        self.assertFalse(policy.refresh())
        self.assertEqual(policy.decide([OWNER]), "unavailable")

    def test_write_acceptance_does_not_grant_reads(self):
        events = standard_events()
        policy, scanner = self.policy(events)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        write = write_policy.WritePolicy(env={"BUDABIT_BRANCHES": ADDRESS, "STRFRY_POLICY_MIN_FREE_BYTES": "0",
                                             "STRFRY_POLICY_DB_FILE": str(Path(directory.name) / "data.mdb")},
                                        scanner=FakeScanner(events), start_loader=False)
        self.addCleanup(write.stop)
        write.budabit.loader.warm_up()
        grant = shard(OWNER, "general", [OUTSIDER])
        response = write.handle(request(grant))
        self.assertEqual(response["action"], "accept")
        self.assertNotIn("policyRelevant", response)
        policy.refresh()
        self.assertEqual(policy.decide([OUTSIDER]), "deny")
        scanner.events.append(grant)
        policy.refresh()
        self.assertEqual(policy.decide([OUTSIDER]), "allow")

    def test_blocked_refresh_does_not_block_admission(self):
        entered, release = threading.Event(), threading.Event()
        policy, scanner = self.policy(standard_events())
        policy.refresh()
        def blocked(_):
            entered.set()
            release.wait(2)
            raise ReadScanError("blocked")
        scanner.scan = blocked
        thread = threading.Thread(target=policy.refresh)
        thread.start()
        self.assertTrue(entered.wait(1))
        self.assertEqual(policy.decide([MEMBER]), "allow")
        release.set()
        thread.join()

    def test_bad_requests_and_config(self):
        policy, _ = self.policy()
        for value in [None, {}, {"type": "committed"},
                      {"type": "read-admission", "request_id": "1", "authenticated_pubkeys": [True]},
                      {"type": "read-admission", "request_id": "1", "authenticated_pubkeys": [OWNER] * 33}]:
            with self.assertRaises((ValueError, TypeError)):
                policy.handle(value)
        for overrides in [{"BUDABIT_BRANCHES": ""}, {"BUDABIT_DRY_RUN": "1"},
                          {"BUDABIT_AUTO_HOST_URL": "wss://relay.test"}, {"BUDABIT_DISABLE_LOADER": "1"},
                          {"BUDABIT_READ_MAX_AGE_SECONDS": "nan"}, {"BUDABIT_READ_REFRESH_SECONDS": "10"},
                          {"BUDABIT_READ_SCAN_TIMEOUT_SECONDS": "10"}, {"BUDABIT_READ_MAX_PUBKEYS": "0"}]:
            with self.assertRaises(ValueError):
                self.config(**overrides)


class BoundedScannerTests(unittest.TestCase):
    def scan(self, program, *, maximum=100000, timeout=2, scans=1):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "scan"
            executable.write_text(f"#!{sys.executable}\n" + program)
            executable.chmod(0o700)
            config = BudabitConfig(strfry_bin=str(executable), read_scan_max_bytes=maximum, read_scan_timeout_seconds=timeout)
            scanner = BoundedReadScanner(config)
            return [scanner.scan({}) for _ in range(scans)]

    def test_valid_empty_and_malformed(self):
        ev = definition()
        self.assertEqual(self.scan(f"print({json.dumps(ev)!r})"), [[ev]])
        self.assertEqual(self.scan("pass"), [[]])
        for output in ("{", "null", "[]", '{"kind":5}', "not JSON"):
            with self.assertRaises(ReadScanError):
                self.scan(f"print({output!r})")

    def test_budget_deadline_and_nonzero_exit(self):
        for program, scans in [("print('x'*200)", 1), ("import sys\nprint('x'*200, file=sys.stderr)", 1), ("print(' '*60)", 2)]:
            with self.assertRaises(ReadScanError):
                self.scan(program, maximum=100, scans=scans)
        with self.assertRaises(ReadScanError):
            self.scan("import time\ntime.sleep(2)", timeout=0.1)
        with self.assertRaises(ReadScanError):
            self.scan("import sys\nsys.exit(1)")
