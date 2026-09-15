"""Environment configuration for the Budabit write-control stage."""

from dataclasses import dataclass, field
import math

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
    read_control: str = "off"
    read_max_pubkeys: int = 20000
    read_scan_max_bytes: int = 32 * 1024**2
    read_scan_timeout_seconds: float = 5.0
    read_refresh_seconds: float = 1.0
    read_max_age_seconds: float = 10.0

    @property
    def enabled(self):
        return bool(self.branches) or bool(self.auto_host_url)

    def validate_read_control(self):
        if self.read_control not in ("off", "members"):
            raise ValueError("BUDABIT_READ_CONTROL must be off or members")
        if self.read_control == "off":
            return
        if not self.enabled or self.dry_run or not self.loader_enabled:
            raise ValueError("member reads require enforcing write control and its live loader")
        if len(self.branches) != 1 or self.auto_host_url:
            raise ValueError("member reads require one explicit branch and no auto-hosting")
        for name, value, maximum in (
            ("read_max_pubkeys", self.read_max_pubkeys, 200000),
            ("read_scan_max_bytes", self.read_scan_max_bytes, 256 * 1024**2),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be between 1 and {maximum}")
        for value in (self.reconcile_seconds, self.read_scan_timeout_seconds,
                      self.read_refresh_seconds, self.read_max_age_seconds):
            if not math.isfinite(value) or not 0 < value <= 3600:
                raise ValueError("private reconcile/scan timeouts must be finite, positive, at most 3600 seconds")
        if self.read_scan_timeout_seconds >= self.read_max_age_seconds or self.read_refresh_seconds >= self.read_max_age_seconds:
            raise ValueError("reader scan/refresh intervals must be below the maximum policy age")

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
        config = cls(
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
            read_control=env.get("BUDABIT_READ_CONTROL", "off").strip().lower(),
            read_max_pubkeys=int(env.get("BUDABIT_READ_MAX_PUBKEYS", "20000")),
            read_scan_max_bytes=int(env.get("BUDABIT_READ_SCAN_MAX_BYTES", str(32 * 1024**2))),
            read_scan_timeout_seconds=float(env.get("BUDABIT_READ_SCAN_TIMEOUT_SECONDS", "5")),
            read_refresh_seconds=float(env.get("BUDABIT_READ_REFRESH_SECONDS", "1")),
            read_max_age_seconds=float(env.get("BUDABIT_READ_MAX_AGE_SECONDS", "10")),
        )
        config.validate_read_control()
        return config
