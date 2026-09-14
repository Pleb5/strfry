#pragma once

#include <array>
#include <cstdint>
#include <cstring>
#include <string_view>
#include <vector>
#include <algorithm>

#include "Bytes32.h"

struct AuthKeys {
  std::vector<Bytes32> values;
  bool empty() const { return values.empty(); }
  bool contains(Bytes32 key) const { return std::find(values.begin(), values.end(), key) != values.end(); }
  void add(Bytes32 key) { if (!contains(key)) values.push_back(key); }
};

struct AuthSession {

  static constexpr size_t kChallengeSize = 22;

  std::array<char, kChallengeSize> challenge{};
  Bytes32 authed;
  AuthKeys keys;

  AuthSession(std::string_view token) {
    if (token.size() != kChallengeSize)
      throw herr("challenge size not ", kChallengeSize, " bytes");
    ::memcpy(challenge.data(), token.data(), kChallengeSize);
  }

  void markAuthed(Bytes32 pubkey) { keys.add(pubkey); if (authed.isNull()) authed = pubkey; }

  bool isAuthed() const { return !authed.isNull(); }

  std::string_view challengeSv() const {
    return std::string_view(challenge.data(), challenge.size());
  }
};
