# Budabit write-control plugin for strfry — implementation plan

Status: implemented on branch `feat/budabit-write-control` through phase 5
with one exception: the client-side fetch-profile change that would consume
an owner-signed enforcement declaration (§3.7 option 2, phase 5) is
proposed in the docs but not implemented. Auto-host enforcement was deployed
on 2026-09-13 with passthrough mode and dry-run disabled; the original mandatory
week-long dry-run plan is superseded by the verified rollout in
[DEPLOYMENT-2026-09-13.md](DEPLOYMENT-2026-09-13.md). Target: `deploy/budabit/`
on the Pleb5 strfry fork (`master`, which tracks upstream `hoytech/strfry` master).

The deployed revision subsequently failed a cold-ingestion outsider test on
2026-09-14. The source readiness fix below is covered by new startup/reload tests;
it is not yet recorded as deployed. See [INCIDENT-2026-09-14.md](INCIDENT-2026-09-14.md).

**Current scope:** this document owns signed-author **write** rules. Optional
[plugin-owned read admission](READ-CONTROL-PLAN.md) is now implemented separately,
default off and not recorded as deployed. Public reads remain the public preset;
the private preset requires AUTH and enforcing write policy but does not turn
readership into a universal prerequisite for EVENT. The deployment/phases below
are historical records where dated, not proof of the current running image.

Implementation note discovered during phase 1: strfry treats every `a`
tag on a `kind:5` as an NIP-09 target and rejects the event when the
address pubkey differs from the signer. Budabit's report retractions,
admission-response deletes, and moderator-request deletes used to carry
`["a", "32222:<owner>:<communityId>", <relay|"">, "community"]` as branch
context, so a moderator or applicant could not delete anything
community-scoped on strfry, and an owner's report retraction tombstoned the
community definition. Resolution: Budabit changed the wire shape.
Community-scoped `kind:5` requests now carry only `h` plus their `e`/`k`
references (`Communikeys.md`, "Deletion Requests"; `makeCommunityDeleteTags`
/ `deleteMatchesCommunity` in `community-protocol.ts`), and readers accept
the legacy marked-`a` shape while it names their exact branch. The plugin
mirrors this (`protocol.delete_matches_community`). No relay core change
is needed or made; no legacy-shape deletes were found on the live relays.

---

## 1. Goals and non-goals

### Goals

1. Reject community-scoped writes that the Budabit client would not admit
   under the current community state: authors without a current grant for the
   relevant section, effectively person-banned authors, malformed
   authority-sensitive events, and invalid authority events.
2. Accept the authorized signed authority updates that define the policy
   (definitions, referenced profile-list shards, reports, deletions,
   admission and moderator-request workflow events) from the pubkeys entitled
   to publish them, so the relay can bootstrap and follow the community
   without operator intervention, except protected-kind deletions forbidden by
   the operator policy (§3.5).
3. Keep the relay **disposable and verifiable**: the relay executes signed
   rules, never owns them. Anyone can replay the plugin's decision against
   the signed events. If the relay disappears, Budabit's client-side
   admission loses nothing.
4. Coexist with the existing rate-limit and storage-guard policy, and with
   the public-write role of `relay.budabit.club` (non-community traffic
   continues to pass, subject to rate limits).
5. Be observable: every rejection has a stable, machine-readable NIP-01 `OK`
   prefix and reason; Prometheus-friendly counters are exposed via logs.

### Non-goals

- **Read restriction in the write plugin.** This channel does not gate
  REQ/COUNT/NEG-OPEN. Optional whole-relay membership admission uses a separate
  read process; per-event community read curation remains out of scope (Appendix A).
  Public community reads with signed-author write enforcement remain supported.
- **Being an authority.** Budabit's render-time authority check remains
  mandatory (`Budabit-Community-Moderation.md`, "The render-time authority
  check is mandatory"). The client MUST keep filtering; the plugin is a
  spam/scale optimisation.
- **Replacing NIP-29/NIP-72 semantics.** No relay-signed group state, no
  relay-owned member lists.
- **Deleting already-stored content by default.** Grants gate new writes.
  Revocation and censor sweeps are an optional, separately-scheduled tool
  (§7), off by default.
- **Repository-scoped authority (NIP-34 owner/maintainer rules).** Repo
  events belong on GRASP or the repository's own relays. Without a community
  `h` they are outside community scope (passthrough in passthrough mode,
  rejected in strict mode); with `h` they need a section that lists the
  kind. Maintainer rules are not a relay concern here.

---

## 2. Decisions

| # | Decision | Rationale |
| - | -------- | --------- |
| D1 | Python 3, standard library only, same interpreter as the existing `write-policy.py`. | Already in the Alpine image; no new toolchain. Semantic parity with the TypeScript client is guaranteed by golden test vectors exported from Budabit (§8), not by sharing code. |
| D2 | Hosted communities use exact definition addresses `32222:<owner>:<communityId>`, selected explicitly and/or discovered by valid definition `r` tags matching `BUDABIT_AUTO_HOST_URL`. | Same-ID branches remain distinct. Auto-hosting shipped in phase 5 and is the live September configuration, with an empty explicit list. |
| D3 | Default mode is **passthrough**: events not attributable to a hosted branch are handed to the existing rate/storage policy only. A **strict** mode rejects them. | `relay.budabit.club` is a public-write relay today; members also publish personal kinds (0, 3, 10002, 10050, 10063, 10000, 30078) there per the publishing policy. Strict mode serves dedicated community relays. |
| D4 | The plugin learns state from (a) inline accepted authority events and (b) a warm-up/reconcile scan of the relay's own LMDB via `strfry scan` in a background thread. | Inline observation reduces same-relay grant races but is speculative until storage accepts. Reconciliation handles restarts/imports and storage rejection (§10). Read admission never consumes this speculative state. |
| D5 | Gate all new writes until this instance completes its initial load; afterwards fail closed for unavailable hosted-branch authority while allowing unrelated passthrough traffic through later pipeline stages. | Undiscovered must not mean unhosted during startup. A transient `error:` reply tells clients to retry; dry-run does not bypass initial loading. |
| D6 | Rejections use NIP-01 machine-readable prefixes: `blocked:` (policy), `invalid:` (malformed Communikeys structure), `rate-limited:`, `error:` (transient, e.g. warming up). | Signed-author policy does not depend on NIP-42. A separately enabled private relay requires AUTH before EVENT without replacing these rules. |
| D7 | Enforcement is advertised relay-side, not (yet) in the signed definition. | The V1 `["r", url, "enforced"]` marker is invalid in V2 (`r` has exactly two values). A V2 declaration needs a Communikeys amendment (§3.7); until then NIP-11 or out-of-band. |
| D8 | Person-ban evaluation, moderator protection, shard union, replacement and deletion rules are implemented to match `community-reports.ts` / `community-permissions.ts` / `community-protocol.ts` exactly, including fixpoint iteration for bans. | Any divergence would make the relay reject what the client shows, or vice versa. Golden vectors (§8) pin this. |

---

## 3. Semantics to enforce

Everything below is derived from the current Budabit docs and code. Where
the plugin cannot fully evaluate a rule (e.g. it needs an event stored on
another relay), the fallback is stated.

### 3.1 Community state model (per hosted branch)

Maintained in memory, rebuilt from LMDB on start, updated inline:

```
Branch {
  address            = "32222:<owner>:<communityId>"
  owner              : pubkey
  communityId        : 64-hex
  definition         : current valid kind:32222 (greatest created_at, then lowest id),
                       or None if tombstoned/missing
  sections[]         : name, kinds[(kind, subtype?)], profileListRefs[address, relay?]
  shards{address}    : current valid kind:30000 at that coordinate (same selection rule),
                       honouring same-author kind:5 `a` tombstones
  reports[]          : kind:1984 with h + marked community a for this branch
  reportDeletes[]    : kind:5 by report author referencing the report id with marker "report"
  derived:
    structuralMembers  = { owner of every non-owner profileListRef anywhere in definition }
    sectionModerators  = per section: owner ∪ { ref owner : current shard exists and not declined }
    sectionGrants      = per section: ∪ valid p tags of current non-declined shards
    personBans         = effective person reports (fixpoint, see 3.4)
}
```

Validation rules for the definition follow `Communikeys.md` §"Definition
Validity" and §"Content Sections" exactly (exact arities, `d` = communityId,
one `name`, ≥1 `r`, ≥1 section, each section ≥1 `k`, unique `(kind,subtype)`
across sections, section-local tags before first `content` invalidate, unknown
tags ignored). Invalid definitions are rejected as `invalid:` and never become
current.

Shard validity: kind 30000, author = coordinate pubkey, exactly one `d`
matching the coordinate identifier of the form
`<communityId>-<section-purpose>[.<shard>]`, referenced by the current
definition. `["status","declined"]` marks a declined invitation: the list
contributes no grants and confers no moderator authority, but the owner keeps
the structural member/write role while referenced. Only valid 64-hex `p`
values contribute.

### 3.2 Attributing an event to a branch

An incoming event is **community-scoped for a hosted branch** when:

1. It has exactly one `h` tag with two values whose value equals a hosted
   `communityId`; and
2. Either it carries a marked authority tag `["a", "<32222:owner:communityId>", <relay|"">, "community"]`
   naming exactly one hosted branch whose identifier equals `h` (then that
   branch is selected), or it carries no marked community `a` and exactly one
   hosted branch has that `communityId` (then that branch is selected).

If several hosted branches share the `communityId` and the event carries no
marked `a`, the event is evaluated against **every** such branch and accepted
if any branch admits it (the client will do the same per selected branch).

`kind:30222` wrappers are attributed per target pair (`h` immediately
followed by `a` with kind 32222 and identifier equal to `h`); each pair whose
address is a hosted branch is evaluated independently, and all hosted pairs
must pass. Wrapper grammar violations (unpaired `h`/`a`, >12 pairs, missing
`d`/`k`, duplicate address) are `invalid:`.

`kind:32222` is attributed by `(pubkey, d)` = a hosted address.
`kind:30000` shards are attributed by coordinate `30000:<pubkey>:<d>` being
referenced in a hosted branch's current definition.

Everything else (no `h`, `h` not hosted, targetable originals whose `h` is a
targeting id, repo-scoped NIP-34 events, personal kinds) is **passthrough**
(D3), or `blocked: community-only relay` in strict mode with the carve-outs
in §3.6.

### 3.3 Section resolution and subtype

Each community-native event maps to exactly one `(kind, subtype)`:

| Event | Derived subtype |
| ----- | --------------- |
| `kind:11` with a `room` tag | `room` (Room-creator section) |
| `kind:11` without `room` tag | `threads` (Thread-creator section) |
| `kind:9` | `room-message` |
| any other kind | none (exact empty subtype) |

The pair is looked up in the current definition's sections (`Communikeys.md`:
exact match, empty subtype is exact, at most one section). If no section
declares the pair, the event is not a permitted community publication:
`blocked: kind <k>[/<subtype>] is not enabled in this community` (strict and
default modes alike, because Budabit will never render it under this
branch). Kinds handled by dedicated workflow rules in §3.5 are exempt from
this section lookup.

### 3.4 Writer set and person bans

For a resolved section, an author may write when (mirrors
`canWriteCommunitySection`):

```
author == owner                                            -> allow
author in personBans                                        -> deny
author in structuralMembers                                 -> allow
author owns a current non-declined shard referenced by S    -> allow
author in sectionGrants[S]                                  -> allow
else                                                        -> deny
```

`personBans` is computed as in `getEffectiveCommunityReportState`:

1. Keep only `kind:1984` events with exactly one `h` and one marked community
   `a` for this branch, reason-bearing `["p", <target>, "spam"]` and no
   reason-bearing `e`/`a` target (person report), not deleted by a same-author
   `kind:5` that carries the same `h` + marked `a`, an `e` tag
   `["e", <reportId>, "", <reporter>, "report"]`, and either no `k` tag or a
   `k` = `1984`.
2. Drop reports whose target is the owner, or a current moderator (any
   section) when the reporter is not the owner.
3. Iterate to a fixpoint: a person report counts only if its reporter is
   (a) the owner, or (b) a grant-capable moderator for **every** section, and
   (c) not itself person-banned by the current effective set. Repeat until
   the set is stable (bounded by the number of reports).

Event reports (`["e", id, "spam"]` / `["a", addr, "spam"]`) do not affect
write permission except as in §3.5 (optional censored-address rejection).

The owner is never denied by a ban in their own branch.

### 3.5 Per-kind workflow rules

Community-scoped events that carry `h` + marked `a` for a hosted branch and
are part of a Communikeys workflow are evaluated with role rules instead of,
or in addition to, the section lookup. R = role of `event.pubkey` in the
branch: `owner`, `moderator(S)` (grant-capable for section S),
`all-sections-moderator`, `structural-member`, `grantee(S)`, `banned`,
`outsider`.

| Kind | Shape requirements | Accept when | Notes |
| ---- | ------------------ | ----------- | ----- |
| 32222 | Valid definition (3.1), `(pubkey,d)` hosted | Always (owner by construction) | On accept, replace current definition if newer by selection rule; recompute derived sets. Invalid → `invalid:`. |
| 30000 (referenced shard) | Valid shard (3.1) | Always (author is the referenced coordinate owner by construction) | Update `shards`, recompute grants/moderators immediately. Includes `status=declined` responses. |
| 30000 (moderator request) | `h` + marked `a`, empty `p` list, empty content | R ≠ banned | Requester-authored; must be accepted from outsiders. Rate-limited by existing buckets. |
| 30000 `d = app/budabit/renounced-communities` | user preference list | Always (passthrough class) | Renunciations are user-owned; never gate. |
| 30000 other | — | passthrough | |
| 5 | No protected kind target | Unless any `k`/`a` tag names 32222 or 30000, or an `e` id is a currently tracked definition/shard | Protected targets reject the entire request with `blocked: Deletion of kinds 32222 and 30000 is not allowed` (`protected_kind_deletion`), even for owners and explicitly tagged unhosted coordinates. strfry still enforces same-author deletion for allowed targets. |
| 1984 event report | `["e"|"a", target, "spam"]`, `content` tag = section S, `h` + marked `a` | owner, moderator(S), **or** grantee/structural-member of the section owning `(1984, ∅)` (member content report queue) | Reporter ≠ target. Non-owner reporting owner/current moderator is still accepted (it will be ignored at render time) — reject only when reporter is banned. Rationale: member reports are review input, and the relay must not suppress evidence. |
| 1984 person report | `["p", target, "spam"]`, `h` + marked `a` | owner or all-sections-moderator | Others: `blocked: person reports require community-wide moderator authority`. Update `personBans`. |
| 5 report retraction | `h`, `["e", reportId, "", reporter, "report"]`, `k=1984`; **no** marked branch `a` | Unless mixed with a protected deletion target | Removes the report from the effective set. Legacy marked `a` is still understood in stored history, but new writes carrying it are rejected. |
| 1985 review label / room archive label | `h` (+ marked `a` where present) | section lookup for `(1985, ∅)` or moderator(any) | Budabit ignores non-authoritative labels. |
| 30168 admission form | `h` + marked `a`, `content` tag = S | owner or moderator(S) | |
| 1069 admission response | `h` + marked `a`, form `a` with marker `form` | R ≠ banned | Outsiders apply; must pass. One-active-submission rule is enforced client-side; the plugin only applies rate limits. |
| 7 admission review | `e` with marker `response`, `k=1069`, form `a`, `content` tag = S, `h` + marked `a` | owner or moderator(S) | Other `kind:7` fall through to the section rule for `(7, ∅)`. |
| 7 moderator-request decision | `e` to a 30000 request, `k=30000`, `h` + marked `a` | owner | |
| 30009 / 8 badges | `h` + marked `a` | owner or moderator(any) | Badges are endorsements; issuance is delegated. |
| 30222 wrapper | Closed grammar (3.2) | For each hosted pair: author has grant for `(k, ∅)` in that branch | Implicit-original rule (same author, `h = d`) is checked by clients, not the relay. |
| 30617 repo announcement | exactly one `h` = communityId | section rule (`Code-curator`) | Repos are directly bound; no wrapper. |
| 11 / 9 / 1111 / 7 / 1985 / 31922 / 31923 / 9041 / 1623 / 30033 with `h` = communityId | exactly one `h` | section rule (3.3, 3.4) | Targetable originals normally carry a targeting id in `h`, not the communityId; they are passthrough unless the community id is used directly. |
| stars/bookmarks (`h` + marked `a`, no community `p`) | | R ≠ banned | User preference events. |

Optional (flag `BUDABIT_REJECT_CENSORED_ADDRESSES=1`): when an effective
event report carries a reason-bearing `a` target, reject later replacements
at that exact `kind:pubkey:d` with `blocked: address is moderated in this
community`. Off by default because the client renders a placeholder and the
report can be deleted later.

### 3.6 Strict mode carve-outs

In strict mode (`BUDABIT_MODE=strict`) passthrough traffic is rejected with
`blocked: this relay only stores hosted community content`, except:

- Personal kinds `0, 3, 10000, 10002, 10050, 10063, 10317, 10019, 30008, 30078`
  and Welshman list kinds used by Budabit, when the author has any role other
  than `outsider`/`banned` in at least one hosted branch (matches "Do not
  publish before membership").
- `kind:5` from anyone, except protected-kind targets in §3.5 (deletions of own content).
- `kind:1069` / moderator-request `kind:30000` (admission must stay open).
- Targetable originals (`31922, 31923, 9041, 1623, 30033`) whose author has
  the corresponding grant in at least one hosted branch (they are published
  to community relays alongside their wrapper).

NIP-34 repository kinds get no carve-out. A `kind:30617` announcement with
`h=<communityId>` is community content and follows the section rule (the
Code-curator grant by default). Every other repository event (`30618`,
`1617–1633`, repo-scoped `1111`/`1985`) belongs on GRASP or the
repository's own relays; it is admitted only when a community deliberately
lists that kind in a content section and the author holds the grant, which
Budabit's default sections do not do. Without `h` such events are
unattributable and strict mode rejects them like any other passthrough
event.

### 3.7 Declaring enforcement (spec gap)

The V1 marker `["r", url, "enforced"]` is invalid under V2 (`r` arity is
exactly two). Two complementary declarations:

1. **NIP-11 (relay-claimed), implemented.** strfry gained
   `relay.info.extra`, a JSON object merged into the relay information
   document without overriding generated fields. `write-policy.py
   --nip11-extra` emits
   `{"budabit": {"policy_version": "1", "mode": "passthrough|strict",
   "enforced_branches": ["32222:…"], "auto_host": "wss://…"}}` for the
   current configuration. This is an operator claim, useful for discovery
   and auditing, not a trust input.
2. **Definition-level (owner-signed) declaration, proposed.** A new
   top-level definition tag that V2 readers ignore and editors preserve:

   ```json
   ["enforced-relay", "wss://relay.example"]
   ```

   Zero to 20 occurrences; each value MUST also appear as an `r` relay and be
   a normalized `wss://` URL, otherwise the tag is ignored (it never
   invalidates the definition). It states the owner's expectation that the
   relay runs this policy for this branch. A client MAY use it to prefer that
   relay for community reads and MAY, after spot-checking a sample of
   returned events against current grants, skip client-side author
   filtering for that relay's results. It MUST fall back to full client-side
   admission the moment an unadmitted event is observed. The proposal is
   recorded in `Budabit-Community-Architecture.md`; adopting it requires a
   Communikeys amendment and a client change, and is not part of this
   branch.

Until (2) is adopted, Budabit must not change its fetch profile because of
this plugin.

---

## 4. Architecture

### 4.1 Process model

strfry runs one writer thread and one plugin process (`RelayWriter.cpp`,
`PluginEventSifter.h`). Requests are strictly sequential; the plugin must
answer each within `relay.writePolicy.timeoutSeconds` (2 s in the budabit
config) or the event is rejected with `error: internal error` and the plugin
is respawned. Consequences:

- The request loop must never block on I/O. State loading runs in a
  background thread; the request thread reads a snapshot under a lock.
- Loading remains asynchronous, but **all new writes wait for a complete initial
  load of the live plugin instance**. They receive `error: relay policy is loading,
  retry shortly` without blocking on the scanner or queuing events; known
  protected deletions retain their permanent denial. Dry-run cannot bypass this
  readiness gate. Initial failures retry with bounded backoff. After initialization,
  per-branch warm-up rules apply and unrelated passthrough works normally.
- Strfry launches/replaces a persistent plugin on demand; it does not create a
  process per event. The readiness gate covers every instance. Eager launch is
  not implemented here and would not eliminate the need for a gate on respawn.
- The plugin is reloaded when the script mtime changes (single-token
  command). Deployment must therefore install atomically (`mv`), and the
  plugin must rebuild state quickly (§4.3).

### 4.2 Module layout

```
deploy/budabit/
  write-policy.py            # entrypoint: JSONL loop, pipeline, health CLI
  policy/
    __init__.py
    pipeline.py              # ordered stages; first non-accept wins
    storage.py               # existing storage guard (moved)
    ratelimit.py             # existing token buckets (moved)
    budabit/
      __init__.py
      config.py              # env parsing, branch list, mode flags
      protocol.py            # tag parsing, hex/address/URL normalisation,
                             # definition/shard/wrapper/report/authority validation
      selection.py           # replaceable selection, kind:5 tombstones
      state.py               # Branch model, derived sets, inline updates
      reports.py             # effective person bans (fixpoint), report deletes
      rules.py               # §3 decision table -> Decision(action, msg, reason_code)
      loader.py              # background warm-up + periodic reconcile via `strfry scan`
      metrics.py             # counters, structured log lines
  tests/
    test_write_policy.py     # existing (moved)
    test_retention.py        # existing (moved)
    test_protocol.py
    test_selection.py
    test_reports.py
    test_rules.py
    test_loader.py
    vectors/                 # golden vectors exported from budabit (§8)
      *.json
    test_vectors.py
```

`write-policy.py` remains a single-token command (no spaces) so strfry's
mtime reload keeps working; it adds `deploy/budabit` to `sys.path`.

### 4.3 State loading (`loader.py`)

- On start, for each configured branch, run
  `strfry --config $STRFRY_CONFIG scan '<filter>'` for:
  `{"kinds":[5],"authors":[owner],"#a":[branchAddress]}` followed by
  `{"kinds":[32222],"authors":[owner],"#d":[communityId]}`;
  for each referenced shard coordinate,
  `{"kinds":[5],"authors":[author],"#a":[shardAddress]}` followed by
  `{"kinds":[30000],"authors":[author],"#d":[identifier]}`;
  finally separate `{"kinds":[5],"#h":[communityId]}` and
  `{"kinds":[1984],"#h":[communityId]}` scans.
  There is no full kind:5 scan per authority author.
  Parse JSONL output, feed through the same `state.apply(event)` as inline
  updates. Mark branch warm.
- LMDB permits concurrent logical reads from another process in the same
  container, but strfry startup can check/write environment metadata and set up
  indexes. The DB directory remains writable even with a read-only rootfs.
  Do not test a new binary against the live DB on the assumption that `scan`
  (or a read-only bind mount) is a safe compatibility boundary; use an isolated
  restore first, as described in the deployment record.
- Reconcile every `BUDABIT_RECONCILE_SECONDS` (default 300) to pick up events
  written via `import`/`sync`, and whenever the current definition changes
  (new shard references may need loading).
- Shard coordinates not present on this relay contribute no grants
  (Communikeys: "An unresolved shard contributes no grants"). Log a warning
  once per coordinate; Budabit publishes shards to all community relays so
  this indicates a publication gap.

### 4.4 Inline updates (`state.apply`)

Called for every **accepted** authority event before the response is
written. Order of operations per request:

1. Parse and attribute (§3.2).
2. Evaluate rules against the current snapshot (§3).
3. If accepted and the event is an authority event (32222, referenced 30000,
   1984, 5), apply it to state using the selection rule. Because strfry may
   still refuse the write (`replaced: have newer event`, `deleted:`), the
   plugin also checks observed same-author deleted ids. An older event never
   displaces the current one in memory. Unknown e-id deletions can temporarily
   diverge from storage when a coordinate is empty; reconcile removes the
   inline event after grace and remembers its id (§10, Following storage).
   Only an event that changes state earns grace; replaying it does not renew it.
4. Respond.

### 4.5 Rejection messages and reason codes

| Prefix / message | Reason code (log/metric) |
| ---------------- | ------------------------ |
| `blocked: not a current writer for section "<S>" in <communityId[:8]>` | `no_grant` |
| `blocked: author is moderated in this community` | `person_banned` |
| `blocked: kind <k>[/<st>] is not enabled in this community` | `kind_not_enabled` |
| `blocked: person reports require community-wide moderator authority` | `report_authority` |
| `blocked: admission forms require section moderator authority` | `form_authority` |
| `blocked: this relay only stores hosted community content` | `strict_passthrough` |
| `invalid: <communikeys rule that failed>` | `invalid_definition`, `invalid_shard`, `invalid_wrapper`, `invalid_authority_tags` |
| `error: relay policy is loading, retry shortly` | `warming_up` |
| `rate-limited: …` (existing) | existing codes |

Log one structured line per rejection (`ts kind pubkey[:8] communityId[:8] reason_code`) and per state change (`definition_updated`, `shard_updated`, `person_ban_added/removed`). No full event bodies in logs.

### 4.6 Configuration (environment, compose)

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `BUDABIT_BRANCHES` | empty | Comma-separated exact addresses `32222:<owner>:<communityId>`. The stage is inert only when this and `BUDABIT_AUTO_HOST_URL` are both empty. |
| `BUDABIT_AUTO_HOST_URL` | empty | Discover valid definitions whose `r` tags name this relay; live value is `wss://relay.budabit.club`. |
| `BUDABIT_DRY_RUN` | `0` | Log would-be rejections without enforcing when set to `1`; not required for the approved September rollout. |
| `BUDABIT_DISABLE_LOADER` | `0` | Offline/test override only; keep the loader enabled in production. |
| `BUDABIT_MODE` | `passthrough` | `passthrough` or `strict` (§3.6). |
| `BUDABIT_REJECT_CENSORED_ADDRESSES` | `0` | §3.5 optional rule. |
| `BUDABIT_RECONCILE_SECONDS` | `300` | Background reconcile interval. |
| `BUDABIT_STRFRY_BIN` | `/usr/local/bin/strfry` | For `scan`. |
| `STRFRY_CONFIG` | `/etc/strfry.conf` | Already set in compose. |
| `BUDABIT_POLICY_VERSION` | `1` | Emitted in logs / NIP-11 extension. |

The Compose health check combines HTTP, `--check-storage`, and `--check-policy`.
Policy health rejects loader errors and unavailable explicit branches, but can
succeed with no auto-discovered branches or with missing permission lists.
Require expected exact addresses and inspect shard presence in `--status`.

In the fixed source, stage health also rejects an incomplete initial load of
that instance. A standalone `--check-policy` invocation loads its own state,
not the live ingestion process. Actual cold-write regression tests and the
ingestion `initial_load_complete` log are necessary to avoid the earlier false
readiness inference.

---

## 5. Interaction with strfry core behaviour

- **Signature and id** are verified before the plugin runs; `event.pubkey`
  is trustworthy.
- **Replacement/deletion** are applied after `accept`; the plugin's in-memory
  selection mirrors strfry's (`created_at` then lowest id on ties —
  `events.cpp` comparator). Observed deletions are remembered, but the plugin
  does not preload strfry's full deletion index; see the bounded-window
  trade-off under Following storage (§10).
- **`a`-tag deletions** (NIP-09 addressable) are supported by this strfry
  revision (`events.cpp` handles `a` for kind 5). Shard/definition tombstones
  therefore take effect in storage as well as in the plugin.
- **Marked community `a` on `kind:5`** would be an NIP-09 target to strfry
  (ownership check at ingestion, address deletion at write time, and the
  `replaceDeletion` index). Budabit therefore never puts it on a `kind:5`;
  deletes are scoped by `h` only. The relay core is unchanged.
- **`import`** bypasses the plugin. The reconcile loop re-derives state from
  storage, but imported *content* is not re-validated. The runbook already
  treats imports as trusted operations. An optional audit (§7) is separate
  from restore integrity checks and is not a prerequisite for deployment.
- **`sync`/`stream`/`router`** invoke the plugin with `sourceType`
  `Sync`/`Stream`. Same rules apply; `sourceInfo` is the peer URL, which the
  rate limiter already keys on.
- **Timeout**: plugin failure yields `error: internal error` and respawn.
  State is rebuilt from scoped LMDB scans. A respawn loses in-memory deletion
  knowledge that those scans do not cover (§10), as well as costing warm-up.
- **NIP-70 protected events:** current source defaults `relay.nip70.enabled` to
  `false`, ignoring protection semantics while preserving signed tags and ordinary
  validation. Explicit enable restores author-AUTH and embedded-repost checks in
  C++, not Python. The recorded public `2fc1b38` image instead rejected protected
  events with AUTH disabled; do not carry that historical behavior into source claims.
- **Read admission and DMs:** `read-policy.py` uses independent storage scans and
  checks authenticated reader keys, not event-author write eligibility. Core DM
  participant restrictions for `4`/`1059`/`4444` apply even when admission is off.
- **Kind 41** is treated as replaceable by this revision (runbook note);
  irrelevant to Communikeys but keep the retention test.

---

## 6. Deployment changes

- `Dockerfile`: copy `deploy/budabit/policy/` alongside `write-policy.py`;
  add `.dockerignore` allowlist entries; keep non-root and read-only rootfs.
- `compose.yaml`: add the `BUDABIT_*` variables; extend the health check with
  `--check-policy`; raise `relay.writePolicy.timeoutSeconds` to 5 in
  `strfry.conf`.
- `strfry.conf`: preserve server-specific limits; the implemented NIP-11
  extension (§3.7 option 1) uses `relay.info.extra` and is deployed in `2fc1b38`.
- `RUNBOOK.md`: new sections — configuring branches, reading rejection logs,
  what to do when a shard is missing on this relay, how to run the audit and
  optional sweep, how to roll back a **public** deployment to rate-limit-only
  (clear both `BUDABIT_BRANCHES` and `BUDABIT_AUTO_HOST_URL`, then recreate the
  container). This is not a private rollback: keep read admission and enforcing
  write policy intact or remain stopped; see [PRIVATE-READS.md](PRIVATE-READS.md).
- `README.md`: one paragraph describing the plugin's role and linking here.

Completed rollout for `relay.budabit.club`: empty `BUDABIT_BRANCHES`,
`BUDABIT_AUTO_HOST_URL=wss://relay.budabit.club`, `BUDABIT_MODE=passthrough`,
and `BUDABIT_DRY_RUN=0`. A separately built image passed tests, an isolated
full-count restore, exact-address/permission-list checks, and real NIP-11 HTTP
checks. The maintenance cutover took a stopped native checkpoint, recreated
only the relay, then verified public HTTPS, health, authority state, and timer
restoration. No mandatory week-long dry run or historical audit/sweep was
required. See the dated deployment record for checkpoints and safeguards.

---

## 7. Optional tools (separate scripts, off by default)

- `audit.py`: replay the rule set over stored events for the hosted branches
  (`strfry scan` per structural filter), report events that would be rejected
  today (revoked authors, banned persons, censored addresses). Read-only.
- `sweep.py`: given an audit report, `strfry delete --filter` the listed ids
  in batches, gated by a fresh successful backup exactly like `retention.py`.
  Documented trade-off: deleting censored events removes Budabit's
  "Moderated event" placeholder shape for that content; deleting
  revoked-author content diverges from Budabit's "regrant refetches history"
  behaviour. Recommend sweeping only person-banned authors' content, and only
  on operator request.

---

## 8. Testing and conformance

1. **Unit tests** (`unittest`, like the existing tests): protocol validation
   edge cases from `Communikeys.md` (arity, duplicate singletons, section
   ordering, shard `d` grammar, wrapper grammar, marker positions, `p`
   validity), selection/tombstone rules, ban fixpoint, every row of the §3.5
   table with allow/deny cases, strict-mode carve-outs, warm-up gating.
2. **Golden vectors from Budabit.** Add a small script in the Budabit repo
   (`scripts/export-community-policy-vectors.ts`, run with `tsx`/`vitest`)
   that builds fixture communities with the existing TS helpers
   (`parseCommunityDefinition`, `getEffectiveCommunityReportState`,
   `canWriteCommunitySection`, `canPublishCommunityEventReport`,
   `canPublishCommunityPersonReport`, `parseTargetedPublication`) and emits
   JSON `{events[], cases[{event, expectedClientAdmission}]}`. Commit the
   output under `deploy/budabit/tests/vectors/` and assert the plugin's
   decision equals the client's admission for every case. Re-export when the
   client rules change; the vector file records the budabit commit hash.
3. **Integration test** in strfry's harness: `test/tests/budabitPolicyTest.js`
   using `test/utils/relay.js` with a config whose `writePolicy.plugin` points
   at `deploy/budabit/write-policy.py` and `BUDABIT_BRANCHES` set. Scenario:
   publish definition → outsider content rejected → shard granting outsider →
   same content accepted immediately (no reconcile wait) → person ban →
   rejected again → ban deleted with kind:5 → accepted. Also: plugin restart
   mid-test rebuilds state from LMDB.
4. **Optional replay**: `audit.py` can evaluate historical content against
   current authority, but this is not a restore-integrity check or rollout
   prerequisite. Any sweep requires separate operator authorization.
5. **Cold auto-host ingestion**: `test/tests/budabitStartupTest.js` seeds an
   isolated LMDB and gates the actual ingestion scanner. A successful independent
   health check must not let the first outsider event through. Verify cold/warm
   replies and stored IDs, then force a plugin reload and scan failure and check
   recovery. No external relay, live account, or global freshness assumption.

---

## 9. Phases

| Phase | Deliverable | Status |
| ----- | ----------- | ------ |
| 0 | Refactor `write-policy.py` into `policy/pipeline.py` + `storage.py` + `ratelimit.py`; move tests. Behaviour unchanged. | Done. |
| 1 | `protocol.py`, `selection.py`, `state.py`, `loader.py` (warm-up + reconcile), `--check-policy`. Section writer rule, `kind_not_enabled`, strict/passthrough attribution. Dry-run flag. | Done. |
| 2 | `reports.py`: person-ban fixpoint, report deletes, report authority rules and workflow shapes (§3.5). | Done. |
| 3 | Budabit-side vector export (`src/app/core/community-policy-vectors.test.ts`) and `tests/test_vectors.py`; NIP-11 via `relay.info.extra`; verified enforcement rollout. | Done. Auto-host enforcement deployed 2026-09-13 (§6). |
| 4 | `audit.py`, `sweep.py`. | Done. (A strict-mode NIP-34 repo-relay carve-out was considered and rejected: repository events are not community content unless a section lists them; see §3.6.) |
| 5 | Auto-host mode (`BUDABIT_AUTO_HOST_URL`); owner-signed enforcement declaration; client fetch-profile change. | Auto-host done. Declaration proposed in docs (§3.7); client change not started. |

## 10. Risks and open questions

- **Cross-relay grant race.** A user granted on relay A publishes to relay B
  before the shard reaches B; B rejects with `no_grant`. Budabit publishes
  shards to all community relays concurrently, so the window is small; the
  client now shows relay-specific rejection details, explains the permission
  refresh/propagation step, and offers manual same-event retry. Loading and
  rate-limit outcomes are distinguished from bans and malformed events; there
  is no blind automatic retry loop. See Budabit's
  `docs/architecture/Relay-Publish-Outcomes.md`.
- **Missing shards on this relay.** A missing shard contributes no grants;
  writes relying on those grants fail closed. Policy health alone need not
  fail: inspect `--status` and missing-shard warnings. The runbook documents
  the fix (republish the shard from Budabit's admin panel).
- **Same-ID branches.** Rare, but content without a marked `a` is evaluated
  against all hosted branches with that id (§3.2). Acceptable and documented.
- **Memory.** State per branch is O(grants + reports); thousands of members
  and reports are trivial. Budabit's documented list ceiling
  (~800–3,000 `p` per shard) bounds shard size anyway.
- **Plugin reload on mtime.** Atomic install; state rebuild costs one
  warm-up window with `error:` replies for hosted content.
- **URL normalisation parity.** `protocol.normalize_url` reproduces the
  WHATWG serializer for the inputs Communikeys allows (lower-casing, default
  ports, dot segments, IPv6 compression, trailing slash) and returns None for
  anything the serializer would percent-encode. For definition `r` tags the
  client requires the input to already be canonical, so both sides reject
  the same inputs. For optional relay *hints* the client normalises instead
  of requiring canonical input, so a hint containing characters the Python
  side refuses makes the relay reject a definition the client accepts —
  fail-closed, visible via `--check-policy`, and the case golden vectors
  must pin.
- **Warm-up.** Every new write is gated until this plugin instance completes
  its full initial load; undiscovered must not mean unhosted. Afterwards,
  ordinary hosted content is rejected with `error: … loading` until its branch
  has definition, shards, *and* reports. Bootstrap authority updates can then
  proceed without waiting for a per-branch refresh. No stronger cross-relay
  freshness guarantee is introduced.
- **Following storage.** Same-author deleted ids learned from kind:5 events
  seen inline or in the scoped `#a`/`#h` scans are remembered per branch.
  There is no per-author kind:5 history scan and no complete reload of
  strfry's persistent deletion index. Reconcile drops definitions, shards,
  and reports absent from storage, allowing 60 s for inline events to commit.
  Only state-changing events earn grace; duplicate or rejected replays never
  refresh it. When reconcile drops an inline definition or shard after grace,
  it remembers that exact `(id, author)` as refused by storage, preventing
  repeat resurrection without any additional LMDB reads. Storage-only events
  are dropped without this inference. Absence is not proof of author deletion:
  an inline event removed by an operator or otherwise not retained can also
  be refused for the rest of that branch's in-memory lifetime.
  After restart, a replay whose e-only deletion is outside the scoped scans
  can temporarily restore authority for one 60 s grace plus up to one
  reconcile interval (about 6 minutes at defaults, plus scan/scheduling time),
  per id while that branch remains in memory. This assumes successful
  reconciliation; loader failures can extend the window. Restart or
  auto-unhosting discards this memory. strfry still refuses the deleted event
  itself, but content authorized during that window can be stored.
  Normal revocation by a newer kind:32222/30000 replacement is unchanged;
  existing/imported `a`-tag coordinate tombstones are also reloaded. New
  protected-kind deletions are forbidden, so use replacement to remove a grant.
  Exact-id memory does not block a different, older version of the coordinate
  that was never deleted. This residual risk was explicitly accepted for the
  September deployment, not left as an investigation or release gate.
- **Divergence from client rules over time.** Budabit's dedicated conformance
  workflow compares a fresh export against the immutable strfry revision in
  `community-policy-conformance.json`, excluding only separately validated
  provenance/timestamp metadata. It then runs the pinned relay conformance
  test against the fresh vectors. The pinned revision must be published before
  remote CI can check it out. Protected-kind deletion is an additional operator
  restriction and is covered separately by relay unit/integration tests.
- **Open: should member content reports (`1984` event reports by grantees)
  be limited to the General section's writers or any section writer?**
  Current client (`canPublishCommunityContentReport`) uses the section that
  owns `(1984, ∅)`; the plan follows the client.
- **NIP-11 placement: resolved.** `relay.info.extra` is implemented in strfry
  core and verified through the public HTTPS route. No Caddy change was needed.

---

## Appendix A — Read-side curation (explicitly out of scope)

Do not confuse **reader admission** with **content curation**. The implemented
[read plugin](READ-CONTROL-PLAN.md) decides whether any verified key may read the
whole endpoint before executing a REQ and during periodic connection rechecks.
It does not inspect filters, event authors or results. Retained content and imports
can therefore include records that the client must hide under current grants or
moderation, even on a members-only relay.

Serving only currently admissible community content would require additional
per-event policy or materialized admission state, with grant/ban invalidation and
query-completeness consequences. Neither is implemented or part of the chosen
read design. The existing core `ReadRestrictor` protects DM participants separately;
it is not a community curation engine. Client render-time checks remain mandatory,
and regrant may make retained content visible again. Optional sweeps change that
retained history and require separate operator authorization.

## Appendix B — Mapping to Budabit source

| Plan section | Budabit source of truth |
| ------------ | ----------------------- |
| 3.1 definition validity | `community-protocol.ts` `parseCommunityDefinition`, `Communikeys.md` §Definition Validity / Content Sections |
| 3.1 shard validity, declined | `community.ts` `getProfileListPubkeys`, `isProfileListDeclined`; `community-permissions.ts` `findProfileListEvent` |
| 3.1 selection/tombstones | `community-protocol.ts` `selectCurrentAddressableEvent` |
| 3.2 authority tags | `community-protocol.ts` `parseCommunityAuthority`, `makeCommunityAuthorityTags` |
| 3.2 wrappers | `community-protocol.ts` `parseTargetedPublication`; `Communikeys.md` §Targeted Publications |
| 3.3 subtype | `community-feeds.ts` `isRoomRoot`; `community-permissions.ts` `COMMUNITY_WRITE_TARGETS` |
| 3.4 writer rule | `community-permissions.ts` `canWriteCommunitySection`, `getCommunitySectionWriterPubkeys` |
| 3.4 bans | `community-reports.ts` `getEffectiveCommunityReportState`, `isCommunityReportDeleted` |
| 3.5 report authority | `community-reports.ts` `canPublishCommunityEventReport`, `canPublishCommunityPersonReport`, `canPublishCommunityContentReport`, `getAllSectionModeratorPubkeys` |
| 3.5 admission | `community-forms.ts`; `Budabit-Community-Moderation.md` §Review Events |
| 3.5 moderator requests | `community-moderator-requests.ts`, `community-admin.ts` |
| 3.5 renunciations | `community-renunciations.ts` (`app/budabit/renounced-communities`) |
| 3.6 personal kinds | `Budabit-Relay-Publishing-Policy.md` §Personal User-Data Publication |
