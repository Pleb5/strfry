#!/usr/bin/env python3
"""Conservative config/advertisement preflight for Budabit read admission.

Not proof of running plugin freshness or member access. Use controlled
authenticated member/outsider probes before opening ingress.
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

TOKEN = re.compile(r'\s+|\#[^\n]*|"(?:[^"\\]|\\.)*"|[{}=;]|[A-Za-z_][A-Za-z_0-9]*|-?[0-9]+')
UNFILTERED = {1, 5, 1984, 30000, 32222}


def parse_config(text):
    tokens, pos = [], 0
    for match in TOKEN.finditer(text):
        if match.start() != pos:
            raise ValueError("unsupported configuration syntax")
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
            value = json.loads(tokens[index])
            index += 1
            if type(value) not in (str, bool, int):
                raise ValueError("unsupported configuration value")
            values[key] = value
        else:
            raise ValueError("unsupported configuration statement")
    if stack:
        raise ValueError("unclosed configuration block")
    return values


def private_claim(info):
    claim = info.get("budabit", {}).get("read_control", {})
    return (type(claim.get("version")) is int and claim["version"] == 2
            and claim.get("mode") == "members" and claim.get("scope") == "relay")


def validate(values, env):
    policy = BudabitConfig.from_env(env)
    if any(key.startswith("relay.readControl.") for key in values):
        raise ValueError("remove obsolete snapshot readControl configuration")
    plugin = values.get("relay.readPolicy.plugin", "")
    if not isinstance(plugin, str) or bool(plugin) != (policy.read_control == "members"):
        raise ValueError("readPolicy.plugin and BUDABIT_READ_CONTROL disagree")
    extra = json.loads(values.get("relay.info.extra", "") or "{}")
    if not isinstance(extra, dict):
        raise ValueError("invalid relay metadata")
    if not plugin:
        if extra.get("read_policy") or extra.get("budabit", {}).get("read_control"):
            raise ValueError("public mode must not advertise private access")
        return False

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    for key in ("relay.readPolicy.plugin", "relay.writePolicy.plugin"):
        value = values.get(key, "")
        require(isinstance(value, str) and Path(value).is_absolute() and os.access(value, os.X_OK), "policy must be an executable absolute path")
    for key, default, maximum in (("timeoutSeconds", 2, 30), ("recheckSeconds", 5, 300),
                                   ("maxPending", 1024, 65536), ("maxConnections", 4096, 100000)):
        value = values.get("relay.readPolicy." + key, default)
        require(type(value) is int and 1 <= value <= maximum, "invalid admission bound")
    require(values.get("relay.auth.enabled", True) is True, "private reads require AUTH")
    url = values.get("relay.auth.serviceUrl", "")
    require(isinstance(url, str) and normalize_relay(url) and not re.search(r"[?#\s]", url), "AUTH requires exact service URL")
    require(values.get("relay.auth.maxAgeSeconds", 600) >= 120, "AUTH age too short for human signers")
    require(values.get("relay.maxFilterLimitCount", 1000000) == 0, "disable COUNT")
    require(values.get("relay.negentropy.enabled", True) is False, "disable Negentropy")
    db = values.get("db", "")
    require(isinstance(db, str) and Path(db).is_absolute(), "private database must be absolute")
    require(Path(env.get("STRFRY_POLICY_DB_FILE", "/var/lib/strfry/db/data.mdb")) == Path(db) / "data.mdb", "policy/core databases differ")
    for key in ("dumpInAll", "dumpInEvents", "dumpInReqs"):
        require(values.get("relay.logging." + key, False) is False, "disable raw private logging")
    require(private_claim(extra), "configure version-2 members/relay metadata")
    require(set(extra.get("budabit", {})) == {"read_control"}, "private metadata must not disclose branch lists")
    claim = extra["budabit"]["read_control"]
    require(set(claim) <= {"version", "mode", "scope", "unfiltered_kinds"}, "unexpected private metadata")
    kinds = claim.get("unfiltered_kinds", [])
    require(isinstance(kinds, list) and all(type(kind) is int for kind in kinds) and set(kinds) == UNFILTERED, "declare authority/text completeness kinds")
    restricted = {int(value.strip()) for value in values.get("relay.auth.restrictedReadKinds", "4,1059,4444").split(",") if value.strip()}
    if values.get("relay.auth.restrictReadToInvolvedPubkey", True):
        require(not restricted & UNFILTERED, "unfiltered metadata conflicts with event restrictions")
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
    core = info.get("read_policy", {})
    if enabled:
        if (not private_claim(info) or info.get("limitation", {}).get("auth_required") is not True
                or type(core.get("version")) is not int or core["version"] != 1
                or core.get("admission") != "req" or core.get("consistency") != "eventual"
                or type(core.get("recheck_seconds")) is not int or not 1 <= core["recheck_seconds"] <= 300):
            raise ValueError("serving endpoint lacks read admission capability")
    elif core or info.get("budabit", {}).get("read_control"):
        raise ValueError("serving endpoint/config disagree")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("STRFRY_CONFIG", "/etc/strfry.conf"))
    parser.add_argument("--config-only", action="store_true", help="configuration only (also the default)")
    parser.add_argument("--url", help="also check serving NIP-11; not an authenticated access probe")
    args = parser.parse_args()
    try:
        with open(args.config, encoding="utf-8") as handle:
            text = handle.read(1048577)
        if len(text) > 1048576:
            raise ValueError("configuration too large")
        enabled = validate(parse_config(text), {**os.environ, "STRFRY_CONFIG": args.config})
        if args.url:
            check_advertisement(args.url, enabled)
        print("read admission config/advertisement: " + ("members" if enabled else "off"))
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print("read admission preflight failed; check local config/env and plugin wiring", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
