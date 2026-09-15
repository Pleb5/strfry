# Community reads: plugin-owned REQ admission

This source replaces the previous committed ReadGate design. Default off; not a
live-deployment or release claim. Previous architecture remains in Git history.
Operator instructions: [PRIVATE-READS.md](PRIVATE-READS.md).

## Contract

```text
AUTH -> C++ verifies identity -> OK true
REQ  -> plugin checks membership once
           allow -> query history and establish live subscription
           deny  -> CLOSED restricted: + disconnect
Every five seconds on active connections -> same plugin check -> continue/disconnect
```

AUTH success is not community admission. Unauthenticated requests receive an AUTH
challenge and CLOSED auth-required without disconnecting before they can respond.
Unavailable policy is an error, not a successful empty query or a membership denial.
A denial applies to the whole connection. There is no per-event community policy,
classification of mixed filters, or preflight scan of potential query results.

Membership is unchanged: the owner may bootstrap after a successful complete
initial load even without a stored definition; non-owners need an available
definition and an unbanned structural, moderator or any-section-grant role.
Personal renunciation preferences are not reader authority. The Python predicate
and client vectors remain the semantic oracle. Readership does not confer writing
rights in every section, or override client content moderation.

## Implementation boundaries

| C++ relay | Python read plugin |
| --- | --- |
| NIP-42 proof verification and authenticated keys | One configured community address |
| Bounded asynchronous REQ/plugin transport | Background bounded local database scans |
| Request and connection lifecycle tokens | Existing membership/ban derivation |
| Execute query only after allow | In-memory eligible-key cache |
| Periodic connection rechecks | Allow/deny/unavailable lookup |
| Protocol replies, cancellation and disconnect | Maximum successful policy age |

The read process has its own persistent JSONL channel and worker, separate from
write-policy execution. Its input is `{type:"read-admission", request_id, authenticated_pubkeys}`;
its response echoes `request_id` and supplies `decision:"allow"|"deny"|"unavailable"`.
Keys come from verified AUTH, never from an unverified filter author field.
No event bodies, membership rosters or branch coordinates cross this interface.

C++ keeps only connection and REQ state. Token checks prevent cancelled/replaced
requests and dead connections from being resurrected by delayed allows. Delivery
checks for already-closed connections are lifecycle checks, not membership checks.

## Consistency and failure

There are no pre-commit barriers, database-policy epochs/sequences, snapshots on
disk, commit notices, or writer/expiry ordering changes. Policy scans may overlap
database commits; an installed plugin view is a completed bounded pass, not a
transactionally fenced global snapshot. A subsequent pass converges. Speculative
write-plugin acceptance is not used to grant reads.

Defaults: one second between refresh passes, five-second aggregate scan deadline,
ten-second maximum policy age measured from the successful pass's start; five
seconds between active-connection rechecks; two-second admission queue/RPC budget.
A failed refresh does not renew age. Initial failure blocks the owner too. Old
views can be used only within the configured freshness budget. None of these
settings promises a five-second commit-to-revocation bound.

Requests and existing connections fail closed on expired decisions, malformed
responses, pipe failure or plugin unavailability. The worker does not block the
WebSocket/ingester thread. Pending work, frame size, connections, subscriptions,
inbound bytes, request rate and outbound backlog are bounded. Rechecks share work
fairly with new requests. Proxy/IP/TLS limits are still required; NIP-42 is not a
DoS-proof identity system. Authorized users can still submit expensive queries.

Closing cancels unsent application work and prevents late completion/EOSE from
representing interrupted history as complete. Bytes already handed to the network
cannot be recalled. Strict commit-synchronized zero-window revocation is explicitly
not part of this replacement.

## Independent privacy and client behavior

Kind4444 and retained kind4/1059 participant privacy are mandatory independently of
community mode. COUNT must be safely participant-scoped; shared Negentropy trees
are bypassed when results may include restricted events. Community mode disables
COUNT/Negentropy entirely. NIP-70 is a separate default-off switch.

Budabit retains ACK-confirmed AUTH, consent and signer lifecycle safeguards,
private invitation routing, isolated memory-only repositories, signed private
intent, destination guards, deletion-aware authority and diagnostics privacy.
Denied sockets are disposed; explicit retry establishes a new socket/identity
proof. Late CLOSED/disconnect callbacks cannot complete a terminated request.

Limited results remain limited results: separate authority and text filters each
need explicit unfiltered-kind coverage and a known cap before received-count
exhaustion can establish completeness. Retained content still requires current
client admission. No public discovery or applicant-specific exemptions are added.

## Capabilities, migration and evidence

The core generates `read_policy:{version:1,admission:"req",consistency:"eventual",
recheck_seconds:5}` and `limitation.auth_required:true`. The Budabit preset supplies
an operator-configured `budabit.read_control` version2 members/relay declaration;
preflight checks its completeness claims against configured restrictions and
refuses branch-list leakage. Core enforcement alone does not prove what an
arbitrary trusted plugin's policy means. The client still recognizes the older
version1 member capability and requires the generic facts with version2.

Old `readControl.enabled=true` refuses relay startup rather than silently becoming
public. Configure the replacement explicitly. No automatic config renderer or
disclosure migration is provided.

Tests: `readAdmissionTest.js`, `budabitReadAdmissionTest.js`, `dm4444Test.js`, `nip70Test.js`,
Python `test_read_admission.py` and `test_read_admission_preflight.py`, plus retained
AUTH/write/read/client regressions. The old epoch/snapshot test suites were replaced,
not relabeled as proof of the old guarantees. Native-client testing uses controlled
keys and local storage; it is not a deployed proxy/signer or container audit.

Release still requires an authorized immutable server publication/client vector
pin refresh, a verified container image and private-capable rollback, and controlled
member/outsider probes before ingress opens. Never treat disabling admission as rollback.
