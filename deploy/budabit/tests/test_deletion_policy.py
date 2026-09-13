import io
import unittest

from fixtures import ADDRESS, OWNER, config, event, standard_events, warm_state
from policy.budabit.metrics import Metrics
from policy.budabit.stage import BudabitWriteControl
from policy.budabit import rules
from policy.pipeline import Pipeline


class DeletionPolicyTests(unittest.TestCase):
    def make_stage(self, **overrides):
        stream = io.StringIO()
        stage = BudabitWriteControl(config(**overrides), metrics=Metrics(stream=stream), start_loader=False)
        stage.state = warm_state(standard_events())
        return stage, stream

    def send(self, stage, deletion):
        return Pipeline([stage], clock=lambda: 0.0).handle({
            "type": "new", "event": deletion, "receivedAt": 1,
            "sourceType": "IP4", "sourceInfo": "127.0.0.1",
        })

    def test_rejected_delete_never_mutates_authority(self):
        stage, stream = self.make_stage()
        original = stage.state.branch(ADDRESS).definition_coord.current
        for tags in ([["a", ADDRESS]], [["e", original["id"]]], [["k", "32222"]]):
            response = self.send(stage, event(5, OWNER, tags))
            self.assertEqual(response["action"], "reject")
            self.assertEqual(response["msg"], rules.PROTECTED_DELETE_MESSAGE)
            self.assertIs(stage.state.branch(ADDRESS).definition_coord.current, original)
        self.assertIn('"reason":"protected_kind_deletion"', stream.getvalue())

    def test_dry_run_observes_without_enforcing(self):
        stage, stream = self.make_stage(BUDABIT_DRY_RUN="1")
        self.assertEqual(self.send(stage, event(5, OWNER, [["k", "30000"]]))["action"], "accept")
        self.assertIn('"event":"would_reject"', stream.getvalue())

    def test_nip11_distinguishes_dry_run_and_enforcement(self):
        stage, _ = self.make_stage()
        advertised = stage.nip11_extra()["budabit"]
        self.assertTrue(advertised["enforcing"])
        self.assertFalse(advertised["dry_run"])
        self.assertEqual(advertised["protected_deletion_kinds"], [30000, 32222])
        stage, _ = self.make_stage(BUDABIT_DRY_RUN="1")
        advertised = stage.nip11_extra()["budabit"]
        self.assertFalse(advertised["enforcing"])
        self.assertEqual(advertised["enforced_branches"], [])
        self.assertEqual(advertised["configured_branches"], [ADDRESS])


if __name__ == "__main__":
    unittest.main()
