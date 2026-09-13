"""Per-branch community state and derived permission sets.

A ``Branch`` is one exact definition address ``32222:<owner>:<communityId>``.
It tracks the current definition, the current event at every profile-list
coordinate referenced by that definition, community reports, and the
``kind:5`` events that tombstone coordinates or delete reports. Derived sets
(structural members, moderators, grants, bans) are recomputed lazily.

Every accepted authority event flows through ``Branch.apply`` at most twice:
once when the loader replays storage, and once inline when the relay accepts
a new write. ``apply`` is idempotent under the selection rule.
"""

import threading
import time

from . import protocol

# An event accepted inline is not yet in LMDB when strfry asks the plugin; a
# reconcile scan that runs in that window must not treat it as deleted.
INLINE_GRACE_SECONDS = 60.0
from .reports import AuthorityView, compute_report_state
from .selection import Coordinate


class Branch:
    def __init__(self, owner, community_id):
        self.owner = owner
        self.community_id = community_id
        self.address = protocol.make_definition_address(owner, community_id)
        self.definition_coord = Coordinate(self.address, owner)
        self.definition = None  # parsed protocol.Definition
        self.shards = {}  # address -> Coordinate
        self.reports = {}  # report id -> event
        self.report_deletes = {}  # report id -> [kind:5 events]
        self.pending_tombstones = []  # kind:5 seen before their coordinate was referenced
        self.inline_seen_at = {}  # event id -> monotonic time it was applied inline
        self.clock = time.monotonic
        self.warm = False
        self.needs_reconcile = False
        self._dirty = True
        self._derived = None
        self.lock = threading.RLock()

    # --- mutation -----------------------------------------------------------

    def apply(self, event, inline=False):
        """Feed an authority event. Returns a change label or None.

        ``inline=True`` marks events accepted from the live write path (as
        opposed to replayed from storage) so reconcile grants them a grace
        period before treating their absence from a scan as a deletion.
        """
        with self.lock:
            if inline and event.get("id"):
                self.inline_seen_at[event["id"]] = self.clock()
                if len(self.inline_seen_at) > 10000:
                    cutoff = self.clock() - INLINE_GRACE_SECONDS
                    self.inline_seen_at = {
                        k: v for k, v in self.inline_seen_at.items() if v >= cutoff
                    }
            kind = event.get("kind")
            if kind == protocol.COMMUNITY_DEFINITION_KIND:
                return self._apply_definition(event)
            if kind == protocol.PROFILE_LIST_KIND:
                return self._apply_shard(event)
            if kind == protocol.REPORT_KIND:
                return self._apply_report(event)
            if kind == protocol.DELETE_KIND:
                return self._apply_delete(event)
            return None

    def _apply_definition(self, event):
        if protocol.get_addressable_address(event) != self.address:
            return None
        try:
            parsed = protocol.parse_definition(event)
        except protocol.InvalidEvent:
            return None
        if not self.definition_coord.offer(event):
            return None
        self.definition = parsed
        self._sync_shard_coordinates()
        self._dirty = True
        return "definition_updated"

    def _sync_shard_coordinates(self):
        # Without a definition keep the loaded shards: a recreated definition
        # that references the same coordinates then has its grants at once.
        if self.definition is None:
            return
        referenced = {}
        for _, ref in self.definition.profile_list_refs():
            referenced[ref.address] = ref
        for address, ref in referenced.items():
            if address not in self.shards:
                coord = Coordinate(address, ref.owner)
                self.shards[address] = coord
                for delete in self.pending_tombstones:
                    if address in protocol.tombstone_addresses(delete):
                        coord.tombstone(delete)
                self.needs_reconcile = True
        for address in list(self.shards):
            if address not in referenced:
                del self.shards[address]

    def shard_ref_for(self, event):
        """The referenced ProfileListRef this kind:30000 event belongs to, if any."""
        if self.definition is None or event.get("kind") != protocol.PROFILE_LIST_KIND:
            return None
        address = protocol.get_addressable_address(event)
        if address is None or address not in self.shards:
            return None
        for _, ref in self.definition.profile_list_refs():
            if ref.address == address and protocol.is_valid_shard(event, ref):
                return ref
        return None

    def _apply_shard(self, event):
        ref = self.shard_ref_for(event)
        if ref is None:
            return None
        if not self.shards[ref.address].offer(event):
            return None
        self._dirty = True
        return "shard_updated"

    def _apply_report(self, event):
        authority = protocol.parse_authority(event.get("tags") or [])
        if not authority or authority.address != self.address:
            return None
        report_id = event.get("id")
        if not report_id or report_id in self.reports:
            return None
        self.reports[report_id] = event
        self._dirty = True
        return "report_added"

    def _apply_delete(self, event):
        changed = None
        deleter = event.get("pubkey")
        deleted_ids = {
            tag[1] for tag in protocol.get_tags(event.get("tags") or [], "e") if len(tag) > 1
        }
        if deleted_ids:
            # strfry removes any same-author event named by an e tag. Mirror
            # that for the definition, shards, and reports we track.
            current = self.definition_coord.current
            if current is not None and current.get("id") in deleted_ids and current.get("pubkey") == deleter:
                self.definition_coord.current = None
                self.definition = None
                self._sync_shard_coordinates()
                self._dirty = True
                changed = "definition_deleted"
            for coord in self.shards.values():
                if coord.current is not None and coord.current.get("id") in deleted_ids and coord.current.get("pubkey") == deleter:
                    coord.current = None
                    self._dirty = True
                    changed = changed or "shard_deleted"
            for report_id in list(self.reports):
                if report_id in deleted_ids and self.reports[report_id].get("pubkey") == deleter:
                    del self.reports[report_id]
                    self._dirty = True
                    changed = changed or "report_deleted"
        for address in protocol.tombstone_addresses(event):
            if address == self.address:
                if self.definition_coord.tombstone(event):
                    self.definition = None
                    self._sync_shard_coordinates()
                    changed = "definition_deleted"
                self._dirty = True
            elif address in self.shards:
                if self.shards[address].tombstone(event):
                    changed = "shard_deleted"
                self._dirty = True
            elif protocol.parse_address(address, protocol.PROFILE_LIST_KIND):
                # Might reference a coordinate a future definition will add.
                self.pending_tombstones.append(event)
                if len(self.pending_tombstones) > 1000:
                    self.pending_tombstones.pop(0)
        reference = protocol.marked_report_reference(event.get("tags") or [])
        if reference:
            if protocol.delete_matches_community(event, self.community_id, self.address):
                deletes = self.report_deletes.setdefault(reference[0], [])
                if not any(d.get("id") == event.get("id") for d in deletes):
                    deletes.append(event)
                    self._dirty = True
                    changed = changed or "report_deleted"
        return changed

    def _in_grace(self, event_id):
        seen = self.inline_seen_at.get(event_id)
        return seen is not None and self.clock() - seen < INLINE_GRACE_SECONDS

    def retain_only(self, address, present_ids):
        """Drop the current event at ``address`` if storage no longer holds it.

        Called by the loader after scanning a coordinate so that deletions the
        plugin never saw inline (``strfry delete``, imports) converge on the
        next reconcile. Events accepted inline within the grace window are
        kept because strfry may not have committed them yet.
        """
        with self.lock:
            if address == self.address:
                current = self.definition_coord.current
                if current is not None and current.get("id") not in present_ids and not self._in_grace(current.get("id")):
                    self.definition_coord.current = None
                    self.definition = None
                    self._sync_shard_coordinates()
                    self._dirty = True
                    return "definition_removed"
                return None
            coord = self.shards.get(address)
            if (
                coord is not None
                and coord.current is not None
                and coord.current.get("id") not in present_ids
                and not self._in_grace(coord.current.get("id"))
            ):
                coord.current = None
                self._dirty = True
                return "shard_removed"
            return None

    def retain_reports(self, present_ids):
        """Drop tracked reports that storage no longer holds (outside the grace window)."""
        with self.lock:
            removed = 0
            for report_id in list(self.reports):
                if report_id not in present_ids and not self._in_grace(report_id):
                    del self.reports[report_id]
                    removed += 1
            if removed:
                self._dirty = True
            return removed

    # --- derived sets -------------------------------------------------------

    def _current_shard(self, ref):
        coord = self.shards.get(ref.address)
        return coord.current if coord else None

    def _active_shard(self, ref):
        """Current shard event that is not declined, else None."""
        event = self._current_shard(ref)
        if event is None or protocol.is_profile_list_declined(event):
            return None
        return event

    def _section_moderators_raw(self, section):
        result = {self.owner}
        for ref in section.profile_lists:
            if self._active_shard(ref) is not None:
                result.add(ref.owner)
        return result

    def derived(self):
        with self.lock:
            if not self._dirty and self._derived is not None:
                return self._derived
            self._derived = self._compute()
            self._dirty = False
            return self._derived

    def _compute(self):
        if self.definition is None:
            return Derived(self, None, set(), {}, {}, set(), set(), set(), set(), set(), set())
        definition = self.definition
        structural = set()
        for _, ref in definition.profile_list_refs():
            if ref.owner != self.owner:
                structural.add(ref.owner)
        section_moderators = {}
        section_grants = {}
        for section in definition.sections:
            section_moderators[section.name_key] = self._section_moderators_raw(section)
            grants = set()
            for ref in section.profile_lists:
                grants.update(protocol.profile_list_pubkeys(self._active_shard(ref)))
            section_grants[section.name_key] = grants
        view = AuthorityView(
            definition, lambda section: section_moderators[section.name_key]
        )
        report_state = compute_report_state(view, self.reports.values(), self.report_deletes)
        return Derived(
            self,
            definition,
            structural,
            section_moderators,
            section_grants,
            view.current_moderators(),
            view.all_sections_moderators(),
            report_state.person_bans,
            report_state.censored_addresses,
            report_state.censored_event_ids,
            report_state.person_report_ids | report_state.event_report_ids,
        )


class Derived:
    """Immutable snapshot of a branch's permission sets."""

    def __init__(
        self,
        branch,
        definition,
        structural_members,
        section_moderators,
        section_grants,
        current_moderators,
        all_sections_moderators,
        person_bans,
        censored_addresses,
        censored_event_ids,
        effective_report_ids,
    ):
        self.branch = branch
        self.owner = branch.owner
        self.community_id = branch.community_id
        self.address = branch.address
        self.definition = definition
        self.structural_members = structural_members
        self.section_moderators = section_moderators
        self.section_grants = section_grants
        self.current_moderators = current_moderators
        self.all_sections_moderators = all_sections_moderators
        self.person_bans = person_bans
        self.censored_addresses = censored_addresses
        self.censored_event_ids = censored_event_ids
        self.effective_report_ids = effective_report_ids

    @property
    def available(self):
        return self.definition is not None

    def is_banned(self, pubkey):
        return pubkey != self.owner and pubkey in self.person_bans

    def is_moderator(self, pubkey, section=None):
        """Grant-capable moderator (owner or active list owner); ban-filtered like the client."""
        if pubkey == self.owner:
            return True
        if self.is_banned(pubkey):
            return False
        if section is None:
            return pubkey in self.current_moderators
        return pubkey in self.section_moderators.get(section.name_key, set())

    def is_all_sections_moderator(self, pubkey):
        if pubkey == self.owner:
            return True
        return not self.is_banned(pubkey) and pubkey in self.all_sections_moderators

    def can_write_section(self, pubkey, section):
        """Mirror of canWriteCommunitySection (section already resolved)."""
        if pubkey == self.owner:
            return True
        if self.is_banned(pubkey):
            return False
        if pubkey in self.structural_members:
            return True
        if pubkey in self.section_moderators.get(section.name_key, set()):
            return True
        return pubkey in self.section_grants.get(section.name_key, set())

    def can_write(self, pubkey, kind, subtype=None):
        if self.definition is None:
            return False
        section = self.definition.section_for(kind, subtype)
        if section is None:
            return False
        return self.can_write_section(pubkey, section)

    def has_any_role(self, pubkey):
        """Participant of any kind: owner, structural member, moderator, or grantee."""
        if pubkey == self.owner:
            return True
        if self.is_banned(pubkey):
            return False
        if pubkey in self.structural_members or pubkey in self.current_moderators:
            return True
        return any(pubkey in grants for grants in self.section_grants.values())


class CommunityState:
    """All hosted branches, indexed for fast attribution."""

    def __init__(self, addresses):
        self.branches = {}
        self.by_community_id = {}
        for address in addresses:
            parsed = protocol.parse_definition_address(address)
            if not parsed:
                raise ValueError(f"invalid branch address: {address}")
            owner, community_id = parsed
            branch = Branch(owner, community_id)
            self.branches[branch.address] = branch
            self.by_community_id.setdefault(community_id, []).append(branch)

    def branch(self, address):
        return self.branches.get(address)

    def branches_for_community(self, community_id):
        return self.by_community_id.get(community_id, [])

    def branches_for_shard(self, event):
        """Branches whose current definition references this kind:30000 coordinate."""
        return [
            branch for branch in self.branches.values() if branch.shard_ref_for(event) is not None
        ]

    def branches_for_coordinate(self, event):
        """Branches that reference the storage coordinate (first d tag) of this kind:30000."""
        address = protocol.get_addressable_address(event)
        if address is None:
            return []
        return [branch for branch in self.branches.values() if address in branch.shards]

    def apply(self, event, inline=False):
        """Offer an event to every branch it could concern. Returns [(branch, change)]."""
        changes = []
        for branch in self.branches.values():
            change = branch.apply(event, inline=inline)
            if change:
                changes.append((branch, change))
        return changes

    def all_warm(self):
        return all(branch.warm for branch in self.branches.values())
