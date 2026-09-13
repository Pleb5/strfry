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
        self.deleted_ids = {}  # author -> ids deleted or inferred refused by storage
        self.definition_coord = Coordinate(self.address, owner, self._deleted_for(owner))
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
            kind = event.get("kind")
            if kind == protocol.COMMUNITY_DEFINITION_KIND:
                change = self._apply_definition(event)
            elif kind == protocol.PROFILE_LIST_KIND:
                change = self._apply_shard(event)
            elif kind == protocol.REPORT_KIND:
                change = self._apply_report(event)
            elif kind == protocol.DELETE_KIND:
                change = self._apply_delete(event)
            else:
                change = None
            # Only an event that actually changed state earns the reconcile
            # grace; a rejected replay of a deleted event must not.
            if inline and change and event.get("id"):
                self.inline_seen_at[event["id"]] = self.clock()
                if len(self.inline_seen_at) > 10000:
                    cutoff = self.clock() - INLINE_GRACE_SECONDS
                    # Keep inline provenance for current authority events even
                    # after grace: retain_only needs it to remember refused ids.
                    current_ids = {
                        coord.current.get("id")
                        for coord in (self.definition_coord, *self.shards.values())
                        if coord.current is not None
                    }
                    self.inline_seen_at = {
                        k: v for k, v in self.inline_seen_at.items()
                        if v >= cutoff or k in current_ids
                    }
            return change

    def _deleted_for(self, author):
        return self.deleted_ids.setdefault(author, set())

    def is_deleted(self, event):
        """True for a same-author deleted id or an inline id absent after grace."""
        return event.get("id") in self.deleted_ids.get(event.get("pubkey") or "", ())

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
                coord = Coordinate(address, ref.owner, self._deleted_for(ref.owner))
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
        if not report_id or report_id in self.reports or self.is_deleted(event):
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
        if deleted_ids and deleter:
            # strfry removes any same-author event named by an e tag and keeps
            # a persistent (id, author) deletion index that refuses replays.
            # Mirror both: drop what we track now and remember the ids.
            tracked = self._deleted_for(deleter)
            before = len(tracked)
            tracked.update(deleted_ids)
            if len(tracked) != before:
                changed = "ids_deleted"
            if self.definition_coord.owner == deleter:
                for event_id in deleted_ids:
                    if self.definition_coord.delete_id(event_id):
                        self.definition = None
                        self._sync_shard_coordinates()
                        changed = "definition_deleted"
            for coord in self.shards.values():
                if coord.owner == deleter:
                    for event_id in deleted_ids:
                        if coord.delete_id(event_id):
                            changed = "shard_deleted"
            for report_id in list(self.reports):
                if report_id in deleted_ids and self.reports[report_id].get("pubkey") == deleter:
                    del self.reports[report_id]
                    changed = "report_deleted"
            self._dirty = True
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
        kept because strfry may not have committed them yet. If an inline event
        is still absent after grace, remember its id as refused by storage so
        replays cannot repeatedly restore its authority. This avoids scanning
        every kind 5 by each authority author; absence is a conservative signal,
        not proof of an author deletion. Storage-only events are just dropped.
        """
        with self.lock:
            coord = self.definition_coord if address == self.address else self.shards.get(address)
            if coord is None or coord.current is None:
                return None
            event_id = coord.current.get("id")
            if event_id in present_ids or self._in_grace(event_id):
                return None
            if event_id in self.inline_seen_at:
                coord.delete_id(event_id)
                del self.inline_seen_at[event_id]
            else:
                coord.current = None
            self._dirty = True
            if address == self.address:
                self.definition = None
                self._sync_shard_coordinates()
                return "definition_removed"
            return "shard_removed"

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
    """All hosted branches, indexed for fast attribution.

    Explicit branches come from configuration and are never removed. Auto-host
    branches are added when a valid definition names this relay and removed
    when its current definition no longer does. Mutation happens under
    ``lock``; readers iterate over snapshots so the request thread never sees
    a dict change mid-iteration.
    """

    def __init__(self, addresses):
        self.lock = threading.RLock()
        self.branches = {}
        self.by_community_id = {}
        self.auto_addresses = set()
        for address in addresses:
            if not self.add_branch(address):
                raise ValueError(f"invalid branch address: {address}")

    def add_branch(self, address, auto=False):
        """Add a hosted branch. Returns the Branch, or None for an invalid address."""
        parsed = protocol.parse_definition_address(address)
        if not parsed:
            return None
        with self.lock:
            existing = self.branches.get(address)
            if existing is not None:
                if not auto:
                    self.auto_addresses.discard(address)
                return existing
            owner, community_id = parsed
            branch = Branch(owner, community_id)
            branch.needs_reconcile = True
            branches = dict(self.branches)
            branches[branch.address] = branch
            by_id = {k: list(v) for k, v in self.by_community_id.items()}
            by_id.setdefault(community_id, []).append(branch)
            self.branches = branches
            self.by_community_id = by_id
            if auto:
                self.auto_addresses.add(address)
            return branch

    def remove_branch(self, address):
        """Remove an auto-host branch. Explicit branches are never removed."""
        with self.lock:
            if address not in self.auto_addresses or address not in self.branches:
                return False
            branch = self.branches[address]
            branches = dict(self.branches)
            del branches[address]
            by_id = {k: list(v) for k, v in self.by_community_id.items()}
            by_id[branch.community_id] = [b for b in by_id.get(branch.community_id, []) if b is not branch]
            if not by_id[branch.community_id]:
                del by_id[branch.community_id]
            self.branches = branches
            self.by_community_id = by_id
            self.auto_addresses.discard(address)
            return True

    def is_auto(self, address):
        return address in self.auto_addresses

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
