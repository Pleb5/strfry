"""NIP-94 descriptors use community membership, not section-specific grants."""
from fixtures import (
    ADDRESS, COMMUNITY, MEMBER, MEMBER2, MOD_CODE, MOD_GENERAL, OTHER_COMMUNITY,
    OUTSIDER, OWNER, authority_tags, config, definition, event, person_report,
    shard, standard_events, tombstone, warm_state,
)
from test_rules import RulesBase


class FileMetadataTests(RulesBase):
    def descriptor(self, pubkey, scope=()):
        return event(1063, pubkey, [
            ["url", "https://blossom.example/" + "a" * 64],
            ["x", "a" * 64], ["m", "image/png"], ["size", "123"],
            *scope,
        ])

    def test_any_member_in_either_mode_without_a_1063_section(self):
        for mode in ("strict", "passthrough"):
            for pubkey in (OWNER, MEMBER, MEMBER2, MOD_GENERAL, MOD_CODE):
                for scope in ([], [["h", COMMUNITY]], authority_tags()):
                    with self.subTest(mode=mode, pubkey=pubkey, scope=scope):
                        self.assert_accept(self.descriptor(pubkey, scope), cfg=config(BUDABIT_MODE=mode))

    def test_outsiders_cannot_use_passthrough_or_unhosted_tags(self):
        for mode in ("strict", "passthrough"):
            for scope in ([], authority_tags(), [["h", OTHER_COMMUNITY]]):
                self.assert_reject(self.descriptor(OUTSIDER, scope), "file_metadata_membership",
                                   cfg=config(BUDABIT_MODE=mode))

    def test_bans_override_grants_and_structural_membership(self):
        for pubkey in (MEMBER, MOD_CODE):
            self.state.apply(person_report(OWNER, pubkey))
            for scope in ([], authority_tags()):
                self.assert_reject(self.descriptor(pubkey, scope), "person_banned")

    def test_revoking_last_grant_removes_descriptor_write_access(self):
        self.assert_accept(self.descriptor(MEMBER2))
        self.state.apply(shard(MOD_GENERAL, "general", [], shard_no=2))
        self.assert_reject(self.descriptor(MEMBER2), "file_metadata_membership")
        self.state.apply(shard(OWNER, "thread-creator", [MEMBER2]))
        self.assert_accept(self.descriptor(MEMBER2))

    def test_declined_list_owner_remains_a_structural_member(self):
        self.state.apply(shard(MOD_CODE, "code-curator", [], declined=True))
        self.assert_accept(self.descriptor(MOD_CODE))

    def test_incomplete_or_missing_definition_cannot_authorize(self):
        self.state.branch(ADDRESS).warm = False
        self.assert_reject(self.descriptor(OWNER), "warming_up")
        self.state.branch(ADDRESS).warm = True
        self.state.apply(tombstone(OWNER, ADDRESS, created_at=2_000_000_000))
        self.assert_reject(self.descriptor(OWNER), "definition_unavailable")
        self.assert_reject(self.descriptor(OUTSIDER), "file_metadata_membership",
                           state=warm_state([], addresses=()))

    def test_any_hosted_membership_for_unscoped_but_respect_explicit_scope(self):
        other = f"32222:{OUTSIDER}:{OTHER_COMMUNITY}"
        state = warm_state(standard_events() + [definition(owner=OUTSIDER, community=OTHER_COMMUNITY,
                           sections=[("General", [["k", "1"]], [])])],
                           addresses=(ADDRESS, other))
        self.assert_accept(self.descriptor(OUTSIDER), state=state)
        self.assert_reject(self.descriptor(OUTSIDER, authority_tags()), "file_metadata_membership", state=state)

    def test_scoped_descriptors_still_validate_community_tags(self):
        self.assert_reject(self.descriptor(MEMBER, [["h", COMMUNITY], ["h", COMMUNITY]]),
                           "invalid_authority_tags")
        self.assert_reject(self.descriptor(MEMBER, [["h", COMMUNITY],
                           ["a", f"32222:{OWNER}:{OTHER_COMMUNITY}", "", "community"]]),
                           "invalid_authority_tags")
