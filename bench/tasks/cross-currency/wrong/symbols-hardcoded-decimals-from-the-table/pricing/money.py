from pricing.symbols import DECIMALS

_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}


def format_price(cents, currency="USD"):
    """An amount in minor units with its currency's symbol: "$12.34", "¥1234"."""
    symbol = _SYMBOLS[currency]
    if DECIMALS.get(currency, 2) == 0:
        return f"{symbol}{cents}"
    return f"{symbol}{cents // 100}.{cents % 100:02d}"
