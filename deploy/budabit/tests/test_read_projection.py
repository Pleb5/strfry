import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fixtures import (
    ADDRESS, MEMBER, MEMBER2, MOD_GENERAL, OUTSIDER, OWNER,
    definition, person_report, request, shard, standard_events,
)
from policy.budabit.config import BudabitConfig
from policy.budabit.metrics import Metrics
from policy.budabit.readers import ReadProjection, check_read_snapshot
from policy.budabit.read_scanner import BoundedReadScanner, ReadScanError
from test_loader import FakeScanner
from test_write_policy import write_policy

EPOCH = "1" * 64


class ReadProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "readers.json"
        self.env = {
            "BUDABIT_BRANCHES": ADDRESS,
            "BUDABIT_READ_CONTROL": "members",
            "BUDABIT_READ_SNAPSHOT_PATH": str(self.path),
            "STRFRY_POLICY_DB_FILE": str(Path(self.tmp.name) / "data.mdb"),
            "STRFRY_POLICY_MIN_FREE_BYTES": "0",
        }

    def config(self, **overrides):
        return BudabitConfig.from_env({**self.env, **overrides})

    def projection(self, events=(), factory=None, **overrides):
        scanner = FakeScanner(events)
        projection = ReadProjection(
            self.config(**overrides), Metrics(stream=io.StringIO()),
            scanner_factory=factory or (lambda: scanner), auto_start=False,
        )
        self.addCleanup(projection.stop)
        return projection, scanner

    def init(self, projection, seq=0):
        projection.control({"type": "read-control-init", "epoch": EPOCH,
                            "seq": seq, "branch_address": ADDRESS})

    def commit(self, projection, seq):
        projection.control({"type": "committed", "epoch": EPOCH, "seq": seq})

    def snapshot(self):
        return json.loads(self.path.read_text())

    def test_invalid_config_fails_before_start(self):
        for overrides in (
            {"BUDABIT_READ_CONTROL": "surprise"}, {"BUDABIT_BRANCHES": ""},
            {"BUDABIT_DRY_RUN": "1"}, {"BUDABIT_DISABLE_LOADER": "1"},
            {"BUDABIT_AUTO_HOST_URL": "wss://relay.example"},
            {"BUDABIT_BRANCHES": ADDRESS + "," + ADDRESS.replace(OWNER, OUTSIDER)},
            {"BUDABIT_READ_SNAPSHOT_PATH": "relative.json"},
            {"BUDABIT_READ_MAX_PUBKEYS": "0"}, {"BUDABIT_READ_MAX_PUBKEYS": "999999999"},
            {"BUDABIT_READ_MAX_SNAPSHOT_BYTES": "12"},
            {"BUDABIT_READ_SCAN_MAX_BYTES": "-1"},
            {"BUDABIT_READ_SCAN_TIMEOUT_SECONDS": "nan"},
            {"BUDABIT_RECONCILE_SECONDS": "inf"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.config(**overrides)

    def test_default_off_no_projection_files_or_response_metadata(self):
        env = {**self.env, "BUDABIT_READ_CONTROL": "off"}
        policy = write_policy.WritePolicy(env=env, start_loader=False, scanner=FakeScanner(standard_events()))
        self.addCleanup(policy.stop)
        self.assertIsNone(policy.readers)
        policy.budabit.loader.warm_up()
        result = policy.handle(request(shard(OWNER, "general", [OUTSIDER])))
        self.assertEqual(set(result), {"id", "action"})
        self.assertEqual(result["action"], "accept")
        self.assertFalse(self.path.exists())

    def test_complete_empty_scan_bootstraps_owner_not_uninitialized_disk_file(self):
        projection, _ = self.projection()
        self.assertFalse(projection.rebuild())
        self.assertFalse(self.path.exists())
        self.init(projection)
        self.assertTrue(projection.rebuild())
        self.assertEqual(self.snapshot()["eligible_pubkeys"], [OWNER])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertTrue(check_read_snapshot(projection.config, expected_epoch=EPOCH, expected_seq=0)[0])
        self.assertNotIn("eligible_pubkeys", json.loads(projection.status_path.read_text()))

    def test_committed_rebuild_never_uses_optimistic_stage(self):
        events = standard_events()
        policy = write_policy.WritePolicy(env=self.env, start_loader=False, scanner=FakeScanner(events))
        self.addCleanup(policy.stop)
        policy.budabit.loader.warm_up()
        scanner = FakeScanner(events)
        projection = policy.readers
        projection.scanner_factory = lambda: scanner
        self.init(projection)
        self.assertTrue(projection.rebuild())
        grant = shard(OWNER, "general", [OUTSIDER])
        self.assertEqual(policy.handle(request(grant))["policyRelevant"], True)
        self.assertTrue(policy.budabit.state.branch(ADDRESS).derived().has_any_role(OUTSIDER))
        self.assertTrue(projection.rebuild())
        self.assertNotIn(OUTSIDER, self.snapshot()["eligible_pubkeys"])
        scanner.events.append(grant)  # simulate confirmed storage, not just acceptance
        self.commit(projection, 1)
        self.assertTrue(projection.rebuild())
        self.assertIn(OUTSIDER, self.snapshot()["eligible_pubkeys"])
        scanner.events.append(person_report(OWNER, OUTSIDER))
        self.commit(projection, 2)
        self.assertTrue(projection.rebuild())
        self.assertNotIn(OUTSIDER, self.snapshot()["eligible_pubkeys"])

    def test_commit_during_scan_does_not_relabel_older_projection(self):
        projection, scanner = self.projection(standard_events())
        scan = scanner.scan
        def intervening_commit(filter_obj):
            if projection.seq == 0:
                self.commit(projection, 1)
            return scan(filter_obj)
        scanner.scan = intervening_commit
        self.init(projection)
        self.assertFalse(projection.rebuild())
        self.assertFalse(self.snapshot()["ready"])
        self.assertTrue(projection.dirty)
        self.assertTrue(projection.rebuild())
        self.assertEqual(self.snapshot()["seq"], 1)

    def test_control_epoch_and_sequence_reject_restarts_regressions_gaps(self):
        projection, _ = self.projection()
        with self.assertRaises(ValueError):
            self.commit(projection, 0)
        self.init(projection, 7)
        for message in (
            {"type": "committed", "epoch": "2" * 64, "seq": 8},
            {"type": "committed", "epoch": EPOCH, "seq": 6},
            {"type": "committed", "epoch": EPOCH, "seq": 9},
            {"type": "committed", "epoch": EPOCH, "seq": True},
            {"type": "read-control-init", "epoch": EPOCH, "seq": 7, "branch_address": ADDRESS},
        ):
            with self.subTest(message=message), self.assertRaises(ValueError):
                projection.control(message)
        self.commit(projection, 7)  # idempotent notice
        self.commit(projection, 8)
        self.assertTrue(projection.rebuild())

    def test_partial_failed_scan_clears_owner_and_all_readers(self):
        projection, scanner = self.projection(standard_events())
        self.init(projection)
        self.assertTrue(projection.rebuild())
        scanner.scan = lambda _: (_ for _ in ()).throw(RuntimeError("private detail"))
        self.assertFalse(projection.rebuild())
        self.assertFalse(self.snapshot()["ready"])
        self.assertEqual(self.snapshot()["eligible_pubkeys"], [])
        self.assertNotIn("private detail", projection.status_path.read_text())

    def test_reader_and_file_bounds_fail_closed_never_truncate(self):
        for overrides in ({"BUDABIT_READ_MAX_PUBKEYS": "1"},
                          {"BUDABIT_READ_MAX_SNAPSHOT_BYTES": "1024"}):
            projection, _ = self.projection(
                [definition(), shard(OWNER, "general", [f"{i:064x}" for i in range(100)])], **overrides)
            self.init(projection)
            self.assertFalse(projection.rebuild())
            self.assertFalse(self.snapshot()["ready"])
            self.assertEqual(self.snapshot()["eligible_pubkeys"], [])

    def test_snapshot_health_checks_current_identity_bytes_keys_and_age(self):
        projection, _ = self.projection()
        self.init(projection)
        projection.rebuild()
        original = self.snapshot()
        self.assertFalse(check_read_snapshot(projection.config, expected_epoch="2" * 64)[0])
        self.assertFalse(check_read_snapshot(projection.config, expected_seq=1)[0])
        for values in ({"updated_at": 0}, {"updated_at": float("nan")}, {"ready": False},
                       {"version": True}, {"eligible_pubkeys": [OWNER, OWNER]},
                       {"eligible_pubkeys": [MEMBER]}, {"write_enforcement": False}):
            self.path.write_text(json.dumps({**original, **values}))
            self.assertFalse(check_read_snapshot(projection.config)[0], values)
        self.path.write_text("x" * (projection.config.read_max_snapshot_bytes + 1))
        self.assertFalse(check_read_snapshot(projection.config)[0])
        self.path.unlink()
        self.assertFalse(check_read_snapshot(projection.config)[0])

    def test_jsonl_control_no_stdout_and_invalid_control_fails_closed(self):
        policy = write_policy.WritePolicy(env=self.env, start_loader=False, scanner=FakeScanner([]))
        self.addCleanup(policy.stop)
        policy.budabit.loader.warm_up()
        lines = [
            {"type": "read-control-init", "epoch": EPOCH, "seq": 0, "branch_address": ADDRESS},
            {"type": "committed", "epoch": EPOCH, "seq": 1},
            request(definition()),
            {"type": "committed", "epoch": "2" * 64, "seq": 2},
        ]
        output = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO("\n".join(map(json.dumps, lines)))), patch.object(sys, "stdout", output):
            write_policy.serve(policy)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["action"], "accept")
        self.assertTrue(responses[0]["policyRelevant"])
        self.assertFalse(self.snapshot()["ready"])
        self.assertTrue(policy.readers.invalid)

    def test_worker_rebuilds_then_heartbeats_and_stops(self):
        projection, scanner = self.projection(standard_events(), BUDABIT_RECONCILE_SECONDS="0.02")
        projection.auto_start = True
        self.init(projection)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if projection.snapshot and projection.snapshot["ready"] and len(scanner.calls) > 25:
                break
            time.sleep(0.01)
        self.assertTrue(projection.snapshot["ready"])
        self.assertGreater(len(scanner.calls), 25)
        projection.stop()
        self.assertFalse(self.snapshot()["ready"])
        self.assertFalse(projection.thread.is_alive())

    def test_blocked_worker_does_not_heartbeat(self):
        entered, release = threading.Event(), threading.Event()
        class BlockedScanner:
            def scan(self, _):
                entered.set()
                release.wait(3)
                raise RuntimeError("blocked")
        projection, _ = self.projection(factory=BlockedScanner)
        projection.auto_start = True
        self.init(projection)
        self.assertTrue(entered.wait(2))
        before = self.path.read_bytes()
        time.sleep(1.1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.snapshot()["ready"])
        release.set()


class BoundedScannerTests(unittest.TestCase):
    def scan(self, program, *, maximum=100000, timeout=2, scans=1):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "scan"
            executable.write_text(f"#!{sys.executable}\n" + program)
            executable.chmod(0o700)
            config = BudabitConfig(strfry_bin=str(executable), read_scan_max_bytes=maximum,
                                   read_scan_timeout_seconds=timeout)
            scanner = BoundedReadScanner(config)
            return [scanner.scan({}) for _ in range(scans)]

    def test_valid_and_empty(self):
        ev = definition()
        self.assertEqual(self.scan(f"print({json.dumps(ev)!r})"), [[ev]])
        self.assertEqual(self.scan("pass"), [[]])

    def test_malformed_or_incomplete_output_is_not_silently_skipped(self):
        for output in ("{", "null", "[]", '{"kind":5}', "not JSON"):
            with self.subTest(output=output), self.assertRaises(ReadScanError):
                self.scan(f"print({output!r})")
        with self.assertRaises(ReadScanError):
            self.scan("import sys\nprint('private scanner detail', file=sys.stderr)\nsys.exit(1)")

    def test_output_budget_aggregates_stdout_stderr_and_scans(self):
        for program, scans in (("print('x'*200)", 1),
                               ("import sys\nprint('x'*200, file=sys.stderr)", 1),
                               ("print(' '*60)", 2)):
            with self.subTest(program=program), self.assertRaises(ReadScanError):
                self.scan(program, maximum=100, scans=scans)

    def test_hung_child_is_killed_at_deadline(self):
        start = time.monotonic()
        with self.assertRaises(ReadScanError):
            self.scan("import time\ntime.sleep(30)", timeout=0.15)
        self.assertLess(time.monotonic() - start, 2)


if __name__ == "__main__":
    unittest.main()
