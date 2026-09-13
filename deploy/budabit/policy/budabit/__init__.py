"""Budabit Communikeys V2 write control for strfry.

Modules:

- ``protocol``  tag grammar, URL/address normalisation, definition, shard,
                wrapper, report and authority-tag validation
- ``selection`` replaceable-event selection and ``kind:5`` tombstones
- ``state``     per-branch community state and derived permission sets
- ``reports``   effective person bans (NIP-56 overlay, fixpoint)
- ``rules``     the decision table applied to incoming events
- ``loader``    warm-up and reconcile from the relay's own LMDB via ``strfry scan``
- ``stage``     the pipeline stage wiring the above together
- ``config``    environment parsing
"""
