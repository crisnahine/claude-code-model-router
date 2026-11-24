import unittest

from shop import api
from shop.app import build_app
from shop.errors import OutOfStock, PriceMismatch


class Checkout(unittest.TestCase):
    def setUp(self):
        self.app = build_app()
        for sku in ("LAMP-9", "MUG-1", "PAN-3"):
            self.app.storefront.page(sku)  # live traffic keeps these pages warm

    def test_a_plain_order_takes_the_stock(self):
        quote = self.app.orders.quote([("MUG-1", 2)])
        order = self.app.orders.place_order(quote)
        self.assertEqual(order.total, 2400)
        self.assertEqual(self.app.inventory.current_stock("MUG-1"), 83)
        self.assertEqual(self.app.storefront.page("MUG-1")["stock"], 83)

    def test_an_order_for_more_than_the_stock_is_refused(self):
        quote = self.app.orders.quote([("PAN-3", 10)])
        with self.assertRaises(OutOfStock):
            self.app.orders.place_order(quote)
        self.assertEqual(self.app.inventory.current_stock("PAN-3"), 9)

    def test_a_quote_made_before_a_price_change_is_refused(self):
        quote = self.app.orders.quote([("MUG-1", 1)])
        self.app.admin.save_product("MUG-1", price=1100)
        with self.assertRaises(PriceMismatch):
            self.app.orders.place_order(quote)

    def test_a_price_only_edit_checks_out_at_the_new_price(self):
        self.app.admin.save_product("PAN-3", price=3900)
        order = self.app.orders.place_order(self.app.orders.quote([("PAN-3", 1)]))
        self.assertEqual(order.total, 3900)

    def test_checkout_after_a_price_and_name_edit(self):
        self.app.admin.save_product("LAMP-9", name="Desk Lamp, brass", price=1299)
        status, body = api.handle_checkout(self.app, {"items": [["LAMP-9", 1]]})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["total"], 1299)


if __name__ == "__main__":
    unittest.main()
