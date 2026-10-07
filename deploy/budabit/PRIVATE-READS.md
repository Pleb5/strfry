# Member-only REQ admission: operator guide

Default off, implemented in source, not a claim about the recorded public live
deployment. [Architecture](READ-CONTROL-PLAN.md); [live inventory](RUNBOOK.md).

## What is private

One community governs the endpoint/database. Definitions, profiles, lists and forms
remain behind membership. The supplied preset exempts kind `1063` NIP-94/Blossom
descriptors with `readPolicy.publicKinds = "1063"`: anyone can retrieve stored or live
descriptors using explicit `kinds: [1063]` filters, without AUTH. Mixed/private and
ID-only REQs still require membership. Descriptor writes require an eligible member,
with no dedicated section grant; effective bans and last-grant revocation block them.
AUTH proves key control, not membership. Each private REQ is admitted once by a
separate Python plugin. Connections with private subscriptions are rechecked
periodically and disconnected on denial. A failed plugin never exposes private
data; public-only descriptor subscriptions remain available independently.

The owner can bootstrap a missing definition only after a successful complete load.
Ordinary members need the existing structural/moderator/any-section-grant role and
must not be banned. Writes still use signed-author rules, not general readership.
This is not encryption, prevention of copying, or retraction of previously public data.
Budabit's current client contract is relay-only protection: received events enter
shared persistent storage and ordinary application flows. Logout, account changes
and relay revocation do not purge or conceal those events. Publication, external
media/Git/Blossom/widgets and diagnostics are not subject to private-data isolation
or private-only destination guards. Do not promise downstream confidentiality.

## Choose the hosting model

| Requirement | Recommended arrangement |
| --- | --- |
| Public community content with independently controlled writes | Shared public relay with community write enforcement; independent DM restrictions still apply |
| Member-only community content | Dedicated relay instance/database, preferably community-operated or entrusted to a chosen operator |
| Several private communities on one provider's infrastructure | Separate configured instances/databases; the provider remains trusted |
| Different reader populations within one relay database | Not supported by the current private preset; multi-community read isolation is deliberately deferred |

A dedicated instance/database keeps the community read boundary aligned with the
deployment boundary. Separate URLs pointing to the same database are not isolation.
Separate instances can share a physical host, but its administrator can access
plaintext community data and host failures/resource contention can affect them all.
Protect backups and exports under the same operator trust boundary.

Self-hosting gives operational control, not a security certification. The community
or its chosen operator takes responsibility for updates, monitoring, backups,
recovery and availability. Public shared hosting still needs independent write
checks, spam controls and resource limits. See the [architecture trade-offs](READ-CONTROL-PLAN.md#design-decisions-and-trade-offs).

## Configure the replacement explicitly

Copy and edit `strfry.conf` in an operator-owned file; do not edit generated/runtime
configuration. Use the existing nested config blocks, not dotted assignments.

```conf
relay {
    auth {
        enabled = true
        serviceUrl = "wss://private.example/"
        maxAgeSeconds = 600
    }
    readPolicy {
        plugin = "/usr/local/lib/strfry/read-policy.py"
        publicKinds = "1063"
        timeoutSeconds = 2
        recheckSeconds = 5
        maxPending = 1024
        maxConnections = 4096
    }
    writePolicy {
        plugin = "/usr/local/lib/strfry/write-policy.py"
        timeoutSeconds = 5
    }
    maxFilterLimitCount = 0
    negentropy { enabled = false }
    info {
        extra = "{\"budabit\":{\"read_control\":{\"version\":2,\"mode\":\"members\",\"scope\":\"relay\",\"unfiltered_kinds\":[1,5,1984,30000,32222]}}}"
    }
}
```

Set the environment used by both plugin processes:

```sh
BUDABIT_READ_CONTROL=members
BUDABIT_BRANCHES='32222:<owner-hex-pubkey>:<64-hex-community-id>'
BUDABIT_AUTO_HOST_URL=
BUDABIT_DRY_RUN=0
BUDABIT_READ_REFRESH_SECONDS=1
BUDABIT_READ_MAX_AGE_SECONDS=10
BUDABIT_READ_SCAN_TIMEOUT_SECONDS=5
BUDABIT_READ_SCAN_MAX_BYTES=33554432
BUDABIT_READ_MAX_PUBKEYS=20000
```

Do not disable the live loader. `STRFRY_CONFIG` and `STRFRY_POLICY_DB_FILE` must
refer to the same configuration/database used by the core. The community address
is configured in Python only, not duplicated in C++. Disallow raw request/event
logging. Do not reuse public-mode NIP-11 branch lists on a private endpoint.

Remove old `readControl` blocks and snapshot-related environment variables.
`readControl.enabled=true` is a startup error in the replacement. Old snapshots
are ignored; retain/remove them only during an explicit stopped maintenance task.
Do not point an old image at a new private configuration: use the reviewed new
entrypoint and probe the actual running binary before opening ingress.

All `relay.readPolicy` settings are restart-required. Restart after changing the
read executable or environment too; the write plugin's mtime reload behavior does
not apply to the read process. Do not hot-reload incompatible AUTH, restriction or
query-limit settings while privately serving: the core fails closed on disagreement.
The generic protocol/defaults are documented in
[plugins.md](../../docs/plugins.md#read-admission-plugins).

## Expected delays and failure behavior

- The plugin refreshes its own in-memory view; REQs are cheap lookups, not scans.
- Refresh interval and view age are separate from the write loader's legacy
  300-second reconciliation. No write-plugin acceptance grants readership.
- There is no transaction fence across authority scans. Read access may reflect
  an older or transient mixed view until a later pass converges.
- Revocation delay includes observation/scan time, the next connection recheck
  and bounded scheduling/IPC. Five-second rechecks are **not** a five-second
  commit-to-revocation SLA. Slow/failing refresh eventually makes policy unavailable.
- A stopped/malformed/timed-out plugin closes affected active service rather than
  keeping old admission indefinitely. AUTH and signed-author repair writes remain
  separate from read admission.
- CLOSED restricted means authorization denial; CLOSED error means unavailable
  policy. Interrupted history is incomplete. Disconnection cannot retract network
  bytes already sent or copies retained by an admitted member.

Use edge connection/IP/TLS limits. Native admission mode also bounds connections,
pending decisions, aggregate inbound queue bytes (32 MiB), per-connection messages
(100/s) and aggregate ingress messages (5000/s), plus configured subscriptions,
frame size and outbound backlog. These are overload limits, not a DDoS guarantee.

## Verification and health

Before startup, with the actual environment and mounted config:

```sh
python3 deploy/budabit/check-read-control.py --config /path/to/strfry.conf --config-only
```

After startup, `--url http://127.0.0.1:7777/` also checks the serving NIP-11 facts.
`read-policy.py --check-policy` independently checks a fresh local policy scan.
Compose's conditional health combines storage/write/read checks and advertisement.
These are **not** proof of the running read plugin's cache or authenticated access.
There is no core-status/reader-snapshot artifact health contract anymore.

Keep ingress closed while using controlled keys to verify:

1. Anonymous private REQ: AUTH + CLOSED auth-required; no EVENT/EOSE.
2. Nonmember: AUTH OK true, then private REQ CLOSED restricted and disconnection.
3. Owner/member: complete expected retained history on appropriately scoped filters.
4. Ban/removal with an already-live subscription: eventual CLOSED/disconnection
   without sending another REQ; record actual elapsed time.
5. Grant/regrant: reconnect, AUTH and retry after refresh; no signing/reconnect loop.
6. Plugin failure: no successful new private reads; existing private reads stop within configured
   recheck/deadline behavior. Test recovery with fresh decisions.
7. COUNT/Negentropy remain disabled; independent DM participant protection still applies.
8. Anonymous/nonmember `kinds: [1063]` REQ: stored EVENT/EOSE and live descriptors
   succeed, including during read-plugin failure. Nonmember descriptor writes fail;
   eligible member writes succeed without a `1063` section. Mixed/private queries
   retain the denial behavior above.

The client invitation is `/c/<naddr>?read-access=members`; optional repeated `relay`
query parameters override naddr hints. This explicit invitation requires AUTH
consent and limits the initial definition lookup to those hints, without public
discovery/outbox fallback. Subsequent ordinary application routing is not confined
to those destinations. Signed `read-access=members` metadata neither enables relay
enforcement nor imposes client export/publication restrictions. The client does not
require the Budabit NIP-11 extension or a separate bounded private archive before
rendering normal routes or authoring; normal content-authority checks still apply.
The relay has no public definition/form read exception. An outsider may need a
grant arranged outside this endpoint before retrieving its forms or definition.

## Independent DM and NIP-70 settings

Kind4444 uses participant AUTH regardless of `readPolicy.plugin`, the optional
restricted-kind list, or its involved-key toggle. Legacy kind4 also remains
protected. With AUTH disabled/unconfigured, these kinds stay inaccessible, not public.
Enable AUTH with the actual service URL to make them usable. Signed recipient tags
govern participant access; this change does not validate or transform ciphertext.
Malformed retained events are not made public: with no valid recipient, only the
authenticated author can read them. Existing behavior treats any valid 32-byte
`p` tag as a recipient, even if a historical event has more than one; no new
single-recipient validation or rewrite of signed events is introduced here.

Kind1059 encrypted envelopes have no mandatory participant or AUTH requirement.
On a public relay they can be retrieved anonymously, including through mixed
ContextVM subscriptions (`25910`, `1059`, `21059`). Explicit additional-kind
restrictions and whole-relay admission still apply when configured.

**Upgrade from b574d2b or earlier mandatory-1059 builds:** deploy the updated binary
and remove `1059` from any explicit `relay.auth.restrictedReadKinds` list (the new
default is `"4, 4444"`). A config-only edit cannot remove the old binary's mandatory
restriction. Verify stored and live 1059 reads without AUTH after deployment.

`relay.nip70.enabled=false` is the new default. It ignores protected-tag author-auth
semantics and NIP-70 embedded-repost rejection, leaves signed events unchanged,
and omits NIP-70 from advertisement. Opt in explicitly to restore those semantics.

## Build, maintenance and rollback

Build the initialized reviewed local checkout with the allowlisted Docker context.
Record both source revision and resulting image digest; a revision label does not
prove an uncommitted checkout's contents. Native tests and Compose parsing are not
container verification. The old public `2fc1b38` image is not a private rollback.

Keep imports/restores/sweeps offline, verify retention of authority evidence, then
restart and repeat the access probes. There is no commit barrier coordinating
external database mutations. Disabling read policy reveals retained history and
must never be used as a failure-recovery shortcut. Remain stopped if no tested
private-capable replacement or rollback image is available. Publication, image
promotion and live deployment require separate authorization.
