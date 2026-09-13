# Budabit relay deployment

This deployment runs an empty public-write strfry relay at
`relay.budabit.club`. NIP-42 is disabled and NIP-77 Negentropy is enabled.
COUNT requests are disabled, and REQ filter/subscription limits are reduced to
bound public query resource usage. Negentropy remains public, but uncached
reconciliation is limited to 10,000 events and each connection is limited to 10
concurrent subscriptions.

The relay was deployed and verified on 2026-07-13. See [RUNBOOK.md](RUNBOOK.md)
for the deployment record, operating procedures, recovery steps, lessons
learned, and outstanding work.

## Host layout

- Repository and deployment: `/opt/strfry`
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
write control, then rate limits. The Budabit stage is inert until
`BUDABIT_BRANCHES` names one or more exact `32222:<owner>:<communityId>`
definition addresses. For those branches it rejects community-scoped writes
the Budabit client would not admit: authors without a current section grant,
effectively person-banned authors, and structurally invalid Communikeys V2
events. Everything else passes through (or is rejected in `BUDABIT_MODE=strict`).

State is rebuilt from the relay's own LMDB with `strfry scan` at start and
every `BUDABIT_RECONCILE_SECONDS`, and updated inline from every accepted
definition, profile-list shard, report, and deletion, so a grant published to
this relay takes effect for the very next write.

Design, semantics, and rollout: [WRITE-CONTROL-PLAN.md](WRITE-CONTROL-PLAN.md).
Operations: [RUNBOOK.md](RUNBOOK.md#community-write-control).

Tests (from the repository root):

```sh
python3 -m unittest discover -s deploy/budabit/tests -t deploy/budabit/tests -p 'test_*.py'
node test/tests/budabitPolicyTest.js   # needs ./strfry and test/node_modules
```

## Retention

Normal events older than 365 days are deleted monthly. Current replaceable
events, including kinds 0, 3, and 41 and the replaceable ranges, are preserved.
Ephemeral and NIP-40 expiration events are handled by strfry itself.

LMDB does not return freed pages to the filesystem automatically. If pruning
deletes substantial data, stop the relay and run a planned compaction before
the 10 GiB storage guard is reached.

## Backups

The daily job creates a zstd-compressed JSONL export and SHA-256 sidecar. Daily
snapshots are retained for 7 days, weekly snapshots for 35 days, and monthly
snapshots for 190 days. The backup script refuses to run if the Hetzner volume
is not mounted, preventing accidental writes to the root filesystem. Stream and
checksum verification are separate manual or monitoring steps.

These backups remain in the same Hetzner project and are not a replacement for
an off-provider backup once the relay is considered production data.
