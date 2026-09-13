"""Deterministic replaceable-event selection (mirrors selectCurrentAddressableEvent).

Current = greatest ``created_at``, then lexicographically lowest ``id``.
A same-author ``kind:5`` tombstone with exactly one two-value ``a`` tag for
the coordinate hides every version with ``created_at`` <= the tombstone's;
at equal timestamps deletion wins. This matches strfry's own replacement
comparator in ``src/events.cpp``.
"""


def newer(candidate, current):
    """True when ``candidate`` should replace ``current`` under the selection rule."""
    if current is None:
        return True
    if candidate["created_at"] != current["created_at"]:
        return candidate["created_at"] > current["created_at"]
    return candidate["id"] < current["id"]


class Coordinate:
    """Tracks the current event and tombstone for one addressable coordinate."""

    __slots__ = ("address", "owner", "current", "tombstone_at", "deleted_ids")

    def __init__(self, address, owner, deleted_ids=None):
        self.address = address
        self.owner = owner
        self.current = None
        self.tombstone_at = None
        # Shared, same-author set of ids deleted via observed kind 5 e tags
        # or inferred refused by storage when an inline event outlives grace.
        self.deleted_ids = deleted_ids if deleted_ids is not None else set()

    def offer(self, event):
        """Offer a validated candidate. Returns True when it became current."""
        if event.get("pubkey") != self.owner:
            return False
        if event.get("id") in self.deleted_ids:
            return False
        if self.tombstone_at is not None and event["created_at"] <= self.tombstone_at:
            return False
        if newer(event, self.current):
            self.current = event
            return True
        return False

    def delete_id(self, event_id):
        """Record a same-author event-id deletion. Returns True when the current event was removed."""
        self.deleted_ids.add(event_id)
        if self.current is not None and self.current.get("id") == event_id:
            self.current = None
            return True
        return False

    def tombstone(self, delete_event):
        """Apply a same-author tombstone. Returns True when the current event was removed."""
        if delete_event.get("pubkey") != self.owner:
            return False
        at = delete_event["created_at"]
        if self.tombstone_at is None or at > self.tombstone_at:
            self.tombstone_at = at
        if self.current is not None and self.current["created_at"] <= self.tombstone_at:
            self.current = None
            return True
        return False
