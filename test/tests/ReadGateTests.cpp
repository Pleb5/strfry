#include "apps/relay/ReadGate.h"

#include <cassert>
#include <filesystem>
#include <fstream>
#include <future>
#include <iostream>

// Exercise the production gate implementation, with local files and a controlled
// transport handoff. No test-only bypasses/hooks are compiled into the relay.
ConfigValues testConfig(1);
std::atomic<ConfigValues*> currentCfg{&testConfig};

int main() {
    std::string parent = std::getenv("TMPDIR") ? std::getenv("TMPDIR") : "/tmp";
    std::string directory = parent + "/read-gate-XXXXXX";
    assert(mkdtemp(directory.data()));
    std::string owner(64, '1'), member(64, '2'), outsider(64, '3');
    auto key = [](const auto &hex) { return Bytes32(from_hex(hex)); };
    auto &cfg = testConfig;
    cfg.relay__readControl__enabled = true;
    cfg.relay__readControl__branchAddress = "32222:" + owner + ":" + std::string(64, 'a');
    cfg.relay__readControl__snapshotPath = directory + "/readers.json";
    cfg.relay__auth__serviceUrl = "wss://relay.test/private";
    cfg.relay__writePolicy__plugin = "controlled-test-plugin";
    cfg.relay__maxFilterLimitCount = 0;
    cfg.relay__negentropy__enabled = false;
    ReadGate gate;
    gate.configure();
    auto epoch = gate.startEpoch();
    auto status = [&] {
        gate.writeStatus();
        std::ifstream file(gate.path + ".core-status.json");
        return tao::json::from_string(std::string(std::istreambuf_iterator<char>(file), {}));
    };
    assert(!status().at("ready").get_boolean());
    uint64_t heartbeat = 0;
    auto snapshot = [&](uint64_t seq, std::vector<std::string> readers, std::string epochOverride = "") {
        tao::json::value doc = {
            {"version", 1}, {"epoch", epochOverride.empty() ? epoch : epochOverride},
            {"seq", seq}, {"heartbeat", ++heartbeat}, {"ready", true},
            {"branch_address", gate.branch}, {"owner", owner}, {"write_enforcement", true},
            {"eligible_pubkeys", readers},
        };
        std::ofstream(gate.path + ".tmp") << tao::json::to_string(doc);
        std::filesystem::rename(gate.path + ".tmp", gate.path);
        gate.refresh();
    };
    gate.connect(1);
    assert(gate.access(1) == ReadGate::Access::Unauthenticated);
    gate.authenticate(1, key(member));
    assert(gate.access(1) == ReadGate::Access::Unavailable);
    snapshot(0, {owner, member});
    assert(gate.access(1) == ReadGate::Access::Allowed);
    assert(status().at("ready").get_boolean());
    assert(status().at("epoch").get_string() == epoch);
    gate.authenticate(1, key(outsider));
    assert(gate.access(1) == ReadGate::Access::Allowed); // second key doesn't replace first

    // A writer cannot cross invalidation/commit while an authorized final
    // transport handoff holds this mutex.
    std::unique_lock sendLock(gate.mutex);
    std::promise<void> started;
    auto writer = std::async(std::launch::async, [&] {
        started.set_value();
        return gate.beforeCommit();
    });
    started.get_future().wait();
    assert(writer.wait_for(std::chrono::milliseconds(30)) == std::future_status::timeout);
    assert(gate.accessLocked(1) == ReadGate::Access::Allowed);
    sendLock.unlock();
    assert(writer.get() == 1);
    assert(!status().at("ready").get_boolean());
    assert(status().at("pending_seq").get_unsigned() == 1);
    // Simulated LMDB commit happens only AFTER the real gate invalidated.
    assert(gate.access(1) == ReadGate::Access::Unavailable);
    snapshot(0, {owner, member}); // mixed scan captured the old committed seq
    assert(!gate.available());
    snapshot(1, {owner});
    assert(gate.access(1) == ReadGate::Access::Closed);
    // A queued output cannot run ahead of a queued terminate unchecked.
    unsigned sent = 0;
    { std::lock_guard lock(gate.mutex); if (gate.accessLocked(1) == ReadGate::Access::Allowed) ++sent; }
    assert(sent == 0);

    gate.connect(2);
    gate.authenticate(2, key(member));
    assert(gate.access(2) == ReadGate::Access::Denied);
    gate.beforeCommit();
    snapshot(2, {owner, member});
    assert(gate.access(2) == ReadGate::Access::Allowed); // initially denied can retry
    assert(gate.access(1) == ReadGate::Access::Closed); // revoked never resurrects

    // Expired liveness is checked at final send without waiting for the timer.
    gate.lastHeartbeat -= std::chrono::seconds(10);
    assert(!status().at("ready").get_boolean());
    assert(!gate.available());
    assert(gate.access(2) == ReadGate::Access::Unavailable);
    gate.refresh(); // unchanged file MUST NOT renew lease
    assert(!gate.available());
    snapshot(2, {owner, member});
    assert(gate.available());
    gate.lostPlugin();
    assert(!status().at("supervisor_running").get_boolean());
    auto previousEpoch = epoch;
    epoch = gate.startEpoch();
    snapshot(2, {owner, member}, previousEpoch);
    assert(!gate.available());
    snapshot(2, {owner, member});
    assert(gate.access(2) == ReadGate::Access::Closed); // failure replaces private sockets
    assert(gate.available());

    snapshot(3, {owner, member}); // future sequence can't unlock an uncommitted state
    assert(!gate.available());
    snapshot(2, {owner, member, member});
    assert(!gate.available());
    snapshot(2, {owner, member});
    std::filesystem::remove(gate.path);
    gate.refresh();
    assert(!gate.available());
    std::ofstream(gate.path) << std::string(gate.maxBytes + 1, 'x');
    gate.refresh();
    assert(!gate.available());
    snapshot(2, {owner, member});
    cfg.relay__negentropy__enabled = true; // runtime drift cannot bypass gate
    assert(!gate.available());
    std::filesystem::remove(gate.path);
    std::filesystem::remove(gate.path + ".core-status.json");
    std::filesystem::remove(directory);
    std::cout << "PASS ReadGate production synchronization, stale/mixed scans, queued output, epochs, lease and bounds\n";
}
