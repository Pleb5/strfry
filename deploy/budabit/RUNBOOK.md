# Budabit strfry runbook

This document records the Budabit relay deployment completed on 2026-07-13.
It is both an operations runbook and a record of the decisions and lessons from
the initial deployment.

## Current status

- Public endpoint: `wss://relay.budabit.club`
- NIP-11 endpoint: `https://relay.budabit.club`
- Relay name: `Budabit Community Relay`
- Initial database: empty; no migration was performed
- Read access: public
- Write access: public with rate and storage policy controls
- NIP-42: disabled
- NIP-70 protected events: rejected because NIP-42 is disabled
- NIP-77 Negentropy: enabled
- NIP-45 COUNT: disabled
- Event retention: one year for normal events
- Router, stream, and external relay sync: not configured

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
| Deployment bundle | `/opt/strfry/deploy/budabit` |
| Compose file | `/opt/strfry/deploy/budabit/compose.yaml` |
| Relay configuration | `/opt/strfry/deploy/budabit/strfry.conf` |
| Live LMDB database | `/var/lib/strfry/db` |
| Daily backups | `/mnt/HC_Volume_105751807/backups/strfry/daily` |
| Weekly backups | `/mnt/HC_Volume_105751807/backups/strfry/weekly` |
| Monthly backups | `/mnt/HC_Volume_105751807/backups/strfry/monthly` |
| Active Caddy configuration | `/etc/caddy/Caddyfile` |
| SSH key-only drop-in | `/etc/ssh/sshd_config.d/00-key-only.conf` |
| Swap sysctl drop-in | `/etc/sysctl.d/99-strfry.conf` |

The deployment bundle contains:

| File | Purpose |
| --- | --- |
| `Dockerfile` | Pinned-source non-root strfry image |
| `compose.yaml` | Runtime isolation, limits, mounts, and health check |
| `strfry.conf` | Relay protocol and resource configuration |
| `write-policy.py` | Write-policy entrypoint: storage guard, Budabit community write control, rate limits |
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

The image is built locally as `budabit/strfry:b80cda3` from:

- Repository: `https://github.com/Pleb5/strfry.git`
- Commit: `b80cda3a812af1b662223edad47eb70b053508b6`
- Alpine base: `3.22`
- Alpine digest: `sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce`

The Docker build clones the exact strfry commit. The build context allowlists
only the Dockerfile and write-policy source, preventing repository metadata,
local databases, environment files, and unrelated untracked files from being
copied into image layers.

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

NIP-42 is completely disabled. Ordinary clients do not receive AUTH
challenges. NIP-70 protected events containing the `-` marker cannot be
accepted and are rejected by strfry.

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

The Budabit stage of the write policy enforces Communikeys V2 grants for the
branches listed in `BUDABIT_BRANCHES` (see `WRITE-CONTROL-PLAN.md`). It is
disabled when that variable is empty; the relay then behaves exactly as
before.

Configuration lives in `compose.yaml` and is read from the shell environment
or an `.env` file next to it:

| Variable | Default | Meaning |
| --- | --- | --- |
| `BUDABIT_BRANCHES` | empty | Comma-separated exact `32222:<owner>:<communityId>` addresses to enforce. |
| `BUDABIT_MODE` | `passthrough` | `passthrough` keeps the public-write relay; `strict` rejects anything not attributable to a hosted community. |
| `BUDABIT_DRY_RUN` | `0` | `1` accepts everything but logs `would_reject` decisions. Use for rollout. |
| `BUDABIT_REJECT_CENSORED_ADDRESSES` | `0` | Reject replacements at addresses censored by an effective event report. |
| `BUDABIT_RECONCILE_SECONDS` | `300` | Background reconcile interval against LMDB. |
| `BUDABIT_AUTO_HOST_URL` | empty | Auto-host: enforce every valid `kind:32222` whose `r` tags name this URL, in addition to `BUDABIT_BRANCHES`. Branches are unhosted again when their current definition stops naming the relay. |

Rollout order for a branch:

1. Verify the definition and every referenced `kind:30000` shard are stored on
   this relay: `docker compose -f deploy/budabit/compose.yaml exec relay
   /usr/local/lib/strfry/write-policy.py --status`. Missing shards are also
   logged as `shard_missing`; republish them from the Budabit admin panel.
2. Set `BUDABIT_BRANCHES` with `BUDABIT_DRY_RUN=1`, restart the relay, and
   watch `would_reject` lines for at least a week. Compare against what the
   Budabit client hides.
3. Set `BUDABIT_DRY_RUN=0` and restart.

Rejections are plain NIP-01 `OK false` replies with stable prefixes:
`blocked:` (policy), `invalid:` (Communikeys grammar), `rate-limited:`,
`error:` (transient; `error: relay policy is loading` during warm-up after a
plugin start or reload). The plugin logs one JSON line per rejection and per
state change (`definition_updated`, `shard_updated`, `report_added`,
`report_deleted`, `definition_deleted`, `shard_deleted`) to the container log.

Health: the compose health check runs `--check-policy`, which fails when a
configured branch has no valid definition on this relay or the loader hit an
error. `--status` prints per-branch state as JSON.

NIP-11: advertise enforcement so clients and auditors can see it. Generate
the value with `write-policy.py --nip11-extra` and paste it into
`relay.info.extra` in `strfry.conf` (a JSON object merged into the relay
information document; generated fields are never overridden). Re-run when
`BUDABIT_BRANCHES` changes. Auto-hosted branches are not listed statically;
the `auto_host` field names the relay URL instead.

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

To roll back, clear `BUDABIT_BRANCHES` and restart. Nothing stored is removed;
the stage only gates new writes.

Deletion requests and NIP-09: strfry treats every `a` tag on a `kind:5` as
a deletion target. Budabit therefore scopes community deletes with `h` only
and never with the marked branch `a` (`Communikeys.md`, "Deletion
Requests"). A client that still sends the old shape will see its
retractions rejected ("can't delete other user's events") or, for the
owner, will tombstone the community definition; publish a fresh definition
with a newer `created_at` to recover.

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

Negentropy is still a public resource surface. Strfry keeps a cached full-DB
tree whose path does not enforce `maxSyncEvents` in the same way as uncached
queries. The 1280 MiB container memory limit is the final isolation boundary if
many clients create concurrent sessions.

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

### Restart

```bash
cd /opt/strfry
sudo docker compose -f deploy/budabit/compose.yaml restart relay
```

### Stop and start

```bash
cd /opt/strfry
sudo docker compose -f deploy/budabit/compose.yaml down
sudo docker compose -f deploy/budabit/compose.yaml up -d --no-build
```

Use `--no-build` for routine starts. `pull_policy: build` intentionally forces
an explicit local build when Compose is allowed to build, preventing an old
cached image from silently being treated as a new deployment.

### Rebuild the pinned image

```bash
cd /opt/strfry
sudo docker compose -f deploy/budabit/compose.yaml build relay
sudo docker compose -f deploy/budabit/compose.yaml up -d --no-build
```

To upgrade strfry, update both `STRFRY_COMMIT` in `Dockerfile` and the image tag
in `compose.yaml`, run the tests, take a backup, and then rebuild. Database
format changes may require logical export/import rather than direct reuse.

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

Logical JSONL is the preferred portable backup. Perform restores during a
maintenance window. Do not start the relay until checksum, zstd stream, import,
and event-count checks have all succeeded.

1. Stop the relay.
2. Move the current database directory aside rather than deleting it.
3. Create a new empty directory owned by UID/GID 10001.
4. Verify the selected checksum and compressed stream.
5. Count and parse all JSON lines before import.
6. Import with the already-built local image, without Compose rebuilding it.
7. Compare the imported database count with the backup line count.
8. Start the relay only after all checks pass.

Example commands:

```bash
restore_strfry() (
set -Eeuo pipefail

cd /opt/strfry
backup=/mnt/HC_Volume_105751807/backups/strfry/daily/events-YYYYMMDDTHHMMSSZ.jsonl.zst
backup_dir=$(dirname "$backup")
backup_name=$(basename "$backup")

cd "$backup_dir"
sudo sha256sum --check "$backup_name.sha256"
sudo zstd --test --quiet "$backup"
expected=$(sudo zstd -dc "$backup" | wc -l)
sudo zstd -dc "$backup" | python3 -c \
  'import collections, json, sys; collections.deque((json.loads(line) for line in sys.stdin), maxlen=0)'

cd /opt/strfry
sudo docker compose -f deploy/budabit/compose.yaml down
sudo mv /var/lib/strfry/db \
  "/var/lib/strfry/db.before-restore-$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -o 10001 -g 10001 -m 0750 /var/lib/strfry/db

sudo bash -o pipefail -c '
  zstd -dc "$1" |
    docker run --rm -i --pull=never \
      -v /var/lib/strfry/db:/var/lib/strfry/db \
      -v /opt/strfry/deploy/budabit/strfry.conf:/etc/strfry.conf:ro \
      budabit/strfry:b80cda3 --config /etc/strfry.conf import
' _ "$backup"

actual=$(sudo docker run --rm --pull=never \
  -v /var/lib/strfry/db:/var/lib/strfry/db \
  -v /opt/strfry/deploy/budabit/strfry.conf:/etc/strfry.conf:ro \
  budabit/strfry:b80cda3 --config /etc/strfry.conf scan --count '{}')
printf 'backup events=%s imported events=%s\n' "$expected" "$actual"
test "$expected" -eq "$actual"

sudo docker compose -f deploy/budabit/compose.yaml up -d --no-build

)
restore_strfry
```

Standard import validates event IDs and signatures. Administrative imports do
not pass through the WebSocket write-policy path and must be treated as trusted
operations. Strfry can log and skip rejected records without failing the entire
import, which is why the post-import count comparison is mandatory.

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

## Verification performed

The following checks passed:

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
to complete NIP-42. NIP-42 is only necessary here for NIP-70 protected event
publishing, which this relay intentionally does not support.

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
because storage is full or unwritable. The deployed health check validates both
the HTTP listener and the policy storage guard.

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
- Set `relay.info.extra` from `write-policy.py --nip11-extra` when enabling
  enforcement, and keep it in sync with `BUDABIT_BRANCHES`.
- The plugin needs no strfry core change; `STRFRY_COMMIT` in `Dockerfile`
  may stay at the deployed revision. Bump it deliberately when taking newer
  upstream fixes, following the upgrade procedure above.
- Regenerate `deploy/budabit/tests/vectors/budabit-policy-vectors.json` from
  Budabit (`POLICY_VECTORS_OUT=... pnpm exec vitest run
  src/app/core/community-policy-vectors.test.ts`) whenever the client's
  permission rules change; the file records the Budabit commit it came from.
- NIP-34 repository events have no special handling: an `h`-tagged
  `kind:30617` needs the Code-curator grant; other repository kinds are
  admitted only when a content section lists them (none do by default) and
  are rejected in strict mode without `h`. Repository collaboration belongs
  on GRASP.
- Add privacy and terms URLs if the relay becomes a community production service.
- Perform a signed publish and read-back test; read and Negentropy tests passed.
- Configure an external HTTPS/WebSocket uptime monitor.
- Send encrypted backups to an off-provider destination; the current backup
  volume is in the same Hetzner project.
- Perform and document a full restore drill after the relay contains events.
- Apply the pending Ubuntu package updates and required reboot, then verify that
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
