// Run from the repository root. Only controlled keys and isolated local DBs.
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { finalizeEvent } from "@nostr/tools";
import { config, runRelaySuite, writeConfig } from "../utils/relay.js";
import { openWebSocket, WsClient } from "../utils/websocketClient.js";

const workDir = mkdtempSync(path.join(os.tmpdir(), "strfry-auth-age-"));
const secret = new Uint8Array(32).fill(17); // test-only key
const port = 40579;

function sign(kind, age, tags = []) {
  return finalizeEvent({
    kind, created_at: Math.floor(Date.now() / 1000) - age, tags, content: "",
  }, secret);
}

async function authCase(wsUrl, age, accepted, reason, corrupt = false) {
  const client = new WsClient(await openWebSocket(wsUrl));
  try {
    client.send(["REQ", "challenge", { kinds: [4] }]);
    const challenge = await client.waitFor(m => m[0] === "AUTH");
    const event = sign(22242, age, [["relay", "wss://relay.test"], ["challenge", challenge[1]]]);
    if (corrupt) event.sig = "0".repeat(128);
    client.send(["AUTH", event]);
    const result = await client.waitFor(m => m[0] === "OK" && m[1] === event.id);
    assert.equal(result[2], accepted, JSON.stringify(result));
    if (reason) assert.match(result[3], reason);
  } finally {
    await client.close();
  }
}

try {
  for (const maxAge of [undefined, 600, 60]) {
    const relayDbPath = path.join(workDir, `db-${maxAge ?? "default"}`);
    const relayConfigPath = path.join(workDir, `age-${maxAge ?? "default"}.conf`);
    let text = config(relayDbPath, port);
    if (maxAge !== undefined) text = text.replace("auth {", `auth {\n    maxAgeSeconds = ${maxAge}`);
    text += "\nevents { rejectEphemeralEventsOlderThanSeconds = 60\n rejectEventsNewerThanSeconds = 30\n}\n";
    writeConfig(text, relayConfigPath);
    await runRelaySuite({
      relayConfigPath, relayPort: port, relayDbPath,
      tests: async ({ wsUrl, client }) => {
        const allowsSlowProof = maxAge !== 60;
        await authCase(wsUrl, 120, allowsSlowProof, allowsSlowProof ? undefined : /ephemeral event expired/);
        await authCase(wsUrl, 1, true);
        await authCase(wsUrl, 700, false, /ephemeral event expired/);
        await authCase(wsUrl, -120, false, /created_at too late/);
        await authCase(wsUrl, 1, false, /bad signature/, true);

        // A longer AUTH window must not weaken ordinary event validation.
        for (const [kind, age, accepted, reason] of [
          [20001, 120, false, /ephemeral event expired/],
          [1, 120, true],
          [1, -120, false, /created_at too late/],
        ]) {
          const event = sign(kind, age);
          client.send(["EVENT", event]);
          const result = await client.waitFor(m => m[0] === "OK" && m[1] === event.id);
          assert.equal(result[2], accepted, JSON.stringify(result));
          if (reason) assert.match(result[3], reason);
        }
        console.log(`PASS AUTH maxAge=${maxAge ?? "default (600)"}; signature, future skew and ordinary event regressions`);
      },
    });
  }
} finally {
  rmSync(workDir, { recursive: true, force: true }); // only this invocation's mkdtemp
}
