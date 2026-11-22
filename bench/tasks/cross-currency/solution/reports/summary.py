from pricing.money import format_price


def summary_line(name, amount, currency="USD"):
    return f"{name}: {format_price(amount, currency)}"
