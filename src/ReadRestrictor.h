#pragma once

#include <algorithm>
#include <string_view>
#include <vector>

#include "PackedEvent.h"
#include "config.h"
#include "filters.h"

#include "golpe.h"

struct ReadRestrictor {
private:
    inline static thread_local uint64_t configVer = 0;
    inline static thread_local flat_hash_set<uint64_t> restrictedKinds_;

public:
    // Admission of filters that explicitly list a NIP-59 gift-wrap kind.
    enum class GiftWrapScope {
        NotRequested,      // no filter lists kind 1059 or 21059
        Allowed,           // every such filter is #p-scoped to authenticated keys
        NeedsAuth,         // a listed #p recipient is not authenticated yet
        MissingRecipients, // a gift-wrap filter has no #p; AUTH cannot fix it
    };

    static bool shouldSendToSubscriber(const PackedEventView &packed, const std::vector<Bytes32> &keys) {
        if (keys.empty()) return shouldSendToSubscriber(packed, Bytes32());
        return std::any_of(keys.begin(), keys.end(), [&](auto key) { return shouldSendToSubscriber(packed, key); });
    }
    static bool isFilterAllowedToCount(const NostrFilterGroup &fg, const std::vector<Bytes32> &keys) {
        if (keys.empty()) return isFilterAllowedToCount(fg, Bytes32());
        // Conservative for multi-key mixed counts: never broaden the existing
        // count predicate. Per-event REQ still composes all authenticated keys.
        return std::any_of(keys.begin(), keys.end(), [&](auto key) { return isFilterAllowedToCount(fg, key); });
    }
    static void init(){
        parseCommaSeparatedKinds(cfg().relay__auth__restrictedReadKinds, restrictedKinds_);
        // Kinds 4/4444 require participant AUTH independently of community mode.
        restrictedKinds_.insert(4);
        restrictedKinds_.insert(4444);
        // NIP-59 gift wraps (regular 1059, ephemeral 21059) are readable only by an
        // authenticated p-tag recipient, independently of configuration.
        restrictedKinds_.insert(1059);
        restrictedKinds_.insert(21059);
    }

    static bool participantOnly(uint64_t kind) { return kind == 4 || kind == 4444; }

    // Gift wraps are signed by a random one-time key, so their author is never
    // a reader: only an authenticated recipient named in a p tag may read them.
    static bool recipientOnly(uint64_t kind) { return kind == 1059 || kind == 21059; }

    // A filter that lists kind 1059 or 21059 asks for gift wraps explicitly. It is
    // admitted only when it names its recipients in #p and every named recipient is
    // authenticated on this connection. Callers reject the whole REQ/COUNT/NEG-OPEN
    // otherwise, so a mixed subscription (for example ContextVM's 25910 + 1059 +
    // 21059) is told to AUTH and resubscribe instead of silently missing its gift
    // wraps. Kind-less filters do not ask for gift wraps explicitly: they keep their
    // public results, and gift wraps are withheld per event (shouldSendToSubscriber).
    static GiftWrapScope giftWrapScope(const NostrFilterGroup &group, const std::vector<Bytes32> &keys) {
        bool requested = false;
        bool needsAuth = false;

        for (const auto &f : group.filters) {
            if (f.neverMatch || !f.kinds) continue;

            bool listsGiftWrap = false;
            for (size_t i = 0; i < f.kinds->size(); ++i) {
                if (recipientOnly(f.kinds->at(i))) {
                    listsGiftWrap = true;
                    break;
                }
            }
            if (!listsGiftWrap) continue;
            requested = true;

            auto it = f.tags.find('p');
            if (it == f.tags.end()) return GiftWrapScope::MissingRecipients;

            for (size_t i = 0; i < it->second.size(); ++i) {
                Bytes32 recipient(it->second.at(i));
                if (std::find(keys.begin(), keys.end(), recipient) == keys.end()) needsAuth = true;
            }
        }

        if (!requested) return GiftWrapScope::NotRequested;
        return needsAuth ? GiftWrapScope::NeedsAuth : GiftWrapScope::Allowed;
    }

    static bool mayContainRestricted(const NostrFilterGroup &group) {
        for (const auto &filter : group.filters) {
            if (!filter.kinds) return true;
            for (size_t i = 0; i < filter.kinds->size(); ++i)
                if (restrictedKinds().contains(filter.kinds->at(i))) return true;
        }
        return false;
    }

    static flat_hash_set<uint64_t>& restrictedKinds() {
        if (configVer != cfg().version()) {
            init();
            configVer = cfg().version();
        }
        return restrictedKinds_;
    }

    static bool isFilterGroupFullyRestricted(const NostrFilterGroup &fg) {
        if (restrictedKinds().empty() || fg.filters.empty()) return false;
        for (const auto& f : fg.filters) {
            if (!isFilterFullyRestricted(f)) return false;
        }
        return true;
    }

    static bool isFilterFullyRestricted(const NostrFilter &filter) {
        if (!filter.kinds) return false;

        for (size_t i = 0; i < filter.kinds->size(); ++i) {
            uint64_t kind = filter.kinds->at(i);

            if (!restrictedKinds().contains(kind)) {
                return false;
            }
        }
        return true;
    }

    static bool isFilterAllowedToCount(const NostrFilterGroup &fg, Bytes32 pubkey) {
        if (restrictedKinds().empty()) return true;

        bool pubkeyIsNull = pubkey.isNull();

        for (const auto &f: fg.filters) {
            bool hasSomeRestrictedKind = !f.kinds;
            bool mandatory = !f.kinds;
            // Kind-less filters can match gift wraps as well.
            bool giftWrap = !f.kinds;
            for (size_t i = 0; f.kinds && i < f.kinds->size(); ++i) {
                uint64_t kind = f.kinds->at(i);
                mandatory |= participantOnly(kind);
                giftWrap |= recipientOnly(kind);
                if (restrictedKinds().contains(kind)) {
                    hasSomeRestrictedKind = true;
                }
            }
            if (hasSomeRestrictedKind) {
                if (pubkeyIsNull) {
                    return false;
                }
                bool pScoped = false;
                if (auto it = f.tags.find('p'); it != f.tags.end()) {
                    pScoped = allPubkeysMatch(it->second, pubkey);
                }
                // Gift wraps are counted only for their recipient; an authors
                // scope never suffices because the outer author is a random key.
                if (giftWrap) {
                    if (!pScoped) return false;
                    continue;
                }
                if (!mandatory && !cfg().relay__auth__restrictReadToInvolvedPubkey) {
                    continue;
                }
                bool authorScoped = f.authors && allPubkeysMatch(*f.authors, pubkey);
                if (!authorScoped && !pScoped) return false;
            }
        }
        return true;
    }

    static bool allPubkeysMatch(const FilterSetBytes& set, Bytes32 authed) {
        if (set.size() == 0) return false;

        for (size_t i = 0; i < set.size(); ++i) {
            Bytes32 val(set.at(i));
            if (val != authed) return false;
        }
        return true;
    }

    // True when key is named in one of the event's p tags.
    static bool isTaggedRecipient(const PackedEventView &packed, const Bytes32 &key) {
        bool found = false;

        packed.foreachTag([&](char tagName, std::string_view tagVal) {
            if (tagName == 'p' && tagVal.size() == 32 && key == Bytes32(tagVal)) {
                found = true;
                return false;
            }

            return true;
        });

        return found;
    }

    // Returns true if the event should be sent to the subscriber. Every stored,
    // live and negentropy delivery path applies this per event.
    static bool shouldSendToSubscriber(const PackedEventView &packed, const Bytes32 &subscriberAuthedPubkey) {
        if (!restrictedKinds().contains(packed.kind())) {
            return true;
        }

        if (subscriberAuthedPubkey.isNull()) {
            return false;
        }

        // Recipient only: authoring a gift wrap (with its random key) grants no read.
        if (recipientOnly(packed.kind())) return isTaggedRecipient(packed, subscriberAuthedPubkey);

        if(!participantOnly(packed.kind()) && !cfg().relay__auth__restrictReadToInvolvedPubkey) return true;

        bool involved = subscriberAuthedPubkey == packed.pubkey();

        packed.foreachTag([&](char tagName, std::string_view tagVal) {
            if (tagName == 'p' && tagVal.size() == 32) {
                if (subscriberAuthedPubkey == Bytes32(tagVal)) {
                    involved = true;
                    return false;
                }
            }

            return true;
        });

        return involved;
    }
};
