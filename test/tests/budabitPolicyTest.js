// Integration test for the Budabit write-control plugin (deploy/budabit).
//
// Runs strfry with the plugin enabled for one hosted branch and drives the
// grant -> publish -> ban -> unban -> restart scenario from the plan
// (deploy/budabit/WRITE-CONTROL-PLAN.md §8.3) over a real websocket.
//
// Run from the repository root:  node test/tests/budabitPolicyTest.js
// Also exercise strict-mode migration prerequisites:
//   BUDABIT_TEST_MODE=strict node test/tests/budabitPolicyTest.js

import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { finalizeEvent, getPublicKey } from "@nostr/tools";
import { hexToBytes, sha256 } from "../utils/events.js";
import { openWebSocket, waitForRelay, WsClient } from "../utils/websocketClient.js";

const mode = process.env.BUDABIT_TEST_MODE || "passthrough";
if (!["passthrough", "strict"].includes(mode)) throw new Error(`Invalid test mode: ${mode}`);
const workDir = path.join(os.tmpdir(), `strfry-budabit-test-${mode}`);
const dbDir = path.join(workDir, "relay-db");
const cfgPath = path.join(workDir, "budabit.conf");
const port = mode === "strict" ? 40572 : 40571;
const wsUrl = `ws://127.0.0.1:${port}`;

// Deterministic identities; pubkeys are derived so signatures always match.
function identity(name) {
  const sec = sha256(`budabit-policy-test:${name}`);
  return { sec, pub: getPublicKey(hexToBytes(sec)) };
}

const ids = [identity("owner"), identity("moderator"), identity("outsider"), identity("stranger")];
const owner = ids[0];
const moderator = ids[1];
const outsider = ids[2];
const communityId = "c".repeat(64);
const address = `32222:${owner.pub}:${communityId}`;
const pluginPath = path.resolve("deploy/budabit/write-policy.py");

let ts = Math.floor(Date.now() / 1000) - 100;

function sign(who, kind, tags, content = "") {
  return finalizeEvent(
    { kind, created_at: ts++, tags, content },
    hexToBytes(who.sec),
  );
}

const authority = () => [
  ["h", communityId],
  ["a", address, "", "community"],
];

const shardAddress = (who, purpose) => `30000:${who.pub}:${communityId}-${purpose}`;

function config() {
  const advertisement = spawnSync("python3", [pluginPath, "--nip11-extra"], {
    encoding: "utf8",
    env: { ...process.env, BUDABIT_BRANCHES: address, BUDABIT_MODE: mode, BUDABIT_DISABLE_LOADER: "1", BUDABIT_DRY_RUN: "0", BUDABIT_AUTO_HOST_URL: "" },
  });
  if (advertisement.status !== 0) throw new Error(`NIP-11 generation failed: ${advertisement.stderr}`);
  const env = [
    `BUDABIT_BRANCHES=${address}`,
    `BUDABIT_MODE=${mode}`,
    "BUDABIT_DISABLE_LOADER=0",
    "BUDABIT_DRY_RUN=0",
    "BUDABIT_AUTO_HOST_URL=",
    `BUDABIT_STRFRY_BIN=${path.resolve("strfry")}`,
    `STRFRY_CONFIG=${cfgPath}`,
    "STRFRY_POLICY_DB_FILE=" + path.join(dbDir, "data.mdb"),
    "STRFRY_POLICY_MIN_FREE_BYTES=0",
    "STRFRY_POLICY_PUBKEY_CAPACITY=1000",
    "STRFRY_POLICY_IP_CAPACITY=1000",
    "STRFRY_POLICY_GLOBAL_CAPACITY=1000",
  ].join(" ");

  return `
db = "${dbDir}/"

events {
  rejectEventsNewerThanSeconds = 900
}

relay {
  bind = "127.0.0.1"
  port = ${port}
  nofiles = 0
  autoPingSeconds = 0

  info {
    extra = ${JSON.stringify(advertisement.stdout.trim())}
  }

  auth {
    enabled = false
  }

  writePolicy {
    plugin = "${env} python3 ${pluginPath}"
    timeoutSeconds = 10
  }

  numThreads {
    ingester = 1
    reqWorker = 1
    reqMonitor = 1
    negentropy = 1
  }
}
`;
}

function startRelay() {
  const proc = spawn("./strfry", ["--config", cfgPath, "relay"], {
    stdio: ["ignore", "pipe", "pipe"],
  });
  let logs = "";
  proc.stdout.on("data", (d) => (logs += d.toString()));
  proc.stderr.on("data", (d) => (logs += d.toString()));
  return { proc, logs: () => logs };
}

async function stopRelay(relay) {
  relay.proc.kill("SIGTERM");
  await Promise.race([
    new Promise((resolve) => relay.proc.once("exit", resolve)),
    delay(3_000),
  ]);
  if (relay.proc.exitCode === null) relay.proc.kill("SIGKILL");
}

async function publish(client, event) {
  client.send(["EVENT", event]);
  const ok = await client.waitFor((m) => m[0] === "OK" && m[1] === event.id, 15_000);
  return { accepted: ok[2], msg: ok[3] || "" };
}

async function publishRetryWhileLoading(client, who, kind, tags, content) {
  for (let attempt = 0; attempt < 40; attempt++) {
    const res = await publish(client, sign(who, kind, tags, content));
    if (!res.msg.startsWith("error: relay policy is loading")) return res;
    await delay(250);
  }
  throw new Error("plugin never finished warming up");
}

async function publishAfterMigration(client, ...args) {
  // A newly referenced shard is loaded asynchronously from committed storage.
  for (let attempt = 0; attempt < 40; attempt++) {
    const res = await publish(client, sign(...args));
    if (!res.msg.startsWith("blocked: not a current writer") &&
        !res.msg.startsWith("error: relay policy is loading")) return res;
    await delay(250);
  }
  throw new Error("migrated permission list was never loaded");
}

async function count(client, filter) {
  const sub = `s${Math.random().toString(36).slice(2, 8)}`;
  client.send(["REQ", sub, filter]);
  const msgs = await client.collectUntil((m) => m[0] === "EOSE" && m[1] === sub, 5_000);
  client.send(["CLOSE", sub]);
  return msgs.filter((m) => m[0] === "EVENT" && m[1] === sub).length;
}

function expect(cond, message) {
  if (!cond) throw new Error(message);
}

function expectAccepted(res, label) {
  expect(res.accepted === true, `${label}: expected accept, got "${res.msg}"`);
  console.log(`  ok   ${label}`);
}

function expectBlocked(res, prefix, label) {
  expect(res.accepted === false, `${label}: expected rejection, got accept`);
  expect(res.msg.startsWith(prefix), `${label}: expected "${prefix}...", got "${res.msg}"`);
  console.log(`  ok   ${label} -> ${res.msg}`);
}

const definitionTags = (section = "Thread-creator", purpose = "thread-creator") => [
  ["d", communityId],
  ["name", "Integration Community"],
  ["r", "wss://relay.test"],
  ["content", "General"],
  ["k", "1111"],
  ["k", "7"],
  ["k", "1984"],
  ["a", shardAddress(owner, "general")],
  ["content", section],
  ["k", "11", "threads"],
  ["a", shardAddress(owner, purpose)],
  ["a", shardAddress(moderator, purpose)],
];

const thread = (who) => [who, 11, [["h", communityId]], "thread"];
const form = (who, section, identifier) => sign(who, 30168, [
  ["d", identifier], ...authority(), ["content", section],
  ["name", `${section} application`], ["field", "intro", "text", "Introduction"],
]);

async function main() {
  rmSync(workDir, { recursive: true, force: true });
  mkdirSync(dbDir, { recursive: true });
  writeFileSync(cfgPath, config().trim() + "\n");

  let relay = startRelay();
  let client;
  try {
    await waitForRelay(wsUrl);
    client = new WsClient(await openWebSocket(wsUrl));

    const info = await (await fetch(`http://127.0.0.1:${port}/`, { headers: { Accept: "application/nostr+json" }, signal: AbortSignal.timeout(5_000) })).json();
    expect(info.budabit.enforcing === true && info.budabit.dry_run === false, "NIP-11 advertises active enforcement");
    expect(info.budabit.enforced_branches.includes(address), "NIP-11 advertises the exact branch");
    expect(JSON.stringify(info.budabit.protected_deletion_kinds) === "[30000,32222]", "NIP-11 advertises deletion protection");

    console.log("* bootstrap");
    expectBlocked(
      await publishRetryWhileLoading(client, ...thread(owner)),
      "blocked: community definition is not available",
      "owner thread before definition fails closed",
    );
    const noteResult = await publish(client, sign(outsider, 1, [], "public note"));
    if (mode === "strict") {
      expectBlocked(noteResult, "blocked: this relay only stores hosted community content", "strict public note rejected");
    } else {
      expectAccepted(noteResult, "passthrough note");
    }
    expectBlocked(
      await publish(client, sign(owner, 32222, [["d", communityId], ["name", "x"]])),
      "invalid:",
      "invalid definition rejected",
    );
    const definition = sign(owner, 32222, definitionTags());
    expectAccepted(await publish(client, definition), "definition accepted");
    expectAccepted(await publish(client, sign(owner, ...thread(owner).slice(1))), "owner thread after definition");

    console.log("* grants");
    expectBlocked(await publish(client, sign(...thread(outsider))), "blocked: not a current writer", "outsider thread rejected");
    const grant = sign(owner, 30000, [["d", `${communityId}-thread-creator`], ["p", outsider.pub]]);
    expectAccepted(
      await publish(client, grant),
      "owner shard grants outsider",
    );
    expectAccepted(await publish(client, sign(...thread(outsider))), "outsider thread accepted immediately");
    expectBlocked(
      await publish(client, sign(outsider, 1111, [["h", communityId], ["e", "a".repeat(64)]], "comment")),
      "blocked: not a current writer",
      "outsider comment still needs General",
    );

    console.log("* structural member and moderator");
    expectAccepted(await publish(client, sign(...thread(moderator))), "referenced list owner writes before accepting");
    expectAccepted(
      await publish(client, sign(moderator, 30000, [["d", `${communityId}-thread-creator`], ["status", "declined"]])),
      "declined invitation accepted",
    );
    expectBlocked(
      await publish(client, sign(moderator, 1984, [["p", outsider.pub, "spam"], ...authority()])),
      "blocked: person reports require",
      "section moderator cannot person-ban",
    );

    console.log("* bans");
    const report = sign(owner, 1984, [["p", outsider.pub, "spam"], ...authority()]);
    expectAccepted(await publish(client, report), "owner person report");
    expectBlocked(await publish(client, sign(...thread(outsider))), "blocked: author is moderated", "banned outsider rejected");

    // Current Budabit shape: h scope only, no a tag (Communikeys "Deletion Requests").
    const reportDelete = sign(owner, 5, [
      ["h", communityId],
      ["e", report.id, "", owner.pub, "report"],
      ["k", "1984"],
    ]);
    expectAccepted(await publish(client, reportDelete), "owner deletes report");
    expectAccepted(await publish(client, sign(...thread(outsider))), "unbanned outsider accepted");

    // A kind 5 must not carry the branch as an a tag: NIP-09 makes it a target.
    const report2 = sign(owner, 1984, [["p", outsider.pub, "spam"], ...authority()]);
    expectAccepted(await publish(client, report2), "owner person report (second)");
    const badDelete = sign(owner, 5, [
      ["e", report2.id, "", owner.pub, "report"],
      ["k", "1984"],
      ...authority(),
    ]);
    const deletionDenied = "blocked: Deletion of kinds 32222 and 30000 is not allowed";
    for (const target of [definition, grant]) {
      expectBlocked(await publish(client, sign(owner, 5, [["e", target.id]])), deletionDenied, `known kind ${target.kind} e-only deletion denied`);
    }
    expectBlocked(await publish(client, badDelete), deletionDenied, "marked branch a cannot delete the definition");
    expect((await count(client, { kinds: [32222], authors: [owner.pub] })) === 1, "definition survives rejected deletion");
    for (const kind of [32222, 30000]) {
      expectBlocked(await publish(client, sign(owner, 5, [["k", String(kind)], ["e", "b".repeat(64)]])), deletionDenied, `kind ${kind} deletion denied`);
    }
    expectBlocked(await publish(client, sign(owner, 5, [["a", `30000:${owner.pub}:${communityId}-thread-creator`]])), deletionDenied, "permission shard coordinate deletion denied");
    expectBlocked(await publish(client, sign(...thread(outsider))), "blocked: author is moderated", "rejected mixed deletion did not retract the report");
    expectAccepted(await publish(client, sign(owner, 5, [["h", communityId], ["e", report2.id, "", owner.pub, "report"], ["k", "1984"]])), "report-only retraction still allowed");
    expectAccepted(await publish(client, sign(...thread(outsider))), "grantee accepted after real report retraction");
    const foreignDelete = sign(moderator, 5, [
      ["h", communityId],
      ["e", "b".repeat(64), "", moderator.pub, "report"],
      ["k", "1984"],
    ]);
    expectAccepted(await publish(client, foreignDelete), "non-owner h-scoped delete accepted");

    console.log("* owner stages section rename before definition update");
    expectAccepted(
      await publish(client, sign(moderator, 30000, [["d", `${communityId}-thread-creator`]])),
      "moderator accepts original section",
    );
    expectAccepted(await publish(client, form(moderator, "Thread-creator", "apply-threads")), "original section moderator form");
    const migratedGrant = sign(owner, 30000, [["d", `${communityId}-marketplace`], ["p", outsider.pub]]);
    const migratedForm = form(owner, "Marketplace", "apply-marketplace");
    const migratedReview = sign(owner, 1985, [
      ...authority(), ["L", "budabit:community-report"], ["l", "reviewed", "budabit:community-report"],
      ["e", report.id, "", owner.pub, "report"], ["content", "Marketplace"], ["p", owner.pub],
    ]);
    // Match CommunityCreate: verify each prerequisite before activating it.
    for (const artifact of [migratedGrant, migratedForm, migratedReview]) {
      expectAccepted(await publish(client, artifact), `migration prerequisite kind ${artifact.kind}`);
      expect((await count(client, { ids: [artifact.id] })) === 1, `migration prerequisite ${artifact.kind} stored exactly`);
    }
    expect((await count(client, { ids: [definition.id] })) === 1, "old definition still current while staging");
    expectBlocked(await publish(client, form(moderator, "Marketplace", "mod-marketplace")), "blocked: admission form names an unknown section", "moderator cannot stage an undefined section");
    expectBlocked(await publish(client, sign(...thread(ids[3]))), "blocked: not a current writer", "staging does not grant stranger access");

    const renamedDefinition = sign(owner, 32222, definitionTags("Marketplace", "marketplace"));
    expectAccepted(await publish(client, renamedDefinition), "renamed definition activates migration");
    expect((await count(client, { ids: [renamedDefinition.id] })) === 1, "renamed definition stored exactly");
    expectAccepted(await publishAfterMigration(client, ...thread(outsider)), "migrated member grant loaded");
    // Removing the old grant must not affect the migrated member.
    expectAccepted(await publish(client, sign(owner, 30000, [["d", `${communityId}-thread-creator`]])), "old owner list emptied");
    expectAccepted(await publish(client, sign(...thread(outsider))), "member writes using new permission list");
    expectBlocked(await publish(client, form(moderator, "Marketplace", "mod-marketplace")), "blocked: admission forms require section moderator authority", "migrated moderator must accept new permission list");
    expectAccepted(await publish(client, sign(moderator, 30000, [["d", `${communityId}-marketplace`]])), "moderator accepts migrated section");
    expectAccepted(await publish(client, form(moderator, "Marketplace", "mod-marketplace")), "accepted moderator manages renamed section forms");

    console.log("* restart rebuilds state from LMDB");
    await client.close();
    client = null;
    await stopRelay(relay);
    relay = startRelay();
    await waitForRelay(wsUrl);
    client = new WsClient(await openWebSocket(wsUrl));
    expectAccepted(await publishRetryWhileLoading(client, ...thread(outsider)), "outsider thread after restart");
    expect((await count(client, { ids: [migratedForm.id] })) === 1, "copied form survives restart");
    expectAccepted(await publish(client, form(moderator, "Marketplace", "mod-marketplace")), "migrated moderator authority survives restart");
    expectBlocked(
      await publish(client, sign(ids[3], 11, [["h", communityId]], "x")),
      "blocked: not a current writer",
      "stranger still rejected after restart",
    );
    expectAccepted(await publish(client, sign(outsider, 7, [...authority(), ["k", "32222"]], "+")), "star from grantee");
    expectAccepted(await publish(client, sign(ids[3], 7, [...authority(), ["k", "32222"]], "+")), "star from stranger");
    expectAccepted(
      await publish(client, sign(ids[3], 1069, [["a", `30168:${owner.pub}:apply`, "", "form"], ...authority()])),
      "admission response from stranger",
    );

    console.log(`budabitPolicyTest (${mode}): all checks passed`);
  } catch (e) {
    throw new Error(`${e.message}\n\nRelay logs:\n${relay.logs()}`);
  } finally {
    if (client) await client.close();
    await stopRelay(relay);
  }
}

main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
