import unittest

from ranking import top


class RankingTest(unittest.TestCase):
    def test_top_three(self):
        players = [{"name": "ana", "score": 9}, {"name": "ben", "score": 10},
                   {"name": "cy", "score": 72}, {"name": "di", "score": 8}]
        self.assertEqual(len(top(players)), 3)


if __name__ == "__main__":
    unittest.main()
