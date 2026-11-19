# Recipient-only gift-wrap reads (NIP-59 kinds 1059 and 21059)

Requested by the operator on 2026-11-19 (DM `7012ac01…`): make gift-wrap retrieval on
`wss://relay.budabit.club` recipient-only. This is a source change. It has not been
compiled, tested or deployed yet; see [Verification limits](#verification-limits).

## Behaviour

A gift wrap is delivered only to a connection that has authenticated with NIP-42 as a
pubkey named in one of the event's `p` tags. The outer author is a random one-time key
and gets no access unless it is also a `p`-tag recipient. This holds independently of
`relay.auth.restrictedReadKinds` and `relay.auth.restrictReadToInvolvedPubkey`. With
AUTH disabled or no `relay.auth.serviceUrl`, gift wraps cannot be read at all.

The rule is applied per event on every retrieval path through `src/ReadRestrictor.h`:

- stored REQ results (`RelayReqWorker`);
- live delivery, both the catch-up after EOSE and the fan-out of new events, including
  ephemeral 21059 that strfry briefly writes to LMDB (`RelayReqMonitor`);
- negentropy: shared precomputed trees are bypassed for filters that may contain gift
  wraps, so unauthorized event IDs never enter reconciliation (`RelayNegentropy`);
- COUNT: a filter that may match gift wraps (it lists 1059/21059 or has no `kinds`)
  counts only when its `#p` is scoped to the caller's authenticated key; an `authors`
  scope is not enough.

Before any query runs, REQ, COUNT and NEG-OPEN filters are admitted as follows:

| Filter | Relay response |
| --- | --- |
| Lists 1059 or 21059, and every `#p` value is authenticated on this connection | Admitted |
| Lists 1059 or 21059, and some `#p` value is not authenticated (including no AUTH yet) | `["AUTH", <challenge>]`, then `["CLOSED", <id>, "auth-required: gift wraps are readable only by their authenticated #p recipient"]` (`NEG-ERR` for negentropy) |
| Lists 1059 or 21059 without `#p` | `restricted: gift-wrap filters (kinds 1059/21059) must list their recipients in #p` (AUTH cannot fix this) |
| Lists 1059 or 21059 on a relay without AUTH support | `restricted: gift-wrap reads require relay AUTH support` |
| No `kinds` | Admitted unchanged; gift wraps are withheld per event unless the reader is a `p`-tag recipient |
| Other kinds only | Unchanged |

**Whole-subscription refusal is deliberate.** strfry admits one `Subscription` per id,
and NIP-01/NIP-42 only offer a per-subscription `CLOSED`. NIP-67's EOSE `auth` hint is
optional for clients and covers stored events only, so a mixed subscription such as
ContextVM's `25910 + 1059 + 21059` would still silently miss live gift wraps. That was
the failure behind 811f6ff. Clients that want public kinds without AUTH should request
them in a separate REQ.

**Challenge before `auth-required`.** The challenge and the refusal are queued in that
order on the connection's single ordered output. Some clients only answer a challenge
they have already received when the refusal arrives. These include the Freedom
Builders phone ContextVM sessions and the laptop speech service checked below.

**Unchanged:**

- kinds 4/4444 and any configured `restrictedReadKinds` (including their existing silent
  per-event withholding in mixed filters);
- members-mode read admission;
- writes, so publishing a wrap still needs no AUTH;
- the write-policy plugin and its rate limits;
- NIP-09 recipient deletion of gift wraps;
- the database format.

No stored event is deleted or modified. Historical wraps stay stored until retention or
an operator deletion removes them; that is a separate decision.

## Known limits

- **Short pages.** Withheld events still consume a filter's `limit`, because `DBQuery`
  counts matches before the per-event check, as it already does for 4/4444. A kind-less
  query can therefore return fewer than `limit` events while more public events exist.
  Paginate by `until` and do not treat a short page as the end. Recipient-scoped
  gift-wrap filters are exact.
- **Negentropy size check.** The "too many query results" check counts withheld matches,
  which is a one-bit size signal and exposes no IDs.
- **The operator still sees everything.** The relay operator and its logs still see all
  events, IPs and timing. Each authenticated reader is now logged as
  `[connId] AUTHed as <pubkey>` next to the `Connect from <IP>` line, so logs link IPs to
  recipient pubkeys. Backups (`backup.sh`) export stored gift wraps as before.
- **COUNT** is disabled whenever `relay.maxFilterLimitCount = 0`, as in the committed
  template.

## Client compatibility

Required client flow:

1. Request gift wraps in their own REQ, scoped to the keys you will authenticate as, for
   example `{"kinds":[21059],"#p":["<own hex pubkey>"]}`. Keep public kinds in another
   REQ.
2. Expect `["AUTH", <challenge>]` and then `["CLOSED", <id>, "auth-required: …"]`.
3. Sign kind 22242 (`relay` = `wss://relay.budabit.club`, `challenge` = the string
   received) with the recipient key, send `["AUTH", …]` and wait for `OK true`. Repeat
   for each recipient key; this fork accepts up to 32 keys per connection.
4. Resend the same REQ, then wait for EOSE before publishing anything whose reply must
   arrive on this connection. A reply published before the subscription is live can only
   be fetched while the relay still stores it: 1059 per retention, 21059 only for the
   ephemeral lifetime (`events.ephemeralEventsLifetimeSeconds`, 300 s in the template).

Checked in source only. None of these were run.

| Client | Source | Expected result |
| --- | --- | --- |
| Freedom Builders laptop speech service (live per coordinator) | `018b35c0`, `rust/speech_server.rs:824-856` | Works. It subscribes to `[1059,21059]` with its own `#p`, answers the challenge with the service key, ignores `CLOSED` while its AUTH is pending, and resubscribes after `OK`. It would reconnect in a loop if `CLOSED` came first; this change prevents that. |
| Freedom Builders phone ContextVM/DM sessions (app 251 native) | `0f1af06e`, `rust/network.rs` relay exchange | Works when the challenge comes first: it signs AUTH and resends the REQ after `OK`. It fails if `CLOSED` arrives before any challenge. It publishes before the authenticated re-REQ, so it relies on the relay still storing the reply. Later app versions were not inspected here. |
| Freedom Builders `feat/speech-ephemeral-transport` | `58b8af69` (uncompiled, per its worker) | Designed for this policy: 21059-only, own `#p`, AUTH as recipient or service, handles the challenge before or after the refusal, resubscribes, and gates publishing on EOSE (20 s). |
| ContextVM TS SDK (`@contextvm/sdk` 0.14.3) | `src/relay/applesauce-relay-pool.ts`, `src/transport/base-nostr-transport.ts:126-136,553-566` at `3949fe3b` | **Breaks here.** The SDK's relay pool has no NIP-42 handling. Its default subscription is `#p` + `[25910, 1059, 21059]`; the wrap kinds follow the gift-wrap mode, not the encryption mode. Unless an application adds its own AUTH handling, default-configured TS-SDK clients and servers lose **all** ContextVM traffic on this relay, plaintext 25910 included, until they support AUTH or split their filters. |
| ContextVM Rust SDK, Budabit web, other NIP-17 clients | — | Not verified. Budabit web's own `src` has no direct 1059 use (`9691ed871`); its vendored welshman packages were not inspected. |

Any client that reads 1059 from this relay without AUTH stops receiving gift wraps. That
is the intended policy, but it breaks those clients, so announce it before rollout.

## Deployment

This follows the [repeatable upgrade sequence](DEPLOYMENT-2026-09-13.md#repeatable-upgrade-sequence)
and the [runbook](RUNBOOK.md#build-an-upgrade-separately). Do not build in `/opt/strfry`.

### Exact target needed

- **The live revision or image.** The runbook's recorded image `budabit/strfry:2fc1b38`
  is stale: on 2026-11-19 the live NIP-11 advertised NIP-42, which that recorded
  configuration does not.
- **Whether the live write limits come from `feat/dm-rate-budget` (`53c711c`).** That
  branch is unmerged. If it is live, deploy a merge of both branches, otherwise its
  limits are lost; `git merge-tree` reports no conflicts. Merging `master` is not part of
  this change.

### Configuration

Keep the live `strfry.conf`. **Do not replace it with this repository's template**: the
template has AUTH disabled and different limits. No new keys are needed. Confirm these
settings, because without them gift wraps become unreadable:

```bash
sudo grep -n -A6 'auth {' /opt/strfry/deploy/budabit/strfry.conf
# required: enabled = true, serviceUrl = "wss://relay.budabit.club"
```

### Source and build

```bash
REV=<full commit of feat/giftwrap-recipient-reads>   # git ls-remote https://github.com/Pleb5/strfry refs/heads/feat/giftwrap-recipient-reads
sudo git clone https://github.com/Pleb5/strfry.git /opt/strfry-releases/$REV
cd /opt/strfry-releases/$REV
sudo git checkout --detach $REV
sudo git submodule update --init --recursive        # golpe, external/negentropy
# Only if feat/dm-rate-budget is live (record the resulting local commit as the deployed source):
#   sudo git merge --no-ff origin/feat/dm-rate-budget && REV=$(sudo git rev-parse HEAD)
sudo docker build -f deploy/budabit/Dockerfile --build-arg SOURCE_REVISION=$REV \
  -t budabit/strfry:${REV:0:12} .
```

This is a full C++ build: the `golpe.yaml` descriptions changed, so every translation
unit recompiles. The Dockerfile uses `make -j2`; the runbook records a VPS-specific
`make -j1` variant. Keep the old image and record the image ID.

### Activate

1. Inventory the running image ID and timers, take a checkpoint, and pause the relay
   timers, as in steps 1-2 and 7 of the upgrade sequence.
2. Change only the live Compose `image:` to `budabit/strfry:${REV:0:12}`.
3. Recreate the relay alone:

```bash
sudo docker compose --project-directory /opt/strfry/deploy/budabit \
  -p budabit-strfry -f /opt/strfry/deploy/budabit/compose.yaml \
  up -d --no-build --pull never --no-deps --force-recreate --wait --wait-timeout 180 relay
```

### Verify

Use a recipient account through its bunker; never paste a raw key. `<hex>` is a 64-hex
pubkey.

```bash
curl -sS -H 'Accept: application/nostr+json' https://relay.budabit.club/ | jq -c .supported_nips   # still lists 42
nak req -k 1 -l 1 wss://relay.budabit.club                               # public read: 1 event
nak req -k 21059 -p <hex> -l 1 wss://relay.budabit.club                  # refused: auth-required
nak req -k 1059 -l 1 wss://relay.budabit.club                            # refused: restricted (#p missing)
nak req -k 25910 -k 1059 -p <hex> -l 1 wss://relay.budabit.club          # mixed: refused, nothing silently dropped
nak req --auth --sec "<bunker URL of recipient A>" -k 1059 -p <A hex> -l 5 wss://relay.budabit.club   # A's wraps
nak req --auth --sec "<bunker URL of A>" -k 1059 -p <B hex> -l 1 wss://relay.budabit.club            # still refused
```

Then confirm on the phone that DMs and speech replies still arrive, and check the logs
for `AUTHed as` lines and restarts.

### Rollback

This change touches code and comments only. It changes neither the database nor the
configuration. To roll back, restore the previous image tag in the live Compose file and
recreate the relay with the same command; the database stays in place. After rollback,
gift wraps are publicly readable again, as before.

## Verification limits

- **Not compiled.** A shared build-resource guard is in place and no slot was assigned.
- **No tests.** No unit or integration tests were written or run, under the operator's
  testing policy. `test/tests/giftWrapReadTest.js` still asserts the 811f6ff public-read
  behaviour and will fail against this source; it was intentionally left unchanged.
- **No live probes or deployment.** Client results above come from reading source.
