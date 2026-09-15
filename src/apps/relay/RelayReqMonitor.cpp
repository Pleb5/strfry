#include "RelayServer.h"

#include "ActiveMonitors.h"
#include "ReadRestrictor.h"



void RelayServer::runReqMonitor(ThreadPool<MsgReqMonitor>::Thread &thr) {
    auto dbChangeWatcher = hoytech::file_change_monitor(dbDir + "/data.mdb");

    dbChangeWatcher.setDebounce(100);

    dbChangeWatcher.run([&](){
        tpReqMonitor.dispatchToAll([]{ return MsgReqMonitor{MsgReqMonitor::DBChange{}}; });
    });


    Decompressor decomp;
    ActiveMonitors monitors;
    flat_hash_map<uint64_t, AuthKeys> connIdToAuthedPubkey;
    uint64_t currEventId = MAX_U64;

    while (1) {
        auto newMsgs = thr.inbox.pop_all();

        auto txn = env.txn_ro();

        uint64_t latestEventId = getMostRecentLevId(txn);
        if (currEventId > latestEventId) currEventId = latestEventId;

        for (auto &newMsg : newMsgs) {
            if (auto msg = std::get_if<MsgReqMonitor::NewSub>(&newMsg.msg)) {
                auto connId = msg->sub.connId;
                if (!readAdmission.current(msg->sub)) continue;
                auto it = connIdToAuthedPubkey.find(connId);
                auto connAuthedPubkey = it == connIdToAuthedPubkey.end() ? std::vector<Bytes32>{} : it->second.values;

                env.foreach_Event(txn, [&](auto &ev){
                    if (!readAdmission.current(msg->sub)) return false;
                    PackedEventView packed(ev.buf);
                    if (msg->sub.filterGroup.doesMatch(packed)) {
                        if (ReadRestrictor::shouldSendToSubscriber(packed, connAuthedPubkey)) {
                            sendEvent(connId, msg->sub.subId, getEventJson(txn, decomp, ev.primaryKeyId), msg->sub.admissionId);
                        }
                    }

                    return true;
                }, false, msg->sub.latestEventId + 1);

                msg->sub.latestEventId = latestEventId;
                if (!readAdmission.current(msg->sub)) continue;

                if (!monitors.addSub(txn, std::move(msg->sub), latestEventId)) {
                    sendNoticeError(connId, std::string("too many concurrent REQs"));
                }
            } else if (auto msg = std::get_if<MsgReqMonitor::SetAuth>(&newMsg.msg)) {
                connIdToAuthedPubkey[msg->connId].add(msg->authed);
            } else if (auto msg = std::get_if<MsgReqMonitor::RemoveSub>(&newMsg.msg)) {
                monitors.removeSub(msg->connId, msg->subId);
            } else if (auto msg = std::get_if<MsgReqMonitor::CloseConn>(&newMsg.msg)) {
                connIdToAuthedPubkey.erase(msg->connId);
                monitors.closeConn(msg->connId);
            } else if (std::get_if<MsgReqMonitor::DBChange>(&newMsg.msg)) {
                env.foreach_Event(txn, [&](auto &ev){
                    monitors.process(txn, ev, [&](RecipientList &&recipients, uint64_t levId){
                        PackedEventView packed(ev.buf);
                        if (ReadRestrictor::restrictedKinds().contains(packed.kind())) {
                            RecipientList filteredRecipients;
                            for (const auto &recipient : recipients) {
                                auto it = connIdToAuthedPubkey.find(recipient.connId);
                                auto authedPubkey = it == connIdToAuthedPubkey.end() ? std::vector<Bytes32>{} : it->second.values;
                                if (ReadRestrictor::shouldSendToSubscriber(packed, authedPubkey)) {
                                    filteredRecipients.emplace_back(recipient);
                                }
                            }
                            if (!filteredRecipients.empty()) {
                                sendEventToBatch(std::move(filteredRecipients), std::string(getEventJson(txn, decomp, levId)));
                            }
                        } else {
                            sendEventToBatch(std::move(recipients), std::string(getEventJson(txn, decomp, levId)));
                        }
                    });
                    return true;
                }, false, currEventId + 1);

                currEventId = latestEventId;
            }
        }
    }
}
