from pricing.symbols import DECIMALS


def format_price(cents, currency="USD"):
    """An amount in minor units with a dollar sign."""
    if DECIMALS.get(currency, 2) == 0:
        return f"${cents}"
    return f"${cents // 100}.{cents % 100:02d}"
