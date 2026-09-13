"""Communikeys V2 wire grammar.

This is a port of the validation rules in Budabit's
``src/app/core/community-protocol.ts`` and the normative text in
``docs/architecture/Communikeys.md``. Every rule that decides whether an event
is a valid definition, shard, wrapper, report, or carries a valid branch
authority reference lives here. Keep it free of relay state so it can be
tested against golden vectors exported from the Budabit client.
"""

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

COMMUNITY_DEFINITION_KIND = 32222
TARGETED_PUBLICATION_KIND = 30222
PROFILE_LIST_KIND = 30000
BADGE_DEFINITION_KIND = 30009
BADGE_AWARD_KIND = 8
FORM_TEMPLATE_KIND = 30168
FORM_RESPONSE_KIND = 1069
REPORT_KIND = 1984
LABEL_KIND = 1985
REACTION_KIND = 7
DELETE_KIND = 5
MAX_TARGET_COMMUNITIES = 12

PROFILE_LIST_STATUS_DECLINED = "declined"
RENOUNCED_COMMUNITIES_DTAG = "app/budabit/renounced-communities"
REPORT_REASON = "spam"

SUBTYPE_ROOM = "room"
SUBTYPE_THREADS = "threads"
SUBTYPE_ROOM_MESSAGE = "room-message"

LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
CANONICAL_UINT = re.compile(r"^(0|[1-9][0-9]*)$")
GEOHASH = re.compile(r"^[0123456789bcdefghjkmnpqrstuvwxyz]{1,12}$")
SECTION_PURPOSE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
SECTION_SHARD = re.compile(r"^(?:[2-9]|[1-9][0-9]+)$")
SERVICE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
MINT_TYPE = re.compile(r"^[\x21-\x7e]{1,32}$")
RELAY_HINT = re.compile(r"^wss?://", re.IGNORECASE)

MAX_SAFE_INTEGER = 2**53 - 1

SECTION_TAGS = {"k", "a", "badge", "retention"}
TOP_LEVEL_TAGS = {
    "d",
    "name",
    "description",
    "picture",
    "banner",
    "website",
    "r",
    "blossom",
    "grasp",
    "mint",
    "location",
    "g",
    "tos",
    "service",
}


class InvalidEvent(ValueError):
    """Raised when an event violates a Communikeys rule."""


# --- primitives ---------------------------------------------------------------


def utf8_length(value):
    return len(value.encode("utf-8"))


def is_hex64(value):
    return isinstance(value, str) and bool(LOWER_HEX_64.match(value))


def normalize_pubkey(value):
    """Lower-case a hex pubkey; returns '' when it is not 64 hex characters."""
    if not isinstance(value, str):
        return ""
    trimmed = value.strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", trimmed):
        return trimmed.lower()
    return ""


def parse_owner_pubkey(value):
    return value if is_hex64(value) else None


def parse_community_id(value):
    return value if is_hex64(value) else None


def exact_tag(tag, size):
    return isinstance(tag, list) and len(tag) == size


def get_tags(tags, name):
    return [tag for tag in tags if tag and tag[0] == name]


_MISSING = object()
_INVALID = None


def get_singleton(tags, name, sizes):
    """Return the single tag, ``_MISSING`` if absent, or None if invalid."""
    matches = get_tags(tags, name)
    if len(matches) > 1:
        return _INVALID
    if not matches:
        return _MISSING
    return matches[0] if len(matches[0]) in sizes else _INVALID


def parse_bounded_text(value, minimum, maximum):
    if not isinstance(value, str) or value != value.strip():
        return None
    size = utf8_length(value)
    return value if minimum <= size <= maximum else None


def parse_canonical_kind(value):
    if not isinstance(value, str) or not CANONICAL_UINT.match(value):
        return None
    kind = int(value)
    return kind if kind <= 65535 else None


_DEFAULT_PORTS = {"wss": "443", "ws": "80", "https": "443", "http": "80"}

# Characters the WHATWG serializer leaves untouched in path and query. Anything
# else (space, quotes, angle brackets, braces, backslash, caret, pipe, non-ASCII,
# controls) would be percent-encoded, so an input containing it can never equal
# its own normalisation and the client rejects it.
_URL_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~:/?#[]@!$&'()*+,;=%")
_HOST_SAFE = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-.")
_DOT_SEGMENTS = frozenset({".", "..", "%2e", "%2e%2e", ".%2e", "%2e."})


def _remove_dot_segments(path):
    """WHATWG path state: '.' is dropped, '..' pops (never above root)."""
    segments = path.split("/")[1:]
    out = []
    for index, segment in enumerate(segments):
        lowered = segment.lower()
        last = index == len(segments) - 1
        if lowered in ("..", "%2e%2e", ".%2e", "%2e."):
            if out:
                out.pop()
            if last:
                out.append("")
        elif lowered in (".", "%2e"):
            if last:
                out.append("")
        else:
            out.append(segment)
    return "/" + "/".join(out)


def normalize_url(value, schemes):
    """WHATWG-equivalent normalisation for the subset of URLs Communikeys allows.

    Lower-case scheme and host, drop default ports, remove dot segments,
    compress IPv6 hosts, reject credentials, fragments, empty hosts, and any
    character the WHATWG serializer would percent-encode (see ``_URL_SAFE``).
    A terminal ``/`` is removed only when it is the whole path and there is
    no query. Inputs the serializer would rewrite in ways not reproduced here
    return None rather than a guess.
    """
    if not isinstance(value, str) or value != value.strip() or utf8_length(value) > 2048:
        return None
    if not value or any(ch not in _URL_SAFE for ch in value):
        return None
    if "\\" in value:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme + ":" not in schemes:
        return None
    if parts.fragment or "#" in value:
        return None
    netloc = parts.netloc or ""
    if "@" in netloc or parts.username is not None or parts.password is not None:
        return None
    try:
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if not hostname:
        return None
    host = hostname.lower()
    if netloc.startswith("["):
        try:
            import ipaddress

            host = f"[{ipaddress.IPv6Address(host).compressed}]"
        except ValueError:
            return None
    elif any(ch not in _HOST_SAFE for ch in host):
        return None
    if port is not None and str(port) != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    path = _remove_dot_segments(parts.path or "/")
    query = parts.query
    if "'" in query:
        # Special-scheme query percent-encode set includes the apostrophe.
        return None
    normalized = urlunsplit((scheme, netloc, path, query, ""))
    if path == "/" and not query:
        normalized = normalized[:-1]
    return normalized


def normalize_relay(value):
    return normalize_url(value, ("wss:",))


def normalize_https(value):
    return normalize_url(value, ("https:",))


def normalize_website(value):
    return normalize_url(value, ("http:", "https:"))


@dataclass(frozen=True)
class Address:
    kind: int
    pubkey: str
    identifier: str

    @property
    def address(self):
        return f"{self.kind}:{self.pubkey}:{self.identifier}"


def parse_address(value, required_kind=None):
    """Parse ``kind:pubkey:identifier`` for addressable kinds (30000-39999)."""
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) < 3:
        return None
    kind = parse_canonical_kind(parts[0])
    pubkey = parse_owner_pubkey(parts[1])
    identifier = ":".join(parts[2:])
    if kind is None or kind < 30000 or kind >= 40000:
        return None
    if required_kind is not None and kind != required_kind:
        return None
    if not pubkey or not identifier or utf8_length(identifier) > 200:
        return None
    return Address(kind, pubkey, identifier)


def parse_definition_address(value):
    """Parse ``32222:<owner>:<communityId>``; returns (owner, communityId)."""
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != str(COMMUNITY_DEFINITION_KIND):
        return None
    owner = parse_owner_pubkey(parts[1])
    community_id = parse_community_id(parts[2])
    if not owner or not community_id:
        return None
    return owner, community_id


def make_definition_address(owner, community_id):
    return f"{COMMUNITY_DEFINITION_KIND}:{owner}:{community_id}"


def parse_profile_list_identifier(community_id, identifier):
    """Parse ``<communityId>-<purpose>[.<shard>]``; returns (purpose, shard)."""
    if not identifier.startswith(f"{community_id}-") or utf8_length(identifier) > 200:
        return None
    suffix = identifier[len(community_id) + 1 :]
    pieces = suffix.split(".")
    purpose = pieces[0]
    shard = pieces[1] if len(pieces) > 1 else None
    if len(pieces) > 2 or not SECTION_PURPOSE.match(purpose):
        return None
    if shard is not None and not SECTION_SHARD.match(shard):
        return None
    return purpose, (int(shard) if shard else None)


def get_addressable_address(event):
    """``kind:pubkey:d`` of an addressable event, or None."""
    kind = event.get("kind")
    if not isinstance(kind, int) or kind < 30000 or kind >= 40000:
        return None
    d_tags = get_tags(event.get("tags") or [], "d")
    if not d_tags or len(d_tags[0]) < 2:
        return None
    return f"{kind}:{event.get('pubkey')}:{d_tags[0][1]}"


# --- definitions --------------------------------------------------------------


@dataclass
class SectionKind:
    kind: int
    subtype: Optional[str] = None

    @property
    def key(self):
        return f"{self.kind}:{self.subtype or ''}"


@dataclass
class ProfileListRef:
    address: str
    relay: Optional[str] = None

    @property
    def owner(self):
        return self.address.split(":")[1]

    @property
    def identifier(self):
        return ":".join(self.address.split(":")[2:])


@dataclass
class Section:
    name: str
    kinds: list
    profile_lists: list
    badges: list = field(default_factory=list)

    @property
    def name_key(self):
        return normalize_section_name(self.name)

    def supports(self, kind, subtype=None):
        subtype = normalize_subtype(subtype)
        return any(item.kind == kind and item.subtype == subtype for item in self.kinds)


@dataclass
class Definition:
    event: dict
    owner: str
    community_id: str
    name: str
    relays: list
    sections: list

    @property
    def address(self):
        return make_definition_address(self.owner, self.community_id)

    def section_for(self, kind, subtype=None):
        for section in self.sections:
            if section.supports(kind, subtype):
                return section
        return None

    def section_named(self, name):
        key = normalize_section_name(name)
        for section in self.sections:
            if section.name_key == key:
                return section
        return None

    def profile_list_refs(self):
        for section in self.sections:
            for ref in section.profile_lists:
                yield section, ref


def normalize_section_name(name):
    return (name or "").strip()


def normalize_subtype(subtype):
    if subtype is None:
        return None
    trimmed = subtype.strip()
    return trimmed or None


def _fail(reason):
    raise InvalidEvent(reason)


def _parse_section(tags, community_id):
    content = tags[0]
    name = parse_bounded_text(content[1], 1, 100) if exact_tag(content, 2) else None
    if not name:
        _fail("section name must be 1 to 100 bytes")

    kinds = []
    profile_lists = []
    badges = []
    for tag in tags[1:]:
        if tag[0] == "k":
            if len(tag) not in (2, 3):
                _fail("section k tag arity")
            kind = parse_canonical_kind(tag[1])
            subtype = parse_bounded_text(tag[2], 1, 64) if len(tag) == 3 else None
            if kind is None or (len(tag) == 3 and not subtype):
                _fail("section k tag value")
            kinds.append(SectionKind(kind, subtype))
        elif tag[0] in ("a", "badge"):
            if len(tag) not in (2, 3):
                _fail(f"section {tag[0]} tag arity")
            ref = parse_address(tag[1], PROFILE_LIST_KIND if tag[0] == "a" else BADGE_DEFINITION_KIND)
            relay = normalize_relay(tag[2]) if len(tag) == 3 and tag[2] else None
            if not ref or (len(tag) == 3 and tag[2] and not relay):
                _fail(f"section {tag[0]} tag address")
            if tag[0] == "a" and not parse_profile_list_identifier(community_id, ref.identifier):
                _fail("profile-list identifier must be <communityId>-<purpose>[.<shard>]")
            item = ProfileListRef(ref.address, relay)
            (profile_lists if tag[0] == "a" else badges).append(item)
        elif tag[0] == "retention":
            if not exact_tag(tag, 4):
                _fail("retention tag arity")
            kind = parse_canonical_kind(tag[1])
            value = int(tag[2]) if CANONICAL_UINT.match(tag[2] or "") else 0
            if kind is None or value <= 0 or value > MAX_SAFE_INTEGER or tag[3] not in ("time", "count"):
                _fail("retention tag value")
    if not kinds:
        _fail("section requires at least one k tag")
    return Section(name, kinds, profile_lists, badges)


def parse_definition(event):
    """Validate a ``kind:32222`` definition. Raises InvalidEvent."""
    if event.get("kind") != COMMUNITY_DEFINITION_KIND:
        _fail("not a community definition")
    if event.get("content") != "":
        _fail("definition content must be empty")
    owner = parse_owner_pubkey(event.get("pubkey"))
    if not owner:
        _fail("definition author")
    tags = event.get("tags") or []

    d_tags = get_tags(tags, "d")
    if len(d_tags) != 1 or not exact_tag(d_tags[0], 2):
        _fail("definition requires exactly one d tag")
    community_id = parse_community_id(d_tags[0][1])
    if not community_id:
        _fail("definition d must be a 64-hex community id")
    if get_tags(tags, "h"):
        _fail("definition must not carry an h tag")

    name_tag = get_singleton(tags, "name", (2,))
    name = parse_bounded_text(name_tag[1], 1, 100) if name_tag not in (_MISSING, _INVALID) else None
    if not name:
        _fail("definition requires one name tag of 1 to 100 bytes")

    relay_tags = get_tags(tags, "r")
    if not 1 <= len(relay_tags) <= 20:
        _fail("definition requires 1 to 20 r tags")
    relays = []
    for tag in relay_tags:
        if not exact_tag(tag, 2):
            _fail("r tag arity")
        relay = normalize_relay(tag[1])
        if not relay or relay != tag[1]:
            _fail("r tag must be a normalized wss URL")
        if relay not in relays:
            relays.append(relay)

    for tag_name, maximum in (("description", 4096), ("location", 256)):
        tag = get_singleton(tags, tag_name, (2,))
        if tag is _INVALID or (tag is not _MISSING and not parse_bounded_text(tag[1], 1, maximum)):
            _fail(f"{tag_name} tag")
    for tag_name, normalizer in (
        ("picture", normalize_https),
        ("banner", normalize_https),
        ("website", normalize_website),
    ):
        tag = get_singleton(tags, tag_name, (2,))
        if tag is _INVALID or (tag is not _MISSING and not normalizer(tag[1])):
            _fail(f"{tag_name} tag")
    g_tag = get_singleton(tags, "g", (2,))
    if g_tag is _INVALID or (g_tag is not _MISSING and not GEOHASH.match(g_tag[1] or "")):
        _fail("g tag")

    for tag_name, normalizer in (("blossom", normalize_https), ("grasp", normalize_relay)):
        items = get_tags(tags, tag_name)
        if len(items) > 20:
            _fail(f"too many {tag_name} tags")
        for tag in items:
            if not exact_tag(tag, 2):
                _fail(f"{tag_name} tag arity")
            value = normalizer(tag[1])
            if not value or value != tag[1]:
                _fail(f"{tag_name} tag URL")

    mint_tags = get_tags(tags, "mint")
    if len(mint_tags) > 20:
        _fail("too many mint tags")
    for tag in mint_tags:
        if len(tag) not in (2, 3):
            _fail("mint tag arity")
        url = normalize_https(tag[1])
        mint_type = tag[2] if len(tag) == 3 else ""
        if not url or url != tag[1] or (mint_type and not MINT_TYPE.match(mint_type)):
            _fail("mint tag value")

    tos = get_singleton(tags, "tos", (2, 3))
    if tos is _INVALID:
        _fail("tos tag")
    if tos is not _MISSING:
        relay = normalize_relay(tos[2]) if len(tos) == 3 and tos[2] else None
        reference_ok = is_hex64(tos[1]) or bool(parse_address(tos[1]))
        if not reference_ok or (len(tos) == 3 and tos[2] and relay != tos[2]):
            _fail("tos tag value")

    service_tags = get_tags(tags, "service")
    if len(service_tags) > 50:
        _fail("too many service tags")
    for tag in service_tags:
        if not exact_tag(tag, 6) or not SERVICE_NAME.match(tag[1] or ""):
            _fail("service tag")
        request_relay = normalize_relay(tag[3])
        handler_relay = normalize_relay(tag[5])
        if (
            not parse_owner_pubkey(tag[2])
            or not request_relay
            or request_relay != tag[3]
            or not parse_address(tag[4])
            or not handler_relay
            or handler_relay != tag[5]
        ):
            _fail("service tag value")

    raw_sections = []
    current = None
    for tag in tags:
        if not tag:
            continue
        if tag[0] == "content":
            current = [tag]
            raw_sections.append(current)
        elif current is not None:
            if tag[0] in TOP_LEVEL_TAGS:
                _fail("top-level tag inside a section")
            current.append(tag)
        elif tag[0] in SECTION_TAGS:
            _fail("section-local tag before the first content tag")
    if not raw_sections:
        _fail("definition requires at least one content section")

    sections = [_parse_section(raw, community_id) for raw in raw_sections]
    names = set()
    kinds = set()
    for section in sections:
        key = section.name.lower()
        if key in names:
            _fail("duplicate section name")
        names.add(key)
        for item in section.kinds:
            if item.key in kinds:
                _fail("duplicate (kind, subtype) across sections")
            kinds.add(item.key)

    return Definition(event, owner, community_id, name, relays, sections)


# --- profile lists -----------------------------------------------------------


def profile_list_status(event):
    for tag in event.get("tags") or []:
        if tag and tag[0] == "status":
            return tag[1] if len(tag) > 1 else ""
    return ""


def is_profile_list_declined(event):
    return profile_list_status(event) == PROFILE_LIST_STATUS_DECLINED


def is_renounced_communities_list(event):
    return event.get("kind") == PROFILE_LIST_KIND and any(
        tag and tag[0] == "d" and len(tag) > 1 and tag[1] == RENOUNCED_COMMUNITIES_DTAG
        for tag in event.get("tags") or []
    )


def profile_list_pubkeys(event):
    """Valid ``p`` grants of a current, non-declined list (mirrors getProfileListPubkeys)."""
    if not event or event.get("kind") != PROFILE_LIST_KIND:
        return []
    if is_renounced_communities_list(event) or is_profile_list_declined(event):
        return []
    result = []
    for tag in event.get("tags") or []:
        if tag and tag[0] == "p" and len(tag) > 1:
            pubkey = parse_owner_pubkey(tag[1])
            if pubkey and pubkey not in result:
                result.append(pubkey)
    return result


def is_valid_shard(event, ref):
    """A shard is valid for a referenced coordinate when kind, author and exact ``d`` match."""
    if event.get("kind") != PROFILE_LIST_KIND:
        return False
    if event.get("pubkey") != ref.owner:
        return False
    d_tags = get_tags(event.get("tags") or [], "d")
    return len(d_tags) == 1 and len(d_tags[0]) == 2 and d_tags[0][1] == ref.identifier


# --- authority references ---------------------------------------------------


@dataclass(frozen=True)
class Authority:
    owner: str
    community_id: str
    relay: Optional[str] = None

    @property
    def address(self):
        return make_definition_address(self.owner, self.community_id)


def parse_authority(tags):
    """Exactly one ``h`` plus one ``["a", 32222:owner:id, relay|"", "community"]`` (mirrors parseCommunityAuthority)."""
    h_tags = get_tags(tags, "h")
    a_tags = [tag for tag in tags if tag and tag[0] == "a" and len(tag) > 3 and tag[3] == "community"]
    if len(h_tags) != 1 or not exact_tag(h_tags[0], 2) or len(a_tags) != 1 or not exact_tag(a_tags[0], 4):
        return None
    community_id = parse_community_id(h_tags[0][1])
    parsed = parse_definition_address(a_tags[0][1])
    relay = normalize_relay(a_tags[0][2]) if a_tags[0][2] else None
    if not community_id or not parsed or parsed[1] != community_id:
        return None
    if a_tags[0][2] and relay != a_tags[0][2]:
        return None
    return Authority(parsed[0], community_id, relay)


def single_h_tag(tags):
    """The community id of an event with exactly one two-value ``h`` tag, else None."""
    h_tags = get_tags(tags, "h")
    if len(h_tags) != 1 or not exact_tag(h_tags[0], 2):
        return None
    return h_tags[0][1]


def has_marked_community_a(tags):
    return any(tag and tag[0] == "a" and len(tag) > 3 and tag[3] == "community" for tag in tags)


def derive_subtype(event):
    kind = event.get("kind")
    tags = event.get("tags") or []
    if kind == 11:
        return SUBTYPE_ROOM if get_tags(tags, "room") else SUBTYPE_THREADS
    if kind == 9:
        return SUBTYPE_ROOM_MESSAGE
    return None


# --- targeted publications --------------------------------------------------


@dataclass
class Wrapper:
    identifier: str
    kind: int
    targets: list  # of Authority


def _parse_targeting_address_tag(tag, role):
    marked = exact_tag(tag, 4) and tag[3] == role
    if not marked and not exact_tag(tag, 2) and not exact_tag(tag, 3):
        return None
    address = parse_address(tag[1])
    relay = normalize_relay(tag[2]) if len(tag) > 2 and tag[2] else None
    if not address or (len(tag) > 2 and tag[2] and relay != tag[2]):
        return None
    return address, relay


def _parse_targeting_event_tag(tag):
    marked = exact_tag(tag, 5) and tag[4] == "source"
    if not marked and len(tag) not in (2, 3, 4):
        return None
    if not is_hex64(tag[1]):
        return None
    relay = normalize_relay(tag[2]) if len(tag) > 2 and tag[2] else None
    pubkey = parse_owner_pubkey(tag[3]) if len(tag) > 3 and tag[3] else None
    if (len(tag) > 2 and tag[2] and relay != tag[2]) or (len(tag) > 3 and tag[3] and not pubkey):
        return None
    return tag[1]


def parse_wrapper(event):
    """Validate a ``kind:30222`` targeting wrapper (mirrors parseTargetedPublication)."""
    if event.get("kind") != TARGETED_PUBLICATION_KIND:
        _fail("not a targeting wrapper")
    if event.get("content") != "":
        _fail("wrapper content must be empty")
    tags = event.get("tags") or []
    for tag in tags:
        if not tag:
            continue
        if tag[0] == "a" and len(tag) > 3 and tag[3] == "source" and not exact_tag(tag, 4):
            _fail("source a tag arity")
        if tag[0] == "e" and len(tag) > 4 and tag[4] == "source" and not exact_tag(tag, 5):
            _fail("source e tag arity")
        if tag[0] == "a" and len(tag) > 3 and tag[3] == "community" and not exact_tag(tag, 4):
            _fail("community a tag arity")
    d_tags = get_tags(tags, "d")
    k_tags = get_tags(tags, "k")
    if len(d_tags) != 1 or not exact_tag(d_tags[0], 2) or not d_tags[0][1]:
        _fail("wrapper requires exactly one non-empty d tag")
    if len(k_tags) != 1 or not exact_tag(k_tags[0], 2):
        _fail("wrapper requires exactly one k tag")
    kind = parse_canonical_kind(k_tags[0][1])
    if kind is None:
        _fail("wrapper k tag value")

    targets = []
    addresses = set()
    community_indexes = set()
    index = 0
    while index < len(tags):
        tag = tags[index]
        if not tag or tag[0] != "h":
            index += 1
            continue
        if not exact_tag(tag, 2):
            _fail("wrapper h tag arity")
        nxt = tags[index + 1] if index + 1 < len(tags) else None
        if not nxt or nxt[0] != "a":
            _fail("wrapper h must be followed by its community a")
        parsed = _parse_targeting_address_tag(nxt, "community")
        community_id = parse_community_id(tag[1])
        definition = parse_definition_address(parsed[0].address) if parsed else None
        if not community_id or not definition or definition[1] != community_id:
            _fail("wrapper target pair mismatch")
        address = make_definition_address(*definition)
        if address in addresses:
            _fail("duplicate wrapper target")
        addresses.add(address)
        targets.append(Authority(definition[0], community_id, parsed[1]))
        community_indexes.add(index + 1)
        index += 2
    if not 1 <= len(targets) <= MAX_TARGET_COMMUNITIES:
        _fail("wrapper requires 1 to 12 target pairs")

    source_seen = False
    for index, tag in enumerate(tags):
        if not tag or tag[0] == "h" or index in community_indexes:
            continue
        if tag[0] == "a":
            if source_seen or (len(tag) > 3 and tag[3] == "community"):
                _fail("wrapper has more than one source")
            parsed = _parse_targeting_address_tag(tag, "source")
            if not parsed or parsed[0].kind != kind:
                _fail("wrapper source address")
            source_seen = True
        elif tag[0] == "e":
            if source_seen:
                _fail("wrapper has more than one source")
            if not _parse_targeting_event_tag(tag):
                _fail("wrapper source event")
            source_seen = True
    return Wrapper(d_tags[0][1], kind, targets)


# --- reports -----------------------------------------------------------------


@dataclass
class Report:
    event: dict
    target: str  # "event" | "person"
    authority: Authority
    target_pubkey: str
    target_event_id: Optional[str] = None
    target_address: Optional[str] = None
    section_name: Optional[str] = None

    @property
    def reporter(self):
        return self.event.get("pubkey")

    @property
    def id(self):
        return self.event.get("id")


def _has_report_reason(tag):
    reason3 = tag[3].strip() if len(tag) > 3 and tag[3] else ""
    reason2 = tag[2].strip() if len(tag) > 2 and tag[2] else ""
    return bool(reason3 or (reason2 and not RELAY_HINT.match(reason2)))


def _reason_tag(tags, name):
    for tag in tags:
        if tag and tag[0] == name and not (len(tag) > 4 and tag[4] == "report") and _has_report_reason(tag):
            return tag
    return None


def _reason_address_tag(tags):
    for tag in tags:
        if (
            tag
            and tag[0] == "a"
            and not (len(tag) > 3 and tag[3] == "community")
            and _has_report_reason(tag)
            and parse_address(tag[1])
        ):
            return tag
    return None


def _first_tag_value(tags, name):
    for tag in tags:
        if tag and tag[0] == name:
            return tag[1] if len(tag) > 1 else ""
    return None


def parse_report(event, target_author_lookup=None):
    """Parse a ``kind:1984`` community report (mirrors parseCommunityReport).

    ``target_author_lookup(event_id)`` may resolve the author of an event
    report that carries no ``p`` tag; the relay usually has the target stored.
    """
    if event.get("kind") != REPORT_KIND:
        return None
    tags = event.get("tags") or []
    authority = parse_authority(tags)
    if not authority:
        return None

    e_tag = _reason_tag(tags, "e")
    a_tag = _reason_address_tag(tags)
    target_address = parse_address(a_tag[1]) if a_tag else None
    p_reason = _reason_tag(tags, "p")
    has_event_target = bool((e_tag and len(e_tag) > 1 and e_tag[1]) or target_address)

    if has_event_target:
        p_value = _first_tag_value(tags, "p")
        target_pubkey = normalize_pubkey(p_value or "")
        if not target_pubkey and target_address:
            target_pubkey = target_address.pubkey
        if not target_pubkey and e_tag and target_author_lookup:
            target_pubkey = normalize_pubkey(target_author_lookup(e_tag[1]) or "")
        if not target_pubkey:
            return None
        section_name = normalize_section_name(_first_tag_value(tags, "content") or "")
        if not section_name:
            return None
        return Report(
            event,
            "event",
            authority,
            target_pubkey,
            target_event_id=e_tag[1] if e_tag and len(e_tag) > 1 and e_tag[1] else None,
            target_address=target_address.address if target_address else None,
            section_name=section_name,
        )

    if not p_reason or len(p_reason) < 2:
        return None
    target_pubkey = normalize_pubkey(p_reason[1])
    if not target_pubkey:
        return None
    return Report(event, "person", authority, target_pubkey)


def marked_report_reference(tags):
    """``["e", reportId, relay|"", reporter, "report"]`` (exactly one) -> (reportId, reporter)."""
    matches = [tag for tag in tags if tag and tag[0] == "e" and len(tag) > 4 and tag[4] == "report"]
    if len(matches) != 1 or len(matches[0]) != 5:
        return None
    report_id = (matches[0][1] or "").strip()
    relay = matches[0][2]
    author = normalize_pubkey(matches[0][3] or "")
    if not report_id or (relay and normalize_relay(relay) != relay) or not author:
        return None
    return report_id, author


def delete_matches_community(delete_event, community_id, address):
    """Mirror of deleteMatchesCommunity: exactly one ``h`` for the community.

    Deletion requests carry only ``h``; a legacy marked community ``a`` is
    accepted while it names this exact branch. Any other ``a`` on a ``kind:5``
    is an NIP-09 target and is irrelevant to scoping.
    """
    if delete_event.get("kind") != DELETE_KIND:
        return False
    tags = delete_event.get("tags") or []
    if single_h_tag(tags) != community_id:
        return False
    if not has_marked_community_a(tags):
        return True
    authority = parse_authority(tags)
    return authority is not None and authority.address == address


def is_report_delete(delete_event, report):
    """Same-author ``kind:5`` scoped to the report's community that references it with marker ``report``."""
    if delete_event.get("kind") != DELETE_KIND:
        return False
    if normalize_pubkey(delete_event.get("pubkey") or "") != normalize_pubkey(report.reporter or ""):
        return False
    tags = delete_event.get("tags") or []
    if not delete_matches_community(
        delete_event, report.authority.community_id, report.authority.address
    ):
        return False
    reference = marked_report_reference(tags)
    if not reference or reference[0] != report.id or reference[1] != normalize_pubkey(report.reporter or ""):
        return False
    k_tags = get_tags(tags, "k")
    return not k_tags or any(len(tag) > 1 and tag[1] == str(REPORT_KIND) for tag in k_tags)


# --- workflow shapes ---------------------------------------------------------


def parse_form_address(value):
    """``30168:<pubkey>:<identifier>`` (mirrors parseAdmissionFormAddress)."""
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) < 3 or parts[0] != str(FORM_TEMPLATE_KIND):
        return None
    pubkey = parse_owner_pubkey(parts[1])
    identifier = ":".join(parts[2:])
    if not pubkey or not identifier:
        return None
    return value


def _single_marked_form(tags):
    matches = [tag for tag in tags if tag and tag[0] == "a" and len(tag) > 3 and tag[3] == "form"]
    if len(matches) != 1 or len(matches[0]) != 4:
        return None
    return parse_form_address(matches[0][1])


def is_star(event):
    """Community star: kind 7, content '+', authority tags, k=32222 (mirrors parseCommunityStarReaction)."""
    if event.get("kind") != REACTION_KIND or event.get("content") != "+":
        return False
    tags = event.get("tags") or []
    if parse_authority(tags) is None:
        return False
    return _first_tag_value(tags, "k") == str(COMMUNITY_DEFINITION_KIND)


def is_admission_response(event):
    """kind 1069 with authority tags and exactly one marked form address (mirrors parseAdmissionResponse)."""
    if event.get("kind") != FORM_RESPONSE_KIND:
        return False
    tags = event.get("tags") or []
    return parse_authority(tags) is not None and _single_marked_form(tags) is not None


def admission_review_section(event):
    """Section name of a valid admission review, else None (mirrors parseAdmissionReview)."""
    if event.get("kind") != REACTION_KIND or event.get("content") not in ("+", "-"):
        return None
    tags = event.get("tags") or []
    if parse_authority(tags) is None:
        return None
    responses = [tag for tag in tags if tag and tag[0] == "e" and len(tag) > 4 and tag[4] == "response"]
    applicants = get_tags(tags, "p")
    if len(responses) != 1 or len(responses[0]) != 5 or not responses[0][1]:
        return None
    if _single_marked_form(tags) is None:
        return None
    if len(applicants) != 1 or not exact_tag(applicants[0], 2) or not normalize_pubkey(applicants[0][1]):
        return None
    k_tags = get_tags(tags, "k")
    if k_tags and not any(len(tag) > 1 and tag[1] == str(FORM_RESPONSE_KIND) for tag in k_tags):
        return None
    return normalize_section_name(_first_tag_value(tags, "content") or "")


def looks_like_admission_review(tags):
    """Discriminator only; use admission_review_section for the full shape."""
    return any(tag and tag[0] == "e" and len(tag) > 4 and tag[4] == "response" for tag in tags) or (
        _first_tag_value(tags, "k") == str(FORM_RESPONSE_KIND)
    )


def is_moderator_request_decision(event):
    """Owner reaction on a kind 30000 request: authority tags, k=30000, an e target (mirrors getActiveTargetReactions)."""
    if event.get("kind") != REACTION_KIND or event.get("content") not in ("+", "-"):
        return False
    tags = event.get("tags") or []
    if parse_authority(tags) is None:
        return False
    return _first_tag_value(tags, "k") == str(PROFILE_LIST_KIND) and any(
        tag and tag[0] == "e" and len(tag) > 1 and is_hex64(tag[1]) for tag in tags
    )


def is_moderator_request(event):
    """Requester-authored empty kind 30000 with authority tags and no p tags."""
    if event.get("kind") != PROFILE_LIST_KIND or event.get("content") != "":
        return False
    tags = event.get("tags") or []
    if any(tag and tag[0] == "p" for tag in tags):
        return False
    d_tags = get_tags(tags, "d")
    if len(d_tags) != 1 or not exact_tag(d_tags[0], 2) or not d_tags[0][1]:
        return False
    return parse_authority(tags) is not None


# --- kind:5 tombstones -------------------------------------------------------


def tombstone_addresses(delete_event):
    """Addresses a ``kind:5`` tombstones on this relay.

    strfry applies NIP-09 to every ``a`` tag on a ``kind:5`` regardless of
    arity or markers (the signer must own the coordinate; that check happens
    where the tombstone is applied). The plugin follows storage, not the
    client's stricter exactly-one-two-value-``a`` reading, so its view never
    keeps a definition or shard the relay has already deleted.
    """
    if delete_event.get("kind") != DELETE_KIND:
        return []
    return [tag[1] for tag in get_tags(delete_event.get("tags") or [], "a") if len(tag) > 1 and tag[1]]


def is_address_tombstone(delete_event, address):
    return address in tombstone_addresses(delete_event)
