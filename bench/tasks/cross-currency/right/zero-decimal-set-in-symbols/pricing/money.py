from pricing.symbols import SYMBOLS, ZERO_DECIMAL


def format_price(cents, currency="USD"):
    """An amount in minor units with its currency's symbol: "$12.34", "¥1234"."""
    symbol = SYMBOLS[currency]
    if currency in ZERO_DECIMAL:
        return f"{symbol}{cents}"
    return f"{symbol}{cents // 100}.{cents % 100:02d}"
