import unittest

from fixtures import (
    ADDRESS,
    COMMUNITY,
    MEMBER,
    MEMBER2,
    MOD_CODE,
    MOD_GENERAL,
    OTHER_COMMUNITY,
    OUTSIDER,
    OWNER,
    RELAY,
    authority_tags,
    config,
    definition,
    event,
    event_report,
    key,
    person_report,
    room_message,
    room_root,
    shard,
    shard_address,
    standard_events,
    thread,
    tombstone,
    warm_state,
)
from policy.budabit import rules
from policy.budabit.state import CommunityState


class RulesBase(unittest.TestCase):
    def setUp(self):
        self.state = warm_state(standard_events())
        self.config = config()

    def evaluate(self, ev, state=None, cfg=None):
        return rules.evaluate(ev, state or self.state, cfg or self.config)

    def assert_accept(self, ev, **kw):
        outcome = self.evaluate(ev, **kw)
        self.assertTrue(outcome.accepted, outcome.decision.msg)
        return outcome

    def assert_reject(self, ev, reason, **kw):
        outcome = self.evaluate(ev, **kw)
        self.assertFalse(outcome.accepted, f"expected {reason}")
        self.assertEqual(outcome.reason, reason, outcome.decision.msg)
        return outcome


class SectionWriteTests(RulesBase):
    def test_owner_writes_everything(self):
        self.assert_accept(thread(OWNER))
        self.assert_accept(room_root(OWNER))
        self.assert_accept(event(30617, OWNER, [["h", COMMUNITY], ["d", "repo"]]))

    def test_member_grant(self):
        self.assert_accept(thread(MEMBER))
        self.assert_accept(room_root(MEMBER))
        self.assert_accept(room_message(MEMBER))

    def test_outsider_rejected(self):
        outcome = self.assert_reject(thread(OUTSIDER), "no_grant")
        self.assertTrue(outcome.decision.msg.startswith("blocked: not a current writer"))
        self.assertIn("Thread-creator", outcome.decision.msg)

    def test_grant_in_one_section_not_another(self):
        # MEMBER2 is only in the General shard owned by MOD_GENERAL.
        self.assert_accept(room_message(MEMBER2))
        self.assert_reject(thread(MEMBER2), "no_grant")

    def test_structural_member_writes_everywhere(self):
        # MOD_CODE is referenced as a list owner; that alone grants member/write everywhere.
        self.assert_accept(thread(MOD_CODE))
        self.assert_accept(room_root(MOD_CODE))

    def test_declined_invite_keeps_structural_write(self):
        state = warm_state(standard_events() + [shard(MOD_CODE, "code-curator", [], declined=True)])
        self.assert_accept(thread(MOD_CODE), state=state)
        derived = state.branch(ADDRESS).derived()
        self.assertFalse(derived.is_moderator(MOD_CODE, derived.definition.section_named("Code-curator")))

    def test_kind_not_enabled(self):
        self.assert_reject(event(1, MEMBER, [["h", COMMUNITY]], "note"), "kind_not_enabled")
        self.assert_reject(event(30033, OWNER, [["h", COMMUNITY], ["d", "w"]]), "kind_not_enabled")

    def test_banned_member(self):
        self.state.apply(person_report(OWNER, MEMBER))
        self.assert_reject(thread(MEMBER), "person_banned")
        self.assert_reject(event(1069, MEMBER, [["a", f"30168:{OWNER}:apply", "", "form"]] + authority_tags()), "person_banned")
        self.assert_reject(event(7, MEMBER, authority_tags() + [["k", "32222"]], "+"), "person_banned")

    def test_shard_union(self):
        self.state.apply(shard(OWNER, "general", []))
        self.assert_reject(room_message(MEMBER), "no_grant")
        self.state.apply(shard(OWNER, "general", [MEMBER]))
        self.assert_accept(room_message(MEMBER))

    def test_revocation_is_immediate(self):
        self.assert_accept(thread(MEMBER))
        self.state.apply(shard(OWNER, "thread-creator", []))
        self.assert_reject(thread(MEMBER), "no_grant")

    def test_tombstoned_shard_contributes_nothing(self):
        self.state.apply(tombstone(OWNER, shard_address(OWNER, "thread-creator")))
        self.assert_reject(thread(MEMBER), "no_grant")

    def test_multiple_h_tags_invalid(self):
        self.assert_reject(event(11, MEMBER, [["h", COMMUNITY], ["h", COMMUNITY]]), "invalid_authority_tags")

    def test_malformed_marked_a(self):
        # A three-value a tag carries a relay hint, not a marker; the client ignores it too.
        self.assert_accept(event(1111, MEMBER, [["h", COMMUNITY], ["a", ADDRESS, "community"]]))
        ev = event(1111, MEMBER, [["h", COMMUNITY], ["a", f"32222:{OWNER}:{OTHER_COMMUNITY}", "", "community"]])
        self.assert_reject(ev, "invalid_authority_tags")

    def test_unhosted_branch_same_id_passthrough(self):
        ev = event(1111, OUTSIDER, [["h", COMMUNITY], ["a", f"32222:{OUTSIDER}:{COMMUNITY}", "", "community"]])
        outcome = self.assert_accept(ev)
        self.assertEqual(outcome.scope, "passthrough")

    def test_passthrough_events(self):
        self.assert_accept(event(1, OUTSIDER, [], "public note"))
        self.assert_accept(event(0, OUTSIDER, [], "{}"))
        self.assert_accept(event(11, OUTSIDER, [["h", OTHER_COMMUNITY]]))
        self.assert_accept(event(31922, OUTSIDER, [["h", "targeting-id"], ["d", "cal"]]))

    def test_moderator_label_without_section(self):
        sections = [("Threads", [["k", "11", "threads"]], [shard_address(MOD_GENERAL, "threads")])]
        state = warm_state([definition(sections=sections), shard(MOD_GENERAL, "threads", [MEMBER])])
        label = lambda pk: event(1985, pk, [["h", COMMUNITY], ["L", "budabit:room"], ["l", "archived", "budabit:room"]])
        self.assert_accept(label(OWNER), state=state)
        self.assert_accept(label(MOD_GENERAL), state=state)
        self.assert_reject(label(MEMBER), "kind_not_enabled", state=state)
        state.apply(person_report(OWNER, MOD_GENERAL))
        self.assert_reject(label(MOD_GENERAL), "kind_not_enabled", state=state)

    def test_warm_up_gates_content_even_with_definition(self):
        state = CommunityState([ADDRESS])
        for ev in standard_events():
            state.apply(ev)
        # Definition and grants loaded, reports not yet: content must wait.
        self.assert_reject(thread(MEMBER), "warming_up", state=state)
        self.assert_reject(person_report(OWNER, OUTSIDER), "warming_up", state=state)
        # Bootstrap authority events do not wait.
        self.assert_accept(definition(), state=state)
        self.assert_accept(shard(OWNER, "general", [OUTSIDER]), state=state)
        self.assert_accept(event(5, MEMBER, [["e", "a" * 64]]), state=state)
        for branch in state.branches.values():
            branch.warm = True
        self.assert_accept(thread(MEMBER), state=state)

    def test_censored_address_flag(self):
        address = f"30617:{MEMBER}:repo"
        self.state.apply(event_report(OWNER, MEMBER, "c" * 64, section="Code-curator", target_address=address))
        self.state.apply(shard(MOD_CODE, "code-curator", [MEMBER]))
        repo = event(30617, MEMBER, [["h", COMMUNITY], ["d", "repo"]])
        self.assert_accept(repo)
        self.assert_reject(repo, "censored_address", cfg=config(BUDABIT_REJECT_CENSORED_ADDRESSES="1"))


class AuthorityEventTests(RulesBase):
    def test_definition_accepted_and_applied(self):
        state = CommunityState([ADDRESS])
        for branch in state.branches.values():
            branch.warm = True
        self.assert_reject(thread(OWNER), "definition_unavailable", state=state)
        outcome = self.assert_accept(definition(), state=state)
        self.assertTrue(outcome.authority)
        state.apply(definition())
        self.assert_accept(thread(OWNER), state=state)

    def test_warming_up(self):
        state = CommunityState([ADDRESS])
        outcome = self.assert_reject(thread(OWNER), "warming_up", state=state)
        self.assertTrue(outcome.decision.msg.startswith("error:"))
        self.assert_accept(event(1, OUTSIDER, []), state=state)

    def test_invalid_definition_rejected(self):
        bad = definition(relays=())
        outcome = self.assert_reject(bad, "invalid_definition")
        self.assertTrue(outcome.decision.msg.startswith("invalid:"))

    def test_definition_for_other_owner_passthrough(self):
        self.assert_accept(definition(owner=OUTSIDER))

    def test_definition_tombstone_disables_branch(self):
        self.state.apply(tombstone(OWNER, ADDRESS, created_at=2_000_000_000))
        self.assert_reject(thread(OWNER), "definition_unavailable")

    def test_referenced_shard_accepted_from_owner_only(self):
        outcome = self.assert_accept(shard(OWNER, "general", [OUTSIDER]))
        self.assertTrue(outcome.authority)
        # An impostor publishing at someone else's coordinate is not a shard;
        # without authority tags it is passthrough (strfry keys it by author anyway).
        forged = shard(OUTSIDER, "general", [OUTSIDER])
        outcome = self.assert_accept(forged)
        self.assertFalse(outcome.authority)
        self.assertEqual(outcome.scope, "passthrough")

    def test_malformed_event_at_referenced_coordinate_rejected(self):
        # strfry indexes the first d tag, so this would replace the stored shard.
        forged = shard(OWNER, "general", [OUTSIDER])
        forged["tags"].append(["d", "second"])
        outcome = self.assert_reject(forged, "invalid_shard")
        self.assertTrue(outcome.decision.msg.startswith("invalid:"))
        # Declined status is a valid shard shape.
        self.assert_accept(shard(MOD_GENERAL, "general", [], shard_no=2, declined=True))

    def test_e_tag_deletion_of_shard_and_definition(self):
        granting = shard(OWNER, "thread-creator", [MEMBER])
        state = warm_state([definition()] + [granting])
        branch = state.branch(ADDRESS)
        self.assert_accept(thread(MEMBER), state=state)
        state.apply(event(5, OWNER, [["e", granting["id"]]]))
        self.assert_reject(thread(MEMBER), "no_grant", state=state)
        # Replaying the deleted shard must not resurrect the grant: strfry's
        # deletion index refuses it, and so does the plugin.
        self.assert_reject(granting, "deleted_replay", state=state)
        state.apply(granting, inline=True)
        self.assert_reject(thread(MEMBER), "no_grant", state=state)
        self.assertNotIn(granting["id"], branch.inline_seen_at)
        # A fresh shard (new id) from the owner works as usual.
        state.apply(shard(OWNER, "thread-creator", [MEMBER]))
        self.assert_accept(thread(MEMBER), state=state)
        # Someone else's e-tag delete of the owner's event does nothing.
        state.apply(event(5, OUTSIDER, [["e", branch.shards[shard_address(OWNER, "thread-creator")].current["id"]]]))
        self.assert_accept(thread(MEMBER), state=state)
        definition_id = branch.definition.event["id"]
        state.apply(event(5, OWNER, [["e", definition_id]]))
        self.assert_reject(thread(MEMBER), "definition_unavailable", state=state)
        self.assertIsNone(branch.definition)

    def test_deleted_definition_cannot_be_replayed(self):
        first = definition()
        state = warm_state([first])
        self.assert_accept(thread(OWNER), state=state)
        state.apply(event(5, OWNER, [["e", first["id"]]]))
        self.assert_reject(first, "deleted_replay", state=state)
        state.apply(first)
        self.assert_reject(thread(OWNER), "definition_unavailable", state=state)
        self.assert_accept(definition(), state=state)

    def test_renunciation_cannot_mask_a_referenced_shard(self):
        forged = shard(OWNER, "thread-creator", [OUTSIDER])
        forged["tags"].append(["d", "app/budabit/renounced-communities"])
        self.assert_reject(forged, "invalid_shard")
        # A genuine renunciation list (first d is the preference coordinate) passes.
        renounce = event(30000, OUTSIDER, [["d", "app/budabit/renounced-communities"], ["a", ADDRESS]])
        self.assert_accept(renounce)
        # ...but not with a hosted coordinate hidden behind it.
        masked = event(30000, OWNER, [["d", f"{COMMUNITY}-thread-creator"], ["d", "app/budabit/renounced-communities"], ["p", OUTSIDER]])
        self.assert_reject(masked, "invalid_shard")

    def test_e_tag_deletion_of_report(self):
        report = person_report(OWNER, MEMBER)
        self.state.apply(report)
        self.assert_reject(thread(MEMBER), "person_banned")
        self.state.apply(event(5, OWNER, [["e", report["id"]]]))
        self.assert_accept(thread(MEMBER))

    def test_moderator_request(self):
        req = event(30000, OUTSIDER, [["d", "mod-request"], ["content", "General"]] + authority_tags())
        self.assert_accept(req)
        self.state.apply(person_report(OWNER, OUTSIDER))
        self.assert_reject(req, "person_banned")

    def test_moderator_request_with_p_tags_invalid(self):
        req = event(30000, OUTSIDER, [["d", "x"], ["p", MEMBER]] + authority_tags())
        self.assert_reject(req, "invalid_shard")

    def test_renunciation_always_accepted(self):
        ev = event(30000, OUTSIDER, [["d", "app/budabit/renounced-communities"], ["a", ADDRESS]])
        self.assert_accept(ev, cfg=config(BUDABIT_MODE="strict"))

    def test_unprotected_delete_accepted(self):
        outcome = self.assert_accept(event(5, OUTSIDER, [["e", "a" * 64], ["h", COMMUNITY], ["k", "1984"]]))
        self.assertTrue(outcome.authority)

    def test_protected_kind_deletions_rejected_in_both_modes(self):
        for mode in ("passthrough", "strict"):
            for kind in ("32222", "30000"):
                for tag in (["k", kind], ["a", f"{kind}:{OUTSIDER}:unhosted"], ["a", f"{kind}:{OWNER}:{COMMUNITY}", "", "community"]):
                    for author in (OWNER, OUTSIDER):
                        with self.subTest(mode=mode, tag=tag, author=author):
                            # Any protected target rejects the entire mixed delete.
                            outcome = self.assert_reject(
                                event(5, author, [["k", "1984"], ["e", "b" * 64], tag]),
                                "protected_kind_deletion", cfg=config(BUDABIT_MODE=mode),
                            )
                            self.assertEqual(outcome.decision.msg, rules.PROTECTED_DELETE_MESSAGE)
                            self.assertFalse(outcome.authority)

    def test_protected_id_deletions_rejected_without_kind_hint(self):
        branch = self.state.branch(ADDRESS)
        for coordinate in (branch.definition_coord, *branch.shards.values()):
            if coordinate.current:
                target = coordinate.current
                self.assert_reject(event(5, target["pubkey"], [["e", target["id"]]]), "protected_kind_deletion")

    def test_protected_tag_deletion_rejected_before_warmup(self):
        state = CommunityState([ADDRESS])
        self.assert_reject(event(5, OWNER, [["k", "30000"]]), "protected_kind_deletion", state=state)

    def test_non_delete_events_can_reference_protected_kinds(self):
        self.assert_accept(event(7, OWNER, [["k", "32222"]] + authority_tags(), "+"))

    def test_similar_kind_numbers_are_not_protected(self):
        for tag in (["k", "300001"], ["k"], ["k", "not-a-kind"], ["a", f"30009:{OWNER}:badge"]):
            self.assert_accept(event(5, OWNER, [tag]))


class ReportRuleTests(RulesBase):
    def test_owner_person_report(self):
        self.assert_accept(person_report(OWNER, OUTSIDER))

    def test_section_moderator_person_report_rejected(self):
        self.assert_reject(person_report(MOD_GENERAL, OUTSIDER), "report_authority")

    def test_member_person_report_rejected(self):
        self.assert_reject(person_report(MEMBER, OUTSIDER), "report_authority")

    def test_section_moderator_event_report(self):
        self.assert_accept(event_report(MOD_GENERAL, OUTSIDER, "c" * 64, section="General"))
        # Outside their section a moderator still holds the structural member
        # grant for General, which owns kind 1984: the report is accepted as a
        # member content report (advisory), exactly like the client allows.
        self.assert_accept(event_report(MOD_GENERAL, OUTSIDER, "c" * 64, section="Code-curator"))

    def test_member_content_report_via_general_grant(self):
        self.assert_accept(event_report(MEMBER, OUTSIDER, "c" * 64, section="Code-curator"))
        self.assert_reject(event_report(OUTSIDER, MEMBER, "c" * 64), "report_authority")

    def test_self_report_rejected(self):
        self.assert_reject(person_report(OWNER, OWNER), "self_report")

    def test_unknown_section(self):
        # Owner/members can always file an advisory content report; an
        # outsider naming an unknown section gets the section error.
        self.assert_accept(event_report(OWNER, OUTSIDER, "c" * 64, section="Nope"))
        self.assert_accept(event_report(MEMBER, OUTSIDER, "c" * 64, section="Nope"))
        self.assert_reject(event_report(OUTSIDER, MEMBER, "c" * 64, section="Nope"), "unknown_section")

    def test_malformed_report(self):
        ev = event(1984, OWNER, [["p", OUTSIDER]] + authority_tags())
        self.assert_reject(ev, "invalid_report")
        ev = event(1984, OWNER, [["p", OUTSIDER, "spam"], ["h", COMMUNITY]])
        self.assert_reject(ev, "invalid_authority_tags")

    def test_report_applies_inline(self):
        self.assert_accept(thread(MEMBER))
        self.state.apply(person_report(OWNER, MEMBER))
        self.assert_reject(thread(MEMBER), "person_banned")


class WorkflowRuleTests(RulesBase):
    def test_admission_form(self):
        form = lambda pk, section: event(30168, pk, [["d", "apply"], ["content", section]] + authority_tags())
        self.assert_accept(form(OWNER, "General"))
        self.assert_accept(form(MOD_GENERAL, "General"))
        self.assert_reject(form(MOD_GENERAL, "Code-curator"), "form_authority")
        self.assert_reject(form(MEMBER, "General"), "form_authority")
        self.assert_reject(form(OWNER, "Nope"), "unknown_section")

    def test_admission_response_open_to_outsiders(self):
        response = event(1069, OUTSIDER, [["a", f"30168:{MOD_GENERAL}:apply", "", "form"]] + authority_tags())
        self.assert_accept(response)
        self.assert_reject(event(1069, OUTSIDER, [["h", COMMUNITY]]), "invalid_workflow_shape")
        # Missing the marked form reference.
        self.assert_reject(event(1069, OUTSIDER, authority_tags()), "invalid_workflow_shape")
        self.assert_reject(
            event(1069, OUTSIDER, [["a", f"30168:{MOD_GENERAL}:apply", "", "form"], ["a", f"30168:{OWNER}:b", "", "form"]] + authority_tags()),
            "invalid_workflow_shape",
        )

    def test_malformed_workflow_shapes_gain_no_permission(self):
        # Star with only h and k, arbitrary content, event/person targets.
        self.assert_reject(
            event(7, OUTSIDER, [["h", COMMUNITY], ["k", "32222"], ["e", "c" * 64], ["p", MEMBER]], "lol"),
            "invalid_workflow_shape",
        )
        self.assert_reject(event(7, OUTSIDER, authority_tags() + [["k", "32222"]], "-"), "invalid_workflow_shape")
        # Admission review without form address or applicant.
        self.assert_reject(
            event(7, MOD_GENERAL, [["e", "b" * 64, "", OUTSIDER, "response"], ["k", "1069"]] + authority_tags() + [["content", "General"]], "+"),
            "invalid_workflow_shape",
        )
        self.assert_reject(
            event(7, MOD_GENERAL, [["e", "b" * 64, "", OUTSIDER, "response"], ["k", "1069"], ["a", f"30168:{MOD_GENERAL}:apply", "", "form"], ["p", OUTSIDER], ["content", "General"], ["h", COMMUNITY]], "+"),
            "invalid_workflow_shape",
        )
        # Moderator request decision without authority tags.
        self.assert_reject(event(7, OWNER, [["h", COMMUNITY], ["e", "d" * 64], ["k", "30000"]], "+"), "invalid_workflow_shape")

    def test_admission_review(self):
        review = lambda pk, section, content="+": event(
            7,
            pk,
            [["e", "b" * 64, "", OUTSIDER, "response"], ["p", OUTSIDER], ["k", "1069"], ["a", f"30168:{MOD_GENERAL}:apply", "", "form"]]
            + authority_tags()
            + [["content", section]],
            content,
        )
        self.assert_accept(review(MOD_GENERAL, "General"))
        self.assert_accept(review(OWNER, "Code-curator", "-"))
        self.assert_reject(review(MOD_GENERAL, "Code-curator"), "review_authority")
        self.assert_reject(review(MEMBER, "General"), "review_authority")

    def test_star_from_anyone(self):
        star = event(7, OUTSIDER, authority_tags() + [["k", "32222"]], "+")
        self.assert_accept(star)

    def test_plain_reaction_needs_general_grant(self):
        react = lambda pk: event(7, pk, [["h", COMMUNITY], ["e", "c" * 64]], "+")
        self.assert_accept(react(MEMBER))
        self.assert_reject(react(OUTSIDER), "no_grant")

    def test_moderator_request_decision_owner_only(self):
        decision = lambda pk: event(7, pk, [["e", "d" * 64], ["k", "30000"]] + authority_tags(), "+")
        self.assert_accept(decision(OWNER))
        self.assert_reject(decision(MOD_GENERAL), "owner_only")

    def test_badges(self):
        badge = lambda pk: event(30009, pk, [["d", "b"]] + authority_tags())
        self.assert_accept(badge(OWNER))
        self.assert_accept(badge(MOD_GENERAL))
        self.assert_reject(badge(MEMBER), "badge_authority")
        self.assert_reject(event(8, MEMBER, [["a", f"30009:{OWNER}:b"], ["p", OUTSIDER]] + authority_tags()), "badge_authority")


class WrapperRuleTests(RulesBase):
    def wrapper(self, pubkey, kind="31922", targets=None):
        tags = [["d", "targeting"], ["k", kind]]
        for owner, community in targets or [(OWNER, COMMUNITY)]:
            tags += [["h", community], ["a", f"32222:{owner}:{community}", RELAY]]
        return event(30222, pubkey, tags)

    def test_calendar_section_without_lists_owner_only(self):
        self.assert_accept(self.wrapper(OWNER))
        self.assert_reject(self.wrapper(MEMBER), "no_grant")
        # Structural members write everywhere, including sections without lists.
        self.assert_accept(self.wrapper(MOD_CODE))

    def test_wrapper_kind_not_in_any_section(self):
        self.assert_reject(self.wrapper(OWNER, kind="9041"), "no_grant")

    def test_invalid_wrapper(self):
        ev = event(30222, OWNER, [["d", "t"], ["k", "31922"], ["h", COMMUNITY]])
        self.assert_reject(ev, "invalid_wrapper")

    def test_wrapper_for_other_community_passthrough(self):
        self.assert_accept(self.wrapper(OUTSIDER, targets=[(OUTSIDER, OTHER_COMMUNITY)]))

    def test_multi_target_requires_all_hosted(self):
        second = f"32222:{OUTSIDER}:{OTHER_COMMUNITY}"
        state = warm_state(standard_events() + [definition(owner=OUTSIDER, community=OTHER_COMMUNITY, sections=[("Cal", [["k", "31922"]], [])])], addresses=(ADDRESS, second))
        ev = self.wrapper(OWNER, targets=[(OWNER, COMMUNITY), (OUTSIDER, OTHER_COMMUNITY)])
        self.assert_reject(ev, "no_grant", state=state)
        ev = self.wrapper(OUTSIDER, targets=[(OWNER, COMMUNITY), (OUTSIDER, OTHER_COMMUNITY)])
        self.assert_reject(ev, "no_grant", state=state)


class StrictModeTests(RulesBase):
    def setUp(self):
        super().setUp()
        self.config = config(BUDABIT_MODE="strict")

    def test_public_note_rejected(self):
        self.assert_reject(event(1, OUTSIDER, [], "x"), "strict_passthrough")

    def test_participant_personal_kinds(self):
        self.assert_accept(event(0, MEMBER, [], "{}"))
        self.assert_accept(event(10002, MOD_CODE, [["r", RELAY]]))
        self.assert_reject(event(0, OUTSIDER, [], "{}"), "strict_passthrough")

    def test_delete_passes(self):
        self.assert_accept(event(5, OUTSIDER, [["e", "a" * 64]]))

    def test_nip34_kinds_are_not_special(self):
        # Repository collaboration belongs on GRASP/repo relays. Without a
        # community h tag these are unattributable and strict mode rejects them.
        repo_a = ["a", f"30617:{MEMBER}:repo"]
        self.assert_reject(event(1621, MEMBER, [repo_a], "issue"), "strict_passthrough")
        self.assert_reject(event(1618, OWNER, [repo_a], "pr"), "strict_passthrough")
        self.assert_reject(event(30618, MEMBER, [["d", "repo"]]), "strict_passthrough")
        # An h-tagged announcement is community content under Code-curator.
        self.assert_accept(event(30617, MOD_CODE, [["h", COMMUNITY], ["d", "repo"]]))
        self.assert_reject(event(30617, OUTSIDER, [["h", COMMUNITY], ["d", "repo"]]), "no_grant")
        # Other h-tagged NIP-34 kinds need a section that lists them.
        self.assert_reject(event(1621, OWNER, [["h", COMMUNITY], repo_a], "issue"), "kind_not_enabled")
        sections = [("Issues", [["k", "1621"]], [shard_address(OWNER, "issues")])]
        state = warm_state([definition(sections=sections), shard(OWNER, "issues", [MEMBER])])
        self.assert_accept(event(1621, MEMBER, [["h", COMMUNITY], repo_a], "issue"), state=state)
        self.assert_reject(event(1621, OUTSIDER, [["h", COMMUNITY], repo_a], "issue"), "no_grant", state=state)

    def test_targetable_original_with_grant(self):
        self.assert_accept(event(31922, OWNER, [["h", "targeting"], ["d", "cal"]]))
        self.assert_reject(event(31922, MEMBER, [["h", "targeting"], ["d", "cal"]]), "strict_passthrough")

    def test_hosted_content_still_evaluated(self):
        self.assert_accept(thread(MEMBER))
        self.assert_reject(thread(OUTSIDER), "no_grant")

    def test_strict_exceptions_wait_for_warm_up(self):
        state = CommunityState([ADDRESS])
        for ev in standard_events():
            state.apply(ev)
        self.assert_reject(event(31922, OWNER, [["h", "targeting"], ["d", "cal"]]), "warming_up", state=state)
        self.assert_reject(event(0, MEMBER, [], "{}"), "warming_up", state=state)
        self.assert_accept(event(5, OUTSIDER, [["e", "a" * 64]]), state=state)
        for branch in state.branches.values():
            branch.warm = True
        self.assert_accept(event(31922, OWNER, [["h", "targeting"], ["d", "cal"]]), state=state)


class SameIdBranchTests(unittest.TestCase):
    def test_content_without_marked_a_accepted_if_any_branch_admits(self):
        second_owner = key("second-owner")
        second = f"32222:{second_owner}:{COMMUNITY}"
        events = standard_events() + [
            definition(owner=second_owner, sections=[("Threads", [["k", "11", "threads"]], [shard_address(second_owner, "threads")])]),
            shard(second_owner, "threads", [MEMBER2]),
        ]
        state = warm_state(events, addresses=(ADDRESS, second))
        cfg = config()
        self.assertTrue(rules.evaluate(thread(MEMBER), state, cfg).accepted)
        self.assertTrue(rules.evaluate(thread(MEMBER2), state, cfg).accepted)
        self.assertFalse(rules.evaluate(thread(OUTSIDER), state, cfg).accepted)
        marked = event(1111, MEMBER2, [["h", COMMUNITY], ["a", ADDRESS, "", "community"]])
        self.assertTrue(rules.evaluate(marked, state, cfg).accepted)  # MEMBER2 has General in ADDRESS
        marked = event(1111, MEMBER, [["h", COMMUNITY], ["a", second, "", "community"]])
        self.assertEqual(rules.evaluate(marked, state, cfg).reason, "kind_not_enabled")


if __name__ == "__main__":
    unittest.main()
