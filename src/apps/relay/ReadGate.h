#pragma once

#include <chrono>
#include <mutex>
#include <fstream>
#include <sstream>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <openssl/rand.h>

#include "golpe.h"
#include "AuthSession.h"

// One serialization point for policy invalidation, snapshot installation, and
// the final private transport handoff. No Python calls on a read/send path.
struct ReadGate {
    using Clock = std::chrono::steady_clock;
    enum class Access { Allowed, Unauthenticated, Denied, Unavailable, Closed };
    struct ReaderSet {
        uint64_t seq;
        flat_hash_set<Bytes32> keys;
    };
    struct Connection {
        AuthKeys keys;
        bool everAllowed = false;
        bool revoked = false;
    };

    // Recursive only for uWS's synchronous disconnect callback during a send.
    std::recursive_mutex mutex;
    std::mutex refreshMutex;
    bool enabled = false;
    std::string branch, path, serviceUrl, plugin;
    std::string restrictedKinds;
    bool restrictToInvolved = true;
    uint64_t filterLimit = 0;
    uint64_t maxBytes = 0, timeoutSeconds = 3;
    std::string epoch;
    uint64_t pendingSeq = 0, heartbeat = 0;
    bool lastProjectionReady = false;
    std::shared_ptr<const ReaderSet> snapshot;
    Clock::time_point lastHeartbeat = Clock::now(), unavailableSince = Clock::now();
    flat_hash_map<uint64_t, Connection> connections;
    std::string lastFileIdentity;

    // Local-only sampled serving status, not an authorization input. Publish at
    // invalidation and every cron tick; never expose roster/connection identities.
    static std::string processStart() {
        std::ifstream file("/proc/self/stat");
        std::string line, field;
        std::getline(file, line);
        auto end = line.rfind(')');
        if (end == std::string::npos) return "";
        std::istringstream fields(line.substr(end + 2));
        for (int i = 0; i <= 19; ++i) if (!(fields >> field)) return "";
        return field; // /proc stat field22, guards PID reuse
    }
    void writeStatus() {
        if (!enabled || path.empty()) return;
        std::lock_guard lock(mutex);
        auto statusPath = path + ".core-status.json";
        std::string temp = statusPath + ".XXXXXX";
        int fd = -1;
        try {
            static const auto start = processStart();
            static const auto boot = [] { std::ifstream f("/proc/sys/kernel/random/boot_id"); std::string id; f >> id; return id; }();
            auto nanos = [](Clock::time_point at) { return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(at.time_since_epoch()).count()); };
            tao::json::value data = {
                {"version", uint64_t(1)}, {"pid", uint64_t(getpid())}, {"process_start", start}, {"boot_id", boot},
                {"epoch", epoch}, {"branch_address", branch}, {"pending_seq", pendingSeq},
                {"installed_seq", snapshot ? snapshot->seq : pendingSeq}, {"installed", bool(snapshot)},
                {"heartbeat", heartbeat}, {"ready", availableLocked()}, {"supervisor_running", !epoch.empty()},
                {"measured_monotonic_ns", nanos(Clock::now())},
                {"lease_deadline_monotonic_ns", nanos(lastHeartbeat + std::chrono::seconds(timeoutSeconds))},
            };
            auto content = tao::json::to_string(data) + "\n";
            fd = ::mkstemp(temp.data()); // private0600, same-dir atomic replacement
            if (fd < 0) throw herr("core status create failed");
            size_t offset = 0;
            while (offset < content.size()) {
                auto n = ::write(fd, content.data() + offset, content.size() - offset);
                if (n < 0 && errno == EINTR) continue;
                if (n <= 0) throw herr("core status write failed");
                offset += n;
            }
            if (::close(fd)) { fd = -1; throw herr("core status close failed"); }
            fd = -1;
            if (::rename(temp.c_str(), statusPath.c_str())) throw herr("core status replace failed");
        } catch (...) {
            if (fd >= 0) ::close(fd);
            ::unlink(temp.c_str());
            ::unlink(statusPath.c_str()); // health must fail, not reuse old success
        }
    }

    static bool hexKey(std::string_view key) {
        return key.size() == 64 && std::all_of(key.begin(), key.end(), [](char c) {
            return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
        });
    }
    static bool authorityKind(uint64_t kind) {
        return kind == 5 || kind == 1984 || kind == 30000 || kind == 32222;
    }
    void configure() {
        enabled = cfg().relay__readControl__enabled;
        if (!enabled) return;
        branch = cfg().relay__readControl__branchAddress;
        path = cfg().relay__readControl__snapshotPath;
        serviceUrl = cfg().relay__auth__serviceUrl;
        plugin = cfg().relay__writePolicy__plugin;
        restrictedKinds = cfg().relay__auth__restrictedReadKinds;
        restrictToInvolved = cfg().relay__auth__restrictReadToInvolvedPubkey;
        filterLimit = cfg().relay__maxFilterLimit;
        maxBytes = cfg().relay__readControl__maxSnapshotBytes;
        timeoutSeconds = cfg().relay__readControl__gateTimeoutSeconds;
        if (branch.size() != 135 || !branch.starts_with("32222:") || branch[70] != ':'
            || !hexKey(std::string_view(branch).substr(6, 64)) || !hexKey(std::string_view(branch).substr(71)))
            throw herr("private reads require relay.readControl.branchAddress = 32222:<owner>:<communityId>");
        if (!cfg().relay__auth__enabled || !serviceUrl.starts_with("wss://") || serviceUrl.find_first_of("?# \t\r\n") != std::string::npos
            || plugin.empty() || path.empty() || path.front() != '/' || !path.ends_with(".json")
            || cfg().relay__maxFilterLimitCount != 0 || cfg().relay__negentropy__enabled)
            throw herr("private reads require AUTH/serviceUrl, plugin, absolute snapshotPath, COUNT=0 and Negentropy disabled");
        if (maxBytes < 1024 || maxBytes > 32 * 1024 * 1024 || timeoutSeconds < 1 || timeoutSeconds > 60)
            throw herr("private read snapshot/timeout bounds invalid");
    }
    bool configMatchesLocked() const {
        return cfg().relay__auth__enabled && cfg().relay__auth__serviceUrl == serviceUrl
            && cfg().relay__writePolicy__plugin == plugin && cfg().relay__maxFilterLimitCount == 0
            && cfg().relay__auth__restrictedReadKinds == restrictedKinds
            && cfg().relay__auth__restrictReadToInvolvedPubkey == restrictToInvolved
            && cfg().relay__maxFilterLimit == filterLimit
            && !cfg().relay__negentropy__enabled;
    }
    void unavailableLocked() {
        if (snapshot) unavailableSince = Clock::now();
        snapshot.reset();
    }
    void lostPlugin() {
        if (!enabled) return;
        std::lock_guard lock(mutex);
        unavailableLocked();
        epoch.clear();
        for (auto &[_, conn] : connections) if (!conn.keys.empty()) conn.revoked = true;
        writeStatus();
    }
    std::string startEpoch() {
        std::lock_guard lock(mutex);
        unsigned char random[32];
        if (RAND_bytes(random, sizeof(random)) != 1) throw herr("reader epoch randomness failed");
        epoch = to_hex(std::string_view(reinterpret_cast<char*>(random), sizeof(random)));
        unavailableLocked();
        lastHeartbeat = unavailableSince = Clock::now();
        heartbeat = 0;
        lastProjectionReady = false;
        lastFileIdentity.clear();
        writeStatus();
        return epoch;
    }
    uint64_t beforeCommit() {
        std::lock_guard lock(mutex); // shared with final send, BEFORE LMDB commit
        unavailableLocked();
        if (++pendingSeq == 0) throw herr("reader sequence exhausted");
        writeStatus();
        return pendingSeq;
    }
    bool availableLocked() const {
        return !epoch.empty() && configMatchesLocked() && snapshot && snapshot->seq == pendingSeq
            && Clock::now() - lastHeartbeat < std::chrono::seconds(timeoutSeconds);
    }
    bool available() {
        if (!enabled) return true;
        std::lock_guard lock(mutex);
        return availableLocked();
    }
    Access accessLocked(uint64_t connId) {
        if (!enabled) return Access::Allowed;
        auto it = connections.find(connId);
        if (it == connections.end() || it->second.revoked) return Access::Closed;
        auto &conn = it->second;
        if (conn.keys.empty()) return Access::Unauthenticated;
        if (!availableLocked()) return Access::Unavailable;
        for (auto key : conn.keys.values) {
            if (snapshot->keys.contains(key)) { conn.everAllowed = true; return Access::Allowed; }
        }
        if (conn.everAllowed) { conn.revoked = true; return Access::Closed; }
        return Access::Denied;
    }
    Access access(uint64_t connId) {
        std::lock_guard lock(mutex);
        return accessLocked(connId);
    }
    void connect(uint64_t connId) {
        if (enabled) { std::lock_guard lock(mutex); connections.try_emplace(connId); }
    }
    void authenticate(uint64_t connId, Bytes32 key) {
        if (!enabled) return;
        std::lock_guard lock(mutex);
        auto it = connections.find(connId);
        if (it != connections.end()) { it->second.keys.add(key); accessLocked(connId); }
    }
    void disconnect(uint64_t connId) {
        if (enabled) { std::lock_guard lock(mutex); connections.erase(connId); }
    }
    void close(uint64_t connId) {
        if (!enabled) return;
        std::lock_guard lock(mutex);
        auto it = connections.find(connId);
        if (it != connections.end()) it->second.revoked = true;
    }
    bool needsRestart() {
        std::lock_guard lock(mutex);
        // Initial/rebuild scans are bounded to 30s by the standard plugin;
        // while loading, reads are closed regardless of this recovery deadline.
        auto budget = lastProjectionReady ? timeoutSeconds : uint64_t(35);
        return !epoch.empty() && Clock::now() - lastHeartbeat > std::chrono::seconds(budget);
    }
    std::vector<uint64_t> terminations() {
        std::lock_guard lock(mutex);
        if (snapshot && !availableLocked()) unavailableLocked();
        std::vector<uint64_t> result;
        for (auto &[id, conn] : connections) {
            auto access = accessLocked(id);
            if (access == Access::Closed || (!conn.keys.empty() && !availableLocked()
                    && Clock::now() - unavailableSince >= std::chrono::seconds(timeoutSeconds))) {
                conn.revoked = true;
                result.push_back(id);
            }
        }
        return result;
    }
    void refresh() {
        if (!enabled) return;
        std::lock_guard refreshLock(refreshMutex);
        // No lock while parsing. Install validates the CURRENT epoch/sequence.
        std::string content, identity;
        try {
            int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
            if (fd < 0) throw herr("snapshot missing");
            struct Guard { int fd; ~Guard() { ::close(fd); } } guard{fd};
            struct stat st;
            if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size < 1 || uint64_t(st.st_size) > maxBytes)
                throw herr("snapshot size/type invalid");
            identity = std::to_string(st.st_ino) + ":" + std::to_string(st.st_mtim.tv_sec) + ":" + std::to_string(st.st_mtim.tv_nsec) + ":" + std::to_string(st.st_size);
            { std::lock_guard lock(mutex); if (identity == lastFileIdentity) return; }
            content.resize(st.st_size);
            size_t offset = 0;
            while (offset < content.size()) {
                auto n = ::read(fd, content.data() + offset, content.size() - offset);
                if (n <= 0) throw herr("snapshot read incomplete");
                offset += n;
            }
            auto json = tao::json::from_string(content);
            auto nextEpoch = json.at("epoch").get_string();
            auto nextSeq = json.at("seq").get_unsigned();
            auto nextHeartbeat = json.at("heartbeat").get_unsigned();
            bool ready = json.at("ready").get_boolean();
            auto set = std::make_shared<ReaderSet>();
            set->seq = nextSeq;
            if (json.at("version").get_unsigned() != 1 || !json.at("write_enforcement").get_boolean()
                || json.at("branch_address").get_string() != branch || json.at("owner").get_string() != branch.substr(6, 64))
                throw herr("snapshot configuration mismatch");
            for (auto &value : json.at("eligible_pubkeys").get_array()) {
                auto &key = value.get_string();
                if (!hexKey(key) || !set->keys.insert(Bytes32(from_hex(key))).second) throw herr("snapshot keys invalid");
            }
            if (ready && !set->keys.contains(Bytes32(from_hex(branch.substr(6, 64))))) throw herr("snapshot owner missing");
            std::lock_guard lock(mutex);
            if (nextEpoch != epoch || epoch.empty() || nextSeq != pendingSeq || nextHeartbeat <= heartbeat)
                throw herr("snapshot generation mismatch");
            lastFileIdentity = identity;
            heartbeat = nextHeartbeat;
            lastProjectionReady = ready;
            lastHeartbeat = Clock::now();
            if (ready) {
                snapshot = std::move(set);
                for (auto &[id, _] : connections) accessLocked(id); // latch revocation before any queued sends
            } else unavailableLocked();
        } catch (const std::exception &) {
            std::lock_guard lock(mutex);
            unavailableLocked();
            // A missing/replaced file must be revalidated even if its inode is reused.
            lastFileIdentity.clear();
        }
    }
};
