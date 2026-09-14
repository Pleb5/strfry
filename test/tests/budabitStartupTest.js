// Exercise the real, cold auto-host ingestion process against a seeded LMDB.
// A test-only scanner gate makes the discovery race deterministic; it is not
// a production policy option. All signing and publishing stay on localhost.
// Run from the repository root: node test/tests/budabitStartupTest.js

import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, unlinkSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import os from "node:os";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { finalizeEvent, getPublicKey } from "@nostr/tools";
import { hexToBytes, sha256 } from "../utils/events.js";
import { openWebSocket, waitForRelay, WsClient } from "../utils/websocketClient.js";

const workDir = mkdtempSync(path.join(os.tmpdir(), "strfry-budabit-startup-"));
const dbDir = path.join(workDir, "db");
const cfgPath = path.join(workDir, "strfry.conf");
const binary = path.resolve("strfry");
const policyRoot = path.resolve(process.env.BUDABIT_TEST_POLICY_ROOT || "deploy/budabit");
const pluginPath = path.join(policyRoot, "write-policy.py");
const launcher = path.join(workDir, "policy-launcher.py");
const scanner = path.join(workDir, "gated-scanner.py");
const entered = path.join(workDir, "scan-entered");
const release = path.join(workDir, "scan-release");
const failScan = path.join(workDir, "scan-fail");
const community = "c".repeat(64);
const autoHost = "wss://relay.test";
const loading = "error: relay policy is loading, retry shortly";

function identity(name) {
  const sec = sha256(`budabit-startup-test:${name}`);
  return { sec, pub: getPublicKey(hexToBytes(sec)) };
}

const owner = identity("owner");
const member = identity("member");
const outsider = identity("outsider");
let timestamp = Math.floor(Date.now() / 1000) - 100;
let subscription = 0;

function sign(who, kind, tags, content = "isolated startup regression") {
  return finalizeEvent({ kind, tags, content, created_at: timestamp++ }, hexToBytes(who.sec));
}

const definitionTags = [
  ["d", community], ["name", "Startup fixture"], ["r", autoHost],
  ["content", "General"], ["k", "9", "room-message"], ["k", "1111"],
  ["a", `30000:${owner.pub}:${community}-general`],
  ["content", "Room-creator"], ["k", "11", "room"],
  ["a", `30000:${owner.pub}:${community}-room-creator`],
  ["content", "Thread-creator"], ["k", "11", "threads"],
  ["a", `30000:${owner.pub}:${community}-thread-creator`],
];
const definition = sign(owner, 32222, definitionTags, "");
const room = sign(owner, 11, [["h", community], ["room"], ["title", "Fixture room"]]);
const rootThread = sign(owner, 11, [["h", community], ["title", "Fixture thread"]]);
const seed = [definition, room, rootThread, ...["general", "room-creator", "thread-creator"].map(
  (section) => sign(owner, 30000, [["d", `${community}-${section}`], ["p", member.pub]], ""),
)];
const thread = (who) => sign(who, 11, [["h", community], ["title", "Thread test"]]);
const roomMessage = () => sign(outsider, 9, [
  ["h", community], ["E", room.id, "", owner.pub], ["K", "11"],
  ["e", room.id, "", owner.pub], ["k", "11"], ["p", owner.pub],
]);

const env = {
  ...process.env,
  PYTHONDONTWRITEBYTECODE: "1",
  STRFRY_CONFIG: cfgPath,
  STRFRY_POLICY_DB_FILE: path.join(dbDir, "data.mdb"),
  STRFRY_POLICY_MIN_FREE_BYTES: "0",
  STRFRY_POLICY_MAX_DB_BYTES: "10737418240",
  STRFRY_POLICY_PUBKEY_CAPACITY: "1000",
  STRFRY_POLICY_IP_CAPACITY: "1000",
  STRFRY_POLICY_GLOBAL_CAPACITY: "1000",
  BUDABIT_BRANCHES: "",
  BUDABIT_AUTO_HOST_URL: autoHost,
  BUDABIT_MODE: "passthrough",
  BUDABIT_DRY_RUN: "0",
  BUDABIT_DISABLE_LOADER: "0",
  BUDABIT_RECONCILE_SECONDS: "300",
  BUDABIT_SCAN_TIMEOUT_SECONDS: "30",
  BUDABIT_STRFRY_BIN: scanner,
};

function expect(condition, message) {
  if (!condition) throw new Error(message);
}

async function waitUntil(predicate, label, timeout = 8_000) {
  const deadline = Date.now() + timeout;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error(`timed out: ${label}`);
    await delay(20);
  }
}

async function freePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const port = server.address().port;
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return port;
}

async function publish(client, event) {
  client.send(["EVENT", event]);
  const response = await client.waitFor((m) => m[0] === "OK" && m[1] === event.id, 5_000);
  return { accepted: response[2], msg: response[3] || "" };
}

async function expectRejected(client, event, message, label) {
  const response = await publish(client, event);
  expect(response.accepted === false, `${label}: expected rejection, got accept`);
  expect(response.msg === message, `${label}: expected "${message}", got "${response.msg}"`);
  console.log(`  ok   ${label} -> ${response.msg}`);
}

async function expectStored(client, events, expected) {
  const sub = `startup-${subscription++}`;
  client.send(["REQ", sub, { ids: events.map((event) => event.id) }]);
  const messages = await client.collectUntil((m) => m[0] === "EOSE" && m[1] === sub, 5_000);
  client.send(["CLOSE", sub]);
  expect(messages.filter((m) => m[0] === "EVENT" && m[1] === sub).length === expected,
    `expected ${expected} stored test events`);
}

async function stopRelay(proc) {
  if (!proc || proc.exitCode !== null || proc.signalCode !== null) return;
  const exited = new Promise((resolve) => proc.once("exit", resolve));
  proc.kill("SIGTERM");
  await Promise.race([exited, delay(3_000)]);
  if (proc.exitCode === null && proc.signalCode === null) {
    proc.kill("SIGKILL");
    await exited;
  }
}

async function main() {
  let proc;
  let client;
  let logs = "";
  let passed = false;
  try {
    const port = await freePort();
    const wsUrl = `ws://127.0.0.1:${port}`;
    mkdirSync(dbDir);
    const launcherSource = `#!/usr/bin/env python3\nimport runpy\nrunpy.run_path(${JSON.stringify(pluginPath)}, run_name="__main__")\n`;
    writeFileSync(launcher, launcherSource, { mode: 0o700 });
    writeFileSync(scanner, `#!/usr/bin/env python3
import json, os, pathlib, sys, time
if "scan" in sys.argv and json.loads(sys.argv[-1]) == {"kinds": [32222]}:
    pathlib.Path(${JSON.stringify(entered)}).touch()
    while not pathlib.Path(${JSON.stringify(release)}).exists():
        time.sleep(0.01)
    if pathlib.Path(${JSON.stringify(failScan)}).exists():
        print("injected initial scan failure", file=sys.stderr)
        sys.exit(1)
os.execv(${JSON.stringify(binary)}, [${JSON.stringify(binary)}, *sys.argv[1:]])
`, { mode: 0o700 });
    writeFileSync(cfgPath, `db = ${JSON.stringify(dbDir)}
relay {
  bind = "127.0.0.1"
  port = ${port}
  nofiles = 0
  autoPingSeconds = 0
  writePolicy {
    plugin = ${JSON.stringify(launcher)}
    timeoutSeconds = 5
  }
  numThreads {
    ingester = 1
    reqWorker = 1
    reqMonitor = 1
    negentropy = 1
  }
}
`);
    const imported = spawnSync(binary, ["--config", cfgPath, "import"], {
      input: seed.map((event) => JSON.stringify(event)).join("\n") + "\n",
      encoding: "utf8", timeout: 15_000,
    });
    expect(imported.status === 0, `fixture import failed: ${imported.stderr}`);
    proc = spawn(binary, ["--config", cfgPath, "relay"], { env, stdio: ["ignore", "pipe", "pipe"] });
    proc.stdout.on("data", (data) => logs += data.toString());
    proc.stderr.on("data", (data) => logs += data.toString());
    await waitForRelay(wsUrl);
    client = new WsClient(await openWebSocket(wsUrl));

    console.log("* a separate successful checker does not initialize ingestion");
    const health = spawnSync("python3", [pluginPath, "--check-policy"], {
      env: { ...env, BUDABIT_STRFRY_BIN: binary }, encoding: "utf8", timeout: 15_000,
    });
    expect(health.status === 0, `independent policy checker failed: ${health.stderr}`);
    const negative = [
      [roomMessage(), "General", "outsider room message"],
      [sign(outsider, 11, [["h", community], ["room"], ["title", "Room test"]]), "Room-creator", "outsider room creation"],
      [thread(outsider), "Thread-creator", "outsider thread creation"],
      [sign(outsider, 1111, [["h", community], ["E", rootThread.id, "", owner.pub], ["K", "11"], ["P", owner.pub]]), "General", "outsider thread reply"],
    ];
    for (const [event, , label] of negative) {
      await expectRejected(client, event, loading, `cold ${label}`);
    }
    await waitUntil(() => existsSync(entered), "ingestion scanner entered the gate");
    const publicNote = sign(outsider, 1, [], "public passthrough after initialization");
    const memberThread = thread(member);
    const newDefinition = sign(outsider, 32222, [
      ["d", "b".repeat(64)], ["name", "New community"], ["r", autoHost],
      ["content", "General"], ["k", "1111"],
    ], "");
    for (const event of [publicNote, memberThread, newDefinition]) {
      await expectRejected(client, event, loading, `cold kind ${event.kind}`);
    }
    await expectStored(client, [...negative.map(([event]) => event), publicNote, memberThread, newDefinition], 0);
    await expectStored(client, seed, seed.length); // Reads still work while loading.

    writeFileSync(release, "continue\n");
    await waitUntil(() => logs.includes('"event":"initial_load_complete"'), "ingestion initial load completed");
    console.log("* initialized ingestion uses grants and normal passthrough");
    for (const [event, section, label] of negative) {
      await expectRejected(client, event,
        `blocked: not a current writer for section "${section}" in ${community.slice(0, 8)}`, `warm ${label}`);
    }
    await expectStored(client, negative.map(([event]) => event), 0);
    for (const event of [publicNote, memberThread, newDefinition]) {
      const result = await publish(client, event);
      expect(result.accepted === true, `initialized kind ${event.kind}: ${result.msg}`);
    }
    await expectStored(client, [publicNote, memberThread, newDefinition], 3);

    console.log("* live plugin reload and failed discovery remain fail-closed");
    const logOffset = logs.length;
    unlinkSync(entered);
    unlinkSync(release);
    writeFileSync(failScan, "fail\n");
    // The single-token executable path is unchanged, but mtime changes. This
    // exercises strfry's real plugin replacement without restarting the relay.
    writeFileSync(launcher, launcherSource + "# reload the ingestion plugin\n");
    await expectRejected(client, negative[0][0], loading, "first write after plugin reload");
    await waitUntil(() => existsSync(entered), "replacement scanner entered the gate");
    writeFileSync(release, "continue into injected failure\n");
    await waitUntil(() => logs.slice(logOffset).includes('"event":"loader_error"'), "initial scan failure logged");
    expect(!logs.slice(logOffset).includes('"event":"initial_load_complete"'), "failed initialization must not report completion");
    await expectRejected(client, negative[2][0], loading, "failed initialization cannot open passthrough");
    const reloadNote = sign(outsider, 1, [], "wait for initial retry");
    await expectRejected(client, reloadNote, loading, "public writes also wait during failed initialization");
    await expectStored(client, [negative[0][0], negative[2][0], reloadNote], 0);
    await expectStored(client, seed, seed.length);

    unlinkSync(failScan);
    await waitUntil(() => logs.slice(logOffset).includes('"event":"initial_load_complete"'), "initial retry recovers without a 300s reconcile wait");
    await expectRejected(client, negative[2][0],
      `blocked: not a current writer for section "Thread-creator" in ${community.slice(0, 8)}`, "recovered outsider still denied");
    expect((await publish(client, reloadNote)).accepted === true, "recovered public passthrough");
    await expectStored(client, negative.map(([event]) => event), 0);
    passed = true;
    console.log("budabitStartupTest: all checks passed");
  } catch (error) {
    throw new Error(`${error.message}\n\nRelay logs:\n${logs}\nTest artifacts: ${workDir}`);
  } finally {
    // Release only this test's scanner before terminating its relay process.
    writeFileSync(release, "test finished\n");
    if (client) await client.close();
    await stopRelay(proc);
    if (passed) rmSync(workDir, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
