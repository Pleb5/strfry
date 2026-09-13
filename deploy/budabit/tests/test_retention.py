import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "retention.py"
SPEC = importlib.util.spec_from_file_location("retention", MODULE_PATH)
retention = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retention)


class RetentionTests(unittest.TestCase):
    def test_preserves_replaceable_kinds(self):
        for kind in (0, 3, 41, 10000, 19999, 30000, 39999, 34550):
            with self.subTest(kind=kind):
                self.assertTrue(retention.is_replaceable_kind(kind))

    def test_prunes_regular_and_ephemeral_ranges(self):
        for kind in (1, 4, 5, 7, 9999, 20000, 29999, 40000):
            with self.subTest(kind=kind):
                self.assertFalse(retention.is_replaceable_kind(kind))


if __name__ == "__main__":
    unittest.main()
