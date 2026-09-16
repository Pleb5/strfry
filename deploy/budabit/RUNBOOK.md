# Budabit strfry runbook

This is the operations runbook for the Budabit relay, initially deployed on
2026-07-13 and upgraded on 2026-09-13. Current deployment details below supersede
the initial state. Historical verification is labeled separately.

See [DEPLOYMENT-2026-09-13.md](DEPLOYMENT-2026-09-13.md) for the upgrade evidence,
checkpoint locations, restore-limit and Compose-path failures, cutover guards,
and recovery lessons. These records describe the image actually deployed, not
whichever source commit a later checkout or rebase happens to select.

## Current status

This is the last recorded deployment inventory, not a fresh live inspection.

**Optional private source preset (not live):** see [PRIVATE-READS.md](PRIVATE-READS.md)
for config/env preflight, conditional health, invitations, maintenance fences and
private-capable rollback. The deployment templates now build a reviewed local
checkout. The live public inventory below remains unchanged by these source edits.

The replacement source uses plugin-owned REQ admission and periodic rechecks,
not committed reader snapshots. Source defaults now protect kind4444 independently
and disable NIP-70 semantics unless opted in; these changes are **not** evidence
that the recorded live image below has been upgraded.

- Public endpoint: `wss://relay.budabit.club`
- NIP-11 endpoint: `https://relay.budabit.club`
- Relay name: `Budabit Community Relay`
- Deployed image: `budabit/strfry:2fc1b38`, binary `v607-2fc1b38`
- Deployed revision: `2fc1b38e27cfb86775a4ccbe3190019d37e63a9f`
- Live database: retained in place at upgrade; a separate 4,427-record logical
  backup was restored and tested without replacing production data
- Read access: public
- Write access: public passthrough with rate/storage controls; auto-hosted
  communities additionally enforce the Budabit policy, with dry-run disabled
- Auto-host URL: `wss://relay.budabit.club`; explicit branch list empty
- Verified hosted communities: BudaBit and Test, both valid and warm; all seven
  BudaBit permission lists present (60 section-grant entries, not unique users)
- Protected deletion kinds: 30000 and 32222
- NIP-42: disabled
- NIP-70 protected events: rejected because NIP-42 is disabled
- NIP-77 Negentropy: enabled
- NIP-45 COUNT: disabled
- Event retention: one year for normal events
- Router, stream, and external relay sync: not configured

**Known issue in that deployed image (2026-09-14):** the first outsider room
message raced lazy plugin initialization and was stored before auto-host discovery.
A later warm-state thread write was rejected correctly. The readiness fix and
regression tests are in source, not yet recorded as deployed. See the
[incident record](INCIDENT-2026-09-14.md); September health/metadata checks alone
must not be described as proof of correct cold-ingestion enforcement.

Strfry does not fetch events from other relays by itself. Events appear here
only when Budabit, a Nostr client, or a future administrative sync operation
explicitly publishes them.

## Server inventory

The relay shares the existing Hetzner CPX22 named `blossom-grasp`.

| Resource | Available at deployment |
| --- | --- |
| OS | Ubuntu 24.04.4 LTS, x86-64 |
| CPU | 2 AMD EPYC-Genoa vCPUs |
| RAM | 3.7 GiB usable |
| Local SSD | 75 GiB formatted, 58 GiB free |
| Attached volume | 98 GiB formatted, 93 GiB free |
| Swap | 2 GiB `/swapfile` |

Existing workloads at assessment time were light:

| Service | Runtime | Memory observed |
| --- | --- | --- |
| Blossom | Docker | approximately 171 MiB |
| ngit-grasp | systemd | approximately 427 MiB, 984 MiB peak |
| Caddy | systemd | approximately 35 MiB |
| Chii | systemd | approximately 39 MiB |

The server had approximately 2.7 GiB available RAM and negligible CPU load.
This was enough headroom for a small relay with explicit resource limits. A
separate server was not justified.

## Files and paths

| Purpose | Path |
| --- | --- |
| Deployment bundle (not a Git checkout) | `/opt/strfry/deploy/budabit` |
| Separately staged source | `/opt/strfry-releases/2fc1b38` |
| Validated upgrade candidates and isolated-test reports | `/opt/strfry-upgrade/2fc1b38` |
| Compose file | `/opt/strfry/deploy/budabit/compose.yaml` |
| Relay configuration | `/opt/strfry/deploy/budabit/strfry.conf` |
| Live LMDB database | `/var/lib/strfry/db` |
| Daily backups | `/mnt/HC_Volume_105751807/backups/strfry/daily` |
| Weekly backups | `/mnt/HC_Volume_105751807/backups/strfry/weekly` |
| Monthly backups | `/mnt/HC_Volume_105751807/backups/strfry/monthly` |
| Active Caddy configuration | `/etc/caddy/Caddyfile` |
| SSH key-only drop-in | `/etc/ssh/sshd_config.d/00-key-only.conf` |
| Swap sysctl drop-in | `/etc/sysctl.d/99-strfry.conf` |

Do not `git pull` here or replace `/opt/strfry` wholesale. The existing bundle
contains server-specific configuration, old import files, and logs. Stage source
elsewhere and replace only reviewed deployment files at cutover. The Python
policy used by the relay is packaged in the selected image; changing a source
checkout or the old host-side policy files does not update that image.

The deployment bundle contains:

| File | Purpose |
| --- | --- |
| `Dockerfile` | Reviewed-checkout non-root strfry image (historical deployments used remote pins) |
| `compose.yaml` | Runtime isolation, limits, mounts, and health check |
| `strfry.conf` | Relay protocol and resource configuration |
| `write-policy.py` | Write-policy entrypoint: storage guard, Budabit community write control, rate limits |
| `read-policy.py` | Separate cached membership plugin for REQ admission and periodic connection rechecks |
| `policy/` | Policy stages; `policy/budabit/` implements Communikeys V2 write control |
| `audit.py` | Read-only replay of stored community content against the current rules |
| `sweep.py` | Deletes audit-listed events (person-banned authors by default) |
| `WRITE-CONTROL-PLAN.md` | Design and rollout plan for community write control |
| `backup.sh` | Logical export, validation metadata, and rotation |
| `retention.py` | One-year pruning while preserving replaceable events |
| `Caddyfile` | TLS/WebSocket reverse-proxy host block |
| `systemd/` | Backup and retention services and timers |
| `tests/` | Policy, write-control, and retention regression tests |

## Image provenance

The current image was built locally as `budabit/strfry:2fc1b38` from:

- Repository: `https://github.com/Pleb5/strfry.git`
- Commit: `2fc1b38e27cfb86775a4ccbe3190019d37e63a9f`
- Alpine base: `3.22`
- Alpine digest: `sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce`

The build explicitly supplied `--build-arg STRFRY_COMMIT=2fc1b38e27cfb86775a4ccbe3190019d37e63a9f`.
The source-controlled deployment templates now build local source; do not mistake
their `local-read-control` default for the live version. The live Compose
file instead selects the already-built new image, has no `build` block, and uses
`pull_policy: never`.

On this shared VPS, a separate `Dockerfile.vps` changed `make -j2` to
`make -j1 NPROC=1`; its matching `.dockerignore` was copied from the original
Dockerfile-specific allowlist. This was a resource precaution, not a strfry
requirement. The old image was not overwritten or removed. Actual image IDs and
the rollback tag are recorded in the cutover checkpoint's `images.txt`.

The historical Docker build cloned the exact strfry commit. The updated source
template instead allowlists core source, initialized vendored dependencies and
policy/deployment scripts, excluding Git metadata, DBs, environment files and
object files. Record the reviewed revision and image digest as described in the
private guide; do not replace a deployed bundle wholesale.

The source commit and Alpine base image are immutable, but `apk add` package
versions are resolved from the Alpine 3.22 repositories at build time. Rebuilding
later can therefore produce different package revisions. Record the deployed
image ID when promoting this beyond testing, or pin a published image digest.

## Host hardening

### SSH

SSH was confirmed to use a passphrase-protected private key. The passphrase
unlocks the key locally and is not a server password.

The effective key-only configuration is:

```text
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin prohibit-password
```

`sshd -T` may render `PermitRootLogin prohibit-password` as the older alias
`without-password`. A second SSH connection was tested successfully before the
original session was closed.

The existing `/etc/ssh/sshd_config.d/90-ocjump.conf` only controls forwarding
for the `ocjump` user and does not conflict with key-only authentication.

### Firewall

UFW remains inactive. A Hetzner Cloud Firewall named `ocmux-public` is attached
to the server with these inbound rules:

| Protocol | Port | Source |
| --- | --- | --- |
| TCP | 22 | Any IPv4 and IPv6 |
| TCP | 80 | Any IPv4 and IPv6 |
| TCP | 443 | Any IPv4 and IPv6 |
| ICMP | Any | Any IPv4 and IPv6 |

There are no public rules for ports `3001`, `7334`, `7777`, or `9222`.
Direct access to Chii on `9222` and strfry on `7777` was tested from outside
and timed out as expected. Chii remains available through Caddy at
`https://debug.budabit.club`.

### Swap

A 2 GiB swapfile was created at `/swapfile`, added to `/etc/fstab`, and enabled.
`vm.swappiness` is set to `10`, making swap emergency headroom rather than
normal working memory.

## Container isolation

The relay container uses the following controls:

- Non-root UID/GID `10001:10001`
- Read-only root filesystem
- Writable LMDB bind mount only at `/var/lib/strfry/db`
- 16 MiB temporary filesystem at `/tmp`
- All Linux capabilities dropped
- `no-new-privileges` enabled
- 128 PID limit
- 1 CPU limit
- 1280 MiB memory limit
- 65,536 open-file limit
- Docker JSON logs rotated at 10 MiB with three files
- Listener published only as `127.0.0.1:7777`

The database directory must exist before Compose starts:

```bash
sudo install -d -o 10001 -g 10001 -m 0750 /var/lib/strfry/db
```

Compose uses `create_host_path: false` so a typo cannot silently create a
root-owned directory that the non-root container cannot write.

## Relay policy

### Authentication and access

In the recorded public `2fc1b38` deployment, NIP-42 is completely disabled.
Ordinary clients do not receive AUTH challenges. NIP-70 protected events containing
the `-` marker cannot be accepted and are rejected by strfry.

That is historical image behavior, not the current source default. The new source
defaults NIP-70 off (ignore its protection semantics, preserve signed tags) and
always protects DM kinds `4`, `1059` and `4444`; usable DM reads require configured
AUTH. Optional whole-relay admission has a separate configuration and periodic
recheck contract. See [PRIVATE-READS.md](PRIVATE-READS.md) before building or operating
that source; no image promotion is implied by these documentation updates.

Writes are public rather than community-member-only. This avoids NIP-42 client
compatibility problems but means the relay must be treated as an internet-facing
public service, not a private community store.

Strfry validates event IDs and signatures before invoking the write policy, so
the policy's pubkey bucket operates on the verified event author. The policy
does not use NIP-42 state.

### Write limits

The write policy applies token buckets with these initial settings:

| Scope | Burst capacity | Refill rate |
| --- | ---: | ---: |
| Event pubkey | 30 events | 2 events/minute |
| Source IP | 100 events | 10 events/minute |
| Whole relay | 200 events | 20 events/minute |

Tracking is bounded to 10,000 pubkeys and 4,096 sources. New identities are
rejected when a non-stale tracking map is full. Bucket state is in memory and
resets when the plugin or container restarts; the disk guard remains the final
persistent safety boundary.

### Storage limits

The policy rejects all new writes when either condition is reached:

- `data.mdb` is 10 GiB or larger
- The local database filesystem has less than 25 GiB available

The policy fails closed when it cannot inspect the database or filesystem. The
container health check covers both the HTTP endpoint and the policy storage
check, so a relay that can serve reads but cannot safely write becomes
unhealthy.

### Community write control

The Budabit stage enforces Communikeys V2 grants for explicit
`BUDABIT_BRANCHES` and/or branches discovered through `BUDABIT_AUTO_HOST_URL`
(see `WRITE-CONTROL-PLAN.md`). It is disabled only when **both** are empty.

The September deployment uses:

```env
BUDABIT_AUTO_HOST_URL=wss://relay.budabit.club
BUDABIT_BRANCHES=
BUDABIT_MODE=passthrough
BUDABIT_DRY_RUN=0
BUDABIT_DISABLE_LOADER=0
```

Valid kind-32222 definitions whose `r` tags name this relay activate auto-hosting;
new community creation needs no grant in an existing community. Malformed or
non-hosting new definitions do not activate enforcement and follow ordinary
passthrough checks. Invalid updates to an already hosted definition are rejected.
Auto-hosted branches can be removed on reconciliation if their current definition
stops naming this relay. Empty explicit branches intentionally permit that.

Configuration lives in `compose.yaml`. The source template exposes substitutions
from the shell or a neighboring `.env` file. The deployed candidate instead pins
the five Budabit values above and the two bind-source paths explicitly: changing
`.env` will not override those literal values. Edit/review the service configuration
and recreate the container when changing them. Other preserved substitutions
(for example, storage thresholds) still resolve normally.

The table describes the policy defaults, not a claim that all defaults are live:

| Variable | Default | Meaning |
| --- | --- | --- |
| `BUDABIT_BRANCHES` | empty | Comma-separated exact `32222:<owner>:<communityId>` addresses to enforce, in addition to auto-host discovery. |
| `BUDABIT_MODE` | `passthrough` | `passthrough` keeps the public-write relay; `strict` is community-only with the documented workflow/personal-kind exceptions. |
| `BUDABIT_DRY_RUN` | `0` | `1` logs would-be community-policy rejections without enforcing them. Signature, storage, and rate checks still apply. Optional for rollout. |
| `BUDABIT_REJECT_CENSORED_ADDRESSES` | `0` | Reject replacements at addresses censored by an effective event report. |
| `BUDABIT_RECONCILE_SECONDS` | `300` | Background reconcile interval against LMDB. |
| `BUDABIT_AUTO_HOST_URL` | empty | Auto-host: enforce every valid `kind:32222` whose `r` tags name this URL, in addition to `BUDABIT_BRANCHES`. Branches are unhosted again when their current definition stops naming the relay. |
| `BUDABIT_DISABLE_LOADER` | `0` | Test/offline override only. Never disable the loader on the live relay. |

Rollout gates:

1. Have the Budabit relay-error UI ready before enabling enforcement: loading,
   propagation, bans, invalid events, and permanent failures need distinct
   feedback rather than blind retries.
2. Verify the new image on a separate restore, not by mounting the live database
   into the new binary. Run `write-policy.py --status`; require the expected
   **exact** addresses with `auto`, `warm`, and `definition` true, and inspect
   each referenced shard. A zero-grant section with no referenced lists is not
   a loader failure (this was the Test community's state at cutover).
3. Run `--check-storage`, `--check-policy`, and test the real HTTP NIP-11 response
   against the staged configuration. Dry-run is optional; the approved rollout
   did not require a week-long dry run or a historical audit/sweep. The accepted
   deletion-replay residual risk below is not an outstanding release gate.
4. Follow the checkpoint/cutover procedure in the dated upgrade record, then
   recreate the container to apply image/environment changes. A restart alone
   is insufficient. Recheck exact branches and public HTTPS after cutover.

Rejections are plain NIP-01 `OK false` replies with stable prefixes:
`blocked:` (policy), `invalid:` (Communikeys grammar), `rate-limited:`,
`error:` (transient; `error: relay policy is loading` during warm-up after a
plugin start or reload). The plugin logs one JSON line per rejection and per
state change (`definition_updated`, `shard_updated`, `report_added`,
`report_deleted`, `definition_deleted`, `shard_deleted`) to the container log.

Health: the Compose check covers HTTP, storage, and `--check-policy`. Policy
health fails for loading errors or unavailable explicit branches, but can succeed
with **zero auto-discovered branches**. It also does not require every referenced
shard to exist. Use `--status` to verify expected addresses and missing lists;
`shard_missing` warns of unavailable authority. Do not infer enforcement readiness
from a green container alone.

The fixed source also checks completion of that instance's full initial load.
Until completion, no new writes pass (including ordinary passthrough and
bootstrap updates); the response is the transient loading error, except known
protected deletions keep their permanent denial. Initial failures retry with
capped backoff. The ingestion process logs `initial_load_complete` on success.
`docker exec ... --check-policy` and `--status` still load **separate instances**:
use the live process's logs and actual cold-ingestion tests, not those commands
alone, to establish startup safety. No eager-launch change or stronger global
authority-freshness guarantee is implied.

NIP-11: advertise enforcement so clients and auditors can see it. Generate
the value with `write-policy.py --nip11-extra` and paste it into
`relay.info.extra` in `strfry.conf` (a JSON object merged into the relay
information document; generated fields are never overridden). Re-run when
`BUDABIT_BRANCHES` changes. Auto-hosted branches are not listed statically;
the `auto_host` field names the relay URL instead.

The output also contains `dry_run`, `enforcing`, `configured_branches`, and
`protected_deletion_kinds`. Dry-run does not claim active enforcement and leaves
`enforced_branches` empty. Regenerate when changing mode/dry-run as well as the
branch list. The deployed `2fc1b38` binary supports `relay.info.extra`; the
Dockerfile's old default `b80cda3…` core pin does not. Always select the intended
core revision and built image explicitly. Local code/tests alone do not update
the running image. For auto-host-only advertising, omit frozen
`configured_branches`/`enforced_branches` arrays and retain the dynamic
`auto_host` rule; the exact deployed payload is in the upgrade record.

Audit and sweep (read-only unless `--apply`):

```bash
# what the current rules would reject among stored community content
sudo docker compose -f deploy/budabit/compose.yaml exec -T relay \
  python3 /usr/local/lib/strfry/audit.py > audit.json

# dry run: content by effectively person-banned authors
sudo python3 /opt/strfry/deploy/budabit/sweep.py audit.json

# delete it, refusing unless a backup newer than 30 h exists
sudo python3 /opt/strfry/deploy/budabit/sweep.py audit.json --apply \
  --require-recent-backup /mnt/HC_Volume_105751807/backups/strfry/daily
```

`sweep.py` deletes only `person_banned` content unless other reason codes
are named with `--reasons`. Removing `no_grant` content diverges from
Budabit's "regrant refetches history" model and removing censored events
removes the client's "Moderated event" placeholder; do that only on explicit
community request.

Offline replay of a JSONL export against the community policy only (rate
limits and the storage guard are not applied; authority comes from LMDB plus
the export itself, replayed in `created_at` order):

```bash
sudo docker compose -f deploy/budabit/compose.yaml exec -T relay \
  /usr/local/lib/strfry/write-policy.py --replay /dev/stdin < events.jsonl
```

Append `export` to evaluate with authority taken from the export alone.

To roll back, clear both `BUDABIT_BRANCHES` and `BUDABIT_AUTO_HOST_URL`, then
recreate the relay container (`docker compose ... up -d --no-build --force-recreate relay`).
Environment changes require recreation, not just `docker compose restart`. Nothing stored is removed;
the stage only gates new writes.

Deletion requests and NIP-09: strfry treats every `a` tag on a `kind:5` as
a deletion target. Budabit therefore scopes community deletes with `h` only
and never with the marked branch `a` (`Communikeys.md`, "Deletion
Requests"). A client that still sends the old shape will see its
retractions rejected ("can't delete other user's events") or, for the
owner, will tombstone the community definition; publish a fresh definition
with a newer `created_at` to recover.

#### Deletion replay trade-off

**New-write policy:** kind-5 requests with a `k` tag naming 32222/30000 or an `a`
coordinate of either kind are rejected with
`blocked: Deletion of kinds 32222 and 30000 is not allowed` (reason
`protected_kind_deletion`). This applies to the enabled Budabit stage in both
modes, even for owners or explicitly tagged unhosted lists. E-only targets
currently held as definitions/shards are also rejected. Mixed requests are
rejected whole. Unknown e-only ids cannot be classified by this rule.
Report-only kind-1984 retractions remain allowed; marked branch `a` context on
a delete is rejected. Update definitions and list contents by replacement.

The following discussion concerns existing/imported deletions and the accepted
residual edge case, not permission to submit new protected-kind deletions.

Warm-up and reconcile use coordinate-scoped definition/shard scans and their
`#a` tombstones, plus community-scoped `#h` deletes and reports. They do **not**
scan every kind:5 by each authority author. Same-author deleted ids learned
from those scans or inline writes remain in memory for that branch.

Reconcile also remembers an inline definition/shard id if storage still lacks
it after the 60 s commit grace. Replaying the same event does not refresh that
grace, and once remembered, the id cannot restore authority. This costs no
extra LMDB reads. Absence is a conservative refusal signal, not proof that its
author deleted it; even an operator-removed inline event can be refused until
the branch state is recreated. Events seen only in storage are simply dropped.

**Residual risk:** after restart, replaying an event deleted by an e-only kind:5
outside the scoped scans can temporarily restore its grants. With successful
reconciliation, this lasts one 60 s grace plus up to
`BUDABIT_RECONCILE_SECONDS` (about 6 minutes at defaults, plus scan/scheduling
time), once per id for that in-memory branch. Loader failures can extend it;
restart or auto-unhosting loses the memory. strfry still refuses the deleted
authority event, but newly authorized content can be stored during the window.
A shorter reconcile interval reduces the interval portion, **not** the grace
or the risk to zero.

For revocation, publish a newer kind:32222/30000 replacement without the grant
(Budabit's normal flow). New NIP-09 deletion of these coordinates is forbidden;
existing/imported author-signed `a` tombstones still take effect. Avoid deleting only the current event id and leaving
the coordinate empty: even strfry can accept a different older version whose
id was never deleted.

### Query limits

- Maximum five filters per REQ
- Maximum ten kinds per validated filter
- Maximum three tag filters per filter
- Maximum 200 returned events per filter
- Maximum ten subscriptions per connection
- Maximum 4 MiB pending outbound data per connection
- COUNT disabled
- Broad filters remain allowed for client compatibility

COUNT was disabled because public COUNT requests can materialize large result
sets and consume disproportionate CPU and memory.

### Negentropy

NIP-77 remains enabled because efficient reconciliation was a deployment goal.
Uncached reconciliation is limited to 10,000 events, one Negentropy worker is
used, and connections are limited to ten subscriptions.

On the recorded image, Negentropy is still a public resource surface. Strfry keeps
a cached full-DB tree whose path does not enforce `maxSyncEvents` in the same way as uncached
queries. The 1280 MiB container memory limit is the final isolation boundary if
many clients create concurrent sessions.

Current source bypasses shared precomputed trees when a query may contain
restricted events, using participant-filtered memory reconciliation. Private
admission mode disables Negentropy entirely. Neither change has been verified on
the recorded public image.

## Caddy and DNS

The Caddy host block is:

```caddyfile
relay.budabit.club {
	route {
		@metrics path /metrics
		respond @metrics 404

		reverse_proxy 127.0.0.1:7777 {
			header_up X-Real-IP {remote_host}
		}
	}
}
```

Caddy terminates TLS, handles WebSocket upgrades, overwrites `X-Real-IP`, and
keeps `/metrics` unavailable publicly. Trusting `X-Real-IP` is safe here because
strfry is reachable only through the loopback-published container port.

The DNS records are:

| Type | Name | Value |
| --- | --- | --- |
| A | `relay.budabit.club` | `178.104.89.98` |
| AAAA | `relay.budabit.club` | `2a01:4f8:1c19:d132::1` |

An earlier wildcard CNAME pointed unknown subdomains to `budabit.club` on
shared hosting. Some recursive resolvers retained that six-hour CNAME after the
explicit records were created. Authoritative DNS was correct, but local `nak`
temporarily received the `*.shared.1984.is` certificate from the old server.

For HTTP/TLS checks during DNS propagation, bypass recursive cache without
changing system DNS:

```bash
curl --resolve relay.budabit.club:443:178.104.89.98 \
  -H 'Accept: application/nostr+json' \
  https://relay.budabit.club/
```

For tools without a `--resolve` equivalent, per-link DNS was temporarily set to
public resolvers and then reverted. The workstation currently uses its original
network-provided DNS again.

## Backup and retention

### Backup behavior

`backup.sh` creates a standard JSONL export compressed with zstd. It also
captures the live `/etc/strfry.conf` and writes a SHA-256 sidecar. The scheduled
job creates integrity metadata but does not automatically test the compressed
stream, parse every JSON line, or perform a restore. Verification is a separate
operation.

Retention tiers are:

| Tier | Retention |
| --- | --- |
| Daily | 7 days |
| Weekly | 35 days |
| Monthly | 190 days |

Weekly and monthly snapshots are hard links on the same volume. The first
successful backup in each ISO week and calendar month is promoted using period
markers. A later failure in that same period does not remove the promoted copy;
an entirely missed week or month is not backfilled.

The script refuses to run unless the configured Hetzner volume is a real mount
and the actual backup directory is on that same filesystem. This prevents a
missing volume from silently filling the root disk.

The first live backup was created and verified successfully:

```text
/mnt/HC_Volume_105751807/backups/strfry/daily/events-20260713T125731Z.jsonl.zst
```

### Retention behavior

The monthly job deletes normal events older than 365 days. It preserves current
replaceable events:

- Kinds 0, 3, and 41
- Kinds 10000 through 19999
- Kinds 30000 through 39999
- Kinds 1984 and 5 (reports and deletion/retraction evidence), irrespective of age

The latter exclusions protect long-lived moderation state; do not remove them to
reclaim space without a separate authority-retention design. Counts of preserved
replaceable/policy events appear in dry-run and apply output. This source change
does not establish which historical events a live deployment has already pruned.

The retention script finishes its LMDB read scan and spools selected IDs to a
temporary file before deleting batches. Deleting while retaining a long-lived
LMDB read transaction would prevent page reuse and inflate the database.

The retention systemd service requires a successful fresh backup service run
before applying deletion.

### Timers

```bash
systemctl status \
  budabit-strfry-backup.timer \
  budabit-strfry-retention.timer
```

At deployment, backup was scheduled daily around `02:15 UTC` with randomized
delay. Retention was scheduled monthly with randomized delay.

## Routine operations

Run these commands on the VPS.

### Status

```bash
cd /opt/strfry
sudo docker compose -f deploy/budabit/compose.yaml ps
```

```bash
cd /opt/strfry
sudo docker compose -f deploy/budabit/compose.yaml logs --tail=200 relay
```

```bash
curl -sS -H 'Accept: application/nostr+json' http://127.0.0.1:7777/
```

### Local metrics

```bash
curl -sS http://127.0.0.1:7777/metrics
```

The public `https://relay.budabit.club/metrics` endpoint intentionally returns
404.

### Restart versus recreate

```bash
cd /opt/strfry
sudo docker compose -f deploy/budabit/compose.yaml restart relay
```

This only restarts the existing container. To apply an already-reviewed image or
environment change, recreate it with `up -d --no-build --pull never --no-deps
--force-recreate --wait --wait-timeout 180 relay`, after the upgrade checkpoint
and maintenance guards. Do not use restart as a deployment mechanism.

### Stop and start

```bash
cd /opt/strfry
sudo docker compose -f deploy/budabit/compose.yaml stop relay
sudo docker compose -f deploy/budabit/compose.yaml up -d --no-build --pull never --no-deps relay
```

The live Compose file has `pull_policy: never` and no build block. Use
`--no-build --pull never` as additional guards against selecting an untested
image. Scope operations to `relay`; do not take down unrelated services.

### Build an upgrade separately

Do not build in `/opt/strfry` or run `git pull` there: it is a deployment bundle,
not a checkout. Fetch an exact published revision under `/opt/strfry-releases`,
run its tests, then build under a new tag with an explicit `STRFRY_COMMIT` build
argument. Preserve the old tag/image and the restrictive Docker context
allowlist. Keep Compose activation separate from the build.

Follow [the upgrade procedure](DEPLOYMENT-2026-09-13.md#repeatable-upgrade-sequence)
for the isolated restore, candidate comparison, and maintenance cutover.
Database format changes may require logical export/import rather than direct
reuse; an older binary must not be assumed to understand a newer database.

### Manual backup

```bash
sudo bash /opt/strfry/deploy/budabit/backup.sh
```

Verify compressed exports from inside the backup directory:

```bash
sudo zstd --test --quiet *.jsonl.zst
sudo sha256sum --check *.sha256
```

### Retention dry run

```bash
sudo python3 /opt/strfry/deploy/budabit/retention.py --days 365
```

### Retention apply

Prefer the systemd service because it first requires a successful backup:

```bash
sudo systemctl start budabit-strfry-retention.service
sudo systemctl status budabit-strfry-retention.service --no-pager
```

### Disk and memory checks

```bash
sudo df -h / /var/lib/strfry/db /mnt/HC_Volume_105751807
sudo du -h /var/lib/strfry/db/data.mdb
free -h
sudo docker stats --no-stream budabit-strfry-relay-1
```

Set `dbParams.noReadAhead = true` and restart the relay if the database becomes
materially larger than RAM. This reduces unnecessary read-ahead I/O.

## Restore procedure

Logical JSONL is the portable event backup; a stopped native checkpoint also
preserves LMDB state that an event-only export does not represent. **First test
recovery in an isolated directory while production stays running.** Do not move
or replace the live database just to find out whether an import works.

1. Verify the mounted backup destination, checksum, zstd stream, and every JSON
   record. Measure maximum JSONL line bytes, tags per event, and UTF-8 tag-value
   bytes without printing event contents.
2. Create a new root-private scratch directory on disk, with a database
   subdirectory owned by UID/GID 10001. Do not put the database in the relay's
   16 MiB `/tmp` tmpfs. Bind mount only the scratch DB and copied configurations.
3. Use the already-built image with `--pull=never`, `--network=none`, no published
   ports, non-root UID/GID, and normal container isolation. Feed the root-private
   export through a host-side pipeline; use `set -euo pipefail`.
4. If historical records exceed today's limits, create a separate **import-only**
   config sized to the measured backup. Never relax production write limits or
   use `--no-verify` to make a restore pass. The September values and a statistics
   command are in [the upgrade record](DEPLOYMENT-2026-09-13.md#historical-records-can-exceed-current-write-limits).
5. Inspect import diagnostics and compare `scan --count '{}'` with the parsed
   backup record count. Stop on errors, skips, or unexplained count differences;
   a partial import is not a verified restore. Retry in a fresh scratch DB.
6. Switch back to the original-limit config for count, loader, and HTTP checks.
   Require expected exact branch addresses and inspect missing permission lists.
7. Only if actual production recovery is required, arrange a separate maintenance
   window, pause the relay timers, require maintenance services to be idle, stop
   the relay, and preserve the current DB before promoting the verified recovery
   copy. Retain the displaced database and record the potential lost-write window.

Standard import validates event IDs and signatures but bypasses the WebSocket
write policy; treat administrative imports as trusted operations. The importer
can skip records without returning a failing exit status, so count verification
and diagnostics are both required. An ordinary image/configuration rollback
should retain the current DB if the versions are compatible, not discard writes
by restoring an older checkpoint. The September cutover did **not** promote the
logical test restore or replace the live database.

## LMDB compaction

Deleting events makes pages reusable inside LMDB but does not shrink
`data.mdb`. Compaction requires temporary space for a second database and should
be planned before the 10 GiB guard is reached.

High-level procedure:

1. Take and verify a logical backup.
2. Stop the relay.
3. Run `strfry compact` to a new file on the same local filesystem.
4. Replace `data.mdb` only after the compact command succeeds.
5. Preserve the old file until the relay starts and verifies successfully.

Do not compact while disk space is too low to hold both copies.

## Rollback

### Image/configuration rollback

Use the verified September checkpoint and retained old image described in
[the deployment record](DEPLOYMENT-2026-09-13.md#rollback-is-not-a-database-restore).
Verify the manifest, record/pause the two relay timers, and require their
services to be idle. Restore only the saved `compose.before.yaml` and
`strfry.before.conf` to the live deployment paths, then recreate **only** the
relay with `--no-build --pull never --no-deps --force-recreate --wait`.
Restore timer states only after health verification. Keep the database in place
when compatible; restoring `native-db.tar` is a different recovery operation
that can discard writes since the checkpoint.

The cutover's shell handler was configured to attempt this rollback on failure;
the successful cutover did not exercise that path. It was not a background
monitor and stopped existing when the cutover block completed.

### Decommissioning (not upgrade rollback)

To remove the relay without affecting Blossom, GRASP, Chii, or Caddy:

```bash
cd /opt/strfry
sudo docker compose -f deploy/budabit/compose.yaml down
```

Remove only the `relay.budabit.club` block from `/etc/caddy/Caddyfile`, validate
with `sudo caddy validate --config /etc/caddy/Caddyfile`, and reload Caddy.
A timestamped pre-strfry Caddyfile backup was created during deployment.

Do not remove `/var/lib/strfry/db` or the backup volume data until retention and
recovery requirements have been reviewed.

## Verification performed at initial deployment (2026-07-13)

The following checks passed at initial deployment; do not treat them as fresh
September observations. The [September record](DEPLOYMENT-2026-09-13.md#verification-and-scope)
lists the later isolated and live checks separately.

- Eight Python policy and retention tests
- `docker compose config`
- `bash -n` for the backup script
- `systemd-analyze verify` for services and timers
- Pinned Docker image build on both workstation and VPS
- Non-root container startup with read-only filesystem and dropped capabilities
- Extended health check, including the storage-policy check
- Forced over-budget policy health failure
- NIP-11 over local HTTP and public TLS
- NIP-42 absent from the raw advertised NIP list
- `nak relay` normalized relay metadata reporting `auth_required: false`
- NIP-45 absent after COUNT was disabled
- Ordinary public WebSocket REQ using `nak`
- NIP-77 empty-set reconciliation using `nak --ids-only`
- HTTP WebSocket upgrade returning status 101
- Public `/metrics` returning 404
- Direct external ports `7777` and `9222` timing out
- Existing Blossom and GRASP endpoints returning HTTP 200
- Existing Chii endpoint returning HTTP 401 through Basic Auth
- Logical backup zstd stream and SHA-256 checksum verification
- Backup and retention timers active

## Lessons learned

### NIP-42 is not required for signed-author policy

Strfry verifies an event signature before the write-policy plugin runs. A policy
can therefore rate-limit or whitelist `event.pubkey` without requiring clients
to complete NIP-42. This remains true for the public write preset. In current
source, NIP-42 also supports mandatory participant-only DM reads and optional
whole-relay admission; explicit NIP-70 enable uses it for protected publication.
These independent uses must not be confused with signed-author write authority.

### Public write access requires independent safety layers

An empty community test relay is still public infrastructure once its hostname
is shared. Rate limits alone do not protect finite storage. The deployment uses
per-author, per-source, global, database-size, and free-filesystem limits, with
a hard container memory ceiling.

### Negentropy efficiency does not eliminate resource risk

NIP-77 saves bandwidth when sets overlap, but reconciliation sessions still
consume memory. Public Negentropy should use low worker/session/result limits on
a small VPS and remain inside a memory-limited container.

### LMDB should use local SSD and separate backup storage

Durable LMDB writes benefit from local SSD latency. The attached Hetzner volume
is appropriate for backup staging, not the live database. Keeping them separate
also preserves a recoverable copy if the local database is damaged.

The 10 TiB LMDB `mapsize` is virtual address space and does not reserve 10 TiB
of disk or RAM.

### Retention and compaction are different operations

Retention makes LMDB pages reusable but does not return disk space to the
filesystem. Compaction is a separate maintenance operation. Deleting in batches
while a long read transaction remains open can make the database grow, so the
retention tool completes its scan before beginning writes.

### Replaceable kind 41 must be preserved

This strfry revision treats kind 41 as replaceable in addition to kinds 0 and 3
and the standard replaceable ranges. Retention tests explicitly cover it.

### A health check must test write safety

An HTTP-only health check can report healthy while every write is being rejected
because storage is full or unwritable. The deployed health check validates
the HTTP listener, storage guard, and policy loader. It still does not prove
that expected auto-hosted branches or all their permission lists were found;
verify `--status` explicitly.

### Bind mounts should fail when host paths are missing

Docker Compose otherwise creates missing bind source directories as root. A
non-root container then enters a restart loop. Pre-creating the directory and
using `create_host_path: false` turns that mistake into an immediate deployment
error.

### Backups must verify the actual mounted destination

Checking only that a configured volume exists is insufficient when the backup
path can be overridden. The script canonicalizes both paths and confirms the
actual backup directory is on the same mounted filesystem.

### Build inputs should be explicit and recorded

The original project image used mutable `latest`, ran as root, and used an old
Alpine release. The deployment pins both source commit and base-image digest,
uses a non-root runtime, and allowlists Docker context files. Alpine package
revisions are still resolved at build time, so the final image ID must be
recorded when exact binary reproducibility matters.

### Hetzner Firewall and SSH authentication solve different problems

The cloud firewall decides which hosts and ports are reachable. SSH configuration
decides how users authenticate after reaching port 22. Since SSH remains open to
all source addresses, disabling server password authentication removes the
credential-guessing path while preserving passphrase-protected key login.

### DNS propagation can mislead application tests

Authoritative DNS can be correct while an upstream recursive resolver still
returns an older wildcard CNAME. Certificate errors from the old shared host did
not indicate a Caddy failure. `curl --resolve`, authoritative DNS queries, and a
temporary per-link resolver override separated propagation problems from relay
problems.

### Test maintenance paths before relying on timers

The first local backup test found that `mktemp` creates the destination and zstd
refuses to overwrite it unless explicitly allowed. Exercising export,
compression, checksum verification, promotion, and mount checks before enabling
the timer prevented a silent backup failure.

## Outstanding work

- Add the administrator `npub` and contact field to NIP-11 metadata.
- Keep the deployed NIP-11 policy claim in sync with mode, dry-run, and hosting
  configuration. The September auto-host advertisement is installed and verified.
- Review template defaults deliberately before a future build; the shipped old
  `STRFRY_COMMIT` default is not the September deployed revision. Do not rebuild
  or deploy implicitly during documentation or branch maintenance.
- Regenerate `deploy/budabit/tests/vectors/budabit-policy-vectors.json` from
  Budabit (`POLICY_VECTORS_OUT=... pnpm exec vitest run
  src/app/core/community-policy-vectors.test.ts`) whenever the client's
  permission rules change; the file records the Budabit commit it came from.
- NIP-34 repository events have no special handling: an `h`-tagged
  `kind:30617` needs its current section grant (for example, Code-curator or
  Repositories); other repository kinds are
  admitted only when a content section lists them (none do by default) and
  are rejected in strict mode without `h`. Repository collaboration belongs
  on GRASP.
- Add privacy and terms URLs if the relay becomes a community production service.
- If authorized, perform a live signed publish/read-back test. Isolated signed
  WebSocket tests passed before deployment; no live test events were published
  during the September cutover.
- Configure an external HTTPS/WebSocket uptime monitor.
- Send encrypted backups to an off-provider destination; the current backup
  volume is in the same Hetzner project.
- Repeat isolated restore drills as backup size and formats change. The September
  4,427-event logical restore and stopped native checkpoint checks are complete;
  a destructive production database restore was neither needed nor performed.
- Reassess Ubuntu package updates/reboot requirements (not checked in September),
  then, if maintenance is needed, verify that
  Caddy, Chii, GRASP, Blossom, Docker, strfry, swap, and timers return correctly.
- Observe Budabit client queries before tightening `requireAuthorOrTag`; broad
  filters remain enabled for compatibility.
- Monitor write rejection rates and adjust token buckets only from observed
  traffic, not assumptions.
- Alert before `data.mdb` reaches 10 GiB or local free space approaches 25 GiB.

## Scaling triggers

The current CPX22 remains appropriate while these conditions hold:

- Available memory normally remains above approximately 750 MiB
- Swap is not used continuously
- CPU is not sustained above approximately 70 percent
- Local disk remains below approximately 70 percent used
- Relay queries and writes remain responsive
- Compaction can create a second database copy safely

The next practical step is a 4-vCPU, 8-GiB Hetzner Cloud node with roughly
160 GiB local SSD. Dedicated physical hardware is not required for the expected
community size.
