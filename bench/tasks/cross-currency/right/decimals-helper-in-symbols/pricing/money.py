from pricing.symbols import SYMBOLS, decimals_for


def format_price(cents, currency="USD"):
    """An amount in minor units with its currency's symbol: "$12.34", "¥1234"."""
    symbol = SYMBOLS[currency]
    if decimals_for(currency) == 0:
        return f"{symbol}{cents}"
    return f"{symbol}{cents // 100}.{cents % 100:02d}"
