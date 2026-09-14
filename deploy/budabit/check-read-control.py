#!/usr/bin/env python3
"""Read-only preflight for the explicit Budabit preset, not a config renderer.

Accept a conservative assignment/block subset of strfry's config syntax. Refuse
includes, duplicate keys and expressions rather than disagree with the core.
This validates local artifacts, NOT the core's active epoch/sequence or egress.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import urllib.request

from policy.budabit.config import BudabitConfig
from policy.budabit.protocol import normalize_relay
from policy.budabit.readers import check_read_snapshot


TOKEN = re.compile(r'\s+|\#[^\n]*|"(?:[^"\\]|\\.)*"|[{}=;]|[A-Za-z_][A-Za-z_0-9]*|-?[0-9]+')


def parse_config(text):
    tokens, pos = [], 0
    for match in TOKEN.finditer(text):
        if match.start() != pos:
            raise ValueError("unsupported configuration syntax; use explicit assignments/blocks")
        pos = match.end()
        token = match.group()
        if not token.isspace() and not token.startswith("#"):
            tokens.append(token)
    if pos != len(text):
        raise ValueError("unsupported configuration syntax")
    values, blocks, stack, index = {}, set(), [], 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token == ";":
            continue
        if token == "}":
            if not stack:
                raise ValueError("unbalanced configuration block")
            stack.pop()
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", token) or index >= len(tokens):
            raise ValueError("invalid configuration key")
        key = ".".join([*stack, token])
        operator = tokens[index]
        index += 1
        if key in values or key in blocks:
            raise ValueError("duplicate configuration key/block")
        if operator == "{":
            blocks.add(key)
            stack.append(token)
        elif operator == "=" and index < len(tokens):
            raw = tokens[index]
            index += 1
            try:
                value = json.loads(raw)
            except ValueError as error:
                raise ValueError("configuration values must be strings, booleans or integers") from error
            if type(value) not in (str, bool, int):
                raise ValueError("unsupported configuration value")
            values[key] = value
        else:
            raise ValueError("unsupported configuration statement")
    if stack:
        raise ValueError("unclosed configuration block")
    return values


def validate(values, env, *, snapshot=True):
    policy = BudabitConfig.from_env(env)
    enabled = values.get("relay.readControl.enabled", False)
    if type(enabled) is not bool:
        raise ValueError("relay.readControl.enabled must be boolean")
    if enabled != (policy.read_control == "members"):
        raise ValueError("C++ readControl.enabled and BUDABIT_READ_CONTROL disagree")
    if not enabled:
        return False

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    require(values.get("relay.readControl.branchAddress") == policy.branches[0], "pinned branch differs from BUDABIT_BRANCHES")
    require(values.get("relay.readControl.snapshotPath") == policy.read_snapshot_path, "snapshot path differs between core and policy")
    require(values.get("relay.readControl.maxSnapshotBytes", 2097152) == policy.read_max_snapshot_bytes, "snapshot byte bounds disagree")
    timeout = values.get("relay.readControl.gateTimeoutSeconds", 3)
    require(type(timeout) is int and 1 <= timeout <= 60, "invalid core gate timeout")
    require(values.get("relay.auth.enabled", True) is True, "private reads require AUTH enabled")
    url = values.get("relay.auth.serviceUrl", "")
    require(isinstance(url, str) and normalize_relay(url) and not re.search(r"[?#\s]", url), "AUTH needs an exact wss service URL without query/fragment")
    require(values.get("relay.auth.maxAgeSeconds", 600) >= 120, "AUTH age is too short for human/bunker signing")
    require(values.get("relay.maxFilterLimitCount", 0) == 0, "private COUNT must be disabled")
    require(values.get("relay.negentropy.enabled", True) is False, "private Negentropy must be disabled")
    plugin = values.get("relay.writePolicy.plugin", "")
    require(isinstance(plugin, str) and Path(plugin).is_absolute() and os.access(plugin, os.X_OK), "private mode needs an executable absolute write-policy path")
    db = values.get("db", "")
    require(isinstance(db, str) and Path(db).is_absolute(), "private db path must be absolute")
    require(Path(env.get("STRFRY_POLICY_DB_FILE", "/var/lib/strfry/db/data.mdb")) == Path(db) / "data.mdb", "policy and core must scan the same database")
    for key in ("dumpInAll", "dumpInEvents", "dumpInReqs"):
        require(values.get("relay.logging." + key, False) is False, "disable raw event/request logging on private endpoints")
    if snapshot:
        ok, reason = check_read_snapshot(policy)
        require(ok, reason)
    return True


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("health endpoint redirected")


def check_advertisement(url, enabled):
    request = urllib.request.Request(url, headers={"Accept": "application/nostr+json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=5) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("NIP-11 response oversized")
    info = json.loads(raw)
    if not isinstance(info, dict):
        raise ValueError("invalid NIP-11 object")
    budabit, limitation = info.get("budabit", {}), info.get("limitation", {})
    if not isinstance(budabit, dict) or not isinstance(limitation, dict):
        raise ValueError("invalid NIP-11 capability object")
    claim = budabit.get("read_control", {})
    if not isinstance(claim, dict):
        raise ValueError("invalid NIP-11 read-control claim")
    if enabled and not (limitation.get("auth_required") is True
                        and type(claim.get("version")) is int and claim["version"] == 1
                        and claim.get("mode") == "members" and claim.get("scope") == "relay"):
        raise ValueError("serving endpoint lacks the required private capability")
    if not enabled and claim:
        raise ValueError("serving endpoint/config disagree about private mode")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("STRFRY_CONFIG", "/etc/strfry.conf"))
    parser.add_argument("--config-only", action="store_true", help="pre-start check; do not require a live snapshot")
    parser.add_argument("--url", help="also check NIP-11 at the serving HTTP endpoint")
    args = parser.parse_args()
    try:
        with open(args.config, encoding="utf-8") as handle:
            text = handle.read(1048577)
        if len(text) > 1048576:
            raise ValueError("config exceeds 1 MiB")
        env = {**os.environ, "STRFRY_CONFIG": args.config}
        enabled = validate(parse_config(text), env, snapshot=not args.config_only)
        if args.url:
            check_advertisement(args.url, enabled)
        print("read-control preflight: members" if enabled else "read-control preflight: off")
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        # No private addresses, roster, config or remote response body in output.
        print(f"read-control preflight failed ({type(error).__name__}); check config/env, policy and snapshot locally", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
