# Relay deployment: recipient-only gift-wrap reads (2026-11-19)

The operator ran every server command in an interactive SSH session, following
coordinator-relayed instructions. The agent made no remote changes. Requested in
operator DM `7012ac01…`; the deployment was started from operator DMs `9f71082c…` and
`d5be8d82…`.

## Deployed source and image

| Item | Value |
| --- | --- |
| Host | Hetzner `blossom-grasp` (`relay.budabit.club` → 178.104.89.98), shared with Blossom/GRASP |
| Source | `Pleb5/strfry` branch `deploy/giftwrap-reads-on-dm-rate-budget` at `78f7ca914c022df58ecac95e4752ca5f1acc4eb6` |
| Source composition | Merge `3313711e` of the previously live `feat/dm-rate-budget` (`53c711c`, first parent) with `feat/giftwrap-recipient-reads` (`2562d6ec`; `src/` identical to it), plus `78f7ca91` (Dockerfile `BUILD_JOBS` argument, no source change) |
| Staged source | `/opt/strfry-releases/78f7ca914c02` (fresh clone, submodules initialized) |
| Build | `docker build -f deploy/budabit/Dockerfile --build-arg SOURCE_REVISION=<full sha> --build-arg BUILD_JOBS=1 -t budabit/strfry:78f7ca914c02 .` (log in `/opt/strfry-upgrade/78f7ca914c02/build.log`) |
| Image | `budabit/strfry:78f7ca914c02`, `sha256:f6a48ca55a82e11438ff2d368c9972b5df2723333108a9a71150f4f61a69e911` |
| Previous image (rollback) | `budabit/strfry:53c711c`, `sha256:a774c11bbfce89d513621c9a512fc7a20d8add814a03f30ace4c94811f781a9d`, kept |

## Activation (as reported by the operator)

- **Compose file.** `/opt/strfry/deploy/budabit/compose.yaml` (project `budabit-strfry`).
  The operator backed it up first, then changed only `image:` from
  `budabit/strfry:53c711c` to `budabit/strfry:78f7ca914c02`.
- **Unchanged:**
  - `strfry.conf`: AUTH enabled, `serviceUrl = "wss://relay.budabit.club"`,
    `restrictedReadKinds = "4,4444"`; no `readPolicy` block, so read admission is off;
    negentropy off.
  - Rate-limit environment: per pubkey 60/60, per IP 1000/4000, global 1000/4000.
  - The `/var/lib/strfry/db` mount and the backups.
- **Recreate command:**
  `docker compose -p budabit-strfry --project-directory /opt/strfry/deploy/budabit -f /opt/strfry/deploy/budabit/compose.yaml up -d --no-build --pull never --no-deps --force-recreate --wait --wait-timeout 180 relay`.
  The container was healthy after 8.1 s. 20 s later it was running and healthy, with 0
  restarts and the expected image hash.

## Verification (read-only, from outside, 2026-11-19T20:48Z)

No events were published and no signer was unlocked. Only response types, counts and
kinds were recorded.

| Check | Observed |
| --- | --- |
| Public NIP-11 | `supported_nips` `[1,2,4,9,11,28,40,42,59]` |
| Unauthenticated REQ: kind 21059, `#p` | AUTH challenge, then `CLOSED auth-required: gift wraps are readable only by their authenticated #p recipient`; no events |
| Same connection: kind 1059, another `#p` | `CLOSED auth-required`, **no second challenge** (1 AUTH frame on the connection) |
| Kind 1059 without `#p` | `CLOSED restricted: gift-wrap filters (kinds 1059/21059) must list their recipients in #p` |
| Mixed `25910 + 1059 + 21059` | Whole REQ refused with `auth-required` |
| Kind-less `#p` queries (two keys) | EOSE with 0 events, while the next row shows stored wraps exist for one of them |
| Public kind 1 | 3 events + EOSE |
| COUNT | `COUNT disabled` (live configuration) |
| AUTH as active alias `five`, gift wraps `#p`=five | 50 events (limit), all kind 1059, all `p`-tagged to five |
| AUTH as `five`, gift wraps `#p`=another key | Refused with `auth-required`, 0 events |

**Not verified:**

- live push of new gift wraps (would need test events);
- stored kind 21059 (none observed);
- negentropy (disabled live) and gift-wrap COUNT (COUNT disabled);
- the per-event check for authenticated kind-less queries;
- the random-outer-author exclusion (not testable without a wrap's one-time key; source
  only);
- Freedom Builders phone DMs and speech after the cutover (operator to check);
- other clients. The ContextVM TS SDK lacks NIP-42 support and is expected to break here.

## Rollback

Restore the compose backup (or set `image:` back to `budabit/strfry:53c711c`) and run the
same recreate command. The database and configuration were not changed. After rollback,
gift wraps are publicly readable again.
