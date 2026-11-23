import unittest

from cart import add_item, cart_total


class CartTest(unittest.TestCase):
    def test_one_item(self):
        self.assertEqual(cart_total(add_item("pen", 2, 3)), 6)


if __name__ == "__main__":
    unittest.main()
