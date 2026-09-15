// Session-owned loopback fixture. No live keys, accounts or external relays.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { finalizeEvent, getPublicKey } from "@nostr/tools";
import WebSocket from "ws";
import { waitForRelay, WsClient } from "./websocketClient.js";

export const identities = [21, 22, 23].map(n => {
  const secret = new Uint8Array(32).fill(n);
  return { secret, pubkey: getPublicKey(secret) };
});
export const [owner, member, outsider] = identities;
export const community = "c".repeat(64), branch = `32222:${owner.pubkey}:${community}`;
export const service = "wss://relay.test/private";
let timestamp = Math.floor(Date.now() / 1000) - 300;
export const event = (who, kind, tags = [], content = "") => finalizeEvent({ kind, tags, content, created_at: timestamp++ }, who.secret);
export const definition = () => event(owner, 32222, [
  ["d", community], ["name", "Admission fixture"], ["r", service], ["read-access", "members"],
  ["content", "General"], ["k", "1"], ["k", "1984"], ["a", `30000:${owner.pubkey}:${community}-general`],
]);
export const grant = keys => event(owner, 30000, [["d", `${community}-general`], ...keys.map(key => ["p", key.pubkey])]);
export const report = key => event(owner, 1984, [["h", community], ["a", branch, "", "community"], ["p", key.pubkey, "spam"]]);
export const quote = value => `'${value.replaceAll("'", "'\\''")}'`;

export async function fixture({ budabit = false, publicReads = false, authEnabled = true, port = 40582, extra = "", maxPending = 1024, recheckSeconds = 1, scanFailure = false } = {}) {
  const work = mkdtempSync(path.join(os.tmpdir(), "strfry-admission-"));
  const db = path.join(work, "db"), config = path.join(work, "relay.conf"), control = path.join(work, "control.json"), calls = path.join(work, "calls.jsonl");
  const readPlugin = path.join(work, "read-plugin"), writePlugin = path.join(work, "write-plugin");
  const url = `ws://127.0.0.1:${port}`;
  const clients = [];
  let child, logs = "", nextSub = 0;
  mkdirSync(db);
  const env = { ...process.env, BUDABIT_READ_CONTROL: "members", BUDABIT_BRANCHES: branch,
    BUDABIT_AUTO_HOST_URL: "", BUDABIT_DRY_RUN: "0", BUDABIT_DISABLE_LOADER: "0",
    BUDABIT_STRFRY_BIN: path.resolve("strfry"), STRFRY_CONFIG: config,
    STRFRY_POLICY_DB_FILE: path.join(db, "data.mdb"), STRFRY_POLICY_MIN_FREE_BYTES: "0",
    BUDABIT_READ_REFRESH_SECONDS: "0.1", BUDABIT_READ_MAX_AGE_SECONDS: "3", BUDABIT_READ_SCAN_TIMEOUT_SECONDS: "2",
    STRFRY_POLICY_PUBKEY_CAPACITY: "10000", STRFRY_POLICY_IP_CAPACITY: "10000", STRFRY_POLICY_GLOBAL_CAPACITY: "10000" };
  if (scanFailure) {
    const scanner = path.join(work, "scanner");
    writeFileSync(scanner, `#!/bin/sh\n[ ! -e ${quote(path.join(work, "fail-scan"))} ] || exit 1\nexec ${quote(path.resolve("strfry"))} "$@"\n`, {mode:0o700});
    env.BUDABIT_STRFRY_BIN = scanner;
  }
  writeFileSync(control, JSON.stringify({ keys: [owner.pubkey, member.pubkey] }));
  writeFileSync(readPlugin, budabit
    ? `#!/bin/sh\necho $$ > ${quote(path.join(work, "read.pid"))}\nexec python3 ${quote(path.resolve("deploy/budabit/read-policy.py"))}\n`
    : `#!/usr/bin/env python3
import json,os,sys,time
open(${JSON.stringify(path.join(work, "read.pid"))},'w').write(str(os.getpid()))
for line in sys.stdin:
 r=json.loads(line)
 with open(${JSON.stringify(calls)},'a') as f: f.write(json.dumps(r)+'\\n')
 c=json.load(open(${JSON.stringify(control)}))
 time.sleep(c.get('delay',0))
 if c.get('exit'): sys.exit(1)
 if c.get('oversized'): print('x'*5000,flush=True); continue
 if c.get('malformed'): print('{',flush=True); continue
 decision=c.get('decision') or ('allow' if set(r['authenticated_pubkeys']) & set(c.get('keys',[])) else 'deny')
 print(json.dumps({'request_id': 'wrong' if c.get('wrong') else r['request_id'], 'decision':decision}),flush=True)
`, { mode: 0o700 });
  writeFileSync(writePlugin, `#!/bin/sh\necho $$ > ${quote(path.join(work, "write.pid"))}\nexec python3 ${quote(path.resolve("deploy/budabit/write-policy.py"))}\n`, { mode: 0o700 });
  const metadata = JSON.stringify({ budabit: { read_control: { version: 2, mode: "members", scope: "relay", unfiltered_kinds: [1,5,1984,30000,32222] } } });
  writeFileSync(config, `db = "${db}"
relay {
 bind = "127.0.0.1"
 port = ${port}
 nofiles = 0
 autoPingSeconds = 0
 maxFilterLimit = 500
 maxFilterLimitCount = ${publicReads ? 100000 : 0}
 auth { enabled = ${authEnabled}
 serviceUrl = "${service}"
 restrictedReadKinds = ""
 restrictReadToInvolvedPubkey = false }
 negentropy { enabled = ${publicReads} }
 readPolicy { plugin = "${publicReads ? "" : readPlugin}"
 recheckSeconds = ${recheckSeconds}
 timeoutSeconds = 2
 maxPending = ${maxPending} }
 writePolicy { plugin = "${budabit ? writePlugin : ""}" }
 info { extra = ${JSON.stringify(publicReads ? "" : metadata)} }
 numThreads { ingester = 1
 reqWorker = 1
 reqMonitor = 1
 negentropy = 1 }
 ${extra}
}
`);
  const api = { work, db, config, control, env, url,
    pid: () => child.pid,
    logs: () => logs,
    calls: () => { try { return readFileSync(calls, "utf8").trim().split("\n").filter(Boolean).map(JSON.parse); } catch { return []; } },
    set: value => writeFileSync(control, JSON.stringify(value)),
    import: events => {
      const result = spawnSync("./strfry", ["--config", config, "import"], {env, input: events.map(JSON.stringify).join("\n") + "\n", encoding: "utf8"});
      assert.equal(result.status, 0, result.stderr);
    },
    async connect(who) {
      const ws = new WebSocket(url), client = new WsClient(ws);
      clients.push(client);
      await once(ws, "open", {signal: AbortSignal.timeout(4000)});
      if (!authEnabled) { assert(!who); return client; }
      if (publicReads) client.send(["REQ", "challenge", {kinds: [4444]}]);
      client.challenge = (await client.waitFor(m => m[0] === "AUTH"))[1];
      if (who) await api.auth(client, who);
      return client;
    },
    async auth(client, who) {
      const proof = finalizeEvent({kind:22242, created_at: Math.floor(Date.now()/1000), content:"",
        tags:[["relay", service], ["challenge",client.challenge]]}, who.secret);
      client.send(["AUTH", proof]);
      const result = await client.waitFor(m => m[0] === "OK" && m[1] === proof.id);
      assert.equal(result[2], true, JSON.stringify(result));
    },
    async publish(client, ev) {
      client.send(["EVENT", ev]);
      return client.waitFor(m => m[0] === "OK" && m[1] === ev.id, 5000);
    },
    async read(client, filter = {}) {
      const id = `read-${++nextSub}`;
      client.send(["REQ", id, filter]);
      const result = await client.collectUntil(m => (m[0] === "EOSE" || m[0] === "CLOSED") && m[1] === id, 5000);
      if (result.at(-1)[0] === "EOSE") client.send(["CLOSE", id]);
      return result.filter(m => m[1] === id);
    },
    async stop() {
      for (const client of clients) client.ws.terminate();
      if (child && child.exitCode === null && child.signalCode === null) {
        const ended = once(child, "exit"); child.kill("SIGTERM"); await ended;
      }
      for (const name of ["read", "write"]) {
        try { process.kill(-Number(readFileSync(path.join(work, `${name}.pid`))), "SIGKILL"); } catch {}
      }
      rmSync(work, {recursive:true, force:true});
    },
  };
  api.import([]);
  child = spawn("./strfry", ["--config", config, "relay"], {env, stdio:["ignore","pipe","pipe"]});
  child.stdout.on("data", data => logs += data);
  child.stderr.on("data", data => logs += data);
  try { await waitForRelay(url); } catch (error) { await api.stop(); throw Error(`${error}\n${logs}`); }
  return api;
}

export async function eventuallyMember(f, who, filter = {}) {
  for (let i = 0; i < 40; ++i) {
    const client = await f.connect(who);
    const result = await f.read(client, filter);
    if (result.at(-1)[0] === "EOSE") return client;
    client.ws.terminate();
    await delay(100);
  }
  throw Error(`membership never became available\n${f.logs()}`);
}
