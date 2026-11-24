import unittest

from shop.money import format_cents


class FormatCents(unittest.TestCase):
    def test_whole_and_part_dollars(self):
        self.assertEqual(format_cents(1250), "12.50 USD")
        self.assertEqual(format_cents(5, "EUR"), "0.05 EUR")

    def test_negative(self):
        self.assertEqual(format_cents(-199), "-1.99 USD")
