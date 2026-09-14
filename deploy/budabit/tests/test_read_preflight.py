import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from fixtures import ADDRESS, OWNER

DEPLOY = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("read_preflight", DEPLOY / "check-read-control.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class ReadPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.path = root / "readers.json"
        self.env = {
            "BUDABIT_BRANCHES": ADDRESS, "BUDABIT_READ_CONTROL": "members",
            "BUDABIT_READ_SNAPSHOT_PATH": str(self.path),
            "STRFRY_POLICY_DB_FILE": str(root / "data.mdb"),
        }
        self.values = {
            "db": str(root), "relay.readControl.enabled": True,
            "relay.readControl.branchAddress": ADDRESS,
            "relay.readControl.snapshotPath": str(self.path),
            "relay.auth.enabled": True, "relay.auth.serviceUrl": "wss://private.test/path",
            "relay.negentropy.enabled": False, "relay.maxFilterLimitCount": 0,
            "relay.writePolicy.plugin": str(DEPLOY / "write-policy.py"),
        }
        self.data = {"version": 1, "epoch": "1" * 64, "seq": 0, "branch_address": ADDRESS,
                     "owner": OWNER, "write_enforcement": True, "ready": True,
                     "heartbeat": 1, "updated_at": time.time(), "eligible_pubkeys": [OWNER]}
        self.path.write_text(json.dumps(self.data))
        self.core_path = Path(str(self.path) + ".core-status.json")
        self.core = {"version": 1, "pid": os.getpid(),
                     "process_start": Path('/proc/self/stat').read_text().rsplit(")", 1)[1].split()[19],
                     "boot_id": Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                     "epoch": self.data["epoch"], "pending_seq": 0, "installed_seq": 0,
                     "branch_address": ADDRESS, "heartbeat": 1, "ready": True, "installed": True,
                     "supervisor_running": True, "measured_monotonic_ns": time.monotonic_ns(),
                     "lease_deadline_monotonic_ns": time.monotonic_ns() + 3_000_000_000}
        self.core_path.write_text(json.dumps(self.core))
        self.core_path.chmod(0o600)

    def test_runtime_requires_installed_projection_exact_epoch_sequence_and_lease(self):
        for key, value in {"epoch": "2" * 64, "pending_seq": 1, "installed_seq": 1,
                           "heartbeat": 2, "ready": False, "installed": False,
                           "supervisor_running": False, "measured_monotonic_ns": 1,
                           "lease_deadline_monotonic_ns": 1, "process_start": "old",
                           "boot_id": "old", "pid": 999999999, "version": True}.items():
            self.core_path.write_text(json.dumps({**self.core, key: value}))
            with self.subTest(key=key), self.assertRaises((ValueError, OSError)):
                preflight.validate(self.values, self.env)
        self.core_path.write_text(json.dumps(self.core))
        self.assertTrue(preflight.validate(self.values, self.env))

    def test_core_status_protected_bounded_and_not_required_prestart(self):
        self.core_path.unlink()
        self.assertTrue(preflight.validate(self.values, self.env, snapshot=False))
        with self.assertRaises(OSError):
            preflight.validate(self.values, self.env)
        os.mkfifo(self.core_path, 0o600)
        with self.assertRaises(ValueError):
            preflight.validate(self.values, self.env)
        self.core_path.unlink()
        self.core_path.symlink_to(self.path)
        with self.assertRaises(OSError):
            preflight.validate(self.values, self.env)
        self.core_path.unlink()
        self.core_path.write_text(json.dumps(self.core))
        self.core_path.chmod(0o644)
        with self.assertRaises(ValueError):
            preflight.validate(self.values, self.env)
        self.core_path.chmod(0o600)
        self.core_path.write_text("x" * 16385)
        with self.assertRaises(ValueError):
            preflight.validate(self.values, self.env)

    def test_matching_private_and_public_presets(self):
        self.assertTrue(preflight.validate(self.values, self.env))
        template = preflight.parse_config((DEPLOY / "strfry.conf").read_text())
        self.assertFalse(preflight.validate(template, {}))

    def test_config_and_environment_mismatches(self):
        for key, value in {
            "relay.readControl.enabled": False, "relay.readControl.branchAddress": "wrong",
            "relay.readControl.snapshotPath": "/other.json", "relay.readControl.maxSnapshotBytes": 4000,
            "relay.auth.enabled": False, "relay.auth.serviceUrl": "wss://",
            "relay.auth.maxAgeSeconds": 60, "relay.negentropy.enabled": True,
            "relay.maxFilterLimitCount": 10, "relay.writePolicy.plugin": "",
            "relay.logging.dumpInReqs": True, "db": "/other/db",
        }.items():
            with self.subTest(key=key), self.assertRaises((ValueError, TypeError)):
                preflight.validate({**self.values, key: value}, self.env)
        for key, value in {
            "BUDABIT_READ_CONTROL": "off", "BUDABIT_DRY_RUN": "1",
            "BUDABIT_AUTO_HOST_URL": "wss://private.test", "BUDABIT_BRANCHES": "",
            "BUDABIT_DISABLE_LOADER": "1", "BUDABIT_READ_MAX_PUBKEYS": "0",
        }.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                preflight.validate(self.values, {**self.env, key: value})

    def test_snapshot_missing_stale_unavailable_and_prestart(self):
        for data in ["invalid", json.dumps({**self.data, "updated_at": 1}),
                     json.dumps({**self.data, "ready": False}), "x" * 2097153]:
            self.path.write_text(data)
            with self.assertRaises(ValueError):
                preflight.validate(self.values, self.env)
        self.path.unlink()
        self.assertTrue(preflight.validate(self.values, self.env, snapshot=False))
        with self.assertRaises(ValueError):
            preflight.validate(self.values, self.env)
        os.mkfifo(self.path)
        with self.assertRaises(ValueError):
            preflight.validate(self.values, self.env)
        self.path.unlink()
        self.path.symlink_to(DEPLOY / "strfry.conf")
        with self.assertRaises(ValueError):
            preflight.validate(self.values, self.env)

    def test_parser_fails_closed_instead_of_interpreting_other_syntax(self):
        self.assertEqual(preflight.parse_config('relay { auth { enabled = true } } # comment\n'), {"relay.auth.enabled": True})
        for text in ['include "private.conf"', 'relay { enabled = true enabled = false }',
                     'relay { enabled = true', 'db = "a" trailing', 'relay {} relay {}',
                     'relay.enabled = true', 'enabled = [true]', 'enabled = null']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                preflight.parse_config(text)

    def test_cli_success_and_failure_do_not_expose_private_values(self):
        args = [str(DEPLOY / "check-read-control.py"), "--config", str(DEPLOY / "strfry.conf"), "--config-only"]
        clean = {key: value for key, value in os.environ.items() if not key.startswith("BUDABIT_")}
        result = subprocess.run(args, env=clean, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        result = subprocess.run(args, env={**clean, **self.env}, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(OWNER, result.stdout + result.stderr)

    def test_live_advertisement_requires_exact_private_capability(self):
        claim = {"version": 1, "mode": "members", "scope": "relay"}
        info = {"limitation": {"auth_required": True}, "budabit": {"read_control": claim}}
        for value, succeeds in [(info, True), ({}, False), ([], False),
                                ({**info, "limitation": {}}, False),
                                ({**info, "budabit": {"read_control": {**claim, "version": True}}}, False),
                                ({**info, "budabit": {"read_control": {**claim, "scope": "event"}}}, False)]:
            with patch.object(preflight.urllib.request, "build_opener") as factory:
                factory.return_value.open.return_value = io.BytesIO(json.dumps(value).encode())
                if succeeds:
                    preflight.check_advertisement("http://127.0.0.1/", True)
                else:
                    with self.assertRaises(ValueError):
                        preflight.check_advertisement("http://127.0.0.1/", True)


if __name__ == "__main__":
    unittest.main()
