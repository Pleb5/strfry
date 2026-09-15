#pragma once

#include <condition_variable>
#include <list>
#include <map>
#include <mutex>
#include <optional>
#include <set>
#include <thread>

#include "AuthSession.h"
#include "Subscription.h"
#include "ReadPolicyProcess.h"

// Generic connection/REQ lifecycle. Membership lives entirely in the plugin.
// There are no database revisions, reader rosters, or per-event policy checks.
struct ReadAdmission {
    using Clock = std::chrono::steady_clock;
    struct Entry { uint64_t token; bool admitted = false; };
    struct Connection {
        AuthKeys keys;
        uint64_t authGeneration = 0;
        bool closing = false;
        uint64_t checkId = 0;
        std::map<std::string, Entry> subs;
        Clock::time_point nextCheck = Clock::time_point::max();
    };
    struct Job {
        uint64_t id, connId, authGeneration;
        std::vector<Bytes32> keys;
        Clock::time_point deadline;
        std::optional<Subscription> sub;
    };
    struct Rejection { uint64_t connId; std::vector<std::string> subs; std::string reason; };

    bool enabled = false;
    std::string plugin, serviceUrl, restrictedKinds;
    bool restrictInvolved = true;
    uint64_t timeout = 2, interval = 5, maxPending = 1024, maxConnections = 4096, maxSubs = 200, filterLimit = 0;
    std::function<void(Subscription &&)> onAdmit;
    std::function<void(const Rejection &)> onReject;

    std::mutex mutex;
    std::condition_variable changed;
    std::map<uint64_t, Connection> connections;
    std::list<Job> jobs;
    uint64_t nextId = 0;
    bool stopping = false;
    std::thread worker;

    ~ReadAdmission() {
        { std::lock_guard lock(mutex); stopping = true; changed.notify_all(); }
        if (worker.joinable()) worker.join();
    }
    void configure() {
        if (cfg().relay__readControl__enabled)
            throw herr("readControl was replaced; configure readPolicy.plugin before restarting");
        plugin = cfg().relay__readPolicy__plugin;
        enabled = !plugin.empty();
        if (!enabled) return;
        serviceUrl = cfg().relay__auth__serviceUrl;
        restrictedKinds = cfg().relay__auth__restrictedReadKinds;
        restrictInvolved = cfg().relay__auth__restrictReadToInvolvedPubkey;
        filterLimit = cfg().relay__maxFilterLimit;
        timeout = cfg().relay__readPolicy__timeoutSeconds;
        interval = cfg().relay__readPolicy__recheckSeconds;
        maxPending = cfg().relay__readPolicy__maxPending;
        maxConnections = cfg().relay__readPolicy__maxConnections;
        maxSubs = cfg().relay__maxSubsPerConnection;
        if (!cfg().relay__auth__enabled || !serviceUrl.starts_with("wss://") || serviceUrl.find_first_of("?# \t\r\n") != std::string::npos
            || cfg().relay__maxFilterLimitCount || cfg().relay__negentropy__enabled)
            throw herr("read policy requires AUTH/serviceUrl, COUNT=0 and Negentropy disabled");
        if (timeout < 1 || timeout > 30 || interval < 1 || interval > 300 || maxPending < 1 || maxPending > 65536
            || maxConnections < 1 || maxConnections > 100000 || maxSubs < 1 || maxSubs > 1000)
            throw herr("invalid read policy bounds");
    }
    bool configMatches() const {
        return cfg().relay__auth__enabled && cfg().relay__auth__serviceUrl == serviceUrl
            && cfg().relay__auth__restrictedReadKinds == restrictedKinds
            && cfg().relay__auth__restrictReadToInvolvedPubkey == restrictInvolved
            && cfg().relay__maxFilterLimit == filterLimit && !cfg().relay__maxFilterLimitCount
            && !cfg().relay__negentropy__enabled;
    }
    bool connect(uint64_t id) {
        if (!enabled) return true;
        std::lock_guard lock(mutex);
        if (connections.size() >= maxConnections) return false;
        return connections.try_emplace(id).second;
    }
    void eraseJobs(uint64_t id, const std::string *sub = nullptr) {
        std::erase_if(jobs, [&](const Job &job) {
            return job.connId == id && (!sub || (job.sub && job.sub->subId.str() == *sub));
        });
    }
    void disconnect(uint64_t id) {
        if (!enabled) return;
        std::lock_guard lock(mutex); connections.erase(id); eraseJobs(id);
    }
    void close(uint64_t id) {
        if (!enabled) return;
        std::lock_guard lock(mutex);
        auto it = connections.find(id);
        if (it != connections.end()) { it->second.closing = true; it->second.subs.clear(); }
        eraseJobs(id);
    }
    bool closed(uint64_t id) {
        if (!enabled) return false;
        std::lock_guard lock(mutex);
        auto it = connections.find(id);
        return it == connections.end() || it->second.closing;
    }
    void authenticate(uint64_t id, Bytes32 key) {
        if (!enabled) return;
        std::lock_guard lock(mutex);
        auto it = connections.find(id);
        if (it != connections.end() && !it->second.closing && !it->second.keys.contains(key)) {
            it->second.keys.add(key); ++it->second.authGeneration;
        }
    }
    bool current(uint64_t connId, std::string_view subId, uint64_t token) {
        if (!enabled) return true;
        std::lock_guard lock(mutex);
        auto it = connections.find(connId);
        if (it == connections.end() || it->second.closing) return false;
        auto entry = it->second.subs.find(std::string(subId));
        return entry != it->second.subs.end() && entry->second.token == token && entry->second.admitted;
    }
    bool current(const Subscription &sub) { return current(sub.connId, sub.subId.sv(), sub.admissionId); }
    void cancel(uint64_t id, const std::string &sub) {
        if (!enabled) return;
        std::lock_guard lock(mutex);
        auto it = connections.find(id);
        if (it != connections.end()) {
            auto &conn = it->second;
            conn.subs.erase(sub);
            if (conn.subs.empty()) {
                conn.nextCheck = Clock::time_point::max();
                conn.checkId = 0; // invalidate a recheck for the previous active period
            }
        }
        eraseJobs(id, &sub);
    }
    Rejection rejectLocked(uint64_t id, Connection &conn, const std::string &reason) {
        Rejection rejection{id, {}, reason};
        for (const auto &[sub, _] : conn.subs) rejection.subs.push_back(sub);
        conn.closing = true; conn.subs.clear(); eraseJobs(id);
        return rejection;
    }
    void request(Subscription sub) {
        std::optional<Rejection> rejection;
        {
            std::lock_guard lock(mutex);
            auto it = connections.find(sub.connId);
            if (it == connections.end() || it->second.closing) return;
            auto &conn = it->second;
            auto name = sub.subId.str();
            eraseJobs(sub.connId, &name);
            if (jobs.size() >= maxPending || (!conn.subs.contains(name) && conn.subs.size() >= maxSubs)) {
                rejection = rejectLocked(sub.connId, conn, "rate-limited: read admission capacity exceeded");
                rejection->subs.push_back(name);
            } else {
                sub.admissionId = ++nextId;
                conn.subs.insert_or_assign(name, Entry{sub.admissionId});
                jobs.push_back(Job{sub.admissionId, sub.connId, conn.authGeneration, conn.keys.values,
                    Clock::now() + std::chrono::seconds(timeout), std::move(sub)});
                changed.notify_one();
            }
        }
        if (rejection) onReject(*rejection);
    }
    void tick() {
        if (!enabled) return;
        std::vector<Rejection> rejected;
        {
            std::lock_guard lock(mutex);
            auto now = Clock::now();
            // Visit the queue once, not once per connection under the shared lock.
            std::set<uint64_t> expiredJobs;
            for (const auto &job : jobs) if (now >= job.deadline) expiredJobs.insert(job.connId);
            for (auto &[id, conn] : connections) {
                if (conn.closing || conn.subs.empty()) continue;
                bool expired = !configMatches() || (conn.nextCheck != Clock::time_point::max()
                    && now >= conn.nextCheck + std::chrono::seconds(timeout));
                expired |= expiredJobs.contains(id);
                if (expired) rejected.push_back(rejectLocked(id, conn, "error: read policy temporarily unavailable"));
            }
            changed.notify_one();
        }
        for (const auto &rejection : rejected) onReject(rejection);
    }
    void start() {
        if (enabled) worker = std::thread([this] { run(); });
    }
    void run() {
        setThreadName("ReadPolicy");
        ReadPolicyProcess process;
        // Warm the policy independently of the first reader's request.
        try { process.start(plugin); } catch (...) { process.stop(); }
        bool preferRecheck = true;
        while (true) {
            std::optional<Job> job;
            {
                std::unique_lock lock(mutex);
                if (stopping) return;
                auto now = Clock::now();
                if (preferRecheck || jobs.empty()) {
                    for (auto &[id, conn] : connections) {
                        if (!conn.closing && !conn.checkId && !conn.subs.empty() && conn.nextCheck <= now) {
                            conn.checkId = ++nextId;
                            job.emplace(Job{conn.checkId, id, conn.authGeneration, conn.keys.values,
                                conn.nextCheck + std::chrono::seconds(timeout), std::nullopt});
                            break;
                        }
                    }
                }
                if (!job && !jobs.empty()) { job.emplace(std::move(jobs.front())); jobs.pop_front(); }
                if (!job) { changed.wait_for(lock, std::chrono::milliseconds(100)); continue; }
                preferRecheck = !preferRecheck;
            }
            std::string decision = "unavailable";
            bool broken = false;
            try {
                // Queue expiry is not a broken process generation. It must not
                // invalidate other clients that already have valid decisions.
                if (Clock::now() < job->deadline && configMatches())
                    decision = process.decide(plugin, job->id, job->keys, job->deadline);
            } catch (...) { process.stop(); broken = true; }
            std::optional<Subscription> admitted;
            std::vector<Rejection> rejected;
            {
                std::lock_guard lock(mutex);
                auto it = connections.find(job->connId);
                if (broken) {
                    for (auto &[id, conn] : connections) if (!conn.closing && !conn.subs.empty())
                        rejected.push_back(rejectLocked(id, conn, "error: read policy temporarily unavailable"));
                } else if (it != connections.end() && !it->second.closing) {
                    auto &conn = it->second;
                    auto entry = job->sub ? conn.subs.find(job->sub->subId.str()) : conn.subs.end();
                    bool valid = job->sub ? (entry != conn.subs.end() && entry->second.token == job->id) : conn.checkId == job->id;
                    if (!job->sub && valid) conn.checkId = 0;
                    if (valid && conn.authGeneration != job->authGeneration && Clock::now() < job->deadline) {
                        job->authGeneration = conn.authGeneration; job->keys = conn.keys.values;
                        if (job->sub) jobs.push_front(std::move(*job));
                        // A recheck will be retried on the next loop, with its original due time.
                    } else if (valid) {
                        if (Clock::now() >= job->deadline) decision = "unavailable";
                        if (decision == "allow" && configMatches()) {
                            conn.nextCheck = Clock::now() + std::chrono::seconds(interval);
                            if (job->sub) { entry->second.admitted = true; admitted.emplace(std::move(*job->sub)); }
                        } else rejected.push_back(rejectLocked(job->connId, conn,
                            decision == "deny" ? "restricted: read access denied" : "error: read policy temporarily unavailable"));
                    }
                }
            }
            for (const auto &rejection : rejected) onReject(rejection);
            if (admitted) onAdmit(std::move(*admitted));
        }
    }
};
