import io
import time
import unittest

from fixtures import (
    ADDRESS,
    COMMUNITY,
    MEMBER,
    MEMBER2,
    MOD_GENERAL,
    OUTSIDER,
    OWNER,
    config,
    definition,
    event,
    person_report,
    report_delete,
    request,
    shard,
    shard_address,
    standard_events,
    thread,
)
from policy.budabit.loader import Loader
from policy.budabit.metrics import Metrics
from policy.budabit.stage import BudabitWriteControl
from policy.budabit.state import CommunityState
from policy.pipeline import Pipeline


class FakeScanner:
    """Answers strfry-style filters against an in-memory event list."""

    def __init__(self, events):
        self.events = list(events)
        self.calls = []

    def scan(self, filter_obj):
        self.calls.append(filter_obj)
        out = []
        for ev in self.events:
            if "kinds" in filter_obj and ev["kind"] not in filter_obj["kinds"]:
                continue
            if "authors" in filter_obj and ev["pubkey"] not in filter_obj["authors"]:
                continue
            ok = True
            for key, values in filter_obj.items():
                if not key.startswith("#"):
                    continue
                name = key[1:]
                if not any(tag and tag[0] == name and len(tag) > 1 and tag[1] in values for tag in ev["tags"]):
                    ok = False
                    break
            if ok:
                out.append(ev)
        return out


def make_loader(events, state=None):
    state = state or CommunityState([ADDRESS])
    metrics = Metrics(stream=io.StringIO())
    scanner = FakeScanner(events)
    loader = Loader(state, scanner, metrics, reconcile_seconds=300)
    return state, loader, scanner, metrics


class LoaderTests(unittest.TestCase):
    def test_warm_up_builds_state(self):
        events = standard_events() + [person_report(OWNER, OUTSIDER)]
        state, loader, scanner, _ = make_loader(events)
        branch = state.branch(ADDRESS)
        self.assertFalse(branch.warm)
        loader.warm_up()
        self.assertTrue(branch.warm)
        derived = branch.derived()
        self.assertTrue(derived.available)
        self.assertTrue(derived.can_write(MEMBER, 11, "threads"))
        self.assertTrue(derived.can_write(MEMBER2, 9, "room-message"))
        self.assertEqual(derived.person_bans, {OUTSIDER})
        self.assertFalse(branch.needs_reconcile)

    def test_missing_shard_logged(self):
        events = [definition()]
        stream = io.StringIO()
        state = CommunityState([ADDRESS])
        loader = Loader(state, FakeScanner(events), Metrics(stream=stream))
        loader.warm_up()
        self.assertIn("shard_missing", stream.getvalue())
        derived = state.branch(ADDRESS).derived()
        self.assertTrue(derived.available)
        self.assertFalse(derived.can_write(MEMBER, 1111))

    def test_deleted_report_loaded_before_report(self):
        report = person_report(OWNER, OUTSIDER)
        events = standard_events() + [report, report_delete(report)]
        state, loader, _, _ = make_loader(events)
        loader.warm_up()
        self.assertEqual(state.branch(ADDRESS).derived().person_bans, set())

    def test_reconcile_picks_up_new_reference(self):
        events = standard_events()
        state, loader, scanner, _ = make_loader(events)
        loader.warm_up()
        branch = state.branch(ADDRESS)
        # Owner publishes a definition that adds a new shard coordinate; the
        # shard itself only exists in storage (e.g. arrived via import).
        new_ref = shard_address(MOD_GENERAL, "thread-creator")
        sections = [
            ("General", [["k", "1111"], ["k", "1984"]], [shard_address(OWNER, "general")]),
            ("Thread-creator", [["k", "11", "threads"]], [shard_address(OWNER, "thread-creator"), new_ref]),
        ]
        new_definition = definition(sections=sections)
        scanner.events.append(new_definition)
        scanner.events.append(shard(MOD_GENERAL, "thread-creator", [OUTSIDER]))
        state.apply(new_definition)
        self.assertTrue(branch.needs_reconcile)
        self.assertFalse(branch.derived().can_write(OUTSIDER, 11, "threads"))
        loader.reconcile()
        self.assertFalse(branch.needs_reconcile)
        self.assertTrue(branch.derived().can_write(OUTSIDER, 11, "threads"))

    def test_reconcile_drops_events_missing_from_storage(self):
        events = standard_events()
        state, loader, scanner, _ = make_loader(events)
        loader.warm_up()
        branch = state.branch(ADDRESS)
        self.assertTrue(branch.derived().can_write(MEMBER, 11, "threads"))
        # Operator ran `strfry delete` on the thread-creator shard...
        scanner.events = [e for e in scanner.events if not (e["kind"] == 30000 and "thread-creator" in e["tags"][0][1])]
        loader.reconcile()
        self.assertFalse(branch.derived().can_write(MEMBER, 11, "threads"))
        self.assertTrue(branch.derived().available)
        # ...and then removed the definition itself.
        scanner.events = [e for e in scanner.events if e["kind"] != 32222]
        loader.reconcile()
        self.assertFalse(branch.derived().available)

    def test_reconcile_drops_reports_missing_from_storage(self):
        report = person_report(OWNER, OUTSIDER)
        state, loader, scanner, _ = make_loader(standard_events() + [report])
        loader.warm_up()
        branch = state.branch(ADDRESS)
        self.assertEqual(branch.derived().person_bans, {OUTSIDER})
        scanner.events = [e for e in scanner.events if e["id"] != report["id"]]
        loader.reconcile()
        self.assertEqual(branch.derived().person_bans, set())

    def test_reconcile_keeps_inline_events_within_grace(self):
        state, loader, scanner, _ = make_loader(standard_events())
        loader.warm_up()
        branch = state.branch(ADDRESS)
        # Accepted inline a moment ago; strfry has not committed it yet, so the
        # scan does not return it. It must survive this reconcile.
        granting = shard(OWNER, "thread-creator", [OUTSIDER])
        state.apply(granting, inline=True)
        report = person_report(OWNER, MEMBER)
        state.apply(report, inline=True)
        loader.reconcile()
        self.assertTrue(branch.derived().can_write(OUTSIDER, 11, "threads"))
        self.assertIn(MEMBER, branch.derived().person_bans)
        # Once the grace window has passed and storage still lacks them, drop.
        branch.clock = lambda: time.monotonic() + 10_000.0
        loader.reconcile()
        self.assertFalse(branch.derived().can_write(OUTSIDER, 11, "threads"))
        self.assertNotIn(MEMBER, branch.derived().person_bans)

    def test_stale_definition_in_storage_does_not_regress(self):
        old = definition(created_at=1_000)
        new = definition(sections=[("General", [["k", "1111"]], [])], created_at=2_000)
        state, loader, _, _ = make_loader([old, new])
        loader.warm_up()
        self.assertEqual(len(state.branch(ADDRESS).derived().definition.sections), 1)


class StageTests(unittest.TestCase):
    def make_stage(self, events, **overrides):
        cfg = config(**overrides)
        cfg.loader_enabled = True
        stream = io.StringIO()
        stage = BudabitWriteControl(cfg, metrics=Metrics(stream=stream), scanner=FakeScanner(events), start_loader=False)
        stage.loader.warm_up()
        return stage, stream

    def decide(self, stage, ev):
        pipeline = Pipeline([stage], clock=lambda: 0.0)
        return pipeline.handle(request(ev))

    def test_reject_and_commit(self):
        stage, stream = self.make_stage(standard_events())
        self.assertEqual(self.decide(stage, thread(OUTSIDER))["action"], "reject")
        self.assertIn('"reason":"no_grant"', stream.getvalue())
        # Granting inline makes the very next write pass without reconcile.
        self.assertEqual(self.decide(stage, shard(OWNER, "thread-creator", [MEMBER, OUTSIDER]))["action"], "accept")
        self.assertIn("shard_updated", stream.getvalue())
        self.assertEqual(self.decide(stage, thread(OUTSIDER))["action"], "accept")

    def test_dry_run_accepts_but_logs(self):
        stage, stream = self.make_stage(standard_events(), BUDABIT_DRY_RUN="1")
        self.assertEqual(self.decide(stage, thread(OUTSIDER))["action"], "accept")
        self.assertIn('"event":"would_reject"', stream.getvalue())

    def test_health_and_status(self):
        stage, _ = self.make_stage(standard_events())
        ok, message = stage.health()
        self.assertTrue(ok, message)
        status = stage.status()
        self.assertEqual(status[0]["address"], ADDRESS)
        self.assertTrue(status[0]["definition"])
        stage, _ = self.make_stage([])
        ok, message = stage.health()
        self.assertFalse(ok)
        self.assertIn("no valid definition", message)

    def test_disabled_without_branches(self):
        cfg = config(BUDABIT_BRANCHES="")
        stage = BudabitWriteControl(cfg, metrics=Metrics(stream=io.StringIO()), start_loader=False)
        self.assertIsNone(stage.loader)
        self.assertEqual(self.decide(stage, thread(OUTSIDER))["action"], "accept")

    def test_rejected_authority_event_not_applied(self):
        stage, _ = self.make_stage(standard_events())
        # A moderator's person report is rejected and must not create a ban.
        self.assertEqual(self.decide(stage, person_report(MOD_GENERAL, MEMBER))["action"], "reject")
        self.assertEqual(self.decide(stage, thread(MEMBER))["action"], "accept")

    def test_state_only_updated_when_pipeline_accepts(self):
        class Deny:
            name = "deny"

            def evaluate(self, request, now):
                from policy.pipeline import Decision

                return Decision.reject("rate-limited: test", "test")

            def commit(self, request, now):
                pass

            def health(self):
                return True, ""

        stage, _ = self.make_stage(standard_events())
        pipeline = Pipeline([stage, Deny()], clock=lambda: 0.0)
        response = pipeline.handle(request(person_report(OWNER, MEMBER)))
        self.assertEqual(response["action"], "reject")
        self.assertEqual(stage.state.branch(ADDRESS).derived().person_bans, set())


if __name__ == "__main__":
    unittest.main()
