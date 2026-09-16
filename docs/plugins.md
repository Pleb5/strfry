# Event-sifter plugins

This section describes **write** plugins (`relay.writePolicy.plugin`). Optional
[read-admission plugins](#read-admission-plugins) use a separate process and
protocol; do not send read decisions over the event-sifter channel.

In order to reduce complexity, strfry's design attempts to keep policy logic out of its core relay functionality. Instead, this logic can be implemented by operators by installing policy plugins to decide which events to store. Among other things, plugins can be used for the following:

* White/black-lists (particular pubkeys can/can't post events)
* Rate-limits
* Spam filtering

A plugin can be implemented in any programming language that supports reading lines from stdin, decoding JSON, and printing JSON to stdout. If a plugin is installed, strfry will send the event (along with some other information like IP address) to the plugin over stdin. The plugin should then decide what to do with it and print out a JSON object containing this decision.

Currently strfry always waits until it receives a response from a plugin before sending another request. In the future, multiple requests may be sent concurrently, which is why output messages must include the event ID.

The plugin command can be any shell command, which lets you set environment variables, command-line switches, etc. If the plugin command contains no spaces, it is assumed to be a path to a script. In this case, whenever the script's modification-time changes, the plugin will be reloaded upon the next write attempt.

If the plugin's command in `strfry.conf` (or a router config file) change, then the plugin will also be reloaded.


## Input messages

Input messages contain the following keys:

* `type`: Currently always `new`
* `event`: The event posted by the client, with all the required fields such as `id`, `pubkey`, etc
* `receivedAt`: Unix timestamp of when this event was received by the relay
* `sourceType`: The channel where this event came from: `IP4`, `IP6`, `Import`, `Stream`, `Sync`, or `Stored`.
* `sourceInfo`: Specifics of the event's source. Usually an IP address.
* `authed`: Only present if the connection has completed a NIP-42 AUTH flow. Contains the authenticated public key, in 32-byte hex format.


## Output messages

In response to `new` events, the plugin should print a JSONL message (minified JSON followed by a newline). It should contain the following keys:

* `id`: The event ID taken from the `event.id` field of the input message
* `action`: Either `accept`, `reject`, or `shadowReject`
* `msg`: The NIP-20 response message to be sent to the client. Only used for `reject`


## Example: Whitelist

Here is a simple example `whitelist.js` plugin that will reject all events except for those in a whitelist:

    #!/usr/bin/env node

    const whiteList = {
        '003ba9b2c5bd8afeed41a4ce362a8b7fc3ab59c25b6a1359cae9093f296dac01': true,
    };

    const rl = require('readline').createInterface({
      input: process.stdin,
      output: process.stdout,
      terminal: false
    });

    rl.on('line', (line) => {
        let req = JSON.parse(line);

        if (req.type !== 'new') {
            console.error("unexpected request type"); // will appear in strfry logs
            return;
        }

        let res = { id: req.event.id }; // must echo the event's id

        if (whiteList[req.event.pubkey]) {
            res.action = 'accept';
        } else {
            res.action = 'reject';
            res.msg = 'blocked: not on white-list';
        }

        console.log(JSON.stringify(res));
    });

To install:

* Make the script executable: `chmod a+x whitelist.js`
* In `strfry.conf`, configure `relay.writePolicy.plugin` to `./whitelist.js`


## Notes

* If applicable, you should ensure stdout is *line buffered*
  * In perl use `$|++`
  * In python, write with `print(response, flush=True)`
* If events are being rejected with `error: internal error`, then check the strfry logs. The plugin is misconfigured or failing.
* Normally when a plugin blocks an event, it will log a message. Especially when using plugins in `stream`, `router`, etc, this might be too verbose. In order to silence these logs, return an empty string for `msg` (or no `msg` at all).
* When returning an action of `accept`, it doesn't necessarily guarantee that the event will be accepted. The regular strfry checks are still subsequently applied, such as expiration, deletion, etc.

## Read-admission plugins

This fork provides a default-off whole-relay admission interface. Configure
`relay.readPolicy.plugin` with an executable command to enable it. An empty command
disables admission; a configured command that is missing or broken **never** means
public access. The relay requires NIP-42 AUTH with a valid `wss://` service URL,
`relay.maxFilterLimitCount = 0` and `relay.negentropy.enabled = false` in this mode.
Use nested blocks in the config file, as in the
[Budabit operator example](../deploy/budabit/PRIVATE-READS.md#configure-the-replacement-explicitly).

The core authenticates identities, then asks the plugin before executing each REQ.
One allow admits all of that REQ's filters, historical scan and live subscription.
No filter subdivision, event inspection or per-event policy call occurs. Independent
DM participant restrictions still apply. AUTH success is not admission: a valid
nonmember proof receives `OK true`, followed by denial when that client requests data.

An invalid AUTH proof instead receives `["OK", "<auth-event-id>", false, "<reason>"]`.
There is no `AUTH-FAILED` message, and membership denial must not be disguised as
failed signature/challenge verification.

### Read IPC contract

The relay starts one persistent subprocess through `/bin/sh -c` on its dedicated
read-policy worker. The worker serializes requests and uses nonblocking pipes and
a monotonic deadline; it does not compete for the write-plugin channel. The plugin
must flush exactly one JSON line to stdout per request, without banners, diagnostics
or unsolicited messages. The entire response, including newline, is limited to
4096 bytes. Treat the plugin as trusted operator code, not a sandboxed extension.

Request (key shown as a placeholder; actual keys are 64-character hex strings):

```json
{"type":"read-admission","request_id":"123","authenticated_pubkeys":["<verified-pubkey>"]}
```

Response:

```json
{"request_id":"123","decision":"allow"}
```

Echo the opaque string `request_id` exactly. The only decisions are `allow`, `deny`
and `unavailable`; a well-formed `unavailable` is not a membership denial. Keys are
the connection's verified AUTH keys (up to 32), never `authors` or `#p` filter values.
There are no event bodies, filters, branch coordinates or membership rosters in
this protocol. A plugin decides what eligibility means; the Budabit plugin allows
when **any** authenticated key is a current eligible reader.

An active connection is rechecked through this same interface without asking the
client for another signature. A successful REQ decision or recheck starts the next
recheck interval. Connections without subscriptions need no periodic decision;
their next REQ is checked normally. Rechecks and new requests share worker capacity.

### Read settings and failures

All `relay.readPolicy` settings require a restart:

| Setting | Default | Meaning |
| --- | --- | --- |
| `plugin` | `""` | Disabled unless a command is configured |
| `timeoutSeconds` | `2` | Queue plus IPC budget, not just time spent in the child; range 1–30 |
| `recheckSeconds` | `5` | Interval after a successful decision; range 1–300 |
| `maxPending` | `1024` | Queued REQ decisions; range 1–65536 |
| `maxConnections` | `4096` | Connections in admission mode; range 1–100000 |

Unlike the write plugin, the read plugin has no script-mtime reload contract.
Restart after command, code, environment or admission configuration changes.
Incompatible changes to required AUTH/read-limit/restriction settings fail closed.
Legacy `readControl.enabled=true` refuses startup; it is not an alias for admission.

| Condition | Client-visible outcome |
| --- | --- |
| REQ before AUTH | AUTH challenge plus `CLOSED` with `auth-required: authenticate to read this relay`; connection retained for AUTH/retry |
| Plugin denies | `CLOSED` with `restricted: read access denied`, then disconnect |
| Policy unavailable or decision expires | `CLOSED` with `error: read policy temporarily unavailable`, then disconnect |
| Admission queue/subscription capacity exceeded | `CLOSED` with `rate-limited: read admission capacity exceeded`, then disconnect |
| Authenticated COUNT | `CLOSED` with `blocked: COUNT disabled with read admission` |

Malformed/oversized/mismatched output, EOF, pipe failure or an IPC timeout stops the
process and invalidates active read service depending on it. A later request can
start a new process, but cannot execute without a fresh allow. Queue expiry alone
fails the affected connection, not every otherwise healthy connection. At the
connection limit, excess connections are closed before REQ admission.

`CLOSE`, REQ replacement, identity-state changes and disconnection invalidate stale
work. Lifecycle tokens accompany queued history/live output and EOSE; they are not
policy versions or database epochs. Interrupted history cannot claim successful
EOSE. There is no commit-atomic revocation or ability to retract transmitted bytes.

NIP-11 advertises the core's generic `read_policy` version 1, `admission: "req"`,
`consistency: "eventual"`, configured `recheck_seconds`, and
`limitation.auth_required: true`. These facts do not claim what the plugin's policy
means. Community-specific semantics, cache freshness and operating requirements
are documented in [the Budabit read contract](../deploy/budabit/READ-CONTROL-PLAN.md).
