// Isolated raw-client privacy tests. Controlled keys; no external connections.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync, unlinkSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { finalizeEvent, getPublicKey } from "@nostr/tools";
import WebSocket from "ws";
import { waitForRelay, WsClient } from "../utils/websocketClient.js";

const work = mkdtempSync(path.join(os.tmpdir(), "strfry-private-"));
const db = path.join(work, "db"), cfg = path.join(work, "private.conf");
const readersPath = path.join(db, "readers.json"), pidPath = path.join(work, "plugin.pid");
const wrapper = path.join(work, "plugin");
const scanner = path.join(work, "scanner"), scanPause = path.join(work, "pause-scan");
const url = "ws://127.0.0.1:40582", service = "wss://relay.test/private";
const identities = [21, 22, 23].map(n => {
  const secret = new Uint8Array(32).fill(n);
  return {secret, pubkey: getPublicKey(secret)};
});
const [owner, member, outsider] = identities;
const community = "c".repeat(64), branch = `32222:${owner.pubkey}:${community}`;
let timestamp = Math.floor(Date.now() / 1000) - 100;
const event = (who, kind, tags = [], content = "") => finalizeEvent({kind, tags, content, created_at: timestamp++}, who.secret);
const definition = () => event(owner, 32222, [
  ["d", community], ["name", "Private integration"], ["r", service],
  ["content", "General"], ["k", "1111"], ["k", "1984"],
  ["a", `30000:${owner.pubkey}:${community}-general`],
]);
const grant = keys => event(owner, 30000, [["d", `${community}-general`], ...keys.map(key => ["p", key.pubkey])]);
const report = key => event(owner, 1984, [["h", community], ["a", branch, "", "community"], ["p", key.pubkey, "spam"]]);
let relay, logs = "";
let step = "startup";
const clients = [];
function current() { try { return JSON.parse(readFileSync(readersPath)); } catch { return {}; } }
async function until(predicate, label, timeout = 10000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (predicate()) return;
    if (relay && (relay.exitCode !== null || relay.signalCode !== null)) throw new Error(`relay exited: ${logs}`);
    await delay(30);
  }
  throw new Error(`${label} timed out\n${logs}`);
}
async function connect() {
  step = "connect/proactive AUTH";
  // A proactive AUTH can arrive in the same TCP chunk as the HTTP upgrade.
  // Install the inbox BEFORE awaiting open, not in its promise continuation.
  const ws = new WebSocket(url);
  const client = new WsClient(ws);
  clients.push(client);
  await once(ws, "open", {signal: AbortSignal.timeout(4000)});
  client.challenge = (await client.waitFor(m => m[0] === "AUTH"))[1];
  return client;
}
async function auth(client, who, relayUrl = service, accepted = true) {
  step = `AUTH ${who.pubkey.slice(0, 8)}`;
  const proof = finalizeEvent({kind: 22242, content: "", created_at: Math.floor(Date.now() / 1000),
    tags: [["relay", relayUrl], ["challenge", client.challenge]]}, who.secret);
  client.send(["AUTH", proof]);
  const result = await client.waitFor(m => m[0] === "OK" && m[1] === proof.id);
  assert.equal(result[2], accepted, JSON.stringify(result));
}
async function publish(client, ev) {
  step = `publish kind ${ev.kind}`;
  client.send(["EVENT", ev]);
  return await client.waitFor(m => m[0] === "OK" && m[1] === ev.id, 12000);
}
async function accepted(client, ev) {
  const result = await publish(client, ev);
  assert.equal(result[2], true, JSON.stringify(result));
}
let subId = 0;
async function denied(client, filter, prefix) {
  const id = `deny-${++subId}`;
  client.send(["REQ", id, filter]);
  const result = await client.collectUntil(m => m[0] === "CLOSED" && m[1] === id);
  assert(!result.some(m => m[0] === "EVENT" || m[0] === "EOSE"), "denied request leaked data/completeness");
  assert(result.at(-1)[2].startsWith(prefix), JSON.stringify(result));
}
async function read(client, filter = {}) {
  step = "historical read";
  const id = `read-${++subId}`;
  client.send(["REQ", id, filter]);
  const result = await client.collectUntil(m => m[0] === "EOSE" && m[1] === id, 6000);
  client.send(["CLOSE", id]);
  return result.filter(m => m[0] === "EVENT" && m[1] === id).map(m => m[2]);
}
const configText = (extra = "") => `
db = "${db}/"
relay {
 bind = "127.0.0.1"
 port = 40582
 nofiles = 0
 autoPingSeconds = 0
 maxFilterLimitCount = 0
 auth { enabled = true\n serviceUrl = "${service}" }
 negentropy { enabled = false }
 readControl { enabled = true\n branchAddress = "${branch}"\n snapshotPath = "${readersPath}" }
 writePolicy { plugin = "${wrapper}"\n timeoutSeconds = 2 }
 numThreads { ingester = 2\n reqWorker = 2\n reqMonitor = 2\n negentropy = 1 }
 ${extra}
}
`;
async function stop() {
  if (relay && relay.exitCode === null && relay.signalCode === null) {
    const exited = once(relay, "exit");
    relay.kill("SIGTERM");
    await exited;
  }
  relay = null;
  // strfry's existing SIGTERM path does not destruct stack-owned plugins.
  try { process.kill(-Number(readFileSync(pidPath)), "SIGKILL"); } catch {}
}
async function start() {
  const previousEpoch = current().epoch;
  relay = spawn("./strfry", ["--config", cfg, "relay"], {stdio: ["ignore", "pipe", "pipe"]});
  relay.stdout.on("data", data => logs += data);
  relay.stderr.on("data", data => logs += data);
  await waitForRelay(url);
  await until(() => current().ready === true && current().epoch !== previousEpoch, "ready snapshot/new epoch");
  // Allow the native watcher to install the independently written file.
  await delay(150);
}
try {
  mkdirSync(db);
  writeFileSync(cfg, configText());
  const env = {
    BUDABIT_READ_CONTROL: "members", BUDABIT_BRANCHES: branch,
    BUDABIT_AUTO_HOST_URL: "", BUDABIT_DRY_RUN: "0", BUDABIT_DISABLE_LOADER: "0",
    BUDABIT_READ_SNAPSHOT_PATH: readersPath, BUDABIT_STRFRY_BIN: scanner,
    STRFRY_CONFIG: cfg, STRFRY_POLICY_DB_FILE: path.join(db, "data.mdb"),
    STRFRY_POLICY_MIN_FREE_BYTES: "0", STRFRY_POLICY_PUBKEY_CAPACITY: "10000",
    STRFRY_POLICY_IP_CAPACITY: "10000", STRFRY_POLICY_GLOBAL_CAPACITY: "10000",
  };
  const quote = value => `'${value.replaceAll("'", "'\\''")}'`;
  writeFileSync(scanner, `#!/bin/sh\nwhile [ -f ${quote(scanPause)} ]; do sleep 0.02; done\nexec ${quote(path.resolve("strfry"))} "$@"\n`, {mode: 0o700});
  writeFileSync(wrapper, `#!/bin/sh\necho $$ > ${quote(pidPath)}\n${Object.entries(env).map(([k,v]) => `export ${k}=${quote(v)}\n`).join("")}exec python3 ${quote(path.resolve("deploy/budabit/write-policy.py"))}\n`, {mode: 0o700});
  const preflight = (extra = [], overrides = {}) => spawnSync("python3", ["deploy/budabit/check-read-control.py", "--config", cfg, ...extra], {
    env: {...process.env, ...env, ...overrides}, encoding: "utf8", timeout: 8000,
  });
  assert.equal(preflight(["--config-only"]).status, 0, "fresh private config passes before any snapshot exists");
  assert.notEqual(preflight(["--config-only"], {BUDABIT_READ_CONTROL: "off"}).status, 0, "mismatched Python mode must not start");
  await start();
  assert.equal(preflight(["--url", "http://127.0.0.1:40582/"]).status, 0, "live snapshot and serving capability preflight");
  const anonymous = await connect();
  const secretNote = event(owner, 1, [], "retained private note");
  const noAuthWrite = await publish(anonymous, secretNote);
  assert.equal(noAuthWrite[2], false);
  assert.match(noAuthWrite[3], /^auth-required:/);
  for (const filter of [{}, {ids: [secretNote.id]}, {"#h": [community]}, {limit: 0}])
    await denied(anonymous, filter, "auth-required:");
  const admin = await connect();
  await auth(admin, owner, "wss://relay.test/wrong", false);
  await auth(admin, owner);
  assert.deepEqual(await read(admin), [], "owner can bootstrap an empty committed relay");
  await accepted(admin, definition());
  await until(() => current().seq === 1 && current().ready, "definition activation");
  await accepted(admin, secretNote);
  const reader = await connect();
  await auth(reader, member);
  const stranger = await connect();
  await auth(stranger, outsider);
  for (const client of [reader, stranger]) await denied(client, {}, "restricted:");
  await accepted(admin, grant([member]));
  await until(() => current().seq === 2 && current().ready, "grant activation");
  await delay(150);
  assert((await read(reader)).some(ev => ev.id === secretNote.id), "grant enables retained history without new AUTH");
  await denied(stranger, {ids: [secretNote.id]}, "restricted:");
  for (const client of [anonymous, stranger, reader]) {
    client.send(["COUNT", "count", {}]);
    const result = await client.waitFor(m => m[0] === "CLOSED" && m[1] === "count");
    assert(!result.includes("COUNT"));
    client.send(["NEG-OPEN", "neg", {}, "60"]);
    const neg = await client.waitFor(m => m[0] === "NEG-ERR");
    assert.match(neg[2], /disabled/);
  }
  // Authenticating a second (non-member) key must not erase the first key.
  await auth(reader, outsider);
  assert((await read(reader)).some(ev => ev.id === secretNote.id));
  const proofAsEvent = finalizeEvent({kind: 22242, content: "", created_at: Math.floor(Date.now() / 1000),
    tags: [["relay", service], ["challenge", admin.challenge]]}, owner.secret);
  const rejectedAuthEvent = await publish(admin, proofAsEvent);
  assert.equal(rejectedAuthEvent[2], false);
  assert.match(rejectedAuthEvent[3], /only accepted via AUTH/);
  reader.send(["REQ", "live", {kinds: [1], limit: 0}]);
  await reader.waitFor(m => m[0] === "EOSE" && m[1] === "live");
  // Force a genuine committed-projection pause, not just a mocked predicate.
  writeFileSync(scanPause, "hold controlled scanner");
  await accepted(admin, grant([member, outsider]));
  await until(() => current().ready === false && current().seq === 3, "closed sequence gate");
  assert.notEqual(preflight().status, 0, "pending newer commit must be unhealthy");
  // A newer authority commit arrives while the prior rebuild is blocked. Its
  // final snapshot must not inherit the earlier scan's label/grant.
  await accepted(admin, grant([member]));
  const duringGate = event(owner, 1, [], "must resume after gated DBChange");
  await accepted(admin, duringGate);
  await delay(200);
  assert(!reader.queue.some(m => m[0] === "EVENT" && m[2].id === duringGate.id));
  unlinkSync(scanPause);
  await reader.waitFor(m => m[0] === "EVENT" && m[2].id === duringGate.id, 6000);
  await until(() => current().ready && current().seq === 4, "superseding committed projection");
  await denied(stranger, {}, "restricted:");
  const live = event(owner, 1, [], "before revocation");
  await accepted(admin, live);
  await reader.waitFor(m => m[0] === "EVENT" && m[2].id === live.id);
  const closed = once(reader.ws, "close");
  const ban = report(member);
  await accepted(admin, ban);
  await Promise.race([closed, delay(8000).then(() => { throw new Error("revoked live socket stayed open"); })]);
  await denied(stranger, {}, "restricted:");
  await accepted(admin, event(owner, 5, [["e", ban.id], ["h", community]]));
  await until(() => current().ready && current().eligible_pubkeys.includes(member.pubkey), "regrant after retraction");
  await delay(150);
  const restored = await connect();
  await auth(restored, member);
  assert((await read(restored)).some(ev => ev.id === secretNote.id));
  const info = await (await fetch("http://127.0.0.1:40582", {headers: {accept: "application/nostr+json"}})).json();
  assert.equal(info.limitation.auth_required, true);
  assert.deepEqual(info.budabit.read_control, {version: 1, mode: "members", scope: "relay", unfiltered_kinds: [1, 5, 1984, 30000, 32222]});
  assert(!info.supported_nips.includes(45) && !info.supported_nips.includes(77));
  assert(!JSON.stringify(info).includes(branch));
  // Corruption/removal/oversize/old-epoch files are failures, never old-set fallback.
  const healthy = current();
  const policyPid = Number(readFileSync(pidPath));
  process.kill(policyPid, "SIGSTOP");
  for (const contents of ["{broken", "x".repeat(2097153),
    JSON.stringify({...healthy, epoch: "0".repeat(64)}), null]) {
    if (contents === null) unlinkSync(readersPath);
    else writeFileSync(readersPath, contents);
    await delay(150);
    assert.notEqual(preflight().status, 0, "rejected snapshot must fail serving health");
    await denied(stranger, {ids: [secretNote.id]}, "error: community read policy temporarily unavailable");
  }
  process.kill(policyPid, "SIGCONT");
  await until(() => current().ready && current().heartbeat > healthy.heartbeat, "snapshot repair");
  await delay(150);
  assert((await read(restored)).some(ev => ev.id === secretNote.id));
  assert.equal(preflight().status, 0, "repaired installed projection restores health");
  // Expiring the sole grant is an internal policy mutation, not an EVENT write.
  const expiredConnection = once(restored.ws, "close");
  await accepted(admin, event(owner, 30000, [["d", `${community}-general`], ["p", member.pubkey],
    ["expiration", String(Math.floor(Date.now() / 1000) + 2)]]));
  // Cron intentionally retains the newest levId; a later ordinary event lets
  // this grant be collected and exercises its private writer routing.
  await accepted(admin, event(owner, 1, [], "expiry sentinel"));
  await Promise.race([expiredConnection, delay(12000).then(() => { throw new Error("expired grant did not revoke"); })]);
  await accepted(admin, grant([member]));
  await until(() => current().ready && current().eligible_pubkeys.includes(member.pubkey), "post-expiry regrant");
  await delay(150);
  const idleReader = await connect();
  await auth(idleReader, member);
  const oldEpoch = current().epoch;
  const idleClosed = once(idleReader.ws, "close");
  process.kill(Number(readFileSync(pidPath)), "SIGSTOP");
  await Promise.race([idleClosed, delay(9000).then(() => { throw new Error("stopped idle plugin left private socket live"); })]);
  await until(() => current().ready && current().epoch !== oldEpoch, "idle plugin recovery", 10000);
  const recovered = await connect();
  await auth(recovered, member);
  await delay(150);
  assert((await read(recovered)).some(ev => ev.id === secretNote.id));
  assert.equal(preflight().status, 0, "fresh supervisor epoch restores serving health");
  // A frozen core cannot remain healthy just because Python has a fresh artifact.
  relay.kill("SIGSTOP");
  try {
    await delay(1100);
    assert.notEqual(preflight().status, 0, "stopped serving core must be unhealthy");
  } finally { relay.kill("SIGCONT"); }
  await delay(150);
  console.log("PASS private relay: anonymous/outsider/member, owner bootstrap, ACK writes, full history, multi-key AUTH, live ban/regrant, COUNT/NEG, NIP-11, idle failure/restart");
  await stop();
  // Import only while stopped. Deliberately backpressure a large historical
  // read and assert termination instead of unchecked uWS queued drains/EOSE.
  writeFileSync(cfg, configText("compression { enabled = false }"));
  const bulk = Array.from({length: 256}, (_, i) => event(owner, 1, [], `${i}:` + "x".repeat(60000)));
  const imported = spawnSync("./strfry", ["--config", cfg, "import"], {
    encoding: "utf8", input: bulk.map(ev => JSON.stringify(ev)).join("\n") + "\n", maxBuffer: 4 * 1024 * 1024,
  });
  assert.equal(imported.status, 0, imported.stderr);
  await start();
  const slow = await connect();
  await auth(slow, member);
  const priorSlowLog = logs.length;
  slow.ws._socket.pause();
  slow.send(["REQ", "slow-history", {kinds: [1], limit: 10000}]);
  await until(() => logs.slice(priorSlowLog).includes("Slow client:"), "private backpressure termination");
  const slowClosed = once(slow.ws, "close");
  slow.ws._socket.resume();
  await Promise.race([slowClosed, delay(8000).then(() => { throw new Error("slow client did not disconnect"); })]);
  assert(!slow.queue.some(m => m[0] === "EOSE" && m[1] === "slow-history"), "interrupted history must not claim EOSE");
  await stop();
  console.log("PASS deferred DBChange, malformed/missing/oversize/old-epoch files, cron expiry revocation, restart, and private historical backpressure");
  // Core configuration fails before opening a socket in unsafe private presets.
  for (const [name, text] of [
    ["AUTH disabled", configText().replace("auth { enabled = true", "auth { enabled = false")],
    ["COUNT enabled", configText().replace("maxFilterLimitCount = 0", "maxFilterLimitCount = 100")],
    ["NEG enabled", configText().replace("negentropy { enabled = false", "negentropy { enabled = true")],
    ["unpinned branch", configText().replace(`branchAddress = "${branch}"`, 'branchAddress = ""')],
  ]) {
    writeFileSync(cfg, text);
    const result = spawnSync("./strfry", ["--config", cfg, "relay"], {encoding: "utf8", timeout: 3000});
    assert.notEqual(result.status, 0, name);
    assert.equal(result.signal, null, name + " must fail, not hang until killed");
    assert.match(result.stderr, /private reads/);
  }
  // Roll back a rejected configuration to the last private-capable preset. Never
  // test rollback by disabling reads or using a pre-gate binary against this DB.
  writeFileSync(cfg, configText());
  await start();
  assert.equal(preflight(["--url", "http://127.0.0.1:40582/"]).status, 0);
  const rollbackAnon = await connect();
  await denied(rollbackAnon, {}, "auth-required:");
  const rollbackOutsider = await connect();
  await auth(rollbackOutsider, outsider);
  await denied(rollbackOutsider, {}, "restricted:");
  const rollbackMember = await connect();
  await auth(rollbackMember, member);
  assert((await read(rollbackMember, {kinds: [1], limit: 1})).length > 0);
  console.log("PASS fresh private preflight, config/env mismatch, and private-capable rollback probes");
} catch (error) {
  console.error(`Private test failed at ${step}\n${logs.slice(-20000)}`);
  throw error;
} finally {
  for (const client of clients) client.ws.terminate();
  await stop();
  rmSync(work, {recursive: true, force: true});
}
