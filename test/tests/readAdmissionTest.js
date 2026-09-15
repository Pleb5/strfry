import assert from "node:assert/strict";
import { once } from "node:events";
import {spawnSync} from "node:child_process";
import {readFileSync,writeFileSync} from "node:fs";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fixture, owner, member, outsider, event } from "../utils/readAdmission.js";

const f = await fixture();
try {
  const original = readFileSync(f.config,"utf8"), guard = path.join(f.work,"invalid.conf");
  for (const text of [
    original.replace("readPolicy {", "readControl { enabled = true }\n readPolicy {"),
    original.replace("maxFilterLimitCount = 0", "maxFilterLimitCount = 1"),
    original.replace("negentropy { enabled = false", "negentropy { enabled = true"),
    original.replace("auth { enabled = true", "auth { enabled = false"),
  ]) {
    writeFileSync(guard,text);
    const result = spawnSync("./strfry",["--config",guard,"relay"],{env:f.env,encoding:"utf8",timeout:5000});
    assert.notEqual(result.status,0);
    assert.match(result.stderr,/readControl was replaced|read policy requires/);
  }
  f.import(Array.from({length: 200}, (_, i) => event(owner, 1, [], `history-${i}`)));
  const anon = await f.connect();
  assert.match((await f.read(anon)).at(-1)[2], /^auth-required:/);
  assert.equal(f.calls().length, 0, "anonymous REQ does not consume plugin work");
  const denied = await f.connect(outsider); // successful AUTH is not membership
  const closed = once(denied.ws, "close");
  const deniedResults = await f.read(denied, {ids:["0".repeat(64)]});
  assert.deepEqual(deniedResults.map(m => m[0]), ["CLOSED"], "even empty-result queries require admission");
  assert.match(deniedResults[0][2], /^restricted:/);
  await closed;
  const reader = await f.connect(member);
  const before = f.calls().length;
  const result = await f.read(reader, {kinds:[1], limit:200});
  assert.equal(result.filter(m => m[0] === "EVENT").length, 200);
  assert.equal(f.calls().length - before, 1, "one admission, not 200 per-event decisions");
  await delay(3200);
  assert.equal((await f.read(reader,{kinds:[1],limit:1})).at(-1)[0],"EOSE", "idle sockets start a fresh admission period, not an expired recheck");
  assert(f.calls().every(r => Object.keys(r).sort().join() === "authenticated_pubkeys,request_id,type"));
  await f.auth(reader, outsider);
  assert.equal((await f.read(reader, {kinds:[1], limit:1})).at(-1)[0], "EOSE", "second key does not replace member");
  reader.send(["COUNT", "count", {}]);
  assert.match((await reader.waitFor(m=>m[0]==="CLOSED" && m[1]==="count"))[2], /COUNT disabled/);
  reader.send(["NEG-OPEN", "neg", {}, "60"]);
  assert.match((await reader.waitFor(m=>m[0]==="NEG-ERR"))[2], /disabled/);

  f.set({decision:"allow", delay:0.3});
  reader.send(["REQ", "cancel", {kinds:[1]}]);
  await delay(30);
  reader.send(["CLOSE", "cancel"]);
  await delay(450);
  assert(!reader.queue.some(m => m[1] === "cancel"), "late allow cannot revive CLOSE");
  reader.send(["REQ", "replace", {kinds:[1], limit:10}]);
  await delay(20);
  reader.send(["REQ", "replace", {kinds:[1], limit:1}]);
  const replaced = await reader.collectUntil(m=>m[0]==="EOSE" && m[1]==="replace");
  assert.equal(replaced.filter(m=>m[0]==="EVENT" && m[1]==="replace").length, 1);
  reader.send(["CLOSE", "replace"]);

  f.set({keys:[owner.pubkey, member.pubkey]});
  reader.send(["REQ", "live", {kinds:[1], limit:0}]);
  await reader.waitFor(m=>m[0]==="EOSE" && m[1]==="live");
  const ended = once(reader.ws, "close");
  const started = Date.now();
  f.set({keys:[owner.pubkey]});
  assert.match((await reader.waitFor(m=>m[0]==="CLOSED" && m[1]==="live",4000))[2], /^restricted:/);
  await ended;
  assert(Date.now() - started < 3500, "periodic revocation needs no new REQ");
  console.log(`Periodic generic revoke: ${Date.now() - started}ms (test interval 1s)`);

  for (const bad of [{malformed:true}, {wrong:true}, {oversized:true}, {exit:true}, {decision:"surprise"}, {decision:"allow",delay:5}]) {
    f.set(bad);
    const client = await f.connect(owner);
    const stopped = once(client.ws,"close");
    const results = await f.read(client);
    assert.deepEqual(results.map(m=>m[0]), ["CLOSED"]);
    assert.match(results.at(-1)[2], /^error:/);
    await stopped;
  }
  f.set({keys:[owner.pubkey],delay:0.2});
  const changedIdentity = await f.connect(outsider);
  changedIdentity.send(["REQ","changed-identity",{kinds:[1],limit:1}]);
  await delay(40);
  await f.auth(changedIdentity,owner);
  assert.equal((await changedIdentity.waitFor(m=>(m[0]==="EOSE" || m[0]==="CLOSED") && m[1]==="changed-identity"))[0],"EOSE", "late decision must be rebound to the updated verified keys");
  changedIdentity.send(["CLOSE","changed-identity"]);
  f.set({keys:[owner.pubkey]});
  const restored = await f.connect(owner);
  assert.equal((await f.read(restored,{kinds:[1],limit:1})).at(-1)[0],"EOSE");
  const times = await Promise.all(Array.from({length:30}, async()=> {
    const c = await f.connect(outsider), start = Date.now();
    assert.match((await f.read(c)).at(-1)[2],/^restricted:/);
    return Date.now()-start;
  }));
  console.log(`30 concurrent outsider admissions: max ${Math.max(...times)}ms`);
  console.log("PASS generic REQ admission, whole-query allow, cancellation, periodic revoke, faults and bounded outsider load");
} catch (error) { console.error(f.logs()); throw error; }
finally { await f.stop(); }
