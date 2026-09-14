# Optional Budabit community read control

Status: **implemented in source and verified in isolated tests; default off, not live-deployed**. Prepared 2026-09-14. Operator instructions: [PRIVATE-READS.md](PRIVATE-READS.md).

This is the cross-repository implementation plan for the strfry relay and the
Budabit client. It extends, rather than replaces, [WRITE-CONTROL-PLAN.md](WRITE-CONTROL-PLAN.md).
The existing public relay deployment is not changed by this implementation.

Inspected source baselines:

- strfry: `17912b1` (includes committed initial-authority-load gating), with work
  on `feat/budabit-read-control` from `feat/budabit-write-control`.
- Budabit: `dbf0ca551f7e7f13a09528c19abce4b91a194ca8`, with work on
  `feat/private-community-reads` from `dev`.
- Welshman is currently workspace source under Budabit's
  `packages/welshman/packages/`, not an installed package patch to edit.

The findings below record the original design inspection. Phases 0–8 implemented
and tested the corrected contract: Python projection and reader vectors, core
final-egress gate, AUTH lifecycle, private invitation shell, signed intent,
publication/cache boundary and operator preflight. See the operator guide for
current interfaces and verification scope. The initial client is a bounded
200-event archive/plain-text view, not private parity for all public features.
Native isolated relay and mocked-browser evidence do not establish live deployment
or a built/published container. Immutable remote vector publication remains a
separately authorized release step.

## 1. Recommended product contract

Support three deployment levels:

| Level | Reads | Writes | Client moderation |
| --- | --- | --- | --- |
| Normal Nostr relay | Public | Ordinary relay policy | Required |
| Budabit write-controlled relay | Public | Current Budabit write policy | Required |
| Budabit member-read relay | NIP-42 identity plus current community membership | Current Budabit write policy | Required |

Read control is optional and defaults to **off**. It cannot be enabled without
the actual Budabit write stage enforcing policy; dry-run does not qualify.
Enabling reads must not silently turn off write enforcement, and disabling
write enforcement while reads are restricted must fail configuration validation.

**Authorize the reader, not every returned event's author.** This is not the
read-side curation discussed in the write plan's Appendix A. Authorized readers
can query the relay's stored history normally. The client still applies current
grants, person bans, event censorship, replacements, and deletion rules when
rendering. There is no new content-admission index or per-result Python call.

### First-release boundary: one private access domain per endpoint

For `members` mode, require exactly one explicit `BUDABIT_BRANCHES` address and
disable auto-hosting. All Nostr event reads on that endpoint use that branch's
reader set, including reads without `h` or `a` tags.

This deliberately does **not** implement independently private communities
sharing one endpoint/database. Use separate instances, databases, and endpoints
for unrelated private groups. A URL path pointing to the same unpartitioned
database is not isolation.

Reasons:

- A rule requiring only membership in *any auto-hosted branch* is unsafe:
  outsiders can create definitions naming the relay and make themselves members.
- `#h` is a query selector, not a security boundary. Exact-ID queries, missing
  tags, unmarked same-ID branches, multi-target wrappers, targetable originals,
  profiles, counts, and sync all make partial filtering substantially harder.
- A whole-relay gate is easy to explain, cheap to execute, and hard to bypass.

`BUDABIT_MODE=strict` is recommended for private instances, but need not be a
technical prerequisite: passthrough write mode still applies Budabit rules to
the pinned branch. In either mode, eligible readers can read **all** records in
that database, subject to existing additional restrictions. Strict writes are
not historical data isolation, nor a promise that outsiders can publish nothing;
existing admission/deletion carve-outs remain.

## 2. Exact reader eligibility

Use the same Python protocol, selection, shard, and report derivation as writes.
Add an explicitly named `can_read_community` helper rather than manufacturing a
dummy event and passing it through the write pipeline.

Given a complete, usable, committed branch snapshot:

```text
reader == branch owner                         -> allow
reader is effectively person-banned             -> deny
reader is a structural member                  -> allow
reader is an active moderator                  -> allow
reader has a current grant in any section      -> allow
otherwise                                      -> deny
```

This matches `Derived.has_any_role` in `policy/budabit/state.py`, with explicit
snapshot-readiness checks added before it. Mirror it in a client helper backed
by `community-permissions.ts` and `community-reports.ts`; export read vectors
alongside the existing write vectors.

Consequences that must be visible in the moderation UI and operator docs:

- A grant in one section permits reading the **whole private relay**, including
  history. It does not permit writing in other sections.
- A referenced non-owner list author is a structural member even while their
  moderator invitation is pending or declined. Referencing that coordinate
  therefore gives private read access immediately after the authority change
  becomes effective. Declining moderator duties does not remove membership.
- Removing a user's last role revokes reads. Removing one grant while another
  role remains does not. Person bans override non-owner roles.
- Event censorship is not a reader ban. Renunciation is a user preference, not
  an operator-authenticated membership revocation.
- New/regranted members can read retained history. No join-time cutoff or
  historical key management is introduced.
- Missing stored shards contribute no grants, as today. A successful empty scan
  and a failed/incomplete scan are different: an error must not be interpreted
  as absence, especially when it could deactivate a moderator's ban reports.
- Unavailable policy blocks reads for everyone. After a complete successful
  committed scan, the pinned owner may read even if no definition exists, to
  bootstrap the instance. This is not a bypass for failed/incomplete scans.
  AUTH and owner-signed repair writes remain possible while reads are gated.

Read-only memberships and section-private rooms require a separate protocol
design; do not smuggle them into this feature by reinterpreting existing grants.

## 3. Findings that affect implementation

Paths in this table are relative to the named repository.

| Area | Current behavior | Required treatment |
| --- | --- | --- |
| strfry `src/ReadRestrictor.h` | Restricts selected kinds, optionally to involved pubkeys | Add an independent whole-relay gate; do not enumerate every kind |
| strfry `RelayIngester.cpp` | AUTH is currently demand-driven; accepted sessions hold one pubkey and reject subsequent AUTH | Proactive private-mode challenge; current NIP-42 multi-pubkey handling; fresh connection on client account changes |
| strfry `RelayReqMonitor.cpp` | Ordinary-kind broadcast fast path bypasses `shouldSendToSubscriber` | Gate both this path and initial catch-up |
| strfry `RelayReqWorker.cpp` | Historical sends, COUNT completion, and live-monitor handoff are separate paths | Check all three and their queued output |
| strfry `RelayNegentropy.cpp` | In-memory and cached-tree paths; ongoing NEG-MSG sessions | Gate all paths or disable Negentropy for the initial private preset |
| strfry `RelayIngester.cpp` / `RelayNegentropy.cpp` | AUTH dispatches SetAuth to request workers/monitors, not Negentropy; Negentropy has a SetAuth handler | Fix identity propagation if authenticated Negentropy is enabled; clean it up on disconnect |
| strfry `PluginEventSifter.h` | Lazy-started write-only sequential JSONL exchange; 8 KiB response limit | Cannot just send spontaneous ACL messages through this protocol |
| strfry `stage.py`, `state.py`, `RelayWriter.cpp` | Python applies accepted authority before actual LMDB commit/result | Private reads must use committed, storage-confirmed state |
| strfry `loader.py` | Multiple independent scans mutate the current state incrementally | Build a complete staging snapshot, then install atomically |
| strfry `RelayWebsocket.cpp` | Generated NIP-11 is cached by config version; extras cannot override generated top-level keys | Generate truthful read metadata from validated mode; do not rely on hand-edited extras |
| Budabit `relay-policy.ts` | `relay.budabit.club` overrides auth to `none`; old auth relay overrides to `required` | Preserve traffic limits, but let current metadata/runtime evidence resolve read auth |
| Budabit `util/policies.ts` | Guest signer; local in-flight flag; identity bookkeeping after `doAuth` | No guest auth for membership reads; ACK-confirmed, generation-bound identity |
| Welshman `net/src/auth.ts` | `doAuth` resolves after signing/sending, not positive ACK; stale successful signatures are challenge-checked, but failure completion lacks the equivalent guard | One central generation-safe attempt/result contract |
| Welshman `net/src/policy.ts` | Auth buffer can replay EVENT and REQ traffic and suppress EOSE/rejections while auth is pending | Replace implicit buffer behavior with explicit read replay ownership; preserve publish outcomes |
| Budabit `community-state.ts` | Separate auth orchestration; 2 s normal auth timeout, 31 s recovery timeout | Consolidate initiation; separate human signing, ACK, and query budgets |
| Budabit community layout | Auth errors are derived primarily from the first relay and only for a logged-in pubkey | Guest/read-only login UI and per-relay access state before definition bootstrap |
| Budabit `community-state.ts` | Definition bootstrap fans out to discovery/indexer and owner outbox relays | Private invitation bootstrap must not use this fallback |
| Budabit `util/storage.ts` | Community events and authority enter shared repository/persistence | Private data needs explicit storage, account, and rendering boundaries |

`Relay*.cpp` above means `src/apps/relay/Relay*.cpp` in strfry.

### Verified findings and limits of the earlier review

Static source findings at the baselines above:

- `src/events.cpp:118–134` and `RelayIngester.cpp:282–288`: AUTH inherits the
  ephemeral cutoff; the deployment's 60 seconds can reject slow bunker approval.
  Give AUTH its own bounded freshness window, retaining future-skew checks.
- `deploy/budabit/retention.py:59–67`: only replaceable kinds are preserved;
  ordinary kinds 1984/5 can be removed. This can remove effective moderation
  evidence. Actual production history/impact has **not** been audited.
- `RelayIngester.cpp:117–187`: plugin `authed` is populated for protected NIP-70
  events, not all events on authenticated connections. It is not a general
  connection-auth signal.
- Welshman `net/src/auth.ts:94–142`: `doAuth` finishes after send, and `attemptAuth`
  uses 800 ms polls. `net/src/policy.ts:160–218` reconnect replay is a second
  replay path, including EVENT. Both paths require an explicit read-only owner.
- `RelayWriter.cpp:65–69`, `RelayReqMonitor.cpp:13–15,60–83`, and
  `RelayWebsocket.cpp:344–418`: commit, DB observation, and transport handoff run
  independently. Queueing a terminate message does not cancel earlier sends.

Phase 0 deterministic counterexamples used the real Python Loader/Branch with
controlled scanner versions and a model of the review's proposed C++ predicate.
They are **not a live-relay exploit reproduction**. They established that:

1. Incrementing the pending sequence **after** commit leaves a stale-ACL window.
2. An old grant plus a new ban retraction can produce an eligible reader that
   neither complete committed state permits; seq-before-scans alone does not
   protect the post-commit/pre-increment window.
3. Queued termination without a final-send gate permits queued private output
   after snapshot installation. A retained file is also not a plugin liveness
   signal, and sequence comparison alone does not validate process epochs.

The corrections below preserve the approved fail-closed product contract rather
than the unsafe mechanism simplifications. External DB writers are an explicit
v1 operational exclusion, not a verified capability of the present code.

## 4. Server architecture: Python derives, C++ enforces

### 4.1 Recommended process and data model

Retain Python stdlib and existing policy modules. Use atomic file snapshots for
the full reader set, with a small response-less control extension on plugin stdin:

```text
signed authority -> write evaluation -> PRE-commit read gate -> LMDB commit
                                                                |
                                      committed {epoch, seq} notice
                                                                |
                         Python fresh committed Branch projection
                                                                |
                             atomically replaced reader-snapshot file
                                                                |
                       native immutable set + synchronized final-send gate
```

Use one lifecycle-managed Python policy instance per relay, with the existing write
projection and a separate committed read projection. Sharing algorithms does
not require using speculative write state to authorize reads. Do not initially
change the existing grant-then-write semantics merely to implement reads.

Use PluginEventSifter as the sole owner of stdin/stdout. Private mode sends a
response-less `read-control-init` record with a new C++-minted epoch and known
committed sequence, followed by response-less `committed` notices. Ordinary write
responses may contain `policyRelevant:true`; C++ also conservatively invalidates
for accepted kinds 32222/30000/1984/5. Reader snapshots and status NEVER appear on
stdout, whose sequential ID-matched write responses remain unchanged. No new
negotiated protocol, public API, or network service is needed. Read-off and
unrelated plugins retain their legacy protocol.

The policy process starts at relay startup in private mode, not on the first
write. There must be bounded lifecycle/liveness handling even on an idle relay.
Reads and broadcasts consult an immutable native snapshot; they do not spawn
Python or perform synchronous IPC per REQ, event, or recipient.

Start with full reader-set snapshots, not a delta replication framework:

```text
version
epoch                    # minted by C++, changes on relay/plugin restart
seq                      # captured BEFORE every complete committed rebuild
branch_address
write_enforcement        # validates active non-dry-run pinned configuration
ready
heartbeat                # monotonic revision, not a client authorization token
eligible_pubkeys[]
```

Python writes a temporary file then atomically replaces the configured snapshot.
C++ watches changes (including atomic replacement), validates size before parse,
and swaps an immutable set. Check active epoch, exact pending sequence, pinned
branch, actual enforcement, readiness, and a short monotonic receive-time lease.
Reject future sequences as well as stale ones. The projection worker owns idle
heartbeats; a blocked/failed scan must not renew them. Re-reading an unchanged
file does not renew a lease. Old disk snapshots cannot authorize a new process.
Bounds cover scan bytes, JSON, and reader counts; exceedance fails closed, never
truncates. The roster is local-only, never public NIP-11 or metrics data.

No new network service, Redis, C++ moderation implementation, per-event ACL
index, or public policy API is needed. Independent five-minute scanners are not
an acceptable substitute for committed invalidation and bounded liveness.

### 4.2 Storage confirmation is a release gate

The write plan explicitly accepts temporary divergence after pre-commit inline
authority updates, including deleted-event replay after a restart. That can
last roughly six minutes at current defaults, or longer on loader failure.
It must not become a private-read authorization window.

Required contract:

1. Before committing a batch that may change reader eligibility, mark the read
   projection pending and suspend new private data output. Initially a short
   relay-wide barrier is simpler than predicting individual revoked readers.
2. Commit the batch. Feed actual results/current authority records into the
   committed projection. `accept`, `duplicate`, and `Written` are not by
   themselves proof that a particular version remains current after the batch.
3. Recompute from confirmed definitions, referenced shards, reports, and deletes.
   Never grant reads from a refused, replaced, deleted, or rolled-back event.
4. Install only an active-epoch exact-sequence complete snapshot, under the same
   synchronization as pre-commit invalidation and final transport sends. Revoke
   connections whose authenticated keys are no longer eligible. Queued data must
   pass the final gate, even before a queued termination has executed.
5. Write ACKs describe actual storage results and need not wait for read-policy
   activation. A committed write with failed activation still keeps reads closed;
   do not report that the event was never stored or cause a new-event retry.
   Client publication success does not imply read readiness. Log/report these
   outcomes separately. A failed transaction also rebuilds before reopening.

Serializing authority-changing batches through this barrier is acceptable for
the first release. Unrelated ordinary writes should not require a full snapshot
transfer or a full authority rescan.

For initial load/recovery, build a fresh Branch, never the speculative write
Branch. With one participating writer, capture the notified committed epoch/seq
before all scans: every intervening authority commit invalidates that sequence
BEFORE touching LMDB, so a mixed scan cannot install as current. Only notify a
sequence after its transaction has finished. Publish after all scans and referenced
lookups succeed; malformed scan output is failure, not a skipped event. This
guarded approach avoids adding a single-LMDB-transaction export command. It is
valid only with the exclusive-writer/maintenance restriction below and must pass
deterministic race tests. Seq-before-scans without pre-commit gating is unsafe.

Do not permit a background reconcile started before a live update to overwrite
newer state. Check epoch/revision/storage ordering on installation. Liveness
heartbeats must not renew confidence in an incomplete scan, failed commit feed,
or a stuck loader. Process death, protocol errors, a missing commit sequence,
or an expired lease suspend reads and trigger supervised recovery.

For v1, imports, sync/stream processes writing the same DB, restores, manual
deletes, and retention affecting policy evidence must run through a maintenance
procedure that suspends private serving and rebuilds state before reopening.
Do not claim immediate revocation for uncoordinated external DB writers. Adapt
the existing scheduled retention job to this contract, or prove that its filter
cannot remove authority evidence. Later, a durable cross-process change feed can
remove this operational restriction if actually needed.

### 4.3 Enforcement coverage and revocation

Use one native authorization predicate consistently at:

- REQ admission, historical output, EOSE completion, and handoff to live monitoring.
- Live catch-up and ordinary-kind as well as restricted-kind broadcasts.
- COUNT admission **and response completion**.
- NEG-OPEN, cached-tree and in-memory sync, every NEG-MSG, and output.
- The final WebSocket output boundary, including batched recipients and binary
  paths capable of carrying private data.

Carry a minimal data/control distinction through the output queue; do not reparse
outgoing JSON or exempt generic `sendToConn` data accidentally. Hold final-send
authorization synchronization through transport handoff, not just a preceding
check. AUTH, cancellation/error messages, and repair write ACKs remain deliverable.

Terminate revoked connections and let existing connection cleanup remove their
work and library buffers; no subscription-surgery framework is needed. While
temporarily pending, defer live DBChange processing without advancing currEventId.
Resume with a fresh DB transaction on installation. A bounded wait or queued
historical data that cannot be delivered closes affected sockets instead of
silently dropping results and later sending a misleading EOSE. NewSub handoff
is also gated. Timeout never permits output while policy remains unavailable.
Idle process failure/lease expiry affects existing subscriptions too.

Define the guarantee precisely: after the new policy is activated at the final
send boundary, no new private data is handed to the transport for a revoked
reader. Data already handed to the transport or delivered before that boundary
cannot reliably be recalled. Measure commit-to-activation latency; the barrier
must prevent a stale-ACL send in the intervening interval.

Existing kind/involved-pubkey restrictions compose with the new gate rather
than being overridden. An authenticated member does not automatically gain
access to everybody's restricted DMs. COUNT/Negentropy must not bypass those
additional restrictions via broad filters or cached trees.

**Initial private preset:** leave COUNT disabled (as in the deployment template)
and disable Negentropy. Cover their refusal paths in tests. Enable either only
after its complete authorization and revocation paths pass the same adversarial
tests; public/read-off behavior stays unchanged. This reduces the first release's
surface without leaving an alternate read API open.

## 5. NIP-42 wire behavior

Authentication proves control of a key; authorization decides whether that key
can read. Do not turn a membership denial into a failed signature flow.

1. In private mode, send a fresh connection-bound AUTH challenge promptly after
   WebSocket connection. Also support demand-driven challenges for clients that
   send REQ immediately. Repeated pre-auth requests reuse the current challenge.
2. Validate kind 22242, event ID/signature, challenge, relay URL, and freshness.
   Bind to the configured canonical endpoint. For path-based URLs, test path
   distinction rather than inheriting host-only matching inadvertently.
3. A valid AUTH receives `OK true` and records the authenticated key, even if
   that key currently has no membership. The subsequent read receives
   `restricted:` when unauthorized.
4. Never store or broadcast kind 22242, including one submitted via EVENT.
5. A rejected REQ stays closed. After auth, the client must issue a new REQ; the
   server does not silently revive it.

Examples (human-readable wording can evolve; prefixes remain stable):

```json
["CLOSED", "sub", "auth-required: community membership authentication required"]
["OK", "auth-event-id", true, ""]
["CLOSED", "sub", "restricted: no current community read access"]
["CLOSED", "sub", "error: community read policy temporarily unavailable"]
["NEG-ERR", "sync", "restricted: no current community read access"]
```

Do not expose the roster, report details, or a distinction between “never a
member” and “banned” to denied readers. More specific reasons belong in private
operator diagnostics. A client can explain a ban only when it has appropriate
verified evidence, not by guessing from a generic denial.

The current NIP-42 text requires relays to retain multiple successfully
authenticated pubkeys on one connection. Replace the single-key assumption
rather than relying on `already authenticated`. Bound/rate-limit AUTH work as
with other client operations. Read authorization evaluates the authenticated
identities against current policy, with existing involved-key restrictions
composed consistently. NIP-70 must match the event author against authenticated
identities, not whichever key was most recently recorded.

Budabit itself should still use a fresh private socket on account switch/logout:
there is no universal NIP-42 “forget the old identity” message. A server capable
of multi-key auth makes fresh sockets more important, not less.

Use a dedicated AUTH freshness window suitable for human signing, rather than
blindly inheriting the deployment's 60-second ordinary-ephemeral cutoff. A
bounded window within NIP-42's approximately ten-minute guidance is reasonable;
measure with bunker tests. Reusing a challenge does not allow stale timestamps,
and reconnecting invalidates prior connection proofs.

In private mode require connection AUTH before EVENT processing and any early
duplicate/storage-dependent reply, not only REQ. After AUTH, writes remain based
on signed event authors and existing rules, not a general reader-membership
precondition; keep owner repair and admission carve-outs. NIP-70 still requires
author control. AUTH/control cleanup is available while the read gate is closed.
Do not add a public inventory/status endpoint.

## 6. Operator interface and safe defaults

Proposed high-level setting:

```dotenv
# Existing settings, pinned for a dedicated private instance:
BUDABIT_BRANCHES=32222:<owner>:<communityId>
BUDABIT_AUTO_HOST_URL=
BUDABIT_MODE=strict
BUDABIT_DRY_RUN=0

# New switch; default is off:
BUDABIT_READ_CONTROL=members
```

The enforcing switch is `relay.readControl.enabled` in C++, default false.
Require `relay.auth.enabled=true`, canonical `serviceUrl`, pinned branch and
snapshot path, and a compatible enforcing plugin. A preflight CHECK script
validates core/env agreement; it does not render or rewrite source config.
Missing, wrong-epoch, stale, or unavailable snapshots cannot open the C++ gate.
An environment-only Python switch must never be advertised as restricting reads.

Preflight rejects:

- Missing/incorrect/incompatible Budabit plugin, disabled write stage, or dry-run.
- No explicit branch, multiple explicit branches, or enabled auto-hosting.
- Missing/invalid external URL or disabled NIP-42.
- Unsupported private-mode COUNT/Negentropy configuration.
- A configured policy mode that does not match the core gate/metadata.

Boot with missing policy data may remain up for owner repair writes, but reads
stay closed and private readiness is unhealthy. Startup status must distinguish
this from invalid configuration and from “ready, but this user is denied.”

Mode changes use a controlled restart for v1. Do not attempt seamless hot
public-to-private transition while old sockets keep subscriptions. Turning read
control off is explicitly a **public disclosure operation** for retained data,
not the default outage recovery action. Keep failure recovery fail-closed.

Generate NIP-11 capability metadata, for example:

```json
{
  "supported_nips": [1, 9, 11, 42],
  "limitation": {"restricted_writes": true, "auth_required": true},
  "budabit": {
    "read_control": {
      "version": 1,
      "mode": "members",
      "scope": "relay",
      "membership": "any-role",
      "auth_required": true
    }
  }
}
```

This is an abbreviated example: preserve other accurate supported NIPs and
limits. It is a relay claim, not signed community authority or cryptographic
proof of privacy. Do not expose member lists. The public shell need not reveal
the branch address; remove/redact existing enforcement-branch advertising in
private mode unless the operator deliberately chooses to publish it.

NIP-11's standard `limitation.auth_required` means authentication before other
actions. Private mode's AUTH precondition for reads AND writes makes true accurate.
The `budabit.read_control` block is informational, not an alternative enforcement
switch. Generic NIP-42 clients work without knowing the extension. Neither an
extension's mere presence nor global auth_required alone proves member-only reads
on a third-party relay: private publication must validate the claimed members mode.

Health/status must inspect the **running relay's installed** policy epoch,
revision, readiness, last successful update, and supervisor health. Running a
second Python scanner successfully is not proof the serving process is current.
Expose diagnostics locally/protect them at the proxy; keep metrics and raw
debugging endpoints out of the public private-relay surface.

## 7. Client transport: one auth owner, explicit read outcomes

### 7.1 One auth state and one access state

Reuse Welshman's pool/socket/scheduler. Centralize generation-safe auth mechanics
in `packages/welshman/packages/net/src/auth.ts`; keep Budabit relay selection,
consent, signer choice, timeout budgets, and membership UX in the app.

Use a small shared coordinator rather than independent auth initiators in
`util/policies.ts`, `community-state.ts`, and route components. Existing callers
become adapters to it. Keep provider-operation sockets and signer-relay traffic
within their explicitly different scopes.

Use two small feature states; reuse existing socket state and authority
completeness rather than building two more parallel state machines:

```text
authentication: idle / consent-needed / signer-needed / signing / awaiting-ack /
                authenticated / declined / timed-out / failed
read access: unknown / checking / granted / denied / policy-unavailable
```

`AUTH OK` establishes only authentication. A successfully admitted private read
establishes read access for that socket generation. Rendering community content
still requires the existing authority/admission checks.

Resolve auth policy from current NIP-11 plus runtime evidence; preserve known
subscription/message limits without hard-coding a URL as permanently public.
An `auth-required:` response can override stale “no auth” capability metadata.
An unsolicited challenge alone does not prove every read requires auth and does
not justify sending the user's identity to an unrelated relay.

Consent to authenticate must be distinct from the existing “trusted relay”
setting that can permit unsigned events in `trustPolicy`. Private access never
relaxes event signature verification.

### 7.2 Required concurrency invariants

One in-flight auth attempt per `(socket instance, connection generation,
active-identity generation, challenge)`, shared by all interested reads:

1. Register the attempt before starting asynchronous signing. Every caller
   observes the same promise through the matching terminal ACK or failure.
2. Capture the selected signer and expected pubkey. Verify the signed result
   belongs to that pubkey and expected AUTH template.
3. Before sending a signature, and before applying **either success or failure**,
   confirm the socket/connection/challenge/identity generations still match.
4. Only `OK true` for that exact outstanding AUTH event ID authenticates it.
   An unrelated write ACK, previous attempt's ACK, or resolved sign promise does not.
5. Ignore duplicate challenges without another prompt. A genuinely new
   challenge supersedes old work and permits one new attempt for current intent.
6. Logout/account switch immediately gates private UI, aborts old consumers,
   cancels unsent replay/queued work, and replaces relevant sockets. Do not call
   `retryAuth` on a socket that still carries the previous identity.
7. Reconnect resets authentication. Old signatures, timers, results, and
   subscription IDs cannot affect the new connection generation.
8. One consumer aborting does not cancel another consumer's shared auth attempt.
   When no interested consumers remain, discard late results and queued reads.
9. Explicit signer rejection stays rejected until a user retries or deliberately
   changes identity. Do not reopen signer prompts from reactive subscriptions.
10. All completion paths remove listeners/timers and release scheduler resources.

### 7.3 Read replay and scheduling

- For known member-read relays, connect/challenge/authenticate before sending
  content reads. Do not hold scarce REQ slots while waiting for human approval.
- Some other NIP-42 relays issue challenges only after a REQ. After a bounded
  challenge wait, allow one narrowly scoped pending read to solicit the
  challenge; do not deadlock on “auth before REQ, challenge after REQ.” Its
  rejection remains incomplete and uses the same one-replay lifecycle. Our
  private server's proactive challenge avoids this normal-path delay.
- For unknown/optional relays, ordinary public reads still run promptly. If a
  particular read gets `auth-required:`, retain its incomplete outcome, acquire
  consent/auth, and replay that still-live idempotent read **once**.
- Give replay one owner in the transport/request lifecycle. Do not let both the
  socket buffer and an app loader independently replay the same read.
- Use fresh wire IDs where needed to prevent old EOSE/CLOSED messages completing
  a replayed logical request. Preserve chunking, priority, original filters,
  lifetime limits, and raw-history cursor/completeness semantics.
- Never replay a closed/aborted route request, completed unrelated public read,
  or buffered EVENT merely because authentication succeeded.
- Publish retry remains explicit and same-event, following
  `docs/architecture/Relay-Publish-Outcomes.md`. Audit reconnect buffering too,
  not just `socketPolicyAuthBuffer`.
- `restricted:` means access denied, not “authenticate again.”
- Policy `error:` means temporary policy failure, not “bad signer.” A bounded
  backoff/manual read retry can reuse authenticated identity without re-signing.
- A closed live subscription is rebuilt only if still needed and access is
  available; denial must not cause an automatic subscribe/auth/reconnect loop.

Structured per-relay read outcomes should preserve denial, auth cancellation,
auth timeout, policy-unavailable, disconnect, and ordinary query timeout. Do not
collapse them all into `failedRelays` without a reason. None establishes an
authoritative empty community.

### 7.4 Bunker-specific handling without a second signer stack

Separate connection/challenge, human signing, relay-ACK, and query time budgets.
The existing 2-second community auth budget cannot cover user approval. Start
with a human-signing budget on the order of 60–120 seconds, bounded by the
underlying signer request lifecycle, and a much shorter ACK timeout (for example
10 seconds). Tune against controlled bunker tests, not arbitrary repeated retries.

Show “Waiting for your signer” promptly; allow cancel and an explicit retry.
Do not count this time as an empty query or start query timeouts before sends.
If an attempt times out, invalidate its generation so a late signer answer cannot
silently unlock the view. Retry creates fresh work, not a duplicate in-flight
sign request. Cancellation may only discard a result when the signer protocol
cannot cancel remote work; do not claim the remote prompt was withdrawn.

Reuse `src/app/util/nip46.ts` receiver recovery for tab hiding, mobile signer-app
switches, resume, and reconnect. Receiver recovery is not permission to re-sign
an already outstanding AUTH event. Recover the receive path first.

Avoid a dependency cycle: the only way to reach the bunker cannot be a private
relay that itself requires the user's bunker-produced AUTH signature. Keep
NIP-46 control relays reachable independently and keep their receiver requests
out of private-community auth queues. Detect unsupported configurations and
explain them instead of spinning forever.

## 8. Bootstrap and membership workflows

A completely gated relay cannot anonymously serve its own community definition,
grant lists, admission form, or ban notice. Do not punch public kind-based holes
to make bootstrap convenient.

Recommended v1 onboarding:

1. An administrator grants the known pubkey through an existing profile list.
2. Grant publication is confirmed on the private relay(s).
3. The user receives an out-of-band invitation containing the exact community
   address and private relay URL(s), preferably as an existing naddr plus private
   navigation context. The link is a locator, not a bearer access token.
4. Opening that invitation shows the endpoint and asks for login/auth consent
   before definition discovery. Do not require a previously loaded definition
   to make this user-selected bootstrap relay eligible for auth.
5. Authenticate, request the exact definition only from the invitation scope,
   validate its exact coordinate/signature, then load authority/content from
   its allowed relay scope. A definition that expands private scope needs the
   same validation/consent policy; it cannot silently widen publication.

In this flow, no anonymous indexer/owner-outbox fallback receives the private
community coordinate. A normal unmarked link may not convey privacy early
enough; provide a clear “Open private community” entry path and preserve its
scope across reloads. Hints can constrain navigation or request consent, never
prove membership or authorize writes.

Guest users see “Sign in to access this private community.” Pubkey-only users
see “Connect a signer.” Neither uses a disposable guest key to attempt membership
auth. Preserve optional guest auth for other explicitly suitable relay workflows.

Private v1 is invite-first. Hide the ordinary public admission-form experience
when forms cannot be read by outsiders. Existing outsider-write carve-outs need
not be removed, but they do not by themselves provide a usable private admission
workflow. Later, an explicitly separate public lobby or encrypted application
channel can support self-service applications; it must not share the private
database as an anonymous read backdoor.

## 9. A private relay is not automatically a private client workflow

The server gate can ship behind its default-off switch before all product work
is finished. Do not label the client “closed private group ready” until its
publication and local-data boundaries are addressed.

### 9.1 Signed intent and relay claims

Propose a small Communikeys amendment for a top-level owner-signed tag such as
`["read-access", "members"]`. Final naming/validation belongs in the protocol
change, not an undocumented parser convention. This declares private publication
intent; relay NIP-11 declares enforcement capability. They are different claims.

Default/no tag preserves public behavior. Do not overload the proposed
`enforced-relay` write-curation marker or modify existing two-value `r` tags.
Editors must preserve the new intent and show the consequences of changing it.

For private intent, require an explicitly private relay scope before sign,
optimistic insert, publish, retry, delete, and sensitive read-for-write. Missing,
unknown, mismatched, or public destinations fail closed. Metadata claims cannot
prove an operator is honest; describe the trust boundary accurately.

Older clients may ignore the new tag and publish more broadly. The UI/runbook
must state that only privacy-aware clients are supported for private publishing.
A relay cannot enforce where a member sends another copy of an event.

### 9.2 Routing audit and safe first-release behavior

Audit at least these intentional public/hybrid behaviors from
`Budabit-Relay-Publishing-Policy.md` and their implementation call sites:

| Surface | Private-mode requirement |
| --- | --- |
| Definition/setup/admin publication | No implicit public setup/indexer target |
| Admission reviews | Disable applicant/app-relay plaintext fanout; invite-first needs no public review copies |
| Community-bound repo announcements | Do not send the private association to Git indexers/outbox/public GRASP |
| Repo permalinks and wrappers | No automatic hybrid public/private union |
| Multi-community targeting | Reject mixed private/public targets; defer cross-private-domain sharing unless explicitly designed |
| Stars, membership/watch/renunciation preferences | Avoid publishing private coordinates in public plaintext lists; use existing suitable encrypted/local storage or disable the unsafe action |
| Profile hydration | Public identity lookup may be intentional, but do not send private roster-sized filters to public services by default |
| Original-event/relay-hint recovery | No private IDs/coordinates leaked through generic fallback discovery |
| Notifications, email digests, pushes | No private previews/state sent to unapproved third-party providers |
| Widgets/extensions | Gate queries and exports by private access and explicit capabilities; disable unsupported third-party features initially |
| Zaps and other external workflows | Do not leak private content/context in requests to external providers |
| Diagnostics/logs | No event bodies, filters containing private IDs, AUTH payloads, membership lists, or signer URLs/secrets in routine exports |

Do not build private Git hosting, group encryption, and a private media system
as prerequisites to the relay gate. Instead expose a safe initial private
feature set (for example native text conversations and moderation) and disable
unsupported exporting features, with clear explanations.

Blossom files, Git HTTP objects, video/calls, previews, and external widgets are
not protected by NIP-42 on the Nostr relay. Private upload/publish UI must either
use independently access-controlled/encrypted infrastructure or refuse the
operation. Deliberately linking already-public content can be supported as an
explicit public link, not advertised as private storage. An opaque content hash
is not an access-control mechanism.

### 9.3 Cached data and account isolation

Budabit currently keeps authority and content in shared repositories and
IndexedDB. A socket gate alone must not leave private content visible through
cached widgets, notifications, search, or account switching.

For the first private client release:

- Track private provenance/scope at intake, including authority, targetable
  originals, and deletes that cannot be classified from `h` alone.
- Do not persist private events into the existing unpartitioned cache. Prefer
  memory-only private scope initially over inventing encrypted offline storage.
- Prefer a scope-owned in-memory instance of the existing Welshman `Repository`
  abstraction, passed to private loaders/selectors, rather than placing private
  candidates in the global repository and hoping every generic consumer filters
  them. This includes private authority/bootstrap events. Reuse the repository
  implementation; do not build a second event database engine.
- Gate every private view/export on active identity and private-access state,
  in addition to content admission. Generic consumers must not inherit private
  candidates merely because they share the global repository.
- On logout/account change/confirmed denial, clear private projections,
  notifications, replay buffers, and scope-owned data; invalidate pending
  persistence batches so they cannot write data after cleanup.
- Treat cleanup/provenance carefully for events also intentionally public or
  shared by another authorized scope. Do not erase unrelated user data.
- Reconnect starts access as unknown. Keep private content hidden until access
  is re-established in the strict first-release UX; public cached rendering is
  unaffected. Offline private viewing can be a later explicit policy.

These are safeguards in a cooperative client, not revocation of bytes already
downloaded. Browser users, former members, relay operators/backups, and trusted
signing infrastructure can retain plaintext. NIP-42 is not end-to-end encryption
or DRM. Switching an existing public group to private cannot retract public
history; use a new private branch/relay when that history must remain separate.

## 10. UX states and actions

Display the access shell before a definition has loaded. Do not conflate a
locked community with “not found”, “no posts”, or “not a member” inferred from
incomplete authority state.

| Situation | UI/action |
| --- | --- |
| Logged out | Private community; sign in |
| Pubkey-only login | Connect a signer |
| New endpoint needs consent | Show relay hostname/identity disclosure; authenticate or cancel |
| Bunker/extension signing | Waiting for signer; cancel; avoid duplicate prompts/toasts |
| Signed, awaiting matching ACK | Authenticating with relay |
| AUTH accepted, read check pending | Checking community access |
| Read denied | No current read access; switch account, contact admin, manually retry after grant |
| Signer rejected | Request declined; explicit retry |
| Signer/network timeout | Explain which step timed out; recover signer/connectivity, then retry |
| Policy loading/unavailable | Relay access policy unavailable; retry later, no login loop |
| One of several approved private relays fails | Per-relay degraded state; use healthy approved private relays without public fallback |
| Revoked during use | Stop live updates, hide private projections, explain access changed |
| Public/unenforced destination in private settings | Block private publication before signing; identify destination |

After an administrator grants access, an initially denied user's same authenticated socket
can retry its read; no new AUTH signature is needed unless connection identity
changed. Refetch full retained history as appropriate, not only `since=now`.
Denial need not force logout or block unrelated public/other-community usage.
A previously authorized connection terminated on revocation must reconnect and
authenticate again before retry; do not promise reuse of a terminated socket.

## 11. Implementation packages and ordering

| Phase | Deliverable | Exit condition |
| --- | --- | --- |
| 0 — Design reconciliation | Correct review simplifications against security counterexamples | Server/client documents agree on fail-closed requirements |
| 1 — Standalone hardening | Dedicated AUTH freshness and preservation of retention evidence | Build, Python and isolated relay regression tests pass |
| 2 — Shared eligibility | Python/TS read vectors, owner bootstrap/readiness tests | Cross-language semantics agree without user-preference exclusions |
| 3 — Committed projection | Fresh Branch, bounded atomic files, epoch/seq controls and worker liveness | No speculative or mixed current snapshot; strict failure handling |
| 4 — Core gate | Pre-commit and final-send synchronization, AUTH, termination, disabled COUNT/NEG, config/metadata | Raw hostile-client/fault suite and public regressions pass |
| 5 — Client lifecycle | Generation-safe matching ACK, one coordinator/replay owner, budgets and outcomes | Delayed/out-of-order/reconnect tests, no implicit EVENT replay |
| 6 — Bootstrap/UX | Invite scope, access shell, per-relay outcomes, retry after grant | Controlled browser cold-login/denial/retry checks |
| 7 — Private boundaries | Signed intent, routing guards, memory-only isolated data, safe feature subset | No private event/filter/context on unrelated paths |
| 8 — Operator release | Config preflight, health, maintenance, runbook and compatibility docs | Isolated private deployment and fail-closed rollback drill |

Execute the phases in order. The relay gate may ship default-off before all client
work, but a closed-group client claim must wait for phase 7. Use existing abstractions, not a
rewrite of the scheduler, signer, or moderation engine.

### Main source targets

**strfry**

- `deploy/budabit/policy/budabit/{state,rules,reports,loader,config,stage}.py`:
  shared derivation, committed read projection, config and status.
- `deploy/budabit/write-policy.py`, `src/PluginEventSifter.h`, and a focused new
  reader-snapshot component: file transport, response-less controls and lifecycle.
- `src/apps/relay/{AuthSession.h,RelayServer.h,RelayIngester.cpp,RelayWriter.cpp}`:
  identity lifecycle, commit activation, admission, typed messages.
- `src/ReadRestrictor.h`, `RelayReqWorker.cpp`, `RelayReqMonitor.cpp`,
  `RelayNegentropy.cpp`, `RelayWebsocket.cpp`: all read/egress surfaces.
- Config schema `src/apps/relay/golpe.yaml` and sample config; deployment compose,
  Docker/runtime packaging, preflight, health and maintenance scripts.
- `deploy/budabit/tests/`, new `test/tests/budabitReadPolicyTest.js`, and existing
  auth/COUNT/Negentropy integration tests.

**Budabit**

- `packages/welshman/packages/net/src/{auth,policy,socket,request}.ts` and focused
  net tests: generic auth lifecycle/replay mechanics.
- `src/app/core/relay-policy.ts`, `src/app/util/policies.ts`,
  `src/app/core/community-state.ts`: capability, consent, orchestration and
  structured read outcomes; extract a small coordinator rather than expanding
  the already large community-state module indefinitely.
- `src/app/util/nip46.ts` and tests: reuse receiver lifecycle/recovery.
- `src/app/core/community-{protocol,permissions,reports,membership}.ts`, vector
  exporter/tests: private intent, read eligibility and conformance.
- `src/routes/c/[community]/+layout.svelte`, community creation/settings,
  membership/admin views, relay status: access shell and disclosure warnings.
- Community/repository publication helpers, `src/app/extensions/`,
  `src/app/util/{storage,notifications,notification-sources}.ts`, and relevant
  selectors: private routing/storage/view/export boundaries.

Update architecture, moderation, Communikeys, publishing, relay-I/O, and operator
docs together at release. Preserve “public/no NIP-42” as the existing default,
not an unconditional statement about every Budabit community. The old write
plan Appendix A remains about curation, not this reader authorization feature.

## 12. Verification and acceptance tests

### 12.1 Shared semantic vectors

Cover owner, outsider, structural pending/declined invite, active moderator,
one-section grantee, multi-grant removal, last-role removal, effective ban,
moderator-protection/fixpoint interactions, ban retraction, regrant, missing and
declined shards, replacements/ties, deletions, same-ID branches with different
owners, and no valid definition. Python and TypeScript must agree.

Read readiness/commit semantics are additional relay tests, not assumptions
that a client permission helper alone can prove. Extend the pinned cross-repo
conformance workflow with reader vectors and explicit protocol versioning.

### 12.2 Raw relay integration and fault injection

- Anonymous, authenticated outsider, member, and banned key against `REQ {}`,
  omitted kinds, exact IDs, authors, `#h`, unrelated tags, OR-ed/mixed filters,
  live-only `limit:0`, historical pagination, and reused subscription IDs.
- No EVENT/count/sync result leaks before auth, after denied auth identity, or
  while policy is warming/unavailable. EOSE must not masquerade as an empty
  authorized result for a refused subscription.
- COUNT and both NEG implementations are either safely denied/disabled or pass
  the same matrix. NEG-MSG after revocation cannot use an earlier authorized
  view. Test cached trees explicitly before enabling them.
- Grant -> read allowed; ban/revoke -> live/historical connections terminate;
  regrant -> new reads after reconnect/auth. An initially denied, unrevoked
  socket can retry after grant without another signature.
- Same-batch authority changes, DB commit failure, replaced/deleted grant replay,
  report deletion, definition reference changes, preexisting missing shards,
  and startup with stored bans. No speculative state escapes.
- Plugin crash/restart/hang, malformed or oversized snapshots, old-epoch output,
  dropped commit message, failed/stale reconcile, and idle-relay lease expiry.
- Slow-client outbound buffers and live handoff races under concurrent writes;
  no newly sent private frame after the revocation activation boundary.
- Malformed/wrong-kind/wrong-relay/wrong-path/stale/future/bad-signature AUTH;
  duplicate challenges, repeated AUTH, multiple keys, reconnect replay, and
  attempts to publish kind 22242 through EVENT.
- AUTH created 120 seconds ago succeeds with maxAgeSeconds=600 and fails at 60;
  ordinary ephemeral cutoff/future-skew/expiration behavior remains tested.
- Retention never selects kinds 1984/5/32222/30000; report/retraction evidence
  survives a retention pass. Verify with controlled data, not production deletion.
- Self-created auto-host definition cannot affect eligibility; invalid config
  never silently starts a public read path.
- Maintenance/retention/restore workflow refuses private serving until rebuild.
- Read-off regression: existing writes, NIP-70, public reads, no gratuitous AUTH,
  existing COUNT/Negentropy behavior and public performance remain intact.

### 12.3 Client deterministic tests

Use delayed signer promises and controllable socket adapters, not wall-clock
sleeps or real signatures as the sole race coverage.

- Parallel community loaders and auth policy produce one prompt and one AUTH.
- ACK before/after another callback, mismatched ACK ID, signature completion
  after account switch/disconnect, and late **rejection** after a newer success.
- Duplicate/rotated challenges during signing; timeout then late ACK/signature;
  retry after denial; route abort; logout and guest-to-member transition.
- Private denied read after valid AUTH does not become a signer failure or retry loop.
- Auth-required causes exactly one still-needed read replay and zero EVENT replay;
  old EOSE/CLOSED cannot finish the new request; slots/listeners are released.
- Known public relay emits no AUTH wait; stale public metadata does not block
  reactive auth; random relay hints never trigger silent identity disclosure.
- Slow bunker approval, hidden-tab receiver recovery, reconnected signer relays,
  and the unsupported signer-transport/private-relay dependency cycle.
- Cold private invitation with no cached definition/membership; no public
  bootstrap fallback; no anonymous form expectation; manual retry after grant.
- Multiple approved relays: partial success does not become complete absence;
  public fallback is not introduced as “recovery.”
- Private cached content does not survive into another account's views, generic
  search/notifications/extensions, or delayed persistence queues.
- Table-driven destination tests for each public/hybrid publishing exception;
  fail before signing and optimistic insertion when private scope is unsafe.

Extend existing `relay-policy.test.ts`, `community-state-loading.test.ts`,
`welshman-request-patch.test.ts` (despite its historical name), and `nip46.test.ts`.
Add direct tests for the app auth policy/coordinator rather than relying only on
community-loader mocks. Keep the Welshman workspace tests as the transport layer.

### 12.4 Browser and rollout checks

After implementation, use the repository's isolated browser-verification
workflow for targeted cold/warm invitation, signer approval/rejection, account
switch, revoke/regrant, reconnect, and partial-relay flows. Use controlled keys,
relay, and bunker first. Obtain authorization for any live signing/publishing;
do not use the public production community as a security test fixture.

Measure public/read-off overhead, private query throughput, live fanout cost,
policy snapshot size/time, supervisor recovery, and authority commit-to-read
activation latency. Counters should distinguish auth failures, membership
denials, unavailable policy, forced subscription closures, and late results
discarded, without high-cardinality pubkey labels or private payload logs.

Roll out a new dedicated private relay before considering any existing-relay
conversion. Bootstrap approved authority data, verify actual installed state,
run anonymous/outsider/member probes, then distribute invitations to a small
cohort using a compatible client. Roll back to a known-good **private-capable**
image or keep serving disabled. Do not “fix” an outage by disabling read control.

## 13. Execution decisions

The user authorized the whole session plan. Phase 0 corrected unsafe mechanism
suggestions to preserve these product/security decisions:

1. **One pinned community per private endpoint**, auto-host off; whole DB private.
2. **Any current role reads everything**, including pending/declined structural
   members; effective person bans revoke non-owner access.
3. **All retained history** is readable on grant/regrant.
4. **Invite-first and no public Nostr event exceptions** on the private endpoint.
5. **COUNT and Negentropy disabled in the initial private preset**, not bypassed.
6. **Committed-state, fail-closed revocation** rather than TTL-only authorization
   or reuse of the write policy's optimistic grace period.
7. **Memory-only/private-isolated client data initially**, no private offline-view
   promise; unsupported external publishing features are disabled.
8. **AUTH before reads AND EVENT in private mode**; signed-author write rules,
   owner repair and admission exceptions remain; no general membership gate on writes.
9. **Atomic snapshot files**, not a new service or unsolicited stdout protocol.
10. **Pre-commit invalidation and final-send synchronization are mandatory**;
    terminate-on-revoke simplifies cleanup, not authorization ordering.
11. **Active epoch, exact sequence, and bounded idle liveness**; no old-file reuse
    on restart and no unguarded multi-scan installation.
12. **Dedicated AUTH age and retention fixes**, tested independently of read mode.
13. **Standards-compliant multiple authenticated keys**; fresh client sockets on
    identity change remain required. Deferral would violate current NIP-42.
14. **Explicit C++ switch and config check**, not a renderer; controlled restart
    for mode changes, and exclusive DB writer while privately serving.

Multi-tenant ACLs, public admission lobbies, read-only roles, join-time history
cutoffs, offline private caches, encrypted groups, private media/Git hosting, and
server-side content curation are deliberately separate follow-ups.

## 14. Protocol references

Checked against the current upstream texts on 2026-09-14:

- [NIP-42](https://github.com/nostr-protocol/nips/blob/master/42.md): AUTH lifecycle,
  matching ACKs, multiple authenticated pubkeys, `auth-required:`/`restricted:`,
  ephemeral authentication events, challenge and timestamp verification.
- [NIP-11](https://github.com/nostr-protocol/nips/blob/master/11.md): relay capability
  metadata, `restricted_writes`, and the scope of global `auth_required`.
- [NIP-77](https://github.com/nostr-protocol/nips/blob/master/77.md): sync exposes IDs
  and inventory information, including through ongoing sessions; it is a read
  surface even when no event body is returned.
