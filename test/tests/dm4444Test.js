import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {mkdirSync, writeFileSync} from "node:fs";
import path from "node:path";
import { fixture, owner, member, outsider, event } from "../utils/readAdmission.js";

const f = await fixture({publicReads:true});
try {
  const dms = [4,1059,4444].map(kind=>event(owner,kind,[["p",member.pubkey]],`private-${kind}`));
  const other = event(outsider,4444,[["p",outsider.pubkey]],"other-private");
  const publicEvent = event(owner,1,[],"public");
  f.import([...dms,other,publicEvent]);
  // A stored shared tree must not bypass participant filtering in public mode.
  const run = args => {
    const result = spawnSync("./strfry", args, {env:f.env, encoding:"utf8", timeout:15000});
    assert.equal(result.status,0,result.stderr);
    return result.stdout;
  };
  const filter = '{"kinds":[1,4444]}';
  const added = run(["--config",f.config,"negentropy","add",filter]);
  const tree = added.match(/created tree (\d+)/)[1];
  run(["--config",f.config,"negentropy","build",tree]);
  const syncDb = path.join(f.work,"sync-db"), syncConfig = path.join(f.work,"sync.conf");
  mkdirSync(syncDb);
  writeFileSync(syncConfig,`db = "${syncDb}"\n`);
  const missing = run(["--config",syncConfig,"sync",f.url,"--filter",filter,"--print-missing","--timeout","10"])
    .split("\n").filter(line=>line.startsWith("need,"));
  assert.equal(missing.length,1);
  assert(missing[0].includes(publicEvent.id),"precomputed NEG tree must expose only the public id");
  const anon = await f.connect();
  const broad = await f.read(anon);
  assert.deepEqual(broad.filter(m=>m[0]==="EVENT").map(m=>m[2].id),[publicEvent.id]);
  const exact = await f.read(anon,{ids:[dms[2].id]});
  assert(!exact.some(m=>m[0]==="EVENT"));
  anon.send(["COUNT","broad-count",{}]);
  assert.equal((await anon.waitFor(m=>m[1]==="broad-count"))[0],"CLOSED");
  for (const who of [owner, member]) {
    const c = await f.connect(who), results = await f.read(c,{kinds:[4,1059,4444]});
    assert.deepEqual(new Set(results.filter(m=>m[0]==="EVENT").map(m=>m[2].id)),new Set(dms.map(e=>e.id)));
    c.send(["COUNT","dm-count",{kinds:[4444]}]);
    assert.equal((await c.waitFor(m=>m[1]==="dm-count"))[0],"CLOSED");
    c.send(["COUNT","own-count",{kinds:[4444],...(who===owner ? {authors:[owner.pubkey]} : {"#p":[member.pubkey]})}]);
    assert.equal((await c.waitFor(m=>m[1]==="own-count"))[2].count,1);
  }
  const stranger = await f.connect(outsider);
  assert(!(await f.read(stranger,{ids:[dms[2].id]})).some(m=>m[0]==="EVENT"));
  const recipient = await f.connect(member);
  recipient.send(["REQ","live-dm",{kinds:[4444],limit:0}]);
  await recipient.waitFor(m=>m[0]==="EOSE" && m[1]==="live-dm");
  stranger.send(["REQ","other-live",{kinds:[4444],limit:0}]);
  await stranger.waitFor(m=>m[0]==="EOSE" && m[1]==="other-live");
  const incoming = event(owner,4444,[["p",member.pubkey]],"live-private");
  assert.equal((await f.publish(anon,incoming))[2],true);
  assert.equal((await recipient.waitFor(m=>m[0]==="EVENT" && m[1]==="live-dm"))[2].id,incoming.id);
  const sentinel = event(outsider,4444,[["p",outsider.pubkey]],"sentinel");
  assert.equal((await f.publish(anon,sentinel))[2],true);
  const observed = await stranger.collectUntil(m=>m[0]==="EVENT" && m[2].id===sentinel.id);
  assert(!observed.some(m=>m[0]==="EVENT" && m[2].id===incoming.id));

  console.log("PASS automatic 4444/legacy privacy with empty optional restrictions, history/ID/live/COUNT/NEG");
} catch(error) { console.error(f.logs()); throw error; }
finally { await f.stop(); }

const noAuth = await fixture({publicReads:true,authEnabled:false});
try {
  const secret = event(owner,4444,[["p",member.pubkey]],"unavailable AUTH must not disclose");
  noAuth.import([secret]);
  const c = await noAuth.connect();
  assert.match((await noAuth.read(c,{kinds:[4444]})).at(-1)[2],/^restricted:/);
  assert(!(await noAuth.read(c,{})).some(m=>m[0]==="EVENT"));
  assert(!(await noAuth.read(c,{ids:[secret.id]})).some(m=>m[0]==="EVENT"));
  console.log("PASS AUTH-disabled public relay never discloses kind4444");
} finally { await noAuth.stop(); }
