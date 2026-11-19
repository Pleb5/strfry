# Recipient-only gift-wrap reads (NIP-59 kinds 1059 and 21059)

Requested by the operator on 2026-11-19 (DM `7012ac01…`): make gift-wrap retrieval on
`wss://relay.budabit.club` recipient-only. This is a source change. It has not been
compiled, tested or deployed yet; see [Verification limits](#verification-limits).

## Deployment readiness: not ready to roll out yet

Roll out only when all of these hold:

1. **Build from this branch's head, not `3dc14b48`.** At `3dc14b48` the relay could resend
   the same AUTH challenge on one connection: once on open with read admission, then
   again before an `auth-required:` refusal. Freedom Builders apps up to 257 reject a
   repeated challenge, and the speech service `018b35c0` signs once per copy. The current
   head sends each connection's challenge only once.
2. **The live revision or image, and whether `feat/dm-rate-budget` is live, are known**
   (see [Exact target needed](#exact-target-needed)).
3. **If read admission is enabled on the live relay, wait for app 258.**
   `relay.readPolicy.plugin` set, or NIP-11 `budabit.read_control` present, means read
   admission is on. In that case every connection gets a challenge on open, so wait
   until the phones in use run app 258 with `fd50846a`, which answers a reused challenge
   once. With one challenge per connection, apps up to 257 should cope according to their
   source, but that has not been observed.
4. **The image is compiled and checked** with the commands below, with one real phone of
   the installed app version, before the change is announced.

Do **not** turn AUTH off to work around a client problem: with AUTH disabled, no gift
wrap is readable by anyone. To undo, use the [rollback](#rollback).

### AUTH versus read admission

| Setting | Needed here? | Exact configuration | Effect |
| --- | --- | --- | --- |
| NIP-42 AUTH | **Required** | `relay.auth.enabled = true` and `relay.auth.serviceUrl = "wss://relay.budabit.club"`. NIP-11 lists 42 only when both are set. | Without it, gift-wrap filters get `restricted: gift-wrap reads require relay AUTH support`. |
| Read admission ("members mode") | **Optional; independent of this change** | Enabled when `relay.readPolicy.plugin` is non-empty (preset `/usr/local/lib/strfry/read-policy.py` with `BUDABIT_READ_CONTROL=members`). Off when it is `""`, as in the committed template. | Challenges every connection on open and admits only members' non-public reads. Recipient-only gift wraps apply on top. |

No configuration change is needed for this source change, and none was made on the live
relay.

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
| Lists 1059 or 21059, and some `#p` value is not authenticated (including no AUTH yet) | `["AUTH", <challenge>]` if this connection has not been sent its challenge yet, then `["CLOSED", <id>, "auth-required: gift wraps are readable only by their authenticated #p recipient"]` (`NEG-ERR` for negentropy) |
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

**One challenge per connection.** NIP-42 makes a challenge valid for the whole
connection, so the relay sends it at most once per connection:

- on open when read admission is enabled;
- otherwise immediately before the first `auth-required:` refusal, on the same ordered
  output.

Later refusals rely on the challenge the client already holds, and a client may AUTH
several keys with it. This applies to every AUTH path, including kinds 4/4444, NIP-70
and read admission. It changes neither AUTH verification nor the recipient checks.

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
2. Expect `["CLOSED", <id>, "auth-required: …"]`. It is preceded by
   `["AUTH", <challenge>]` only if this connection has not received its challenge yet; it
   may have arrived on open (read admission) or before an earlier refusal. Keep the
   challenge for the life of the connection, because the relay does not repeat it.
3. Sign kind 22242 (`relay` = `wss://relay.budabit.club`, `challenge` = the stored
   string) with the recipient key, send `["AUTH", …]` and wait for `OK true`. Use the same
   challenge for each further recipient key; this fork accepts up to 32 keys per
   connection.
4. Resend the same REQ, then wait for EOSE before publishing anything whose reply must
   arrive on this connection. A reply published before the subscription is live can only
   be fetched while the relay still stores it: 1059 per retention, 21059 only for the
   ephemeral lifetime (`events.ephemeralEventsLifetimeSeconds`, 300 s in the template).

Checked in source only. None of these were run.

| Client | Source | Expected result |
| --- | --- | --- |
| Freedom Builders laptop speech service (live per coordinator) | `018b35c0`, `rust/speech_server.rs:824-856` | Expected to work with **one** challenge per connection. It subscribes to `[1059,21059]` with its own `#p`, answers the challenge with the service key, ignores `CLOSED` while its AUTH is pending, and resubscribes after `OK`. It signs once per challenge copy and stops at a third (`auth_attempts<=2`, line 840). If `CLOSED` arrives before any challenge, it reconnects in a loop. |
| Freedom Builders phone ContextVM and DM sessions, apps up to 257 (251 native read) | `0f1af06e`, `rust/network.rs:750-1100` | Expected to work with **one** challenge per connection: it signs AUTH and resends the REQ after `OK`. It **rejects any further challenge** after answering ("signer: invalid relay challenge", line 865). It also fails if `CLOSED` arrives before any challenge. It publishes before the authenticated re-REQ, so it relies on the relay still storing the reply. Versions 252-257 were not read here; the speech worker reports the same behaviour up to 257. |
| Freedom Builders `fd50846a` (speech worker's final pin; unbuilt, planned app 258 and service) | `rust/network.rs`, `rust/speech_server.rs` | Answers a reused challenge once, ignores repeats and rejects a different one. Robust to either relay behaviour. |
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
  --build-arg BUILD_JOBS=1 -t budabit/strfry:${REV:0:12} .
```

This is a full C++ build: the `golpe.yaml` descriptions changed, so every translation
unit recompiles. On the deploy branch, `BUILD_JOBS` (default 2) sets both `make -j` and
golpe's uWebSockets `NPROC`, which is otherwise unlimited. Use `BUILD_JOBS=1` on the
shared VPS: BuildKit has no per-build memory cap, so this runs one compiler at a time.
Keep the old image and record the image ID.

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

Then, with a phone running the installed app version (up to 257 until 258 ships),
confirm that DMs and speech replies still arrive with no "invalid relay challenge"
failure. Check that the laptop service log shows no AUTH error or reconnect loop, and
that the relay log shows `AUTHed as` lines and no restarts.

### Rollback

This change touches code and comments only. It changes neither the database nor the
configuration. To roll back, restore the previous image tag in the live Compose file and
recreate the relay with the same command; the database stays in place. After rollback,
gift wraps are publicly readable again, as before.

## Verification limits

- **Not compiled.** This covers both the recipient-only policy and the
  once-per-connection challenge. A shared build-resource guard is in place and no slot
  was assigned.
- **No tests.** No unit or integration tests were written or run, under the operator's
  testing policy. `test/tests/giftWrapReadTest.js` still asserts the 811f6ff public-read
  behaviour and will fail against this source; it was intentionally left unchanged.
- **No live probes or deployment.** Client results above come from reading source.
