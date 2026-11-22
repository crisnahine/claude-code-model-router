import unittest

from pages import page_slice


class PageSlice(unittest.TestCase):
    def test_first_page(self):
        self.assertEqual(page_slice(list(range(10)), 1, 3), [0, 1, 2])

    def test_last_page_is_short(self):
        self.assertEqual(page_slice(list(range(10)), 4, 3), [9])


if __name__ == "__main__":
    unittest.main()
