from pricing.money import format_price


class Invoice:
    def __init__(self, lines, currency="USD"):
        self.lines = lines  # (description, amount in minor units)
        self.currency = currency

    def total_line(self):
        return "Total: " + format_price(sum(amount for _, amount in self.lines), self.currency)
