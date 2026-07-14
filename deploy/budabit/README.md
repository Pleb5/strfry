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
