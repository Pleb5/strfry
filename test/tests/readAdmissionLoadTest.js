// Small, reproducible local characterization, not a production DoS benchmark.
import assert from "node:assert/strict";
import {once} from "node:events";
import {readFileSync} from "node:fs";
import {randomBytes} from "node:crypto";
import {setTimeout as delay} from "node:timers/promises";
import {fixture, owner, outsider, event} from "../utils/readAdmission.js";

const f = await fixture();
try {
  const stat = () => {
    const fields = readFileSync(`/proc/${f.pid()}/stat`,"utf8").slice(readFileSync(`/proc/${f.pid()}/stat`,"utf8").lastIndexOf(")") + 2).split(" ");
    return {cpuTicks: Number(fields[11])+Number(fields[12]), rssKiB:Number(readFileSync(`/proc/${f.pid()}/status`,"utf8").match(/^VmRSS:\s+(\d+)/m)[1])};
  };
  const before = stat(), started = Date.now();
  const readers = await Promise.all(Array.from({length:50},async()=>{
    const c = await f.connect(owner);
    c.send(["REQ","live",{kinds:[1],limit:0}]);
    await c.waitFor(m=>m[0]==="EOSE");
    return c;
  }));
  const churn = await Promise.all(Array.from({length:100},async()=>{
    const c = await f.connect(outsider), start = Date.now();
    assert.match((await f.read(c)).at(-1)[2],/^restricted:/);
    return Date.now()-start;
  }));
  await delay(2300);
  assert(readers.every(c=>c.ws.readyState===1));
  assert(f.calls().filter(r=>r.authenticated_pubkeys.includes(owner.pubkey)).length>=150,"50 connections get repeated fair rechecks");
  const ends = readers.map(c=>once(c.ws,"close")), revoke = Date.now();
  f.set({keys:[]});
  await Promise.all(ends);
  const after = stat();
  console.log(JSON.stringify({test:"50 active / 100 outsider churn", elapsedMs:Date.now()-started,
    outsiderMaxMs:Math.max(...churn),revokeAllMs:Date.now()-revoke,
    relayCpuTicks:after.cpuTicks-before.cpuTicks,relayRssKiB:after.rssKiB}));
} finally { await f.stop(); }

const overload = await fixture({maxPending:8});
try {
  const clients = [];
  for (let i=0;i<14;i++) clients.push(await overload.connect(owner));
  overload.set({decision:"allow",delay:0.35});
  const reads = [];
  for (const client of clients) {
    reads.push(overload.read(client,{kinds:[1],limit:0}));
    await delay(10);
  }
  const results = await Promise.all(reads);
  assert(results.some(r=>r.at(-1)[2]?.startsWith("rate-limited:")),"queue exhaustion is explicit, never unchecked admission");
  assert(results.some(r=>r.at(-1)[2]?.startsWith("error:")),"queue wait counts toward the deadline");
  console.log("PASS bounded admission queue overload and queue-inclusive deadlines");
} finally { await overload.stop(); }

const failure = await fixture({recheckSeconds:5});
try {
  const reader = await failure.connect(owner), writer = await failure.connect(owner);
  reader.send(["REQ","live",{kinds:[1],limit:0}]);
  await reader.waitFor(m=>m[0]==="EOSE");
  failure.set({decision:"allow",delay:10});
  const stop = once(reader.ws,"close"), started = Date.now();
  // Read RPC is hung; EVENT handling must not share that channel/worker.
  const pending = failure.read(reader,{kinds:[1],limit:0});
  await delay(100);
  const writeStart = Date.now();
  assert.equal((await failure.publish(writer,event(owner,1,[],"write path remains usable")))[2],true);
  assert(Date.now()-writeStart<1000);
  const result = await pending;
  assert.match(result.at(-1)[2],/^error:/);
  await stop;
  console.log(`PASS policy timeout disconnects at default recheck settings (${Date.now()-started}ms), independent EVENT write accepted`);
  failure.set({keys:[owner.pubkey]});
  const restored = await failure.connect(owner);
  restored.send(["REQ","default-recheck",{kinds:[1],limit:0}]);
  await restored.waitFor(m=>m[0]==="EOSE");
  const rechecked = once(restored.ws,"close"), revoked = Date.now();
  failure.set({keys:[]});
  await restored.waitFor(m=>m[0]==="CLOSED",7500);
  await rechecked;
  assert(Date.now()-revoked>=4500 && Date.now()-revoked<7500);
  console.log(`Default five-second periodic revocation: ${Date.now()-revoked}ms (cached fixture policy)`);
} finally { await failure.stop(); }

const slow = await fixture({extra:"maxPendingOutboundBytes = 4096\n compression { enabled = false }"});
try {
  slow.import(Array.from({length:500},()=>event(owner,1,[],randomBytes(10000).toString("hex"))));
  const client = await slow.connect(owner);
  client.send(["REQ","interrupted-history",{kinds:[1],limit:500}]);
  client.ws._socket.pause();
  const deadline = Date.now()+8000;
  while (!slow.logs().includes("Slow client") && Date.now()<deadline) await delay(50);
  assert(slow.logs().includes("Slow client"),"stalled historical consumer must hit bounded outbound backlog");
  const closed = once(client.ws,"close");
  client.ws._socket.resume();
  await closed;
  assert(!client.queue.some(m=>m[0]==="EOSE" && m[1]==="interrupted-history"),"interrupted history must not claim completion");
  console.log("PASS slow historical consumer is disconnected without a successful EOSE");
} finally { await slow.stop(); }
