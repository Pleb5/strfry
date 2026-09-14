# Budabit relay deployment

This deployment runs a public strfry relay at `relay.budabit.club`, with
Budabit community write enforcement enabled through auto-host discovery.
NIP-42 is disabled and NIP-77 Negentropy is enabled.
COUNT requests are disabled, and REQ filter/subscription limits are reduced to
bound public query resource usage. Negentropy remains public, but uncached
reconciliation is limited to 10,000 events and each connection is limited to 10
concurrent subscriptions.

The relay was initially deployed on 2026-07-13 and upgraded to
`budabit/strfry:2fc1b38` on 2026-09-13. See [RUNBOOK.md](RUNBOOK.md) for current
operations and recovery, and [the September upgrade record](DEPLOYMENT-2026-09-13.md)
for verified checkpoints, the restore-limit and Compose-path failures, and
the safe cutover procedure. A later documentation commit or rebase does not
change the image already running on the VPS.

**2026-09-14 follow-up:** a live outsider write exposed an auto-host initialization
race in the deployed `2fc1b38` policy. Warm-state thread rejection was separately
verified. The source now has a readiness barrier and cold-ingestion regression
tests; no replacement VPS image is recorded as deployed yet. See
[INCIDENT-2026-09-14.md](INCIDENT-2026-09-14.md).

## Host layout

- Existing deployment bundle (**not a Git checkout**): `/opt/strfry`
- Separately staged source: `/opt/strfry-releases/2fc1b38`
- Live LMDB database: `/var/lib/strfry/db`
- Logical backups: `/mnt/HC_Volume_105751807/backups/strfry`
- Relay listener: `127.0.0.1:7777`
- Public TLS and WebSocket endpoint: Caddy on ports 80 and 443

Before starting Compose, create the database directory with UID/GID 10001:

```sh
sudo install -d -o 10001 -g 10001 -m 0750 /var/lib/strfry/db
```

The write-policy plugin applies per-pubkey, per-source, and global token
buckets. It rejects writes when `data.mdb` reaches 10 GiB or when the database
filesystem has less than 25 GiB available.

## Community write control

The plugin is a pipeline (`policy/`): storage guard, then Budabit community
write control, then rate limits. The Budabit stage is inert only when both
`BUDABIT_BRANCHES` and `BUDABIT_AUTO_HOST_URL` are empty. Explicit branches use
exact `32222:<owner>:<communityId>` definition addresses; auto-host discovers
valid definitions whose `r` tags name the configured relay. For hosted branches
it rejects community-scoped writes the Budabit client would not admit:
authors without a current section grant,
effectively person-banned authors, and structurally invalid Communikeys V2
events. Everything else passes through (or is rejected in `BUDABIT_MODE=strict`).

The live deployment uses `BUDABIT_AUTO_HOST_URL=wss://relay.budabit.club`,
empty `BUDABIT_BRANCHES`, `BUDABIT_MODE=passthrough`, and `BUDABIT_DRY_RUN=0`.
BudaBit and Test were both discovered and warm at cutover. New community
creation does not require a grant in an existing community. Only valid
qualifying definitions enroll a new branch; unrelated or malformed new
definitions do not activate hosting. Normal signature, storage, and rate
checks still apply to passthrough traffic.

While enabled, the stage rejects kind-5 requests tagging 32222/30000 through `k`
or `a`, including owner requests and unhosted coordinates. E-only targets currently
held as definitions/shards are protected too. The reply is
`blocked: Deletion of kinds 32222 and 30000 is not allowed`. Update definitions
and lists instead; report-only retractions remain allowed. Unknown e-only ids
cannot be classified without a lookup and are not blocked by this tag rule.

State is rebuilt from the relay's own LMDB with `strfry scan` at start and
every `BUDABIT_RECONCILE_SECONDS`, and updated inline from accepted authority
events. This reduces same-relay grant races; newly auto-hosted branches can
still need warm-up, and cross-relay shard delivery can lag. Check `--status`
for the expected exact addresses, valid/warm definitions, and missing shards:
`--check-policy` can succeed with no auto-discovered branches and does not
guarantee that every referenced permission list is present.

In the fixed source, every plugin instance temporarily rejects new writes until
its complete initial load succeeds, even in dry-run. Initial failures retry with
bounded backoff; unrelated passthrough and normal bootstrap resume after loading.
Known protected deletions retain their permanent denial. This adds no per-event
DB scan and does not change the best-effort freshness of subsequent reconciliation.
The core still launches the persistent plugin lazily; separate health commands
do not initialize that ingestion process.

Deletion history is loaded only through scoped scans, not full per-author
kind:5 scans. Missing inline definitions/shards are remembered after a 60 s
grace so replays cannot repeatedly restore grants. After restart, an unobserved
e-only deletion leaves a temporary authorization window until reconcile;
see the [deletion replay trade-off](RUNBOOK.md#deletion-replay-trade-off).

Conformance with the Budabit client is pinned by golden vectors exported
from the client's own permission code (`tests/vectors/`). `audit.py` replays
stored content against the rules; `sweep.py` deletes what an audit lists.
Neither a historical audit/sweep nor a mandatory dry-run period was a
prerequisite for the September cutover. Client render-time admission remains
required; write enforcement does not curate historical reads or imports.

`write-policy.py --nip11-extra` produces the NIP-11 advertisement.
It includes `dry_run`/`enforcing` state and `protected_deletion_kinds`.
Budabit's `community-policy-conformance.json` pins the fixture for its CI drift
check; the no-deletion restriction has separate relay tests.

Design, semantics, and rollout: [WRITE-CONTROL-PLAN.md](WRITE-CONTROL-PLAN.md).
Operations: [RUNBOOK.md](RUNBOOK.md#community-write-control).

Tests (from the repository root):

```sh
python3 -m unittest discover -s deploy/budabit/tests -t deploy/budabit/tests -p 'test_*.py'
node test/tests/budabitPolicyTest.js   # needs ./strfry and test/node_modules
node test/tests/budabitStartupTest.js # actual cold auto-host / reload / failure recovery
node test/tests/authMaxAgeTest.js     # dedicated AUTH age and normal-event regression
node test/tests/budabitReadProjectionTest.js # projection only; not C++ read enforcement
```

## Private reader projection (preparatory, not a serving gate)

`BUDABIT_READ_CONTROL=members` enables a separate committed reader projection,
requiring one explicit branch, no auto-hosting, the live loader, and non-dry-run
write enforcement. `off` is the default. **The Python flag alone does not restrict
relay reads.** Enable it only with the matching C++ gate once that gate is available.

The parent initializes the JSONL plugin with a fresh `read-control-init` record
containing `epoch`, `seq`, and `branch_address`, then sends response-less
`committed` records after storage transactions. Accepted authority writes carry
optional `policyRelevant:true`. No reader snapshot appears on stdout.

Snapshots are atomically replaced at `BUDABIT_READ_SNAPSHOT_PATH` (default next to
`STRFRY_POLICY_DB_FILE`, named `budabit-readers.json`); sibling `.status.json`
contains health/counts, not the roster. Both are operator-private mode-0600 files.
The reader set is rebuilt from a fresh Branch, never speculative accepted writes.
Defaults: `BUDABIT_READ_MAX_PUBKEYS=20000`, `BUDABIT_READ_MAX_SNAPSHOT_BYTES=2097152`,
`BUDABIT_READ_SCAN_MAX_BYTES=33554432`, `BUDABIT_READ_SCAN_TIMEOUT_SECONDS=30`.
Byte/time budgets cover an entire rebuild; malformed/incomplete/oversize scans
produce unavailable snapshots, not partial lists. The worker refreshes snapshots
once per second while idle and cannot heartbeat through a blocked scan.

`write-policy.py --check-read-policy [EPOCH SEQ]` validates a recent local snapshot.
Without a serving epoch/sequence, this is **not proof of live C++ enforcement**.
The serving gate must independently enforce active epoch, pending sequence,
pre-commit invalidation, monotonic liveness, and final-send authorization.

## Retention

Normal events older than 365 days are deleted monthly. Current replaceable
events, including kinds 0, 3, and 41 and the replaceable ranges, are preserved.
Kinds 1984 (reports) and 5 (including report retractions) are also preserved,
because old moderation evidence may still determine current authority. Dry runs
and applied runs report how many replaceable/policy events were excluded.
Ephemeral and NIP-40 expiration events are handled by strfry itself.

When NIP-42 is enabled, `relay.auth.maxAgeSeconds` defaults to 600 to allow human
signer approval. Ordinary ephemeral events still use their separate 60-second
cutoff; the configured future-skew bound applies to AUTH too. This does not
enable AUTH or private reads in the current deployment template.

LMDB does not return freed pages to the filesystem automatically. If pruning
deletes substantial data, stop the relay and run a planned compaction before
the 10 GiB storage guard is reached.

## Backups

The daily job creates a zstd-compressed JSONL export and SHA-256 sidecar. Daily
snapshots are retained for 7 days, weekly snapshots for 35 days, and monthly
snapshots for 190 days. The backup script refuses to run if the Hetzner volume
is not mounted, preventing accidental writes to the root filesystem. Stream and
checksum verification are separate manual or monitoring steps.

The September restore drill recovered all 4,427 backup records in an isolated
database. Historical records exceeded current write limits, so only the
import configuration used higher size/tag limits; signature verification stayed
enabled and production limits were unchanged. A separate native checkpoint was
taken with the live relay stopped immediately before cutover. See the
[upgrade record](DEPLOYMENT-2026-09-13.md#backup-and-recovery-assets).

These backups remain in the same Hetzner project and are not a replacement for
an off-provider backup once the relay is considered production data.
