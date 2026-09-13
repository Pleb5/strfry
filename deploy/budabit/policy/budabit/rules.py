"""Decision table for Budabit community writes (plan §3).

``evaluate(event, state, config)`` returns an ``Outcome``: the pipeline
decision, a reason code, and whether the event is an authority event that
must be applied to branch state on commit.
"""

from dataclasses import dataclass

from ..pipeline import Decision
from . import protocol as P

AUTHORITY_KINDS = {
    P.COMMUNITY_DEFINITION_KIND,
    P.PROFILE_LIST_KIND,
    P.REPORT_KIND,
    P.DELETE_KIND,
}

TARGETABLE_KINDS = {31922, 31923, 9041, 1623, 30033}

PERSONAL_KINDS = {
    0,
    3,
    10000,
    10002,
    10003,
    10004,
    10019,
    10050,
    10063,
    10317,
    30008,
    30078,
}

# NIP-34 repository kinds pass through in strict mode until repo-relay
# attribution (plan §3.6, phase 4) is implemented.
NIP34_KINDS = {30617, 30618, 1617, 1618, 1619, 1621, 1622, 1623, 1624, 1630, 1631, 1632, 1633}


@dataclass
class Outcome:
    decision: Decision
    reason: str = ""
    authority: bool = False
    community_id: str = ""
    scope: str = "passthrough"  # passthrough | hosted | authority

    @property
    def accepted(self):
        return self.decision.accepted


def _accept(scope, community_id="", authority=False, reason="accept"):
    return Outcome(Decision.accept(), reason, authority, community_id, scope)


def _reject(msg, reason, community_id=""):
    return Outcome(Decision.reject(msg, reason), reason, False, community_id, "hosted")


def _short(community_id):
    return community_id[:8]


def _tag_value(tags, name):
    for tag in tags:
        if tag and tag[0] == name and len(tag) > 1:
            return tag[1]
    return None


def _banned(community_id):
    return _reject("blocked: author is moderated in this community", "person_banned", community_id)


# --- per-branch evaluation ---------------------------------------------------


def _availability(branch, derived):
    # Ordinary hosted content needs the complete snapshot (definition, shards,
    # reports); grants without bans would fail open. Authority events that
    # bootstrap the branch (definitions, referenced shards, deletes) are
    # handled before this point and do not wait.
    if not branch.warm:
        return _reject(
            "error: relay policy is loading, retry shortly",
            "warming_up",
            branch.community_id,
        )
    if not derived.available:
        return _reject(
            "blocked: community definition is not available on this relay",
            "definition_unavailable",
            branch.community_id,
        )
    return None


def _section_rule(event, derived, pubkey, kind, subtype, community_id, config):
    section = derived.definition.section_for(kind, subtype)
    label = f"{kind}/{subtype}" if subtype else str(kind)
    if section is None:
        return _reject(
            f"blocked: kind {label} is not enabled in community {_short(community_id)}",
            "kind_not_enabled",
            community_id,
        )
    if derived.is_banned(pubkey):
        return _banned(community_id)
    if not derived.can_write_section(pubkey, section):
        return _reject(
            f'blocked: not a current writer for section "{section.name}" in {_short(community_id)}',
            "no_grant",
            community_id,
        )
    if config.reject_censored_addresses:
        address = P.get_addressable_address(event)
        if address and address in derived.censored_addresses:
            return _reject(
                "blocked: address is moderated in this community",
                "censored_address",
                community_id,
            )
    return _accept("hosted", community_id)


def evaluate_branch(event, branch, config, authority_required):
    """Evaluate a community-scoped event against one hosted branch."""
    derived = branch.derived()
    community_id = branch.community_id
    unavailable = _availability(branch, derived)
    if unavailable:
        return unavailable

    pubkey = event.get("pubkey") or ""
    kind = event.get("kind")
    tags = event.get("tags") or []
    authority = P.parse_authority(tags)
    banned = derived.is_banned(pubkey)

    if kind == P.REPORT_KIND:
        if authority is None:
            return _reject(
                "invalid: community report requires h and marked community a",
                "invalid_authority_tags",
                community_id,
            )
        report = P.parse_report(event)
        if report is None:
            return _reject("invalid: malformed community report", "invalid_report", community_id)
        if banned:
            return _banned(community_id)
        if report.target_pubkey == pubkey:
            return _reject(
                "blocked: a report cannot target its own author", "self_report", community_id
            )
        if report.target == "person":
            if derived.is_all_sections_moderator(pubkey):
                return _accept("authority", community_id, authority=True)
            return _reject(
                "blocked: person reports require community-wide moderator authority",
                "report_authority",
                community_id,
            )
        section = derived.definition.section_named(report.section_name)
        if section is None:
            return _reject(
                f'blocked: unknown section "{report.section_name}" in report',
                "unknown_section",
                community_id,
            )
        if derived.is_moderator(pubkey, section):
            return _accept("authority", community_id, authority=True)
        report_section = derived.definition.section_for(P.REPORT_KIND)
        if report_section is not None and derived.can_write_section(pubkey, report_section):
            return _accept("authority", community_id, authority=True)
        return _reject(
            "blocked: content reports require a current community grant",
            "report_authority",
            community_id,
        )

    if kind == P.FORM_TEMPLATE_KIND:
        if authority is None:
            return _reject(
                "invalid: admission form requires h and marked community a",
                "invalid_authority_tags",
                community_id,
            )
        section_name = P.normalize_section_name(_tag_value(tags, "content") or "")
        section = derived.definition.section_named(section_name) if section_name else None
        if section is None:
            return _reject(
                "blocked: admission form names an unknown section", "unknown_section", community_id
            )
        if derived.is_moderator(pubkey, section):
            return _accept("hosted", community_id)
        return _reject(
            "blocked: admission forms require section moderator authority",
            "form_authority",
            community_id,
        )

    if kind == P.FORM_RESPONSE_KIND:
        if not P.is_admission_response(event):
            return _reject(
                "invalid: admission response requires h, marked community a, and one marked form a",
                "invalid_workflow_shape",
                community_id,
            )
        if banned:
            return _banned(community_id)
        return _accept("hosted", community_id)

    if kind == P.PROFILE_LIST_KIND:
        # Referenced shards are handled before branch evaluation; here only
        # moderator requests remain.
        if not P.is_moderator_request(event):
            return _reject(
                "invalid: profile list is neither a referenced shard nor a moderator request",
                "invalid_shard",
                community_id,
            )
        if banned:
            return _banned(community_id)
        return _accept("hosted", community_id)

    if kind == P.LABEL_KIND and derived.is_moderator(pubkey):
        # Moderator labels (report reviews, room archive) do not need a section.
        return _accept("hosted", community_id)

    if kind == P.REACTION_KIND:
        if _tag_value(tags, "k") == str(P.COMMUNITY_DEFINITION_KIND):
            if not P.is_star(event):
                return _reject(
                    "invalid: community star requires content '+', h, and marked community a",
                    "invalid_workflow_shape",
                    community_id,
                )
            if banned:
                return _banned(community_id)
            return _accept("hosted", community_id)
        if P.looks_like_admission_review(tags):
            section_name = P.admission_review_section(event)
            if section_name is None:
                return _reject(
                    "invalid: admission review requires h, marked community a, marked form a, marked response e, and one applicant p",
                    "invalid_workflow_shape",
                    community_id,
                )
            section = derived.definition.section_named(section_name) if section_name else None
            if section is None:
                return _reject(
                    "blocked: admission review names an unknown section",
                    "unknown_section",
                    community_id,
                )
            if derived.is_moderator(pubkey, section):
                return _accept("hosted", community_id)
            return _reject(
                "blocked: admission reviews require section moderator authority",
                "review_authority",
                community_id,
            )
        if _tag_value(tags, "k") == str(P.PROFILE_LIST_KIND):
            if not P.is_moderator_request_decision(event):
                return _reject(
                    "invalid: moderator request decision requires content '+'/'-', h, marked community a, and an e target",
                    "invalid_workflow_shape",
                    community_id,
                )
            if pubkey == derived.owner:
                return _accept("hosted", community_id)
            return _reject(
                "blocked: moderator request decisions require the community owner",
                "owner_only",
                community_id,
            )
        return _section_rule(event, derived, pubkey, kind, None, community_id, config)

    if kind in (P.BADGE_DEFINITION_KIND, P.BADGE_AWARD_KIND):
        if derived.is_moderator(pubkey):
            return _accept("hosted", community_id)
        return _reject(
            "blocked: community badges require moderator authority",
            "badge_authority",
            community_id,
        )

    if authority_required and authority is None:
        return _reject(
            "invalid: marked community a is malformed or mismatched",
            "invalid_authority_tags",
            community_id,
        )

    subtype = P.derive_subtype(event)
    return _section_rule(event, derived, pubkey, kind, subtype, community_id, config)


# --- top level ---------------------------------------------------------------


def _best(outcomes):
    """Accept if any branch accepts; otherwise the first rejection."""
    for outcome in outcomes:
        if outcome.accepted:
            return outcome
    return outcomes[0]


def _strict_passthrough(event, state, config):
    kind = event.get("kind")
    pubkey = event.get("pubkey") or ""
    if kind == P.DELETE_KIND:
        return _accept("passthrough", reason="strict_delete")
    if kind in NIP34_KINDS:
        return _accept("passthrough", reason="strict_nip34_passthrough")
    branches = list(state.branches.values())
    if kind in PERSONAL_KINDS:
        if any(branch.derived().has_any_role(pubkey) for branch in branches):
            return _accept("passthrough", reason="strict_participant_personal")
    if kind in TARGETABLE_KINDS:
        if any(branch.derived().can_write(pubkey, kind) for branch in branches):
            return _accept("passthrough", reason="strict_targetable_grant")
    return _reject(
        "blocked: this relay only stores hosted community content", "strict_passthrough"
    )


def _passthrough(event, state, config):
    if config.strict:
        return _strict_passthrough(event, state, config)
    return _accept("passthrough", reason="passthrough")


def evaluate(event, state, config):
    kind = event.get("kind")
    tags = event.get("tags") or []
    pubkey = event.get("pubkey") or ""

    if kind == P.COMMUNITY_DEFINITION_KIND:
        address = P.get_addressable_address(event)
        branch = state.branch(address) if address else None
        if branch is not None:
            try:
                P.parse_definition(event)
            except P.InvalidEvent as error:
                return _reject(f"invalid: {error}", "invalid_definition", branch.community_id)
            return _accept("authority", branch.community_id, authority=True)
        return _passthrough(event, state, config)

    if kind == P.DELETE_KIND:
        return _accept("authority", authority=True, reason="delete")

    if kind == P.PROFILE_LIST_KIND:
        if P.is_renounced_communities_list(event):
            return _accept("passthrough", reason="renunciation")
        # Attribute by the coordinate strfry will store it under (first d tag),
        # then insist on the exact shard structure so a malformed event cannot
        # replace a valid shard in storage while slipping past as passthrough.
        coordinate_branches = state.branches_for_coordinate(event)
        if coordinate_branches:
            branch = coordinate_branches[0]
            if branch.shard_ref_for(event) is None:
                return _reject(
                    "invalid: profile-list shard requires exactly one d tag matching the referenced coordinate",
                    "invalid_shard",
                    branch.community_id,
                )
            return _accept("authority", branch.community_id, authority=True)
        authority = P.parse_authority(tags)
        if authority is not None:
            branch = state.branch(authority.address)
            if branch is not None:
                return evaluate_branch(event, branch, config, authority_required=True)
        return _passthrough(event, state, config)

    if kind == P.TARGETED_PUBLICATION_KIND:
        hosted_ids = [
            tag[1]
            for tag in P.get_tags(tags, "h")
            if len(tag) > 1 and tag[1] in state.by_community_id
        ]
        if not hosted_ids:
            return _passthrough(event, state, config)
        try:
            wrapper = P.parse_wrapper(event)
        except P.InvalidEvent as error:
            return _reject(f"invalid: {error}", "invalid_wrapper", hosted_ids[0])
        outcomes = []
        for target in wrapper.targets:
            branch = state.branch(target.address)
            if branch is None:
                continue
            derived = branch.derived()
            unavailable = _availability(branch, derived)
            if unavailable:
                outcomes.append(unavailable)
            elif derived.is_banned(pubkey):
                outcomes.append(_banned(branch.community_id))
            elif derived.can_write(pubkey, wrapper.kind):
                outcomes.append(_accept("hosted", branch.community_id))
            else:
                outcomes.append(
                    _reject(
                        f"blocked: no current grant for kind {wrapper.kind} in {_short(branch.community_id)}",
                        "no_grant",
                        branch.community_id,
                    )
                )
        if not outcomes:
            # h matched a hosted community id but no target pair named a hosted branch.
            return _passthrough(event, state, config)
        for outcome in outcomes:
            if not outcome.accepted:
                return outcome
        return outcomes[0]

    h_tags = P.get_tags(tags, "h")
    hosted_ids = [tag[1] for tag in h_tags if len(tag) > 1 and tag[1] in state.by_community_id]
    if not hosted_ids:
        return _passthrough(event, state, config)
    if len(h_tags) != 1 or not P.exact_tag(h_tags[0], 2):
        return _reject(
            "invalid: community events carry exactly one two-value h tag",
            "invalid_authority_tags",
            hosted_ids[0],
        )
    community_id = hosted_ids[0]

    if P.has_marked_community_a(tags):
        authority = P.parse_authority(tags)
        if authority is None:
            return _reject(
                "invalid: marked community a is malformed or mismatched",
                "invalid_authority_tags",
                community_id,
            )
        branch = state.branch(authority.address)
        if branch is None:
            # A branch we do not host shares this community id.
            return _passthrough(event, state, config)
        return evaluate_branch(event, branch, config, authority_required=True)

    candidates = state.branches_for_community(community_id)
    outcomes = [
        evaluate_branch(event, branch, config, authority_required=False) for branch in candidates
    ]
    return _best(outcomes)
