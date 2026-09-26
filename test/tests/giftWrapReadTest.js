import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {mkdirSync, writeFileSync} from "node:fs";
import path from "node:path";
import {fixture, owner, member, outsider, event} from "../utils/readAdmission.js";

const mixedKinds = [25910, 1059, 21059];
let nextSub = 0;

async function publicRead(client, filter) {
  const id = `gift-wrap-${++nextSub}`;
  client.send(["REQ", id, filter]);
  const frames = await client.collectUntil(m => ["EOSE", "CLOSED"].includes(m[0]) && m[1] === id);
  assert.equal(frames.at(-1)[0], "EOSE", JSON.stringify(frames));
  assert(!frames.some(m => m[0] === "AUTH"), "public gift-wrap reads must not request AUTH");
  client.send(["CLOSE", id]);
  return frames.filter(m => m[0] === "EVENT" && m[1] === id).map(m => m[2].id);
}

for (const authEnabled of [true, false]) {
  // Omit restrictedReadKinds entirely: test the binary's real default, not a
  // fixture override that could hide a restrictive generated configuration.
  const f = await fixture({publicReads: true, authEnabled, restrictedReadKinds: null});
  try {
    const wrap = event(owner, 1059, [["p", member.pubkey]], "stored-envelope");
    const privateDm = event(owner, 4444, [["p", member.pubkey]], "private-dm");
    f.import([wrap, privateDm]);
    const anon = await f.connect(undefined, {requestChallenge: false});

    for (const filter of [
      {kinds: [1059]},
      {kinds: mixedKinds, "#p": [member.pubkey]},
      {ids: [wrap.id]},
      {},
    ]) {
      assert.deepEqual(await publicRead(anon, filter), [wrap.id]);
    }
    anon.send(["COUNT", "wrap-count", {kinds: [1059]}]);
    const counted = await anon.collectUntil(m => m[1] === "wrap-count");
    assert(!counted.some(m => m[0] === "AUTH"));
    assert.equal(counted.at(-1)[0], "COUNT");
    assert.equal(counted.at(-1)[2].count, 1);

    // Both ordinary and precomputed-tree Negentropy reads expose the envelope.
    const run = args => {
      const result = spawnSync("./strfry", args, {env: f.env, encoding: "utf8", timeout: 15000});
      assert.equal(result.status, 0, result.stderr);
      return result.stdout;
    };
    const filter = '{"kinds":[1059]}';
    const syncDb = path.join(f.work, "sync-db"), syncConfig = path.join(f.work, "sync.conf");
    mkdirSync(syncDb);
    writeFileSync(syncConfig, `db = "${syncDb}"\n`);
    const sync = () => {
      const missing = run(["--config", syncConfig, "sync", f.url, "--filter", filter, "--print-missing", "--timeout", "10"])
        .split("\n").filter(line => line.startsWith("need,"));
      assert.equal(missing.length, 1);
      assert(missing[0].includes(wrap.id));
    };
    sync();
    const tree = run(["--config", f.config, "negentropy", "add", filter]).match(/created tree (\d+)/)[1];
    run(["--config", f.config, "negentropy", "build", tree]);
    sync();

    for (const [id, kinds] of [["wrap-live", [1059]], ["cvm-live", mixedKinds]]) {
      anon.send(["REQ", id, {kinds, "#p": [member.pubkey], limit: 0}]);
      const ready = await anon.collectUntil(m => ["EOSE", "CLOSED"].includes(m[0]) && m[1] === id);
      assert.equal(ready.at(-1)[0], "EOSE");
      assert(!ready.some(m => m[0] === "AUTH"));
    }
    const publisher = await f.connect(undefined, {requestChallenge: false});
    const incoming = event(owner, 1059, [["p", member.pubkey]], "live-envelope");
    const accepted = await f.publish(publisher, incoming);
    assert.equal(accepted[2], true, JSON.stringify(accepted));
    const delivered = new Set();
    const live = await anon.collectUntil(m => {
      if (m[0] === "EVENT" && m[2].id === incoming.id) delivered.add(m[1]);
      return delivered.size === 2;
    });
    assert(!live.some(m => m[0] === "AUTH" || m[0] === "CLOSED"));
    assert.deepEqual(delivered, new Set(["wrap-live", "cvm-live"]));
    console.log(`PASS public 1059 default: stored/ID/broad/COUNT/NEG/live and mixed ContextVM filters, AUTH ${authEnabled ? "enabled" : "disabled"}`);
  } catch (error) { console.error(f.logs()); throw error; }
  finally { await f.stop(); }
}

// Explicit operator restrictions still work. Unlike mandatory kinds 4/4444,
// an optional 1059 restriction respects restrictReadToInvolvedPubkey=false.
const restricted = await fixture({publicReads: true, restrictedReadKinds: "1059"});
try {
  const wrap = event(owner, 1059, [["p", member.pubkey]], "explicitly-restricted");
  restricted.import([wrap]);
  const anon = await restricted.connect(undefined, {requestChallenge: false});
  const denied = await restricted.read(anon, {kinds: [1059]});
  assert.equal(denied.at(-1)[0], "CLOSED");
  assert.match(denied.at(-1)[2], /^auth-required:/);
  const authenticated = await restricted.connect(outsider);
  assert.deepEqual((await restricted.read(authenticated, {kinds: [1059]}))
    .filter(m => m[0] === "EVENT").map(m => m[2].id), [wrap.id]);
  console.log("PASS optional explicit 1059 AUTH restriction");
} finally { await restricted.stop(); }
