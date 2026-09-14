# Operating an optional private community relay

Implemented, default off; **not deployed to the public Budabit relay**. This guide
describes the source implementation, not the public deployment recorded in
[RUNBOOK.md](RUNBOOK.md). Keep the existing public endpoint public unless a
separately reviewed disclosure/migration procedure says otherwise.

## Deployment levels and scope

1. Client moderation only: no Budabit branch or auto-host configuration.
2. Public reads + relay write moderation: enforce explicit or auto-hosted branches;
   leave both read switches off.
3. Member-only reads: one explicit `32222:<owner>:<communityId>` branch per
   endpoint/database, enforcing non-dry-run writes, no auto-hosting, and both read
   switches on. All retained history is readable by eligible members. AUTH proves
   key control, not membership; author-based write admission remains separate.

The owner can bootstrap an empty relay only after a complete committed scan.
Missing/incomplete/stale policy blocks owner reads too. AUTH and signed-author
repair writes remain available while read policy is unavailable. Revoked sockets
terminate; an initially denied, still-authenticated socket can retry after a grant.

## Prepare and validate without opening public ingress

- Use a separate endpoint and DB, writable only by the relay UID (10001 in Compose).
  Disable any external sync/import/router/stream/sweep/retention jobs for this DB.
- Initialize submodules in a reviewed source checkout (`git submodule update --init`
  then `make setup-golpe`). The deployment Dockerfile now builds **that checkout**,
  not a pinned legacy remote binary with a newer Python plugin. The Docker-specific
  context allowlist excludes DBs, environment files, Git metadata and object files.
- Build and record the image ID/digest and source revision before promotion:

  ```sh
  export STRFRY_SOURCE_REVISION="$(git rev-parse HEAD)"
  export STRFRY_IMAGE="budabit/strfry:private-${STRFRY_SOURCE_REVISION}"
  docker compose -f deploy/budabit/compose.yaml build relay
  ```

  Review `git status` first. A revision label cannot prove an uncommitted checkout
  is clean. Because Git metadata is excluded, the native version inside this image
  can say `no-git-commits`; retain the image revision label and digest as provenance.
  No private-capable image tag is claimed to be published by this document.

Copy the sample config to an operator-private file (do not edit live/generated
configuration or commit secrets). Set:

```text
relay.auth.enabled = true
relay.auth.serviceUrl = "wss://private.example/path"
relay.auth.maxAgeSeconds = 600
relay.readControl.enabled = true
relay.readControl.branchAddress = "32222:<owner>:<communityId>"
relay.readControl.snapshotPath = "/var/lib/strfry/db/budabit-readers.json"
relay.readControl.maxSnapshotBytes = 2097152
relay.readControl.gateTimeoutSeconds = 3
relay.readControl.proactiveChallenge = true
relay.readControl.advertiseBranch = false
relay.maxFilterLimitCount = 0
relay.negentropy.enabled = false
```

These dotted names are documentation notation: put assignments **inside the
corresponding blocks** in `strfry.conf`, not literal dotted keys. Keep the packaged
write plugin, an absolute DB path matching `STRFRY_POLICY_DB_FILE`, and raw
event/request logging disabled. Change public descriptive metadata to avoid
misleading readers. Do not put the private roster/branch in `info.extra`, names,
logs or public metrics. The core hides branch extras unless explicitly enabled.

Set Compose environment in a private environment file or shell:

```sh
export BUDABIT_READ_CONTROL=members
export BUDABIT_BRANCHES='32222:<owner>:<communityId>'
export BUDABIT_AUTO_HOST_URL=
export BUDABIT_DRY_RUN=0
export BUDABIT_MODE=strict
export BUDABIT_READ_SNAPSHOT_PATH=/var/lib/strfry/db/budabit-readers.json
export BUDABIT_READ_MAX_PUBKEYS=20000
```

`strict` is the recommended deployment preset, not a new reader-role rule.
`BUDABIT_DISABLE_LOADER` must not be enabled. Never give a wrapper a different
environment/config than preflight. Set `STRFRY_CONFIG_FILE` to the reviewed host
config and `STRFRY_DATA_DIR` to the private DB directory. Do not confuse host paths
with the container's `/etc/strfry.conf` and `/var/lib/strfry/db` paths.

Before opening ingress, run in the **same image, mounts and environment**:

```sh
docker compose -f deploy/budabit/compose.yaml run --rm --no-deps \
  --entrypoint python3 relay /usr/local/lib/strfry/check-read-control.py --config-only
```

This check validates core/env agreement, enforcing branch/loader, executable
plugin, AUTH age/URL, COUNT/NEG, DB/snapshot paths and bounds. It conservatively
rejects config includes, duplicate keys/blocks and expressions rather than guessing
their meaning. It does not generate a config. Both switches default off. The
Compose entrypoint runs the pre-start check on relay startup; old images lacking
this entrypoint cannot silently ignore the private preset.

Start with external ingress still closed. Confirm health and local NIP-11:

```sh
docker compose -f deploy/budabit/compose.yaml exec relay \
  python3 /usr/local/lib/strfry/check-read-control.py --url http://127.0.0.1:7777/
docker compose -f deploy/budabit/compose.yaml exec relay \
  /usr/local/lib/strfry/write-policy.py --check-read-policy
```

The Compose health check performs storage/write-policy checks, config/NIP-11
agreement, installed-core projection status and `--check-read-policy` only in members mode. The NIP-11 claim must be
`limitation.auth_required: true` plus
`budabit.read_control: {version: 1, mode: "members", scope: "relay"}`. Check it
through the intended reverse-proxy path too; no redirect or public fallback.
The current client additionally requires `read_control.unfiltered_kinds` to cover
`[1,5,1984,30000,32222]` for complete text/authority intake. The core advertises only
supported kinds without post-limit involved-key filtering under its active config.
Default DM restrictions (4/1059) remain unchanged. Do not disable them to make a
broad history query look complete: the client uses separate disjoint authority
and text filters, each bounded by `min(200,max_limit)`. Unknown claims/limits or
saturation keep it partial. Older private-capable cores lacking this optional
completeness claim remain read-gated but the new client will not claim ready.
Changing restricted-read settings or maxFilterLimit while privately serving
invalidates the gate; restart and obtain fresh metadata/AUTH before reopening.

Runtime health compares the Python artifact with the running core's installed
epoch, exact pending/installed sequence and heartbeat. The core writes a protected
local status at invalidation and every100ms; the checker verifies readiness,
supervisor epoch, unexpired monotonic lease, status age under one second and Linux
boot/PID/start-time identity. Run it as the relay user in the same PID/time namespace.
Missing/rejected/pending/expired/replaced-core status fails. Pre-start `--config-only`
does not require a status file. `--check-read-policy` alone remains artifact-only.
This is sampled readiness, **not** an end-to-end privacy guarantee or a guarantee
against a failure immediately after checking. Raw protocol probes remain required before opening
ingress: anonymous REQ gets CLOSED with no EVENT/EOSE; outsider AUTH succeeds but
REQ is restricted; member AUTH+REQ returns history; COUNT/NEG never return data.
Use controlled keys and local fixtures, not live accounts for routine tests.

## Monitor and invite

- Snapshot: `budabit-readers.json`, atomic 0600, includes the private roster.
- Status: `budabit-readers.status.json`, atomic 0600, counts/readiness/error but no
  roster. It still includes private coordinates; do not expose it as a public URL.
- Serving-core status: `<snapshotPath>.core-status.json`, atomic0600, epoch,
  installed/pending revision, heartbeat, readiness and process/lease identity.
  No roster or connection identities; still private, never a public status API.
  Do not restore this file as evidence of a running process. Failed writes remove
  it so health cannot reuse an old success. Nothing reads it to authorize clients.
- A worker heartbeat advances once per second; a blocked rebuild cannot renew it.
  The core validates epoch, exact pending sequence and a monotonic lease at final
  sends. A gate timeout (default three seconds) closes rather than permits output.
- On failure, retain private mode, diagnose locally, repair/restart and probe.
  Never “fix health” by switching read control off or fabricating snapshots.

Invite with `/c/<naddr>?read-access=members` using naddr relay hints, or repeated
`relay=` query parameters (URL-encoded). Share privately. Budabit persists only
coordinates/endpoints in session storage, obtains explicit authentication consent,
and uses a real signer (90-second signing, 10-second AUTH ACK budgets). A pubkey-only
identity cannot authenticate. Retry after a grant uses full original filters and
the same authenticated connection; revoked connections authenticate again.

The initial client provides private owner-definition bootstrap and plain-text
posting through capability-checked definition relays. It does not mount the public
community pages. Its archive limit is 200 events per relay: saturation/missing
definition/failed relays are **incomplete**, not empty; do not treat that view as
complete moderation authority. Git, media, Blossom, widgets and zaps are disabled.
For larger deployments, provision an appropriately complete private-capable client
before relying on client-side authoring/moderation workflows.

## Maintenance and rollback

**Exclusive writer:** while privately serving, only `RelayWriter` may change this
DB. Built-in expiry goes through that writer. Never run `strfry import`, `delete`,
`sync`, `stream`, `router`, `sweep.py`, external retention or a second relay writer
against it. Removing a snapshot alone is **not** a maintenance fence: the live
worker can recreate it. Stop the relay and close ingress first.

1. Save the private-capable image digest, config and environment in protected backup
   storage. `backup.sh` logical exports contain private history: apply private file
   permissions/encryption/access rules and keep them off public Blossom/Git. Existing
   read-only backup/export may run online only without invoking another writer.
2. Stop the relay, verify process exit and disabled maintenance timers. Run the
   explicitly approved import/restore/delete/retention offline. Retention excludes
   5/1984/30000/32222 and other replaceables; source changes cannot recover previously
   deleted evidence. Restore policy evidence before reopening.
3. Restart **a tested private-capable image**, preserving both switches and branch.
   Do not reuse snapshot freshness as authorization: the new core epoch requires a
   fresh committed projection. Re-run health and anonymous/outsider/member probes.
4. Reopen ingress only after successful probes. A failed upgrade may roll back to
   the previous tested private-capable image/config, never the historical public
   `2fc1b38`/`b80cda3` images. If no capable rollback exists, remain stopped.

Disabling read control is a **disclosure operation**, not rollback. AUTH is not E2E
encryption; operators and members can copy retained events. Enabling it cannot
retract public history. External Git HTTP, Blossom, embeds and payment services
require independent protection even if Nostr relay reads are private.

## Isolated verification record

Run from the repository root with an approved, unique temporary parent:

```sh
TMPDIR="$(mktemp -d "$HOME/.cache/opencode-v2/tmp/opencode/private-drill.XXXXXX")" \
  node test/tests/budabitReadPolicyTest.js
```

The harness creates a fresh LMDB and controlled keys, validates preflight before
startup and against the live snapshot/NIP-11, rejects env mismatches, exercises
anonymous/outsider/member, owner bootstrap, live revocation/regrant, policy faults,
offline import and backpressure, and restores the private preset after rejected
configs with fresh-epoch access probes. This is a native configuration/rollback
drill, **not a Docker image-switch test or live deployment**. Docker daemon access
was unavailable during implementation verification; no image-build claim is made.
The Python suite covers preset pass/fail, bounded artifact health and parser errors.

Before merge/release, publish the server revision only with authorization and update
Budabit's immutable conformance pin. The existing remote pin predates reader vectors;
local vector agreement is not proof that a matching release is already published.
