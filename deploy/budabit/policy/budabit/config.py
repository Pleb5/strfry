"""Environment configuration for the Budabit write-control stage."""

from dataclasses import dataclass, field

from . import protocol


def _flag(env, name, default="0"):
    return str(env.get(name, default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class BudabitConfig:
    branches: list = field(default_factory=list)
    strict: bool = False
    dry_run: bool = False
    reject_censored_addresses: bool = False
    reconcile_seconds: float = 300.0
    strfry_bin: str = "/usr/local/bin/strfry"
    strfry_config: str = "/etc/strfry.conf"
    scan_timeout_seconds: float = 60.0
    policy_version: str = "1"
    loader_enabled: bool = True
    auto_host_url: str = ""

    @property
    def enabled(self):
        return bool(self.branches) or bool(self.auto_host_url)

    @classmethod
    def from_env(cls, env):
        raw = env.get("BUDABIT_BRANCHES", "")
        branches = []
        for item in raw.replace("\n", ",").split(","):
            item = item.strip()
            if not item:
                continue
            if not protocol.parse_definition_address(item):
                raise ValueError(
                    f"BUDABIT_BRANCHES entry is not a 32222:<owner>:<communityId> address: {item}"
                )
            if item not in branches:
                branches.append(item)
        auto_host_raw = env.get("BUDABIT_AUTO_HOST_URL", "").strip()
        auto_host_url = ""
        if auto_host_raw:
            auto_host_url = protocol.normalize_relay(auto_host_raw) or ""
            if not auto_host_url:
                raise ValueError(f"BUDABIT_AUTO_HOST_URL is not a normalized wss URL: {auto_host_raw}")
        mode = env.get("BUDABIT_MODE", "passthrough").strip().lower()
        if mode not in ("passthrough", "strict"):
            raise ValueError(f"BUDABIT_MODE must be passthrough or strict, got {mode!r}")
        return cls(
            branches=branches,
            strict=mode == "strict",
            dry_run=_flag(env, "BUDABIT_DRY_RUN"),
            reject_censored_addresses=_flag(env, "BUDABIT_REJECT_CENSORED_ADDRESSES"),
            reconcile_seconds=float(env.get("BUDABIT_RECONCILE_SECONDS", "300")),
            strfry_bin=env.get("BUDABIT_STRFRY_BIN", "/usr/local/bin/strfry"),
            strfry_config=env.get("STRFRY_CONFIG", "/etc/strfry.conf"),
            scan_timeout_seconds=float(env.get("BUDABIT_SCAN_TIMEOUT_SECONDS", "60")),
            policy_version=env.get("BUDABIT_POLICY_VERSION", "1"),
            loader_enabled=not _flag(env, "BUDABIT_DISABLE_LOADER"),
            auto_host_url=auto_host_url,
        )
