#include <pthread.h>
#include <signal.h>

#include <docopt.h>

#include "RelayServer.h"


static const char USAGE[] =
R"(
    Usage:
      relay
)";



static void checkConfig() {
    if (cfg().relay__info__pubkey.size()) {
        try {
            if (!cfg().relay__info__pubkey.starts_with("npub1")) {
                auto p = from_hex(cfg().relay__info__pubkey);
                if (p.size() != 32) throw herr("bad size");
            }
        } catch (std::exception &e) {
            LW << "Your relay.info.pubkey is incorrectly formatted. It should be an npub or 64 hex digits.";
        }
    }

    if (cfg().events__rejectEphemeralEventsOlderThanSeconds >= cfg().events__ephemeralEventsLifetimeSeconds) {
        LW << "rejectEphemeralEventsOlderThanSeconds is >= ephemeralEventsLifetimeSeconds, which could result in unnecessary disk activity";
    }
}


void cmd_relay(const std::vector<std::string> &subArgs) {
    docopt::docopt(USAGE, subArgs, true, "");

    RelayServer s;
    s.run();
}

void RelayServer::run() {
    readAdmission.configure(); // validate before starting any serving threads
    readAdmission.onAdmit = [this](Subscription &&sub) {
        auto connId = sub.connId;
        tpReqWorker.dispatch(connId, MsgReqWorker{MsgReqWorker::NewSub{std::move(sub)}});
    };
    readAdmission.onReject = [this](const ReadAdmission::Rejection &rejection) {
        for (const auto &sub : rejection.subs)
            sendToConn(rejection.connId, tao::json::to_string(tao::json::value::array({"CLOSED", sub, rejection.reason})));
        terminateConn(rejection.connId);
    };
    {
        sigset_t set;
        sigemptyset(&set);
        sigaddset(&set, SIGUSR1);
        int s = pthread_sigmask(SIG_BLOCK, &set, NULL);
        if (s != 0) throw herr("Unable to set sigmask: ", strerror(errno));
    }

    tpIngester.init("Ingester", cfg().relay__numThreads__ingester, [this](auto &thr){
        runIngester(thr);
    });

    tpWriter.init("Writer", 1, [this](auto &thr){
        runWriter(thr);
    });

    tpReqWorker.init("ReqWorker", cfg().relay__numThreads__reqWorker, [this](auto &thr){
        runReqWorker(thr);
    });

    tpReqMonitor.init("ReqMonitor", cfg().relay__numThreads__reqMonitor, [this](auto &thr){
        runReqMonitor(thr);
    });

    tpNegentropy.init("Negentropy", cfg().relay__numThreads__negentropy, [this](auto &thr){
        runNegentropy(thr);
    });

    tpWebsocket.init("Websocket", 1, [this](auto &thr){ runWebsocket(thr); });
    while (!websocketReady.load()) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    readAdmission.start();

    cronThread = std::thread([this]{
        runCron();
    });

    signalHandlerThread = std::thread([this]{
        runSignalHandler();
    });

    // Monitor for config file reloads

    checkConfig();

    auto configFileChangeWatcher = hoytech::file_change_monitor(configFile);

    configFileChangeWatcher.setDebounce(100);

    configFileChangeWatcher.run([&](){
        try {
            loadConfig(configFile);
            checkConfig();
        } catch (std::exception &e) {
            LE << "Error parsing config file, continuing with previous config: " << e.what();
        }
    });


    tpWebsocket.join();
}
