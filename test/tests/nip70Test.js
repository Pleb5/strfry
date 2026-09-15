import assert from "node:assert/strict";
import {fixture, owner, member, event} from "../utils/readAdmission.js";

const disabled = await fixture({publicReads:true});
try {
  const c = await disabled.connect();
  const protectedEvent = event(owner,1,[["-"]],"protected semantics disabled");
  assert.equal((await disabled.publish(c,protectedEvent))[2],true);
  assert.equal((await disabled.publish(c,event(owner,6,[],JSON.stringify(protectedEvent))))[2],true);
  const retained = await disabled.read(c,{ids:[protectedEvent.id]});
  assert.deepEqual(retained.find(m=>m[0]==="EVENT")[2],JSON.parse(JSON.stringify(protectedEvent)),"signed tags must remain unchanged");
  const info = await (await fetch("http://127.0.0.1:40582/",{headers:{accept:"application/nostr+json"}})).json();
  assert(!info.supported_nips.includes(70));
  console.log("PASS NIP-70 defaults off: signed protection tags and embedded reposts accepted unchanged");
} finally { await disabled.stop(); }

const enabled = await fixture({publicReads:true, extra:"nip70 { enabled = true }"});
try {
  const c = await enabled.connect();
  const protectedEvent = event(owner,1,[["-"]],"protected semantics enabled");
  assert.equal((await enabled.publish(c,protectedEvent))[2],false);
  await enabled.auth(c,member);
  assert.match((await enabled.publish(c,protectedEvent))[3],/^restricted:/);
  await enabled.auth(c,owner);
  assert.equal((await enabled.publish(c,protectedEvent))[2],true);
  assert.equal((await enabled.publish(c,event(owner,6,[],JSON.stringify(protectedEvent))))[2],false);
  const info = await (await fetch("http://127.0.0.1:40582/",{headers:{accept:"application/nostr+json"}})).json();
  assert(info.supported_nips.includes(70));
  console.log("PASS explicit NIP-70 opt-in enforcement and advertisement");
} finally { await enabled.stop(); }
