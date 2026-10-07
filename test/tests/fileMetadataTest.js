import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";
import { fixture, owner, member, outsider, event, definition, grant, report } from "../utils/readAdmission.js";

const hash = "a".repeat(64);
const descriptor = who => event(who, 1063, [
  ["url", `https://blossom.example/${hash}`], ["x", hash], ["m", "image/png"], ["size", "123"],
], "Blossom descriptor");

// Real signed events, write plugin, membership state and stored/live delivery.
for (const publicReads of [true, false]) {
  for (const writeMode of ["passthrough", "strict"]) {
    const f = await fixture({budabit:true, publicReads, writeMode, authEnabled:!publicReads, port:40589});
    try {
      f.import([definition(), grant([member])]);
      const admin = await f.connect(publicReads ? undefined : owner);
      const publisher = await f.connect(publicReads ? undefined : member);
      const foreign = await f.connect(publicReads ? undefined : outsider);
      const anon = await f.connect();
      const initial = descriptor(owner);
      for (let i = 0; ; ++i) {
        const ack = await f.publish(admin, initial);
        if (ack[2]) break;
        assert(i < 40 && ack[3].includes("loading"), JSON.stringify(ack));
        await delay(100);
      }
      const file = descriptor(member);
      assert.equal((await f.publish(publisher, file))[2], true, "any-section member writes without a 1063 grant");
      for (const client of [anon, foreign, publisher]) {
        const messages = await f.read(client, {kinds:[1063], "#x":[hash]});
        assert.equal(messages.at(-1)[0], "EOSE");
        assert(messages.some(m => m[0] === "EVENT" && m[2].id === file.id));
      }
      // AUTH identity does not let a member launder an outsider-signed event.
      for (const client of [publisher, foreign]) {
        const ack = await f.publish(client, descriptor(outsider));
        assert.equal(ack[2], false);
        assert.match(ack[3], /file metadata requires a current community member/);
      }
      anon.send(["REQ", "live-files", {kinds:[1063], limit:0}]);
      await anon.waitFor(m => m[0] === "EOSE" && m[1] === "live-files");
      const live = descriptor(member);
      assert.equal((await f.publish(publisher, live))[2], true);
      assert.equal((await anon.waitFor(m => m[0] === "EVENT" && m[1] === "live-files"))[2].id, live.id);

      assert.equal((await f.publish(admin, grant([])))[2], true);
      assert.equal((await f.publish(publisher, descriptor(member)))[2], false, "last grant revocation blocks writes");
      assert.equal((await f.publish(admin, grant([member])))[2], true);
      assert.equal((await f.publish(publisher, descriptor(member)))[2], true);
      assert.equal((await f.publish(admin, report(member)))[2], true);
      const banned = await f.publish(publisher, descriptor(member));
      assert.equal(banned[2], false);
      assert.match(banned[3], /moderated/);
      assert((await f.read(publisher, {kinds:[1063], ids:[file.id]})).some(m => m[0] === "EVENT"), "banned members can still read public descriptors");
      console.log(`PASS kind1063 member writes/nonmember reads (${writeMode}, ${publicReads ? "public" : "members"} reads)`);
    } catch (error) { console.error(f.logs()); throw error; }
    finally { await f.stop(); }
  }
}

// The public exception must retain normal lifecycle tokens without depending on
// membership rechecks or allowing an OR filter to broaden private access.
const f = await fixture({publicKinds:" 1063, 10063,1063 \t", port:40589});
try {
  const file = descriptor(owner), privateNote = event(owner, 1, [], "private");
  const serverList = event(owner, 10063, [["server", "https://blossom.example"]]);
  f.import([file, privateNote, serverList]);
  const info = await (await fetch("http://127.0.0.1:40589/", {headers:{accept:"application/nostr+json"}})).json();
  assert.deepEqual(info.read_policy.public_kinds, [1063,10063], "valid decimal comma-separated kinds remain supported");
  const anon = await f.connect(), foreign = await f.connect(outsider);
  assert((await f.read(anon, {kinds:[10063]})).some(m => m[0] === "EVENT" && m[2].id === serverList.id));
  const messages = await f.read(anon, {kinds:[1063]});
  assert.deepEqual(messages.filter(m => m[0] === "EVENT").map(m => m[2].id), [file.id]);
  for (const filters of [[{}], [{ids:[file.id]}], [{kinds:[1063,1]}], [{kinds:[1063]}, {kinds:[1]}], [{kinds:[]}]]) {
    anon.send(["REQ", "private", ...filters]);
    assert.match((await anon.waitFor(m => m[0] === "CLOSED" && m[1] === "private"))[2], /^auth-required:/);
  }
  foreign.send(["REQ", "public", {kinds:[1063], ids:[file.id]}, {kinds:[1063], "#x":[hash]}]);
  await foreign.waitFor(m => m[0] === "EOSE" && m[1] === "public");
  await delay(1300);
  assert.equal(f.calls().length, 0, "public-only subscriptions never call/recheck the membership plugin");

  // An AUTH-rejected anonymous replacement must close the old public generation.
  const anonymousReplacement = await f.connect(), publisher = await f.connect(owner);
  anonymousReplacement.send(["REQ", "same-id", {kinds:[1063], limit:0}]);
  await anonymousReplacement.waitFor(m => m[0] === "EOSE" && m[1] === "same-id");
  anonymousReplacement.send(["REQ", "same-id", {kinds:[1]}]);
  assert.match((await anonymousReplacement.waitFor(m => m[0] === "CLOSED" && m[1] === "same-id"))[2], /^auth-required:/);
  const afterClosed = descriptor(owner);
  assert.equal((await f.publish(publisher, afterClosed))[2], true);
  await assert.rejects(anonymousReplacement.waitFor(m => m[0] === "EVENT" && m[1] === "same-id", 700), /timeout/);
  await f.auth(anonymousReplacement, member);
  anonymousReplacement.send(["REQ", "same-id", {kinds:[1]}]);
  const retried = await anonymousReplacement.collectUntil(m => m[0] === "EOSE" && m[1] === "same-id");
  assert(retried.some(m => m[0] === "EVENT" && m[2].id === privateNote.id), "same socket and ID can AUTH and retry");
  anonymousReplacement.send(["CLOSE", "same-id"]);

  // Replace an in-flight denied private query with a public query on the same ID.
  f.set({decision:"deny", delay:0.3});
  const replacement = await f.connect(outsider);
  replacement.send(["REQ", "replace", {kinds:[1]}]);
  await delay(100);
  replacement.send(["REQ", "replace", {kinds:[1063], ids:[file.id]}]);
  const replaced = await replacement.collectUntil(m => m[0] === "EOSE" && m[1] === "replace");
  assert.deepEqual(replaced.filter(m => m[0] === "EVENT").map(m => m[2].id), [file.id]);
  await delay(400);
  assert(!replacement.queue.some(m => m[0] === "CLOSED"), "late denial cannot close replacement public subscription");

  // Also invalidate a connection recheck when its last private sub becomes public.
  f.set({decision:"allow"});
  const rechecking = await f.connect(member);
  rechecking.send(["REQ", "recheck", {kinds:[1], limit:0}]);
  await rechecking.waitFor(m => m[0] === "EOSE" && m[1] === "recheck");
  const beforeRecheck = f.calls().length;
  f.set({decision:"deny", delay:0.5});
  for (let i = 0; f.calls().length === beforeRecheck; ++i) {
    assert(i < 100, "periodic recheck started");
    await delay(20);
  }
  rechecking.send(["REQ", "recheck", {kinds:[1063]}]);
  await rechecking.waitFor(m => m[0] === "EOSE" && m[1] === "recheck");
  await delay(700);
  assert.equal((await f.read(rechecking, {kinds:[1063]})).at(-1)[0], "EOSE", "late recheck denial cannot close public-only service");

  // Plugin failure on another connection must not interrupt public-only readers.
  f.set({malformed:true});
  const denied = await f.connect(owner);
  assert.match((await f.read(denied, {kinds:[1]})).at(-1)[2], /^error:/);
  const incoming = descriptor(owner);
  assert.equal((await f.publish(await f.connect(owner), incoming))[2], true);
  assert.equal((await foreign.waitFor(m => m[0] === "EVENT" && m[1] === "public" && m[2].id === incoming.id))[2].id, incoming.id);
  assert.equal((await f.read(anon, {kinds:[1063]})).at(-1)[0], "EOSE");

  // Replacing public with private invokes policy instead of retaining the bypass.
  f.set({decision:"deny"});
  replacement.send(["REQ", "replace", {kinds:[1]}]);
  assert.match((await replacement.waitFor(m => m[0] === "CLOSED" && m[1] === "replace"))[2], /^restricted:/);
  assert(!replacement.queue.some(m => m[0] === "EVENT" && m[2].id === privateNote.id));
  console.log("PASS public-kind filter isolation, anonymous/live reads, replacements and policy failure isolation");
} catch (error) { console.error(f.logs()); throw error; }
finally { await f.stop(); }
