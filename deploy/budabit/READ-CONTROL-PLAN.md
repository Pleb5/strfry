# Community reads: plugin-owned REQ admission

Status: **implemented in source, default off**. Updated 2026-09-16. This is the
current architecture/decision record, despite the retained `PLAN` filename. It
replaces the commit-synchronized ReadGate design, which remains in Git history.
It is not a live-deployment or release claim.

- Operator configuration, health and rollback: [PRIVATE-READS.md](PRIVATE-READS.md).
- Generic core/plugin wire protocol: [plugins.md](../../docs/plugins.md#read-admission-plugins).
- Local evidence and release limits: [READ-ADMISSION-VERIFICATION.md](READ-ADMISSION-VERIFICATION.md).
- Independent write policy: [WRITE-CONTROL-PLAN.md](WRITE-CONTROL-PLAN.md).

## Design decisions and trade-offs

| Decision | Reason and consequence |
| --- | --- |
| Keep client-only moderation, public reads with write enforcement, and optional member-only reads as distinct deployment levels | Read admission is not required to use Communikeys or the write plugin. An empty `readPolicy.plugin` preserves public community reads, not public DM reads. |
| Authenticate identity separately from admitting reads | Valid NIP-42 proofs succeed even for nonmembers. The read plugin alone decides eligibility; EVENT rules still use the verified event author. |
| Admit the whole relay once per REQ | No public-kind exceptions, mixed-filter ACL analysis, result buffering or per-event community policy. Simplicity costs public discovery and applicant reads on that endpoint. |
| Keep all membership semantics in Python | Reuse the existing `Branch` derivation and vectors without embedding a community roster/address or general policy framework in C++. |
| Use a separate persistent read process | Serial write-plugin work cannot block read IPC or vice versa. Both remain bounded; this does not remove shared host/database resource contention. |
| Accept eventual consistency, with periodic rechecks | Revoked readers with idle live subscriptions must lose access without another REQ, but policy observation and disconnect are not synchronized to commits. Five seconds is a default interval, not an end-to-end revocation SLA. |
| Build fresh read state from local storage, never speculative write acceptance | A write-plugin `accept` can still be rejected by storage. Completed bounded scans avoid that speculative source, but are not one global LMDB transaction. |
| Distinguish denial from unavailability and disconnect on either | No unchecked query or indefinite old allow. Clients may explicitly retry on a fresh authenticated connection; transient failure must not be presented as an empty archive. |
| Preserve independent DM privacy and default NIP-70 off | Encryption, participant reads, community eligibility and protected-event publication are separate policies, not one privacy switch. |
| Preserve client content checks and publication isolation | Relay admission grants access to retained candidates, not endorsement, current section authorship, complete history or safe external fanout. |

One exact `BUDABIT_BRANCHES` address governs each private endpoint/database. It is
an authority coordinate, not a URL, download instruction or automatic request to
host every discovered community. Auto-hosting and dry-run are prohibited in the
private preset. Every stored kind is behind admission; AUTH/control messages and
NIP-11 are not stored-event exceptions.

## Contract

```text
AUTH -> C++ verifies identity -> OK true
REQ  -> plugin checks membership once
           allow -> query history and establish live subscription
           deny  -> CLOSED restricted: + disconnect
While subscribed, periodic connection recheck -> same plugin check -> continue/disconnect
```

AUTH success is not community admission. Unauthenticated requests receive an AUTH
challenge and CLOSED auth-required without disconnecting before they can respond.
Unavailable policy is an error, not a successful empty query or a membership denial.
A denial applies to the whole connection. There is no per-event community policy,
classification of mixed filters, or preflight scan of potential query results.

Membership is unchanged: the owner may bootstrap after a successful complete
initial load even without a stored definition; non-owners need an available
definition and an unbanned structural, moderator or any-section-grant role.
Referenced list owners remain structural members while pending or declined;
effective person bans exclude non-owners. Personal renunciation preferences are
not reader authority. The Python predicate and client vectors remain the semantic
oracle. Readership does not confer writing rights in every section, or override
client content moderation.

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
Any eligible verified key suffices (up to 32 AUTH keys per connection). No event
bodies, filters, membership rosters or branch coordinates cross this interface.

C++ keeps only connection and REQ state. Token checks prevent cancelled/replaced
requests and dead connections from being resurrected by delayed allows. Delivery
checks for already-closed connections are lifecycle checks, not membership checks.
Tokens cover connection lifetime, authenticated-key state and REQ/recheck generation,
not database-policy epochs. `CLOSE` and REQ replacement invalidate pending allows;
tokens accompany queued EVENT/EOSE and live recipient batches. Closing the last
subscription resets the active recheck period, invalidating an old in-flight recheck.

## Consistency and failure

There are no pre-commit barriers, database-policy epochs/sequences, snapshots on
disk, commit notices, or writer/expiry ordering changes. Policy scans may overlap
database commits; an installed plugin view is a completed bounded pass, not a
transactionally fenced global snapshot. A subsequent pass converges. Speculative
write-plugin acceptance is not used to grant reads.

Defaults: one second between refresh passes, five-second aggregate scan deadline,
ten-second maximum policy age measured from the successful pass's start; five
seconds after a successful REQ/recheck before another connection recheck is due;
two-second admission queue/RPC budget. Rechecks do not request new client signatures.
A failed refresh does not renew age. Initial failure blocks the owner too. Old
views can be used only within the configured freshness budget. None of these
settings promises a five-second commit-to-revocation bound.

Each background pass builds a fresh `Branch` and replaces the in-memory reader set
only on complete success. Passes do not overlap, and requests never trigger scans.
The aggregate scan budget is five seconds / 32 MiB and the eligible-set ceiling is
20,000 keys by default. The one-second refresh interval starts after the preceding
pass finishes; it is not a promise to install fresh state every second. This read
loop is independent of the write loader's roughly 300-second reconciliation and
60-second speculative-acceptance grace.

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

Kind `4444` and retained kinds `4`/`1059` participant privacy are mandatory independently of
community mode. COUNT must be safely participant-scoped; shared Negentropy trees
are bypassed when results may include restricted events. Community mode disables
COUNT/Negentropy entirely. NIP-70 is a separate default-off switch.

Budabit retains ACK-confirmed AUTH, consent and signer lifecycle safeguards,
private invitation routing, isolated memory-only repositories, signed private
intent, destination guards, deletion-aware authority and diagnostics privacy.
Denied sockets are disposed; explicit retry establishes a new socket/identity
proof. Late CLOSED/disconnect callbacks cannot complete a terminated request.

Limited results remain limited results: separate authority and text filters each
need explicit unfiltered-kind coverage, a known positive cap, a below-cap received
count and successful EOSE before the client can establish bounded completeness.
Reaching either cap remains partial, not complete. Retained content still requires
current client admission. No public discovery or applicant-specific exemptions are added.

The current invitation-first join process arranges a grant through an independently
private-capable workflow, then explicitly retries. Discoverable definitions or a
minimal public descriptor, public forms, encrypted contact, and applicant-specific
visibility remain future choices, not implicit exceptions. Full signed `32222`
definitions cannot be selectively redacted without changing the signed event;
public `1069` response tags are plaintext and unsuitable for private answers.

## Capabilities, migration and evidence

At the default recheck interval, the relevant NIP-11 fields are:

```json
{
  "limitation": {"auth_required": true},
  "read_policy": {"version": 1, "admission": "req", "consistency": "eventual", "recheck_seconds": 5},
  "budabit": {"read_control": {"version": 2, "mode": "members", "scope": "relay", "unfiltered_kinds": [1, 5, 1984, 30000, 32222]}}
}
```

The core generates `read_policy` and the auth limitation, not Budabit semantics.
The operator supplies `budabit.read_control` via `relay.info.extra`. Preflight checks
its completeness claims against configured restrictions and refuses branch-list
leakage. `unfiltered_kinds` means no post-limit involved-key filtering for those
authenticated queries, not unlimited results or exemptions from membership.
The core also advertises its normal limits, including `limitation.max_limit`.

The client recognizes version 1 members/relay claims for compatibility. Version 2
additionally requires generic version 1 `req`/`eventual` capability and an integer
`recheck_seconds` in 1–300. Both need `auth_required: true`; neither a generic auth
requirement nor NIP-11 alone proves membership enforcement by a trusted operator.

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

## Implementation map

Paths are relative to this repository's root. Within a row, bare filenames share
the first file's directory:

| Responsibility | Source |
| --- | --- |
| Generic connection/REQ lifecycle and bounded worker | `src/apps/relay/ReadAdmission.h` |
| Persistent read child and JSONL deadlines | `src/apps/relay/ReadPolicyProcess.h` |
| AUTH/REQ intake, history, live subscriptions and final output | `src/apps/relay/RelayIngester.cpp`, `RelayReqWorker.cpp`, `RelayReqMonitor.cpp`, `RelayWebsocket.cpp` |
| Subscription/recipient tokens | `src/Subscription.h`, `src/ActiveMonitors.h` |
| Independent participant filtering/count safety | `src/ReadRestrictor.h`, `src/apps/relay/RelayNegentropy.cpp` |
| Python read entrypoint and cache | `deploy/budabit/read-policy.py`, `deploy/budabit/policy/budabit/read_admission.py` |
| Bounded scans and existing role predicate | `deploy/budabit/policy/budabit/read_scanner.py`, `loader.py`, `state.py` |
| Startup/config/advertisement preflight | `deploy/budabit/check-read-control.py`, `start-relay.sh`, `compose.yaml` |
