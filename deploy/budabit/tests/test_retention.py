import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from fixtures import ADDRESS, MEMBER, OWNER, person_report, report_delete, standard_events, warm_state


MODULE_PATH = Path(__file__).resolve().parents[1] / "retention.py"
SPEC = importlib.util.spec_from_file_location("retention", MODULE_PATH)
retention = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retention)


class RetentionTests(unittest.TestCase):
    def test_preserves_replaceable_kinds(self):
        for kind in (0, 3, 41, 10000, 19999, 30000, 39999, 34550):
            with self.subTest(kind=kind):
                self.assertTrue(retention.is_replaceable_kind(kind))

    def test_prunes_regular_and_ephemeral_ranges(self):
        for kind in (1, 4, 5, 7, 9999, 20000, 29999, 40000):
            with self.subTest(kind=kind):
                self.assertFalse(retention.is_replaceable_kind(kind))

    def test_preserves_all_policy_evidence(self):
        for kind in (5, 1984, 30000, 32222):
            with self.subTest(kind=kind):
                self.assertTrue(retention.is_preserved_kind(kind))
        for kind in (1, 4, 7, 9999, 20000, 29999, 40000):
            with self.subTest(kind=kind):
                self.assertFalse(retention.is_preserved_kind(kind))

    def run_retention(self, apply=False, scan_status=0):
        events = [{"kind": kind, "id": f"{kind:064x}"}
                  for kind in (5, 1984, 30000, 32222, 0, 1, 7)]
        scan = Mock(stdout=io.StringIO("\n".join(map(json.dumps, events))), args=["scan"])
        scan.wait.return_value = scan_status
        output = io.StringIO()
        args = ["retention.py", "--batch-size", "1"] + (["--apply"] if apply else [])
        with patch("sys.argv", args), patch.object(retention.subprocess, "Popen", return_value=scan), \
                patch.object(retention.subprocess, "run") as delete, redirect_stdout(output):
            if scan_status:
                with self.assertRaises(retention.subprocess.CalledProcessError):
                    retention.main()
                delete.assert_not_called()
                scan.terminate.assert_called_once()
            else:
                retention.main()
            return output.getvalue(), delete.call_args_list

    def test_dry_run_counts_exclusions_without_deleting(self):
        output, calls = self.run_retention()
        self.assertIn("would delete 2 events", output)
        self.assertIn("preserved 5 replaceable/policy events", output)
        self.assertEqual(calls, [])

    def test_apply_never_sends_evidence_ids_to_delete(self):
        output, calls = self.run_retention(apply=True)
        self.assertIn("Deleted 2 events", output)
        self.assertIn("preserved 5 replaceable/policy events", output)
        self.assertEqual(len(calls), 2)
        ids = [json.loads(call.args[0][-1].removeprefix("--filter="))["ids"] for call in calls]
        self.assertEqual(ids, [[f"{1:064x}"], [f"{7:064x}"]])

    def test_failed_scan_never_deletes_partial_selection(self):
        self.run_retention(apply=True, scan_status=1)

    def test_pruning_preserves_effective_bans_and_retractions(self):
        report = person_report(OWNER, MEMBER)
        for extra in ([report], [report, report_delete(report)]):
            with self.subTest(retracted=len(extra) == 2):
                events = standard_events() + extra
                retained = [ev for ev in events if retention.is_preserved_kind(ev["kind"])]
                before = warm_state(events).branch(ADDRESS).derived()
                after = warm_state(retained).branch(ADDRESS).derived()
                self.assertEqual(after.person_bans, before.person_bans)
                self.assertEqual(after.has_any_role(MEMBER), before.has_any_role(MEMBER))


if __name__ == "__main__":
    unittest.main()
