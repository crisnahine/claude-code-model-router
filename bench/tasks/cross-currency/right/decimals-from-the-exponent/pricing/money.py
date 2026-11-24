from pricing.symbols import DECIMALS, SYMBOLS


def format_price(cents, currency="USD"):
    """An amount in minor units with its currency's symbol: "$12.34", "¥1234"."""
    places = DECIMALS.get(currency, 2)
    return f"{SYMBOLS[currency]}{cents / 10 ** places:.{places}f}"
