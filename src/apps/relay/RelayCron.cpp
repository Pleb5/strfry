#include <hoytech/timer.h>

#include "RelayServer.h"


void RelayServer::runCron() {
    hoytech::timer cron;

    cron.setupCb = []{ setThreadName("cron"); };

    std::unique_ptr<hoytech::file_change_monitor> readersWatcher;
    if (readGate.enabled) {
        // Watch the directory: snapshots are replaced atomically, not modified
        // in place. Periodic refresh also covers missed notifications/removal.
        readersWatcher = std::make_unique<hoytech::file_change_monitor>(readGate.path.substr(0, readGate.path.find_last_of('/')));
        readersWatcher->setDebounce(20);
        readersWatcher->run([&] { readGate.refresh(); });
        cron.repeat(100'000UL, [&] {
            readGate.refresh();
            tpWriter.dispatch(0, MsgWriter{MsgWriter::Tick{}});
            for (auto id : readGate.terminations()) terminateConn(id);
            // Also resumes deferred live catch-up after a gate installation.
            if (readGate.available()) tpReqMonitor.dispatchToAll([] { return MsgReqMonitor{MsgReqMonitor::DBChange{}}; });
        });
    }


    // Delete expired events

    cron.repeat(9 * 1'000'000UL, [&]{
        std::vector<uint64_t> expiredLevIds;
        uint64_t numEphemeral = 0;
        uint64_t numExpired = 0;

        {
            auto txn = env.txn_ro();

            auto mostRecent = getMostRecentLevId(txn);
            uint64_t now = hoytech::curr_time_s();
            uint64_t ephemeralCutoff = now - cfg().events__ephemeralEventsLifetimeSeconds;

            env.generic_foreachFull(txn, env.dbi_Event__expiration, lmdb::to_sv<uint64_t>(0), lmdb::to_sv<uint64_t>(0), [&](auto k, auto v) {
                auto expiration = lmdb::from_sv<uint64_t>(k);
                auto levId =  lmdb::from_sv<uint64_t>(v);

                if (expiration > now) return false;
                if (levId == mostRecent) return true; // don't delete because it could cause levId re-use

                if (expiration == 1) { // Ephemeral event
                    auto view = env.lookup_Event(txn, levId);
                    if (!view) throw herr("missing event from index, corrupt DB?");
                    uint64_t created = PackedEventView(view->buf).created_at();

                    if (created <= ephemeralCutoff) {
                        numEphemeral++;
                        expiredLevIds.emplace_back(levId);
                    }
                } else {
                    numExpired++;
                    expiredLevIds.emplace_back(levId);
                }

                return true;
            });
        }

        if (expiredLevIds.size() > 0) {
            if (readGate.enabled) {
                tpWriter.dispatch(0, MsgWriter{MsgWriter::Expire{std::move(expiredLevIds)}});
                return;
            }
            auto txn = env.txn_rw();
            NegentropyFilterCache neFilterCache;

            uint64_t numDeleted = deleteEvents(txn, neFilterCache, expiredLevIds);

            txn.commit();

            if (numDeleted) LI << "Deleted " << numDeleted << " events (ephemeral=" << numEphemeral << " expired=" << numExpired << ")";
        }
    });



    cron.run();

    while (1) std::this_thread::sleep_for(std::chrono::seconds(1'000'000));
}
