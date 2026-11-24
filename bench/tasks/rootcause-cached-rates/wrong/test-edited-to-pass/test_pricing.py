import unittest

from pricing import price_list, sale_prices


class PricingTest(unittest.TestCase):
    def test_a_spring_sale(self):
        self.assertEqual(sale_prices("eu", 10), {"pen": 90, "book": 1350, "lamp": 4499})

    def test_d_us_prices(self):
        self.assertEqual(price_list("us"), {"pen": 120, "book": 1700, "lamp": 5499})


if __name__ == "__main__":
    unittest.main()
