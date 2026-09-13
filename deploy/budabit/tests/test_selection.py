import unittest

from fixtures import OWNER, event, shard, shard_address, tombstone
from policy.budabit.selection import Coordinate, newer


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.address = shard_address(OWNER, "general")
        self.coord = Coordinate(self.address, OWNER)

    def test_newer_prefers_timestamp_then_lowest_id(self):
        a = {"created_at": 10, "id": "b"}
        b = {"created_at": 10, "id": "a"}
        self.assertTrue(newer(b, a))
        self.assertFalse(newer(a, b))
        self.assertTrue(newer({"created_at": 11, "id": "z"}, b))

    def test_offer_and_replace(self):
        first = shard(OWNER, "general", [], created_at=100)
        second = shard(OWNER, "general", ["a" * 64], created_at=200)
        self.assertTrue(self.coord.offer(first))
        self.assertTrue(self.coord.offer(second))
        self.assertFalse(self.coord.offer(first))
        self.assertIs(self.coord.current, second)

    def test_foreign_author_ignored(self):
        other = shard("b" * 64, "general", [], created_at=300)
        self.assertFalse(self.coord.offer(other))

    def test_tombstone_hides_older_and_equal(self):
        current = shard(OWNER, "general", [], created_at=100)
        self.coord.offer(current)
        self.assertTrue(self.coord.tombstone(tombstone(OWNER, self.address, created_at=100)))
        self.assertIsNone(self.coord.current)
        self.assertFalse(self.coord.offer(shard(OWNER, "general", [], created_at=100)))
        self.assertTrue(self.coord.offer(shard(OWNER, "general", [], created_at=101)))

    def test_tombstone_out_of_order(self):
        self.assertFalse(self.coord.tombstone(tombstone(OWNER, self.address, created_at=500)))
        self.assertFalse(self.coord.offer(shard(OWNER, "general", [], created_at=400)))
        self.assertTrue(self.coord.offer(shard(OWNER, "general", [], created_at=600)))

    def test_tombstone_by_other_author_ignored(self):
        self.coord.offer(shard(OWNER, "general", [], created_at=100))
        self.assertFalse(self.coord.tombstone(event(5, "c" * 64, [["a", self.address]], created_at=900)))
        self.assertIsNotNone(self.coord.current)


if __name__ == "__main__":
    unittest.main()
