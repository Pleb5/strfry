"""A cold ingestion process must not confuse undiscovered with unhosted."""

import io
import threading
import unittest

from fixtures import (
    ADDRESS,
    COMMUNITY,
    MEMBER,
    OTHER_COMMUNITY,
    OUTSIDER,
    OWNER,
    config,
    definition,
    event,
    request,
    room_message,
    room_root,
    shard,
    standard_events,
    thread,
)
from policy.budabit.metrics import Metrics
from policy.budabit.stage import BudabitWriteControl
from policy.pipeline import Pipeline
from test_loader import FakeScanner


LOADING = "error: relay policy is loading, retry shortly"


class InitializationTests(unittest.TestCase):
    def make_stage(self, events=(), scanner=None, start_loader=False, **overrides):
        cfg = config(**{
            "BUDABIT_BRANCHES": "",
            "BUDABIT_AUTO_HOST_URL": "wss://relay.example",
            **overrides,
        })
        cfg.loader_enabled = True
        stream = io.StringIO()
        stage = BudabitWriteControl(
            cfg,
            scanner=scanner if scanner is not None else FakeScanner(events),
            metrics=Metrics(stream=stream),
            start_loader=start_loader,
        )
        return stage, stream

    def decide(self, stage, ev):
        return Pipeline([stage], clock=lambda: 0.0).handle(request(ev))

    def assert_loading(self, stage, ev):
        self.assertEqual(
            self.decide(stage, ev),
            {"id": ev["id"], "action": "reject", "msg": LOADING},
        )

    def test_auto_host_cold_writes_wait(self):
        stage, stream = self.make_stage(standard_events())
        writes = [
            room_message(OUTSIDER), room_root(OUTSIDER), thread(OUTSIDER),
            event(1111, OUTSIDER, [["h", COMMUNITY]]),
            thread(MEMBER), event(1, OUTSIDER, []), definition(),
            shard(OWNER, "general", [OUTSIDER]),
            event(5, OUTSIDER, [["k", "1984"], ["e", "ab" * 32]]),
        ]
        for ev in writes:
            with self.subTest(kind=ev["kind"], author=ev["pubkey"]):
                self.assert_loading(stage, ev)
        self.assertEqual(stage.state.branches, {})
        self.assertEqual(stage.loader.scanner.calls, [])
        self.assertIn('"reason":"warming_up"', stream.getvalue())

    def test_known_protected_deletion_still_has_its_permanent_denial(self):
        stage, _ = self.make_stage()
        for tags in ([["k", "30000"]], [["a", ADDRESS]]):
            response = self.decide(stage, event(5, OWNER, tags))
            self.assertEqual(response["action"], "reject")
            self.assertEqual(response["msg"], "blocked: Deletion of kinds 32222 and 30000 is not allowed")

    def test_uninitialized_empty_auto_host_is_unhealthy(self):
        stage, _ = self.make_stage()
        ok, message = stage.health()
        self.assertFalse(ok)
        self.assertIn("initial authority load", message)
        stage.loader.warm_up()
        self.assertTrue(stage.loader.initialized)
        self.assertEqual(stage.health(), (True, ""))

    def test_successful_empty_load_allows_passthrough_and_new_communities(self):
        stage, _ = self.make_stage()
        stage.loader.warm_up()
        for ev in (event(1, OUTSIDER, []), thread(OUTSIDER)):
            self.assertEqual(self.decide(stage, ev)["action"], "accept")
        new_definition = definition()
        self.assertEqual(self.decide(stage, new_definition)["action"], "accept")
        self.assert_loading(stage, thread(OWNER))
        stage.loader.scanner.events.append(new_definition)
        stage.loader.reconcile()
        self.assertEqual(self.decide(stage, thread(OWNER))["action"], "accept")
        self.assertIn("not a current writer", self.decide(stage, thread(OUTSIDER))["msg"])

    def test_discovery_alone_does_not_open_the_gate(self):
        stage, _ = self.make_stage(standard_events())
        stage.loader.discover()
        self.assertIn(ADDRESS, stage.state.branches)
        self.assertFalse(stage.loader.initialized)
        self.assert_loading(stage, event(1, OUTSIDER, []))
        self.assert_loading(stage, thread(MEMBER))
        stage.loader.warm_up()
        self.assertEqual(self.decide(stage, thread(MEMBER))["action"], "accept")

    def test_partial_load_of_multiple_branches_is_not_initialization(self):
        events = standard_events() + [definition(
            owner=OUTSIDER,
            community=OTHER_COMMUNITY,
            sections=[("General", [["k", "1111"]], [])],
        )]

        class SecondBranchFails(FakeScanner):
            def scan(self, filter_obj):
                if filter_obj.get("authors") == [OUTSIDER]:
                    raise RuntimeError("second branch scan failed")
                return super().scan(filter_obj)

        stage, _ = self.make_stage(scanner=SecondBranchFails(events))
        with self.assertRaisesRegex(RuntimeError, "second branch scan failed"):
            stage.loader.warm_up()
        self.assertTrue(stage.state.branch(ADDRESS).warm)
        self.assertFalse(stage.loader.initialized)
        self.assert_loading(stage, thread(MEMBER))
        self.assert_loading(stage, event(1, OUTSIDER, []))
        stage.loader.scanner = FakeScanner(events)
        stage.loader.warm_up()
        self.assertEqual(self.decide(stage, thread(MEMBER))["action"], "accept")

    def test_dry_run_does_not_bypass_readiness(self):
        stage, stream = self.make_stage(standard_events(), BUDABIT_DRY_RUN="1")
        self.assert_loading(stage, thread(OUTSIDER))
        self.assertIn('"event":"reject"', stream.getvalue())
        self.assertNotIn('"event":"would_reject"', stream.getvalue())
        stage.loader.warm_up()
        self.assertEqual(self.decide(stage, thread(OUTSIDER))["action"], "accept")
        self.assertIn('"event":"would_reject"', stream.getvalue())

    def test_explicit_branches_also_wait_for_initial_load(self):
        stage, _ = self.make_stage(standard_events(), BUDABIT_BRANCHES=ADDRESS)
        self.assert_loading(stage, definition())
        self.assert_loading(stage, event(1, OUTSIDER, []))
        stage.loader.warm_up()
        self.assertEqual(self.decide(stage, definition())["action"], "accept")

    def test_every_new_instance_starts_uninitialized(self):
        first, _ = self.make_stage(standard_events())
        first.loader.warm_up()
        self.assertEqual(self.decide(first, thread(MEMBER))["action"], "accept")
        replacement, _ = self.make_stage(standard_events())
        self.assert_loading(replacement, thread(MEMBER))
        self.assert_loading(replacement, thread(OUTSIDER))
        replacement.loader.warm_up()
        self.assertIn("not a current writer", self.decide(replacement, thread(OUTSIDER))["msg"])

    def test_disabled_policy_and_offline_override_still_work(self):
        cfg = config(BUDABIT_BRANCHES="", BUDABIT_AUTO_HOST_URL="")
        stage = BudabitWriteControl(cfg, metrics=Metrics(stream=io.StringIO()), start_loader=False)
        self.assertEqual(self.decide(stage, thread(OUTSIDER))["action"], "accept")
        cfg = config(BUDABIT_BRANCHES="", BUDABIT_AUTO_HOST_URL="wss://relay.example")
        stage = BudabitWriteControl(cfg, metrics=Metrics(stream=io.StringIO()), start_loader=False)
        self.assertIsNone(stage.loader)  # fixtures explicitly disable it
        self.assertEqual(self.decide(stage, definition())["action"], "accept")

    def test_no_per_event_scans_after_initialization(self):
        stage, _ = self.make_stage(standard_events())
        stage.loader.warm_up()
        calls = list(stage.loader.scanner.calls)
        for _ in range(20):
            self.assertEqual(self.decide(stage, thread(MEMBER))["action"], "accept")
            self.assertEqual(self.decide(stage, thread(OUTSIDER))["action"], "reject")
        self.assertEqual(stage.loader.scanner.calls, calls)

    def test_background_discovery_cannot_race_the_first_write(self):
        entered = threading.Event()
        release = threading.Event()

        class GatedScanner(FakeScanner):
            def scan(self, filter_obj):
                if filter_obj == {"kinds": [32222]}:
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError("test discovery gate timed out")
                return super().scan(filter_obj)

        stage, _ = self.make_stage(scanner=GatedScanner(standard_events()), start_loader=True)
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(stage.state.branches, {})
            self.assert_loading(stage, room_message(OUTSIDER))
            self.assert_loading(stage, event(1, OUTSIDER, []))
        finally:
            release.set()
            stage.loader.stop()
            stage.loader.thread.join(timeout=2)
        self.assertFalse(stage.loader.thread.is_alive())

    def test_failed_initial_load_retries_with_capped_backoff(self):
        class FailingScanner:
            def scan(self, filter_obj):
                raise RuntimeError("scan unavailable")

        stage, stream = self.make_stage(scanner=FailingScanner())
        delays = []

        def sleep(seconds):
            delays.append(seconds)
            self.assert_loading(stage, event(1, OUTSIDER, []))
            if len(delays) == 7:
                stage.loader.stop()

        stage.loader.sleep = sleep
        stage.loader.run()
        self.assertEqual(delays, [1, 2, 4, 8, 16, 30, 30])
        self.assertFalse(stage.loader.initialized)
        self.assertEqual(stage.loader.last_error, "scan unavailable")
        self.assertFalse(stage.health()[0])
        self.assertNotIn('"event":"initial_load_complete"', stream.getvalue())

    def test_initial_retry_recovers_without_waiting_for_regular_reconcile(self):
        class RecoveringScanner(FakeScanner):
            failures = 2

            def scan(self, filter_obj):
                if self.failures:
                    self.failures -= 1
                    raise RuntimeError("temporary scan failure")
                return super().scan(filter_obj)

        stage, stream = self.make_stage(scanner=RecoveringScanner(standard_events()))
        delays = []

        def sleep(seconds):
            delays.append(seconds)
            if stage.loader.initialized:
                stage.loader.stop()

        stage.loader.sleep = sleep
        stage.loader.run()
        self.assertEqual(delays[:2], [1, 2])
        self.assertEqual(len(delays), 3)
        self.assertTrue(stage.loader.initialized)
        self.assertEqual(stage.loader.last_error, "")
        self.assertEqual(self.decide(stage, thread(MEMBER))["action"], "accept")
        self.assertIn("not a current writer", self.decide(stage, thread(OUTSIDER))["msg"])
        self.assertEqual(stream.getvalue().count('"event":"initial_load_complete"'), 1)

    def test_regular_refresh_failure_does_not_reopen_initialization_or_passthrough(self):
        scanner = FakeScanner(standard_events())
        stage, _ = self.make_stage(scanner=scanner)
        clock = [0.0]
        ticks = []
        stage.loader.clock = lambda: clock[0]
        stage.loader.reconcile_seconds = 1

        class FailingScanner:
            def scan(self, filter_obj):
                raise RuntimeError("refresh unavailable")

        def sleep(seconds):
            clock[0] += seconds
            ticks.append(seconds)
            if len(ticks) == 1:
                stage.loader.scanner = FailingScanner()
            else:
                stage.loader.stop()

        stage.loader.sleep = sleep
        stage.loader.run()
        self.assertTrue(stage.loader.initialized)
        self.assertEqual(stage.loader.last_error, "refresh unavailable")
        self.assertFalse(stage.health()[0])
        self.assertEqual(self.decide(stage, thread(MEMBER))["action"], "accept")
        self.assertIn("not a current writer", self.decide(stage, thread(OUTSIDER))["msg"])
        self.assertEqual(self.decide(stage, event(1, OUTSIDER, []))["action"], "accept")


if __name__ == "__main__":
    unittest.main()
