import unittest

from fixtures import (
    ADDRESS,
    MEMBER,
    MEMBER2,
    MOD_ALL,
    MOD_CODE,
    MOD_GENERAL,
    OUTSIDER,
    OWNER,
    definition,
    event_report,
    person_report,
    report_delete,
    shard,
    shard_address,
    standard_events,
    warm_state,
)


def bans(state):
    return state.branch(ADDRESS).derived().person_bans


class PersonBanTests(unittest.TestCase):
    def test_owner_ban(self):
        state = warm_state(standard_events() + [person_report(OWNER, OUTSIDER)])
        self.assertEqual(bans(state), {OUTSIDER})

    def test_section_moderator_cannot_person_ban(self):
        state = warm_state(standard_events() + [person_report(MOD_GENERAL, OUTSIDER)])
        self.assertEqual(bans(state), set())

    def test_all_sections_moderator_can_person_ban(self):
        # MOD_ALL owns an active list in every section that has lists.
        sections = [
            ("General", [["k", "1111"], ["k", "1984"]], [shard_address(MOD_ALL, "general")]),
            ("Thread-creator", [["k", "11", "threads"]], [shard_address(MOD_ALL, "thread-creator")]),
        ]
        events = [
            definition(sections=sections),
            shard(MOD_ALL, "general", [MEMBER]),
            shard(MOD_ALL, "thread-creator", [MEMBER]),
            person_report(MOD_ALL, OUTSIDER),
        ]
        self.assertEqual(bans(warm_state(events)), {OUTSIDER})

    def test_moderator_cannot_ban_owner_or_moderator(self):
        sections = [
            ("General", [["k", "1111"], ["k", "1984"]], [shard_address(MOD_ALL, "general"), shard_address(MOD_GENERAL, "general", 2)]),
        ]
        events = [
            definition(sections=sections),
            shard(MOD_ALL, "general", []),
            shard(MOD_GENERAL, "general", [], shard_no=2),
            person_report(MOD_ALL, OWNER),
            person_report(MOD_ALL, MOD_GENERAL),
        ]
        self.assertEqual(bans(warm_state(events)), set())

    def test_owner_ban_of_moderator_removes_their_reports(self):
        sections = [
            ("General", [["k", "1111"], ["k", "1984"]], [shard_address(MOD_ALL, "general")]),
        ]
        events = [
            definition(sections=sections),
            shard(MOD_ALL, "general", [MEMBER]),
            person_report(MOD_ALL, OUTSIDER),
        ]
        state = warm_state(events)
        self.assertEqual(bans(state), {OUTSIDER})
        state.apply(person_report(OWNER, MOD_ALL))
        # MOD_ALL is banned, so their report no longer counts: OUTSIDER is unbanned.
        self.assertEqual(bans(state), {MOD_ALL})

    def test_deleted_report_ignored(self):
        report = person_report(OWNER, OUTSIDER)
        state = warm_state(standard_events() + [report])
        self.assertEqual(bans(state), {OUTSIDER})
        state.apply(report_delete(report))
        self.assertEqual(bans(state), set())

    def test_legacy_owner_delete_shape_tombstones_definition_like_strfry(self):
        # The old shape carried the branch as an a tag; strfry deletes the
        # definition for an owner-signed one, and the plugin follows storage.
        report = person_report(OWNER, OUTSIDER)
        state = warm_state(standard_events() + [report, report_delete(report, legacy=True)])
        self.assertFalse(state.branch(ADDRESS).derived().available)

    def test_delete_before_report(self):
        report = person_report(OWNER, OUTSIDER)
        state = warm_state(standard_events() + [report_delete(report), report])
        self.assertEqual(bans(state), set())

    def test_report_for_other_branch_ignored(self):
        report = person_report(OWNER, OUTSIDER)
        report["tags"] = [["p", OUTSIDER, "spam"], ["h", "f" * 64], ["a", f"32222:{OWNER}:{'f' * 64}", "", "community"]]
        state = warm_state(standard_events() + [report])
        self.assertEqual(bans(state), set())

    def test_banned_member_loses_write(self):
        state = warm_state(standard_events() + [person_report(OWNER, MEMBER)])
        derived = state.branch(ADDRESS).derived()
        self.assertFalse(derived.can_write(MEMBER, 1111))
        self.assertTrue(derived.can_write(MEMBER2, 1111))

    def test_owner_never_banned(self):
        state = warm_state(standard_events() + [person_report(OWNER, OWNER)])
        self.assertFalse(state.branch(ADDRESS).derived().is_banned(OWNER))


class EventReportTests(unittest.TestCase):
    def test_section_moderator_event_report_censors_address(self):
        address = f"30617:{MEMBER}:repo"
        state = warm_state(
            standard_events() + [event_report(MOD_GENERAL, MEMBER, "c" * 64, target_address=address)]
        )
        derived = state.branch(ADDRESS).derived()
        self.assertIn(address, derived.censored_addresses)
        self.assertIn("c" * 64, derived.censored_event_ids)

    def test_event_report_wrong_section_moderator_ignored(self):
        state = warm_state(
            standard_events() + [event_report(MOD_CODE, MEMBER, "c" * 64, section="General")]
        )
        derived = state.branch(ADDRESS).derived()
        self.assertEqual(derived.censored_event_ids, set())

    def test_member_event_report_is_not_effective(self):
        state = warm_state(standard_events() + [event_report(MEMBER2, MEMBER, "c" * 64)])
        self.assertEqual(state.branch(ADDRESS).derived().censored_event_ids, set())


if __name__ == "__main__":
    unittest.main()
