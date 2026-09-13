"""Deterministic fixtures for Budabit write-control tests (no signatures)."""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from policy.budabit.config import BudabitConfig  # noqa: E402
from policy.budabit.state import CommunityState  # noqa: E402


def key(name):
    return hashlib.sha256(name.encode()).hexdigest()


OWNER = key("owner")
MOD_GENERAL = key("mod-general")
MOD_CODE = key("mod-code")
MOD_ALL = key("mod-all")
MEMBER = key("member")
MEMBER2 = key("member2")
OUTSIDER = key("outsider")
COMMUNITY = key("community-id")
OTHER_COMMUNITY = key("other-community-id")
RELAY = "wss://relay.example"

ADDRESS = f"32222:{OWNER}:{COMMUNITY}"

_counter = [0]


def event(kind, pubkey, tags, content="", created_at=None):
    _counter[0] += 1
    if created_at is None:
        created_at = 1_700_000_000 + _counter[0]
    body = {
        "kind": kind,
        "pubkey": pubkey,
        "created_at": created_at,
        "tags": tags,
        "content": content,
    }
    body["id"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    body["sig"] = "0" * 128
    return body


def shard_address(owner, purpose, shard=None, community=COMMUNITY):
    identifier = f"{community}-{purpose}" + (f".{shard}" if shard else "")
    return f"30000:{owner}:{identifier}"


DEFAULT_SECTIONS = [
    ("General", [["k", "9", "room-message"], ["k", "1111"], ["k", "7"], ["k", "1984"], ["k", "1985"]], [shard_address(OWNER, "general"), shard_address(MOD_GENERAL, "general", 2)]),
    ("Room-creator", [["k", "11", "room"]], [shard_address(OWNER, "room-creator")]),
    ("Thread-creator", [["k", "11", "threads"]], [shard_address(OWNER, "thread-creator")]),
    ("Code-curator", [["k", "30617"], ["k", "1623"]], [shard_address(MOD_CODE, "code-curator")]),
    ("Calendar-event-creator", [["k", "31922"], ["k", "31923"]], []),
]


def definition(owner=OWNER, community=COMMUNITY, sections=None, extra_tags=(), created_at=None, relays=(RELAY,)):
    tags = [["d", community], ["name", "Test Community"]]
    tags += [["r", relay] for relay in relays]
    tags += [list(tag) for tag in extra_tags]
    for name, kinds, lists in (sections if sections is not None else DEFAULT_SECTIONS):
        tags.append(["content", name])
        tags += [list(tag) for tag in kinds]
        tags += [["a", address] for address in lists]
    return event(32222, owner, tags, created_at=created_at)


def shard(owner, purpose, pubkeys, shard_no=None, declined=False, community=COMMUNITY, created_at=None):
    identifier = shard_address(owner, purpose, shard_no, community).split(":", 2)[2]
    tags = [["d", identifier]] + [["p", p] for p in pubkeys]
    if declined:
        tags.append(["status", "declined"])
    return event(30000, owner, tags, created_at=created_at)


def authority_tags(community=COMMUNITY, owner=OWNER, relay=""):
    return [["h", community], ["a", f"32222:{owner}:{community}", relay, "community"]]


def person_report(reporter, target, created_at=None):
    return event(1984, reporter, [["p", target, "spam"]] + authority_tags(), created_at=created_at)


def event_report(reporter, target_pubkey, target_id, section="General", target_address=None, created_at=None):
    tags = [["e", target_id, "spam"]]
    if target_address:
        tags.append(["a", target_address, "spam"])
    tags += [["p", target_pubkey]] + authority_tags() + [["content", section]]
    return event(1984, reporter, tags, created_at=created_at)


def report_delete(report, created_at=None, legacy=False):
    """Current shape: h scope only. ``legacy=True`` adds the old marked community a."""
    tags = [["h", COMMUNITY], ["e", report["id"], "", report["pubkey"], "report"], ["k", "1984"]]
    if legacy:
        tags = [["e", report["id"], "", report["pubkey"], "report"], ["k", "1984"]] + authority_tags()
    return event(5, report["pubkey"], tags, created_at=created_at)


def tombstone(owner, address, created_at=None):
    return event(5, owner, [["a", address]], created_at=created_at)


def thread(pubkey, content="hello"):
    return event(11, pubkey, [["h", COMMUNITY]], content)


def room_root(pubkey):
    return event(11, pubkey, [["h", COMMUNITY], ["room", ""]], "room")


def room_message(pubkey, root_id="ab" * 32):
    return event(9, pubkey, [["h", COMMUNITY], ["e", root_id]], "hi")


def request(ev, source="192.0.2.1"):
    return {
        "type": "new",
        "event": ev,
        "receivedAt": ev["created_at"],
        "sourceType": "IP4",
        "sourceInfo": source,
    }


def config(**overrides):
    env = {"BUDABIT_BRANCHES": ADDRESS, "BUDABIT_DISABLE_LOADER": "1"}
    env.update(overrides)
    return BudabitConfig.from_env(env)


def warm_state(events, addresses=(ADDRESS,)):
    """CommunityState with every branch warm and the given events applied."""
    state = CommunityState(list(addresses))
    for branch in state.branches.values():
        branch.warm = True
    for ev in events:
        state.apply(ev)
    return state


def standard_events():
    """Definition plus shards so that MEMBER writes General/threads, MOD_* are moderators."""
    return [
        definition(),
        shard(OWNER, "general", [MEMBER]),
        shard(MOD_GENERAL, "general", [MEMBER2], shard_no=2),
        shard(OWNER, "room-creator", [MEMBER]),
        shard(OWNER, "thread-creator", [MEMBER]),
        shard(MOD_CODE, "code-curator", []),
    ]
