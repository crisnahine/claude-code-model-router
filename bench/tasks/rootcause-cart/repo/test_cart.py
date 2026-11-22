import unittest

from cart import add_item, cart_total


class CartTest(unittest.TestCase):
    def test_one_item(self):
        self.assertEqual(cart_total(add_item("pen", 2, 3)), 6)

    def test_a_new_cart_starts_empty(self):
        items = add_item("book", 10)
        self.assertEqual((len(items), cart_total(items)), (1, 10))


if __name__ == "__main__":
    unittest.main()
