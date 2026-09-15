# Read-admission replacement: provenance and verification

Local source verification on 2026-09-15. No push, release, container promotion or
live deployment is implied. See [the contract](READ-CONTROL-PLAN.md) and
[operator guide](PRIVATE-READS.md).

## Retain/replace manifest

This is a forward replacement in the existing checkouts, not a reset, rewritten
history or isolated worktree. Comparison baselines:

- strfry pre-feature `17912b11f1fb907c7c849549670bfc6af31b105f`;
  reviewed implementation `1ff587ad91e156c5252f894a51ab439da5bc5028`.
- Budabit pre-feature `dbf0ca551f7e7f13a09528c19abce4b91a194ca8`;
  reviewed private implementation `b4edf8ddb08f09b9dbe8f972692f545160b2a2e7`.
  Unrelated profile/identity work at `78cc178873ef536f4e9059da73cc1208e697cf33`
  remains intact.

| Area | Disposition |
| --- | --- |
| Write enforcement / initial load | Retained: `stage.py`, `rules.py`, loader and signed-author decisions; no membership prerequisite for every write |
| Reader eligibility | Retained: `state.py`, client predicate, shared reader vectors and authority retention |
| AUTH and client privacy | Retained: multi-key proofs, freshness/ACK/cancellation, consent, scoped sockets/repository, signed private intent, publication and diagnostics guards |
| `ReadGate.h`, `readers.py` | Removed; no C++ reader roster/branch pin or snapshot transport |
| Writer/expiry/DBChange coupling | Removed: init/committed/policyRelevant messages, pre-commit invalidation, writer-routed expiry, snapshot-driven deferred catch-up |
| Final sends | Connection/REQ lifecycle cancellation only; no commit-synchronized membership lock |
| Read plugin | New independent JSONL worker and cached Python predicate; bounded scan freshness, REQ decisions and periodic connection rechecks |
| Strict snapshot/gate tests | Replaced by tests for the new contract, not carried forward as evidence of commit-atomic revocation |
| Deployment / metadata | Retained local-checkout Docker provenance; replaced artifact health with config/advertisement and separate scan checks; versioned eventual-admission capability |
| Client retry / completeness | Denial disposes sockets; explicit retry authenticates anew. Separate bounded authority/text intake and unfiltered-kind evidence retained |
| DM / NIP-70 | Independent participant privacy for 4444/retained 4/1059; separate default-off NIP-70 semantics |

## Reproducible checks

Use a unique `TMPDIR` for each isolated relay run. Tests use controlled local keys
and storage; they do not sign with live accounts or contact a production relay.

```sh
nix-shell --run 'make -j4'
python3 -m unittest discover -s deploy/budabit/tests -t deploy/budabit/tests -p 'test_*.py'
node test/tests/readAdmissionTest.js
node test/tests/budabitReadAdmissionTest.js
node test/tests/readAdmissionLoadTest.js
node test/tests/dm4444Test.js
node test/tests/nip70Test.js
node test/tests/readRestrictTest.js
node test/tests/authMaxAgeTest.js
node test/tests/budabitPolicyTest.js
node test/tests/budabitStartupTest.js
python3 deploy/budabit/check-read-control.py --config deploy/budabit/strfry.conf --config-only
docker compose -f deploy/budabit/compose.yaml config --quiet
```

Client verification includes focused privacy/access/AUTH tests, the opt-in
`private-community-native.test.ts` against actual strfry/LMDB, Welshman regressions,
type/lint/format checks and the retained cold mocked-browser private-community
flow. A dedicated `oc2-browser` cold session also checks that a signed-out private
invitation shows the access shell without text/authoring. The existing full dev
stack/watchers are reused, not restarted. Mocked browser signing is not real-signer
or deployed proxy evidence.

## Local results

- Consistent native build; all listed raw-relay regression suites passed.
- Python: 196 tests passed. Client focus: 118 tests in eight files passed.
  Welshman: 548 passed, one skipped. Real-core/client completeness: three passed.
  Type check: zero errors/warnings; targeted lint/format passed.
- Cold mocked-browser denial, fresh-AUTH retry, reload, revoke, diagnostics and
  publication-boundary flow passed. The dedicated signed-out browser check passed.
- Real stored ban to disconnect: 1,054 ms with 100 ms refresh / 1 s recheck test
  settings. Failed refresh with an otherwise responsive plugin became unavailable
  and recovered in 3,386 ms with a 3 s test view-age limit.
- Cached fixture policy, default 5 s recheck: 5,089 ms to disconnect after revocation.
- 50 active readers plus 100 outsider connections: all scheduled rechecks serviced;
  maximum outsider admission latency 8 ms; relay RSS 31,260 KiB; ten relay CPU ticks
  over 3,227 ms. This excludes child/plugin CPU and is not a production-scale test.
- Bounded queue overload, queue-inclusive deadlines, concurrent EVENT write during
  hung read IPC, and slow historical clients without false EOSE all passed.
- Default-off config preflight, Compose parsing and local semantic-vector comparison
  passed. Neither Docker execution nor publication pins were changed.

## Remaining release limits

- The published server-vector pin is unchanged and lacks the newer reader vector
  section. Local vectors agree; authorized immutable publication/pin refresh is
  still required before release.
- Docker execution was blocked by socket permissions. Compose parsing and native
  tests are not an image startup/promotion/rollback test.
- No production retention audit, live proxy/real-signer check or fleet-scale DoS
  benchmark has been performed. Local timing measurements do not establish an SLA.
- Membership is intentionally eventually consistent. No evidence here establishes
  zero delivery under old membership immediately after a commit.
