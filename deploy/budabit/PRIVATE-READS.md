# Member-only REQ admission: operator guide

Default off, implemented in source, not a claim about the recorded public live
deployment. [Architecture](READ-CONTROL-PLAN.md); [live inventory](RUNBOOK.md).

## What is private

One community governs the whole endpoint/database. All stored kinds remain behind
membership, including definitions, profiles, lists and forms. AUTH proves key
control, not membership. Each REQ is admitted once by a separate Python plugin.
Existing connections with subscriptions are rechecked periodically and disconnected
on denial. A configured plugin that fails never turns into public access.

The owner can bootstrap a missing definition only after a successful complete load.
Ordinary members need the existing structural/moderator/any-section-grant role and
must not be banned. Writes still use signed-author rules, not general readership.
This is not encryption, prevention of copying, or retraction of previously public data.

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

1. Anonymous REQ: AUTH + CLOSED auth-required; no EVENT/EOSE.
2. Nonmember: AUTH OK true, then REQ CLOSED restricted and disconnection.
3. Owner/member: complete expected retained history on appropriately scoped filters.
4. Ban/removal with an already-live subscription: eventual CLOSED/disconnection
   without sending another REQ; record actual elapsed time.
5. Grant/regrant: reconnect, AUTH and retry after refresh; no signing/reconnect loop.
6. Plugin failure: no successful new reads; existing reads stop within configured
   recheck/deadline behavior. Test recovery with fresh decisions.
7. COUNT/Negentropy remain disabled; independent DM participant protection still applies.

The client invitation is `/c/<naddr>?read-access=members`; optional repeated `relay`
query parameters override naddr hints. Private scopes never use public discovery,
outboxes or newly discovered relay destinations. Signed members intent, validated
capabilities and complete bounded authority intake are required for authoring.
No public forms/join workflow or private Git/media/Blossom/widget parity is implied.

## Independent DM and NIP-70 settings

Kind4444 uses participant AUTH regardless of `readPolicy.plugin`, the optional
restricted-kind list, or its involved-key toggle. Legacy kind4/1059 also remain
protected. With AUTH disabled/unconfigured, DMs stay inaccessible, not public.
Enable AUTH with the actual service URL to make them usable. Signed recipient tags
govern participant access; this change does not validate or transform ciphertext.
Malformed retained events are not made public: with no valid recipient, only the
authenticated author can read them. Existing behavior treats any valid 32-byte
`p` tag as a recipient, even if a historical event has more than one; no new
single-recipient validation or rewrite of signed events is introduced here.

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
