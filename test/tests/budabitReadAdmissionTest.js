import assert from "node:assert/strict";
import { once } from "node:events";
import { setTimeout as delay } from "node:timers/promises";
import {readFileSync, writeFileSync, unlinkSync} from "node:fs";
import path from "node:path";
import { fixture, eventuallyMember, owner, member, outsider, community, event, definition, grant, report } from "../utils/readAdmission.js";

const f = await fixture({budabit:true,scanFailure:true});
try {
  const admin = await eventuallyMember(f, owner);
  const def = definition();
  for (let i=0; ; ++i) {
    const ack = await f.publish(admin, def);
    if (ack[2]) break;
    assert(i < 20 && ack[3].includes("loading"), JSON.stringify(ack));
    await delay(100); // only explicitly rejected startup writes are retried
  }
  const note = event(owner, 1, [], "retained private history");
  assert.equal((await f.publish(admin, note))[2], true);
  const stranger = await f.connect(outsider);
  assert.match((await f.read(stranger)).at(-1)[2], /^restricted:/);
  assert.equal((await f.publish(admin, grant([member])))[2], true);
  const reader = await eventuallyMember(f, member);
  assert((await f.read(reader)).some(m=>m[0]==="EVENT" && m[2].id===note.id));
  reader.send(["REQ", "live", {kinds:[1],limit:0}]);
  await reader.waitFor(m=>m[0]==="EOSE" && m[1]==="live");
  const ended = once(reader.ws,"close"), started = Date.now(), ban = report(member);
  assert.equal((await f.publish(admin, ban))[2], true);
  assert.match((await reader.waitFor(m=>m[0]==="CLOSED" && m[1]==="live",6000))[2], /^restricted:/);
  await ended;
  console.log(`Real stored ban to disconnect: ${Date.now()-started}ms (refresh 100ms, recheck 1s)`);
  assert.equal((await f.publish(admin,event(owner,5,[["e",ban.id],["h",community]])))[2],true);
  const restored = await eventuallyMember(f,member);
  assert((await f.read(restored)).some(m=>m[0]==="EVENT" && m[2].id===note.id));
  const profile = await (await fetch("http://127.0.0.1:40582/",{headers:{accept:"application/nostr+json"}})).json();
  assert.equal(profile.limitation.auth_required,true);
  assert.deepEqual(profile.read_policy,{version:1,admission:"req",consistency:"eventual",recheck_seconds:1});
  assert.equal(profile.budabit.read_control.version,2);
  restored.send(["REQ","stale",{kinds:[1],limit:0}]);
  await restored.waitFor(m=>m[0]==="EOSE" && m[1]==="stale");
  const stale = once(restored.ws,"close"), failedAt = Date.now();
  writeFileSync(path.join(f.work,"fail-scan"),"");
  assert.match((await restored.waitFor(m=>m[0]==="CLOSED" && m[1]==="stale",7000))[2],/^error:/);
  await stale;
  process.kill(Number(readFileSync(path.join(f.work,"read.pid"))),0); // responsive process, expired view
  assert(Date.now()-failedAt>=1500,"failed refresh does not immediately masquerade as nonmembership");
  unlinkSync(path.join(f.work,"fail-scan"));
  await eventuallyMember(f,member);
  console.log(`PASS responsive-but-stale Python policy fails closed after ${Date.now()-failedAt}ms and recovers from storage`);
  console.log("PASS real Python/LMDB owner bootstrap, outsider denial, grant, ban and regrant");
} catch(error) { console.error(f.logs()); throw error; }
finally { await f.stop(); }
