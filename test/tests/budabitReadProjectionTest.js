// Real-LMDB smoke of the JSONL/snapshot contract. No relay/network is opened.
// C++ enforcement is tested separately; this test simulates only its notices.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { finalizeEvent, getPublicKey } from "@nostr/tools";

const work = mkdtempSync(path.join(os.tmpdir(), "strfry-reader-projection-"));
const db = path.join(work, "db");
const config = path.join(work, "strfry.conf");
const snapshot = path.join(db, "readers.json");
const secret = new Uint8Array(32).fill(18);
const owner = getPublicKey(secret);
const member = getPublicKey(new Uint8Array(32).fill(19));
const community = "a".repeat(64);
const branch = `32222:${owner}:${community}`;
const epoch = "b".repeat(64);
const plugin = path.resolve("deploy/budabit/write-policy.py");
let timestamp = Math.floor(Date.now() / 1000) - 30;
const sign = (kind, tags) => finalizeEvent({kind, tags, created_at: timestamp++, content: ""}, secret);
const definition = sign(32222, [
  ["d", community], ["name", "Private smoke"], ["r", "wss://relay.test"],
  ["content", "General"], ["k", "1111"], ["k", "1984"],
  ["a", `30000:${owner}:${community}-general`],
]);
const grant = sign(30000, [["d", `${community}-general`], ["p", member]]);
const ban = sign(1984, [["h", community], ["a", branch, "", "community"], ["p", member, "spam"]]);
const env = {
  ...process.env,
  BUDABIT_BRANCHES: branch, BUDABIT_READ_CONTROL: "members", BUDABIT_DRY_RUN: "0",
  BUDABIT_AUTO_HOST_URL: "", BUDABIT_DISABLE_LOADER: "0",
  BUDABIT_READ_SNAPSHOT_PATH: snapshot, BUDABIT_STRFRY_BIN: path.resolve("strfry"),
  STRFRY_CONFIG: config, STRFRY_POLICY_DB_FILE: path.join(db, "data.mdb"),
  STRFRY_POLICY_MIN_FREE_BYTES: "0",
};
let child;
let output = "", errors = "";
const send = value => child.stdin.write(JSON.stringify(value) + "\n");
const importEvents = events => {
  const result = spawnSync("./strfry", ["--config", config, "import"], {
    encoding: "utf8", input: events.map(event => JSON.stringify(event)).join("\n") + "\n",
  });
  assert.equal(result.status, 0, result.stderr);
};
async function waitFor(predicate, label) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    assert.equal(child.exitCode, null, errors);
    await delay(25);
  }
  throw new Error(`${label} timed out: ${errors}`);
}
const current = () => {
  try { return JSON.parse(readFileSync(snapshot)); } catch { return {}; }
};
try {
  mkdirSync(db);
  writeFileSync(config, `db = "${db}/"\nrelay { nofiles = 0 }\n`);
  importEvents([definition]);
  child = spawn("python3", [plugin], {env, stdio: ["pipe", "pipe", "pipe"]});
  child.stdout.on("data", data => output += data);
  child.stderr.on("data", data => errors += data);
  send({type: "read-control-init", epoch, seq: 0, branch_address: branch});
  await waitFor(() => current().ready === true, "initial snapshot");
  assert.deepEqual(current().eligible_pubkeys, [owner]);
  await waitFor(() => errors.includes('"initial_load_complete"'), "write loader");
  send({type: "new", event: grant, receivedAt: timestamp, sourceType: "Import", sourceInfo: "isolated-test"});
  await waitFor(() => output.includes(grant.id), "write response");
  const ack = JSON.parse(output.trim());
  assert.equal(ack.action, "accept");
  assert.equal(ack.policyRelevant, true);
  assert(!current().eligible_pubkeys.includes(member), "acceptance is not committed read authority");
  importEvents([grant]);
  send({type: "committed", epoch, seq: 1});
  await waitFor(() => current().ready && current().seq === 1, "grant rebuild");
  assert(current().eligible_pubkeys.includes(member));
  importEvents([ban]);
  send({type: "committed", epoch, seq: 2});
  await waitFor(() => current().ready && current().seq === 2, "ban rebuild");
  assert(!current().eligible_pubkeys.includes(member));
  const health = spawnSync("python3", [plugin, "--check-read-policy", epoch, "2"], {env, encoding: "utf8"});
  assert.equal(health.status, 0, health.stderr);
  assert.equal(spawnSync("python3", [plugin, "--check-read-policy", epoch, "1"], {env}).status, 1);
  assert.equal(output.trim().split("\n").length, 1, "controls must not contaminate JSONL stdout");
  const exited = once(child, "exit");
  child.stdin.end();
  await exited;
  assert.equal(current().ready, false, "EOF closes projection");
  console.log("PASS real LMDB projection: bootstrap, acceptance vs commit, grant/ban, local health, response-less controls, EOF");
} finally {
  if (child && child.exitCode === null && child.signalCode === null) {
    const exited = once(child, "exit");
    child.kill("SIGKILL");
    await exited;
  }
  rmSync(work, {recursive: true, force: true}); // only this invocation's mkdtemp
}
