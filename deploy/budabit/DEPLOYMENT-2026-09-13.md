# Budabit relay upgrade — 2026-09-13

This is the record of the completed upgrade of `wss://relay.budabit.club`.
The operator ran the VPS commands and supplied their output; the coding agent
did not log into or deploy to the VPS. See [RUNBOOK.md](RUNBOOK.md) for ongoing
operations. Paths and counts here are a dated snapshot, not discovery defaults
for a different host or a future upgrade.

## Deployed result

| Item | Verified state |
| --- | --- |
| Host | `ocmux`, shared 2-vCPU / 3.7-GiB VPS |
| Compose project / service | `budabit-strfry` / `relay` |
| Container | `budabit-strfry-relay-1` |
| New local image | `budabit/strfry:2fc1b38` |
| Source and core build argument | `2fc1b38e27cfb86775a4ccbe3190019d37e63a9f` |
| Reported binary | `v607-2fc1b38` |
| Previous image | `budabit/strfry:b80cda3` |
| Previous image ID | `sha256:cf7de4fd9fafa2db8c51d65d5c00d3c29a83ccb13d8b59cd0dbf0dafd028cc9c` |
| Post-cutover health | Running, healthy, zero container restarts |
| Listener | `127.0.0.1:7777` on the host; public TLS remains with Caddy |
| Database | Existing `/var/lib/strfry/db` retained; no logical restore promoted |
| Maintenance | Backup and retention timers restored to their original active states |

The cutover directory was created at `2026-09-13T21:15:06Z`. Compose reported
the recreated container healthy in 6.8 seconds; that is **not** a measurement of
the entire outage, which also included the stopped-database checkpoint.

Only `/opt/strfry/deploy/budabit/compose.yaml` and `strfry.conf` were replaced.
The deployment bundle, old imports/logs, database, and existing backups were
preserved. Blossom (`blossom-server-blossom-1`), Caddy, and other services were
not reconfigured or restarted by the cutover.

### Policy selected

```env
BUDABIT_AUTO_HOST_URL=wss://relay.budabit.club
BUDABIT_BRANCHES=
BUDABIT_MODE=passthrough
BUDABIT_DRY_RUN=0
BUDABIT_DISABLE_LOADER=0
```

This is automatic hosting, **not** a two-community allowlist. A valid kind-32222
definition naming this relay in its `r` tags can establish a new hosted branch
without a grant in an existing community. Malformed/non-hosting new definitions
do not enroll a branch and follow ordinary passthrough checks; invalid updates
to an already hosted definition are rejected. Auto-hosting can be removed when
the current definition stops naming this relay. All writes still undergo normal
signature validation and storage/rate controls.

Kind-5 requests with a `k` tag naming 30000/32222, an `a` coordinate of either
kind, or a currently tracked definition/shard `e` target are rejected whole,
including owner requests and mixed requests. This also covers explicitly tagged
unhosted kind-30000 lists/moderator requests. The reply is exactly:

```text
blocked: Deletion of kinds 32222 and 30000 is not allowed
```

Report-only kind-1984 retractions remain allowed. Unknown e-only IDs cannot be
classified by this guard. Existing/imported tombstones and the accepted
[deletion-replay residual risk](RUNBOOK.md#deletion-replay-trade-off) remain as
previously documented; this upgrade did not reopen that risk as a release gate.
Client current-grant/render-time admission remains required. Write enforcement
does not curate historical reads/imports, and no historical sweep was required
or performed. Owner-signed enforcement declarations and dropping client
admission remain out of scope.

### Authority state observed

Both exact addresses had `auto: true`, `warm: true`, `definition: true`, and no
missing referenced permission lists in the isolated restore and after cutover:

```text
BudaBit
32222:0a8ecba4868c13e1e84cc5cb58c02c1fd0880d9e5a25a5050ee96ad8a166d7c8:0a8ecba4868c13e1e84cc5cb58c02c1fd0880d9e5a25a5050ee96ad8a166d7c8

Test
32222:d04ecf33a303a59852fdb681ed8b412201ba85d8d2199aec73cb62681d62aa90:a1ac6907e956ecdbd94a9913c89897b8c5c77fcb8ca69470ddfda120e2ef1eda
```

BudaBit had seven permission lists with these section-grant counts:

| Section | Grant entries |
| --- | ---: |
| General | 18 |
| Room-creator | 8 |
| Thread-creator | 10 |
| Calendar | 7 |
| Fundraisers | 5 |
| Repositories | 6 |
| Widget-curator | 6 |

The total is 60 **section-grant entries**, not necessarily 60 distinct people.
Test referenced no permission lists and had zero grants in all seven sections;
that was not a loading failure. Its section-gated content remains owner-only
until authority/grants are configured. These counts are observations, not
permanent policy limits or future health-check expectations.

### Public NIP-11

The real isolated HTTP response, live localhost response, and public HTTPS
response all returned the intended policy:

```json
{
  "budabit": {
    "policy_version": "1",
    "mode": "passthrough",
    "dry_run": false,
    "enforcing": true,
    "protected_deletion_kinds": [30000, 32222],
    "auto_host": "wss://relay.budabit.club"
  }
}
```

The payload advertises the dynamic auto-host rule, not frozen
`configured_branches`/`enforced_branches` arrays. It was JSON-encoded into the
configuration string `relay.info.extra` without changing other relay limits.
If an existing `extra` field is present, merge/review it rather than overwriting
unrelated metadata. NIP-11 is a relay claim, not signed community authority.

`BUDABIT_DISABLE_LOADER=1` was used only for the one-shot metadata generator
without a DB. It was **not** used for discovery tests or the live service.

## Backup and recovery assets

All checkpoint directories were created root-private with `umask 077`, outside
the rotating daily/weekly/monthly tiers, on the verified mounted backup volume.
Do not commit exports, native databases, resolved Compose files, or environment
values to Git. Only non-secret metadata belongs in this record.

### Pre-upgrade logical checkpoint

```text
/mnt/HC_Volume_105751807/backups/strfry/pre-upgrade-20260913T185856Z-X2ykXc
```

Contains `image.txt`, a complete `deployment.tar.gz`, `events.jsonl.zst`, and
`SHA256SUMS`. All three manifest entries, the zstd stream, deployment archive,
and all **4,427 JSON records** were verified. The new image restored the full
record count in an isolated DB with signature verification enabled.

### Successful cutover checkpoint

```text
/mnt/HC_Volume_105751807/backups/strfry/cutover-20260913T211506Z-rH8sTo
```

| File | Purpose |
| --- | --- |
| `deployment.tar.gz` | Complete original `/opt/strfry` bundle |
| `native-db.tar` | Native DB archive captured only after stopping the relay |
| `compose.before.yaml`, `strfry.before.conf` | Original live files for image/configuration rollback |
| `images.txt` | Actual old/new local image IDs, deployed revision, retained rollback tag |
| `SHA256SUMS` | Verified checksums for the five files above |
| `compose.before.resolved.json`, `compose.next.resolved.json` | Root-private, normalized deployment comparison |
| `timers-to-resume.txt` | Which of the two relay timers were originally active |
| `status.after.json` | Post-cutover exact branch/authority state |
| `nip11.after.json`, `nip11.public.json` | Local and public post-cutover HTTP evidence |

`native-db.tar` was compared against the still-stopped source with
`tar --compare`, then the manifest was checked and the backup filesystem synced
before installing either candidate file. No new binary touched the production DB
before this checkpoint. Both old/new revisions use database version 3, and the
new image successfully opened the retained native DB at cutover.

The old local image was additionally tagged:

```text
budabit/strfry:rollback-cutover-20260913T211506Z-rH8sTo
```

This tag retains the local image; it is not an off-host image archive or a
registry publication. Use `images.txt` to identify the actual image objects;
do not assume a later rebuild is byte-identical.

### Staging and failed attempts are not recovery checkpoints

- Published source: `/opt/strfry-releases/2fc1b38` (detached exact revision).
  The VPS-only recipe is `deploy/budabit/Dockerfile.vps`; build output is in
  `image-build.log`.
- Candidate files and isolated smoke-test evidence:
  `/opt/strfry-upgrade/2fc1b38`. `compose.before-mount-fix.yaml` preserves the
  bad candidate for diagnosis; the corrected candidate uses exact live paths.
- Successful isolated restore: `/var/lib/strfry-upgrade-check.7fYYUx`.
- Failed partial restore: `/var/lib/strfry-upgrade-check.lj2AwQ` (246 events).
- Failed cutover preflight:
  `/mnt/HC_Volume_105751807/backups/strfry/cutover-20260913T210655Z-dCrmLw`.
  It contains only the resolved-configuration evidence from the failed guard,
  **not** a native checkpoint. The guard failed before pausing timers, stopping
  the relay, backing up the bundle, or replacing live files.

Nothing in these failed/scratch directories was promoted. They were not deleted
as part of the deployment; their existence alone does not prove a backup is valid.

## Lessons and failure modes

### Build beside the deployment, not over it

`/opt/strfry` is an existing deployment bundle, **not a Git checkout**. It contains
customized limits and earlier files such as `old-events.jsonl`,
`native-import.log`, and pre-limits/pre-migration configuration backups. Preserve
that directory rather than cloning over it, resetting it, or copying an entire
new release over it.

The published source was fetched separately and all 155 Python policy tests
passed on the VPS before building. The shared host had roughly 2.4 GiB available
RAM and 46 GiB free on the root filesystem before the build. To limit contention,
the staged Dockerfile copy used `make -j1 NPROC=1` instead of `make -j2`. This was
a conservative host-specific choice, not a general strfry build requirement.

Renaming a Dockerfile also changes the applicable Dockerfile-specific ignore
filename. Copy `Dockerfile.dockerignore` to `Dockerfile.vps.dockerignore` when
using that recipe; otherwise the restrictive build-context allowlist can be lost.
Match the policy build context to the selected published source as well as
passing `STRFRY_COMMIT`: the core is cloned by the build, while the Python policy
is copied from the local build context.

The deployment templates still default to `b80cda3`. The VPS build explicitly
selected `2fc1b38e27cfb86775a4ccbe3190019d37e63a9f`; promotion selected the new
local image with `pull_policy: never`, removed the build block, and used
`--no-build --pull never`. A successful source build or policy test does not
by itself change the live container. Docker's `docker.io/...` tagging output
also does not imply an image was pushed to a registry.

### Historical records can exceed current write limits

The first isolated import stopped on line 247 after committing 246 records:

```text
strfry error: Line larger than configured maxEventSize on line 247
```

This was not a community-policy rejection or evidence of a corrupt backup.
The importer applies its config limits and counts the JSONL newline too.
Historical stored records exceeded today's admission limits in three ways:

| Measurement / setting | Production limit | Backup maximum | Import-only limit |
| --- | ---: | ---: | ---: |
| JSONL line bytes / `maxEventSize` | 65,536 | 210,433 | 262,144 |
| Tags per event / `maxNumTags` | 2,000 | 2,683 | 3,000 |
| UTF-8 tag-value bytes / `maxTagValSize` | 1,024 | 3,300 | 4,096 |

The largest line was number 3,451. Measure **the selected backup**, rather than
assuming these import-only limits will fit all future backups. For example,
in a root shell, this prints statistics only and does not open the live DB:

```bash
(
  set -euo pipefail
  backup=/mnt/HC_Volume_105751807/backups/strfry/pre-upgrade-20260913T185856Z-X2ykXc/events.jsonl.zst
  zstd -dc "$backup" | python3 -c '
import json, sys
stats = {"records": 0, "largest_line_bytes": 0, "largest_line_number": 0,
         "max_tags_per_event": 0, "max_tag_value_bytes": 0}
for number, raw in enumerate(sys.stdin.buffer, 1):
    event = json.loads(raw)
    tags = event.get("tags", [])
    stats["records"] += 1
    if len(raw) > stats["largest_line_bytes"]:
        stats["largest_line_bytes"] = len(raw)
        stats["largest_line_number"] = number
    stats["max_tags_per_event"] = max(stats["max_tags_per_event"], len(tags))
    largest_tag = max((len(v.encode("utf-8")) for t in tags for v in t
                       if isinstance(v, str)), default=0)
    stats["max_tag_value_bytes"] = max(stats["max_tag_value_bytes"], largest_tag)
print(json.dumps(stats, indent=2))
'
)
```

The successful retry used a **fresh scratch DB** and a separate import-only
config. No `--no-verify` flag was used; no records were edited or filtered to
make the count pass. Import diagnostics and the 4,427-event count were checked.
Count, loader, and HTTP checks then used the original production-limit config.
Production limits were never raised. An importer can commit a partial result
before failing, or skip some rejected records without a failing exit code;
neither exit status alone nor a valid zstd stream proves a complete restore.

### A read command is not a safe new-binary test of the live DB

`strfry scan` performs a logical read, but binary startup opens the LMDB
environment and can check/write metadata or set up indexes. Do not rely on the
command name, or a read-only bind mount, as a compatibility boundary for a new
binary. Use a logical restore in a separate DB first. Do not copy a live
`data.mdb` and label it a consistent native checkpoint.

Isolated containers used `--network=none`, no published ports, a read-only root
filesystem, UID/GID 10001, dropped capabilities, no-new-privileges, bounded
memory/CPU/PIDs, and a 16 MiB `/tmp` tmpfs. The DB was a separate on-disk bind
mount, not that tmpfs. The host decompressed the root-private backup into stdin;
the non-root container did not need access to the backup directory. No isolated
container mounted the live database. Temporary HTTP listeners were reached only
through `docker exec` inside the isolated container, then stopped and removed.

### Compose validity does not prove deployment equivalence

The initial candidate was derived using `config --no-interpolate --no-normalize
--format json`. Those flags did **not** prevent path resolution. Retaining the
variable expression and later resolving the candidate produced:

| Mount | Live source | Bad candidate source |
| --- | --- | --- |
| DB | `/var/lib/strfry/db` | `/opt/strfry/deploy/budabit//var/lib/strfry/db` |
| Config | `/opt/strfry/deploy/budabit/strfry.conf` | `/opt/strfry/deploy/budabit/./strfry.conf` |

The config path was equivalent, but the DB path was wrong. `compose config
--quiet` had passed because the configuration was syntactically valid. The
pre-cutover semantic guard rejected the bind sources **before any downtime**.

The fix preserved the previous candidate and pinned its two bind sources to
the exact absolute paths already mounted in the running container. This
deliberately removed mount-path interpolation in the deployed candidate; it did
not change any policy rate/storage settings. Missing `read_only` in normalized
Compose means false, not a mismatch with the writable DB mount.

For any candidate, resolve both old and new configurations with the same explicit
`--project-directory` and project name. Compare services, bind types/sources/
targets/read-only flags, localhost port publication, isolation, limits, and all
environment values. Allow only reviewed changes (here: image, pull/build policy,
five Budabit environment settings, and health check). Check actual running
container mounts as well. Do not weaken a failed assertion to get to cutover.
Resolved configuration may contain secrets: store it root-private and print
only the needed metadata, not whole configurations or environment dumps.

### Green policy health can mean zero hosted communities

`--status`, `--check-policy`, and `--nip11-extra` perform synchronous loader
warm-up when enabled. In auto-host mode, however, `--check-policy` can succeed
with no discovered branches. Missing shards can also yield a warm branch with
fewer grants rather than failing health. Require the known exact addresses and
inspect `auto`, `warm`, `definition`, and every shard's presence explicitly.
Allow legitimately newly created communities in addition to the expected set;
do not turn a deployment assertion into an unintended two-community allowlist.

### Rollback is not a database restore

Capture the old image ID and retain a distinct tag before recreation. Record
timer states, stop only the two relay timers, then check their services are idle;
stopping a timer does not stop an already-running backup or retention job. Abort
without interrupting an active job. After stopping the relay, archive/compare the
native DB and verify the checkpoint before replacing live files.

The cutover shell handler was prepared to restore the two original configuration
files and recreate the old image with `--no-build --pull never --no-deps
--force-recreate --wait`. It would keep the current DB, preserving any writes
accepted after cutover, and leave maintenance timers paused if recovery failed.
That rollback path was not exercised by the successful live cutover. It was a
shell exit handler, not ongoing automatic rollback protection.

For this version pair the native format is compatible. Do not generalize that
to future upgrades. Restoring `native-db.tar` is a separate, explicitly planned
recovery operation that can discard later writes. Preserve displaced data;
never extract it over an open live database. A verified event-only export and a
native checkpoint are complementary, not interchangeable recovery artifacts.

## Repeatable upgrade sequence

1. Inventory the running image ID, project/service, actual mounts, localhost
   binding, isolation, free disk/RAM, and maintenance timers. Preserve unrelated
   workloads. Treat the server deployment bundle separately from source.
2. Create a dedicated non-rotating logical checkpoint, verify checksums, compressed
   stream, archive, and all JSON records. Keep backups and resolved configuration
   root-private. Verify the backup volume is actually mounted.
3. Fetch exact published source separately; test and build under a new tag with
   explicit core revision and matching policy context. Preserve the old image.
4. Restore into a fresh isolated disk-backed DB. Measure historical limits and
   use import-only overrides if required. Keep ID/signature verification on;
   require full-count recovery and inspect import diagnostics.
5. With original production limits, verify exact auto-host branches and shards,
   storage/policy health, and real HTTP NIP-11. A JSON generator or empty healthy
   auto-host state is not sufficient evidence.
6. Have the client rejection/retry UI ready. Preserve the existing live settings
   in separately staged Compose/config candidates. Validate their fully resolved
   meaning, especially paths, and advertise the dynamic auto-host rule. No
   mandatory week-long dry run or historical-content cleanup was required.
7. In one guarded maintenance block, preserve the old bundle/configuration and
   image; record/pause the relay timers; require their services to be idle; stop
   only the relay; take, compare, checksum, and sync the native checkpoint.
8. Replace only the reviewed Compose/config files. Recreate the relay (not merely
   restart it), keeping the existing DB and forbidding builds/pulls. Wait for
   health; verify actual image ID, NIP-11, and the expected exact hosted branches.
   On failure, attempt the planned compatible image/configuration rollback.
9. Restore only the timers that were previously active. Check public HTTPS with
   normal certificate verification, current health/restart count, and each timer
   against its recorded original state. Preserve recovery artifacts and record
   the result. Do not rotate backups, clean scratch directories, or publish/delete
   live test events as an implicit final step.

The tested recreation command was scoped to the existing project and service:

```bash
# Activation only: run after the checkpoint and maintenance guards above.
docker compose --project-directory /opt/strfry/deploy/budabit \
  -p budabit-strfry -f /opt/strfry/deploy/budabit/compose.yaml \
  up -d --no-build --pull never --no-deps \
  --force-recreate --wait --wait-timeout 180 relay
```

## Verification and scope

Before activation, all 155 Python relay tests passed locally and on the VPS.
Local isolated signed WebSocket/NIP-11 integration covered protected deletions,
mixed-request rejection, and report retractions. The separate VPS logical
restore recovered all 4,427 records, discovered both expected communities, and
passed storage/policy/HTTP checks with original production limits.

At live cutover, the exact new image ID matched the selected local image; the
native checkpoint manifest passed; the recreated service was healthy; both
communities were valid/warm with no missing referenced lists; and the localhost
NIP-11 payload matched the isolated test. Public HTTPS subsequently matched the
binary and policy payload with normal TLS verification. That public-route check
was run from the VPS, not an independent external-network probe. The container
had zero restarts and both maintenance timers were active as originally recorded.

The operator confirmed the updated Budabit client was ready before activation.
The agent did not deploy that frontend or independently verify its production
release. No live signed publish/read-back, cleanup, or historical sweep was
performed in this maintenance session. Those are not implied by HTTP/health
success; perform live signed tests only with separate authorization.

## Source publication and client CI lessons

- The deployed strfry commit is also Budabit's immutable conformance pin in
  `community-policy-conformance.json`. A docs commit or branch rebase must not
  silently change the deployment record or that pin. Keep pinned commits
  reachable before any history rewrite, and verify remote refs after pushing.
- `.github/workflows/docker-build.yml` currently runs on every push and publishes
  `ghcr.io/pleb5/strfry:latest`. The deployment commit and this documentation
  follow-up use `[skip ci]` to avoid an unrelated mutable-image publication.
  Do not treat a documentation push as permission to deploy or publish Docker
  images. Narrowing that workflow is a separate future change.
- Budabit's conformance gate compares fresh vectors against the published pin,
  ignoring only top-level `generatedAt` and `budabitCommit` for semantic equality
  while validating that metadata separately. It also tests the pinned Python
  policy against the fresh export. Protected deletion behavior has separate
  relay tests; it is an additional operator restriction.
- CI initially failed on an unavailable optional Kanban commit. Both affected
  workflows now avoid automatic recursive checkout, explicitly initialize only
  `packages/flotilla-extension-template`, and exclude Kanban from CI Vitest
  project loading while leaving it available locally.
- A missing submodule object can be a **wrong remote**, not a missing commit.
  Template commit `6eb148a91696a444316c771265e5112122ab6e3f` exists on
  `Pleb5/flotilla-extension-template`; `.gitmodules` had named the chebizarro fork.
  Keep the required template in CI and correct its remote rather than deleting
  it or arbitrarily changing the pin. Clean CI also needs `svelte-kit sync` before
  loading the conformance test.
- The green Budabit conformance run for
  `dbf0ca551f7e7f13a09528c19abce4b91a194ca8` is
  [run 34775822870](https://github.com/Pleb5/flotilla-budabit/actions/runs/34775822870).
  This validates that workflow, not a fresh full E2E run. Client relay-error UI
  work was introduced in `70fa44f94b44e5cbeff2473327efa9e93a1d8455`.
