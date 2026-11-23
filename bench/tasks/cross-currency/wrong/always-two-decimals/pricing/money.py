from pricing.symbols import SYMBOLS


def format_price(cents, currency="USD"):
    """An amount in minor units with its currency's symbol."""
    return f"{SYMBOLS[currency]}{cents // 100}.{cents % 100:02d}"
