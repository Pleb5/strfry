import unittest

from fixtures import (
    ADDRESS,
    COMMUNITY,
    MEMBER,
    MOD_GENERAL,
    OUTSIDER,
    OWNER,
    RELAY,
    authority_tags,
    definition,
    event,
    event_report,
    person_report,
    report_delete,
    shard,
    shard_address,
)
from policy.budabit import protocol as P


class UrlTests(unittest.TestCase):
    def test_relay_normalisation(self):
        self.assertEqual(P.normalize_relay("wss://Relay.Example/"), "wss://relay.example")
        self.assertEqual(P.normalize_relay("wss://relay.example:443/"), "wss://relay.example")
        self.assertEqual(P.normalize_relay("wss://relay.example/path/"), "wss://relay.example/path/")
        self.assertEqual(P.normalize_relay("wss://relay.example/?a=1"), "wss://relay.example/?a=1")
        self.assertEqual(P.normalize_relay("wss://relay.example:8443"), "wss://relay.example:8443")

    def test_relay_rejections(self):
        for bad in ("ws://relay.example", "https://relay.example", "wss://user@relay.example", "wss://relay.example/#x", " wss://relay.example", "wss://", ""):
            self.assertIsNone(P.normalize_relay(bad), bad)

    def test_https(self):
        self.assertEqual(P.normalize_https("https://blossom.example/"), "https://blossom.example")
        self.assertIsNone(P.normalize_https("http://blossom.example"))
        self.assertEqual(P.normalize_website("http://site.example"), "http://site.example")


class AddressTests(unittest.TestCase):
    def test_parse_address(self):
        ref = P.parse_address(shard_address(OWNER, "general"))
        self.assertEqual(ref.kind, 30000)
        self.assertEqual(ref.pubkey, OWNER)
        self.assertIsNone(P.parse_address(f"1:{OWNER}:x"))
        self.assertIsNone(P.parse_address(f"30000:{OWNER}:"))
        self.assertIsNone(P.parse_address(f"30000:abc:x"))
        self.assertEqual(P.parse_address(f"30000:{OWNER}:a:b").identifier, "a:b")

    def test_definition_address(self):
        self.assertEqual(P.parse_definition_address(ADDRESS), (OWNER, COMMUNITY))
        self.assertIsNone(P.parse_definition_address(f"30000:{OWNER}:{COMMUNITY}"))
        self.assertIsNone(P.parse_definition_address(f"{ADDRESS}:extra"))

    def test_profile_list_identifier(self):
        self.assertEqual(P.parse_profile_list_identifier(COMMUNITY, f"{COMMUNITY}-general"), ("general", None))
        self.assertEqual(P.parse_profile_list_identifier(COMMUNITY, f"{COMMUNITY}-general-2.3"), ("general-2", 3))
        for bad in (f"{COMMUNITY}-General", f"{COMMUNITY}-general.1", f"{COMMUNITY}-general.2.3", f"{COMMUNITY}--x", f"{COMMUNITY[:10]}-general", f"{COMMUNITY}-"):
            self.assertIsNone(P.parse_profile_list_identifier(COMMUNITY, bad), bad)


class DefinitionTests(unittest.TestCase):
    def test_valid_definition(self):
        parsed = P.parse_definition(definition())
        self.assertEqual(parsed.owner, OWNER)
        self.assertEqual(parsed.community_id, COMMUNITY)
        self.assertEqual(parsed.relays, [RELAY])
        self.assertEqual([s.name for s in parsed.sections][:2], ["General", "Room-creator"])
        self.assertEqual(parsed.section_for(11, "room").name, "Room-creator")
        self.assertEqual(parsed.section_for(11, "threads").name, "Thread-creator")
        self.assertIsNone(parsed.section_for(11))
        self.assertEqual(parsed.section_for(1111).name, "General")

    def assert_invalid(self, ev, fragment):
        with self.assertRaises(P.InvalidEvent) as ctx:
            P.parse_definition(ev)
        self.assertIn(fragment, str(ctx.exception))

    def test_invalid_definitions(self):
        self.assert_invalid(definition(extra_tags=[["d", COMMUNITY]]), "exactly one d")
        self.assert_invalid(definition(extra_tags=[["h", COMMUNITY]]), "h tag")
        self.assert_invalid(definition(extra_tags=[["name", "dup"]]), "name")
        self.assert_invalid(definition(relays=()), "1 to 20 r")
        self.assert_invalid(definition(relays=("wss://Relay.Example",)), "normalized")
        self.assert_invalid(definition(relays=("wss://relay.example", "x")), "normalized")
        self.assert_invalid(definition(sections=[]), "at least one content")
        self.assert_invalid(definition(sections=[("General", [], [])]), "at least one k")
        self.assert_invalid(definition(extra_tags=[["k", "1"]]), "before the first content")
        self.assert_invalid(
            definition(sections=[("A", [["k", "1"]], []), ("a", [["k", "2"]], [])]), "duplicate section name"
        )
        self.assert_invalid(
            definition(sections=[("A", [["k", "1"]], []), ("B", [["k", "1"]], [])]), "duplicate (kind"
        )
        self.assert_invalid(definition(sections=[("A", [["k", "01"]], [])]), "k tag value")
        self.assert_invalid(definition(sections=[("A", [["k", "70000"]], [])]), "k tag value")
        self.assert_invalid(definition(sections=[("A", [["k", "1", ""]], [])]), "k tag value")
        self.assert_invalid(
            definition(sections=[("A", [["k", "1"]], [f"30000:{OWNER}:general"])]), "profile-list identifier"
        )
        self.assert_invalid(
            definition(sections=[("A", [["k", "1"]], [f"30009:{OWNER}:{COMMUNITY}-a"])]), "address"
        )
        ev = definition()
        ev["content"] = "x"
        self.assert_invalid(ev, "content must be empty")
        ev = definition()
        ev["tags"].insert(3, ["r", RELAY, "enforced"])
        self.assert_invalid(ev, "r tag arity")

    def test_section_local_top_level_tag_invalid(self):
        ev = definition()
        ev["tags"].append(["blossom", "https://blossom.example"])
        self.assert_invalid(ev, "top-level tag inside a section")

    def test_unknown_tags_ignored(self):
        ev = definition(extra_tags=[["enforced-relay", RELAY], ["x", "y", "z"]])
        parsed = P.parse_definition(ev)
        self.assertEqual(parsed.name, "Test Community")
        ev["tags"].append(["custom", "inside section"])
        P.parse_definition(ev)

    def test_duplicate_relays_collapse(self):
        parsed = P.parse_definition(definition(relays=(RELAY, RELAY)))
        self.assertEqual(parsed.relays, [RELAY])

    def test_service_and_metadata(self):
        handler = f"31990:{OWNER}:digest"
        ev = definition(
            extra_tags=[
                ["service", "email-digest", MEMBER, RELAY, handler, RELAY],
                ["mint", "https://mint.example", "sat"],
                ["tos", ADDRESS, RELAY],
                ["g", "u2mwdd"],
                ["description", "hello"],
            ]
        )
        P.parse_definition(ev)
        self.assert_invalid(definition(extra_tags=[["service", "Bad", MEMBER, RELAY, handler, RELAY]]), "service")
        self.assert_invalid(definition(extra_tags=[["g", "U2"]]), "g tag")
        self.assert_invalid(definition(extra_tags=[["description", " padded"]]), "description")


class ShardTests(unittest.TestCase):
    def test_profile_list_pubkeys(self):
        ev = shard(OWNER, "general", [MEMBER, MEMBER, "nothex", OUTSIDER])
        self.assertEqual(P.profile_list_pubkeys(ev), [MEMBER, OUTSIDER])
        self.assertEqual(P.profile_list_pubkeys(shard(OWNER, "general", [MEMBER], declined=True)), [])

    def test_is_valid_shard(self):
        ref = P.ProfileListRef(shard_address(OWNER, "general"))
        self.assertTrue(P.is_valid_shard(shard(OWNER, "general", []), ref))
        self.assertFalse(P.is_valid_shard(shard(MOD_GENERAL, "general", []), ref))
        self.assertFalse(P.is_valid_shard(shard(OWNER, "general", [], shard_no=2), ref))


class AuthorityTests(unittest.TestCase):
    def test_parse_authority(self):
        auth = P.parse_authority(authority_tags())
        self.assertEqual(auth.address, ADDRESS)
        self.assertIsNone(P.parse_authority([["h", COMMUNITY]]))
        self.assertIsNone(P.parse_authority(authority_tags(community=COMMUNITY, owner=OWNER) + [["h", COMMUNITY]]))
        self.assertIsNone(P.parse_authority([["h", "x" * 64], ["a", ADDRESS, "", "community"]]))
        self.assertIsNone(P.parse_authority([["h", COMMUNITY], ["a", ADDRESS, "ws://bad", "community"]]))
        self.assertIsNone(P.parse_authority([["h", COMMUNITY], ["a", ADDRESS, "community"]]))
        self.assertEqual(P.parse_authority(authority_tags(relay=RELAY)).relay, RELAY)

    def test_derive_subtype(self):
        self.assertEqual(P.derive_subtype({"kind": 11, "tags": [["room", ""]]}), "room")
        self.assertEqual(P.derive_subtype({"kind": 11, "tags": []}), "threads")
        self.assertEqual(P.derive_subtype({"kind": 9, "tags": []}), "room-message")
        self.assertIsNone(P.derive_subtype({"kind": 1111, "tags": []}))


class WrapperTests(unittest.TestCase):
    def wrapper(self, tags):
        return event(30222, MEMBER, tags)

    def test_valid_wrapper(self):
        w = P.parse_wrapper(
            self.wrapper(
                [["d", "target"], ["k", "31922"], ["a", f"31922:{MEMBER}:cal", RELAY], ["h", COMMUNITY], ["a", ADDRESS, RELAY]]
            )
        )
        self.assertEqual(w.kind, 31922)
        self.assertEqual([t.address for t in w.targets], [ADDRESS])

    def test_invalid_wrappers(self):
        cases = [
            ([["d", "t"], ["k", "31922"], ["h", COMMUNITY]], "followed"),
            ([["d", "t"], ["k", "31922"], ["h", COMMUNITY], ["a", f"32222:{OWNER}:{'f' * 64}"]], "mismatch"),
            ([["d", "t"], ["k", "31922"], ["h", COMMUNITY], ["a", ADDRESS], ["h", COMMUNITY], ["a", ADDRESS]], "duplicate"),
            ([["d", "t"], ["h", COMMUNITY], ["a", ADDRESS]], "k tag"),
            ([["d", "t"], ["k", "31922"], ["a", f"9041:{MEMBER}:g"], ["h", COMMUNITY], ["a", ADDRESS]], "source address"),
            ([["d", "t"], ["k", "31922"], ["e", "a" * 64], ["e", "b" * 64], ["h", COMMUNITY], ["a", ADDRESS]], "more than one source"),
        ]
        for tags, fragment in cases:
            with self.assertRaises(P.InvalidEvent) as ctx:
                P.parse_wrapper(self.wrapper(tags))
            self.assertIn(fragment, str(ctx.exception), tags)


class ReportTests(unittest.TestCase):
    def test_person_report(self):
        report = P.parse_report(person_report(OWNER, OUTSIDER))
        self.assertEqual(report.target, "person")
        self.assertEqual(report.target_pubkey, OUTSIDER)

    def test_event_report(self):
        report = P.parse_report(event_report(OWNER, MEMBER, "c" * 64, target_address=f"30617:{MEMBER}:repo"))
        self.assertEqual(report.target, "event")
        self.assertEqual(report.section_name, "General")
        self.assertEqual(report.target_address, f"30617:{MEMBER}:repo")

    def test_event_report_requires_section(self):
        ev = event(1984, OWNER, [["e", "c" * 64, "spam"], ["p", MEMBER]] + authority_tags())
        self.assertIsNone(P.parse_report(ev))

    def test_report_without_authority(self):
        self.assertIsNone(P.parse_report(event(1984, OWNER, [["p", OUTSIDER, "spam"], ["h", COMMUNITY]])))

    def test_relay_hint_is_not_a_reason(self):
        ev = event(1984, OWNER, [["p", OUTSIDER, RELAY]] + authority_tags())
        self.assertIsNone(P.parse_report(ev))

    def test_report_delete(self):
        report = P.parse_report(person_report(MOD_GENERAL, OUTSIDER))
        delete = report_delete(report.event)
        self.assertTrue(P.is_report_delete(delete, report))
        other = report_delete(report.event)
        other["pubkey"] = OWNER
        self.assertFalse(P.is_report_delete(other, report))
        wrong_kind = report_delete(report.event)
        wrong_kind["tags"][2] = ["k", "7"]
        self.assertFalse(P.is_report_delete(wrong_kind, report))
        self.assertFalse(any(tag[0] == "a" for tag in delete["tags"]))
        # Legacy shape with the marked community a still counts for this branch...
        self.assertTrue(P.is_report_delete(report_delete(report.event, legacy=True), report))
        # ...but not when it names another branch with the same community id.
        other_branch = report_delete(report.event, legacy=True)
        other_branch["tags"] = [
            tag if tag[0] != "a" else ["a", f"32222:{OUTSIDER}:{COMMUNITY}", "", "community"]
            for tag in other_branch["tags"]
        ]
        self.assertFalse(P.is_report_delete(other_branch, report))
        unscoped = report_delete(report.event)
        unscoped["tags"] = [tag for tag in unscoped["tags"] if tag[0] != "h"]
        self.assertFalse(P.is_report_delete(unscoped, report))


if __name__ == "__main__":
    unittest.main()
