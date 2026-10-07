import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fixtures import ADDRESS

DEPLOY = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("read_preflight", DEPLOY / "check-read-control.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class ReadAdmissionPreflightTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.env = {"BUDABIT_BRANCHES": ADDRESS, "BUDABIT_READ_CONTROL": "members",
                    "STRFRY_POLICY_DB_FILE": str(Path(directory.name) / "data.mdb")}
        self.claim = {"version": 2, "mode": "members", "scope": "relay", "unfiltered_kinds": sorted(preflight.UNFILTERED)}
        self.values = {"db": directory.name, "relay.readPolicy.plugin": str(DEPLOY / "read-policy.py"),
                       "relay.writePolicy.plugin": str(DEPLOY / "write-policy.py"),
                       "relay.auth.enabled": True, "relay.auth.serviceUrl": "wss://private.test/path",
                       "relay.maxFilterLimitCount": 0, "relay.negentropy.enabled": False,
                       "relay.info.extra": json.dumps({"budabit": {"read_control": self.claim}})}

    def test_private_and_public_config_no_status_artifact(self):
        self.assertTrue(preflight.validate(self.values, self.env))
        template = preflight.parse_config((DEPLOY / "strfry.conf").read_text())
        self.assertFalse(preflight.validate(template, {}))

    def test_mismatches_obsolete_settings_and_unsafe_completeness(self):
        for key, value in {"relay.readControl.enabled": True, "relay.readPolicy.plugin": "",
                           "relay.writePolicy.plugin": "", "relay.auth.enabled": False,
                           "relay.auth.serviceUrl": "wss://", "relay.maxFilterLimitCount": 1,
                           "relay.negentropy.enabled": True, "relay.readPolicy.recheckSeconds": 0,
                           "relay.readPolicy.timeoutSeconds": True, "relay.info.extra": "{}",
                           "relay.auth.restrictedReadKinds": "1", "relay.logging.dumpInReqs": True,
                           "db": "/different"}.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                preflight.validate({**self.values, key: value}, self.env)
        with self.assertRaises(ValueError):
            preflight.validate(self.values, {**self.env, "BUDABIT_READ_CONTROL": "off"})

    def test_parser_still_rejects_ambiguous_syntax(self):
        self.assertEqual(preflight.parse_config('relay { auth { enabled = true } } # comment\n'), {"relay.auth.enabled": True})
        for text in ['include "private.conf"', 'relay { enabled = true enabled = false }',
                     'relay { enabled = true', 'db = "a" trailing', 'relay {} relay {}',
                     'relay.enabled = true', 'enabled = [true]', 'enabled = null']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                preflight.parse_config(text)

    def test_only_file_metadata_can_be_public_in_budabit_preset(self):
        values = {**self.values, "relay.readPolicy.publicKinds": "1063"}
        self.assertTrue(preflight.validate(values, self.env))
        for kinds in ("1,1063", "4444", "bad", "1_063", "1063junk", 1063):
            with self.subTest(kinds=kinds), self.assertRaises(ValueError):
                preflight.validate({**values, "relay.readPolicy.publicKinds": kinds}, self.env)
        with self.assertRaises(ValueError):
            preflight.validate({**values, "relay.auth.restrictedReadKinds": "1063"}, self.env)

    def test_advertisement_requires_generic_enforcement_and_specific_policy(self):
        core = {"version": 1, "admission": "req", "consistency": "eventual", "recheck_seconds": 5}
        info = {"limitation": {"auth_required": True}, "budabit": {"read_control": self.claim}, "read_policy": core}
        for value, succeeds in [(info, True), ({}, False), ([], False),
                                ({**info, "read_policy": {}}, False), ({**info, "limitation": {}}, False),
                                ({**info, "read_policy": {**core, "recheck_seconds": True}}, False),
                                ({**info, "read_policy": {**core, "public_kinds": [1063]}}, False),
                                ({**info, "budabit": {"read_control": {**self.claim, "version": 1}}}, False)]:
            with patch.object(preflight.urllib.request, "build_opener") as factory:
                factory.return_value.open.return_value = io.BytesIO(json.dumps(value).encode())
                if succeeds:
                    preflight.check_advertisement("http://127.0.0.1/", True)
                else:
                    with self.assertRaises(ValueError):
                        preflight.check_advertisement("http://127.0.0.1/", True)

    def test_public_kind_advertisement_must_match_configuration(self):
        core = {"version": 1, "admission": "req", "consistency": "eventual", "recheck_seconds": 5}
        for kinds in (None, [], [1063], [1, 1063], [True], "1063"):
            info = {"limitation": {"auth_required": True}, "budabit": {"read_control": self.claim},
                    "read_policy": {**core, "public_kinds": kinds}}
            with patch.object(preflight.urllib.request, "build_opener") as factory:
                factory.return_value.open.return_value = io.BytesIO(json.dumps(info).encode())
                if kinds == [1063]:
                    preflight.check_advertisement("http://127.0.0.1/", True, {1063})
                else:
                    with self.assertRaises(ValueError):
                        preflight.check_advertisement("http://127.0.0.1/", True, {1063})
