import unittest

from fixtures import (
    ADDRESS, COMMUNITY, MEMBER, MEMBER2, MOD_GENERAL, OUTSIDER, OWNER,
    definition, person_report, report_delete, shard, standard_events, warm_state,
)
from policy.budabit.state import Branch, can_read_community


class ReadEligibilityTests(unittest.TestCase):
    def branch(self, events):
        return warm_state(events).branch(ADDRESS)

    def test_complete_and_warm_required_even_for_owner(self):
        branch = self.branch(standard_events())
        for key in (OWNER, MEMBER, OUTSIDER):
            self.assertFalse(can_read_community(branch, key, ready=False))
        branch.warm = False
        self.assertFalse(can_read_community(branch, OWNER, ready=True))
        self.assertFalse(can_read_community(None, OWNER, ready=True))

    def test_successful_empty_scan_allows_only_configured_owner(self):
        branch = Branch(OWNER, COMMUNITY)
        branch.warm = True
        self.assertTrue(can_read_community(branch, OWNER, ready=True))
        self.assertFalse(can_read_community(branch, MEMBER, ready=True))
        self.assertFalse(can_read_community(branch, OWNER, ready=False))

    def test_any_role_but_not_outsider(self):
        branch = self.branch(standard_events())
        for key in (OWNER, MEMBER, MEMBER2, MOD_GENERAL):
            self.assertTrue(can_read_community(branch, key, ready=True))
        self.assertFalse(can_read_community(branch, OUTSIDER, ready=True))
        self.assertFalse(can_read_community(branch, "", ready=True))

    def test_pending_and_declined_moderator_is_structural_reader(self):
        for events in ([definition()], [definition(), shard(MOD_GENERAL, "general", [OUTSIDER], shard_no=2, declined=True)]):
            branch = self.branch(events)
            self.assertTrue(can_read_community(branch, MOD_GENERAL, ready=True))
            self.assertFalse(can_read_community(branch, OUTSIDER, ready=True))
            self.assertFalse(can_read_community(branch, MEMBER, ready=True))

    def test_ban_and_retraction(self):
        ban = person_report(OWNER, MEMBER)
        for retracted in (False, True):
            events = standard_events() + [ban] + ([report_delete(ban)] if retracted else [])
            branch = self.branch(events)
            self.assertEqual(can_read_community(branch, MEMBER, ready=True), retracted)
            self.assertTrue(can_read_community(branch, OWNER, ready=True))

    def test_remove_last_grant_and_regrant(self):
        events = standard_events()
        for purpose in ("general", "room-creator", "thread-creator"):
            events.append(shard(OWNER, purpose, []))
            expected = purpose != "thread-creator"
            self.assertEqual(can_read_community(self.branch(events), MEMBER, ready=True), expected)
        events.append(shard(OWNER, "general", [MEMBER]))
        self.assertTrue(can_read_community(self.branch(events), MEMBER, ready=True))


if __name__ == "__main__":
    unittest.main()
