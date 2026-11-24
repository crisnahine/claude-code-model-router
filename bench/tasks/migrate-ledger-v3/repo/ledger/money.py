"""Amounts as whole minor units of their currency: cents for USD, yen for JPY, fils for KWD."""
import re
from decimal import Decimal

EXPONENT = {"JPY": 0, "KRW": 0, "CLP": 0, "KWD": 3, "BHD": 3, "OMR": 3}
DEFAULT_EXPONENT = 2
CODE_LENGTH = 3
PLAIN = re.compile(r"-?[0-9]+(\.[0-9]+)?")


def exponent(currency):
    """How many decimal places the currency has."""
    return EXPONENT.get(currency, DEFAULT_EXPONENT)


def is_currency(code):
    return isinstance(code, str) and len(code) == CODE_LENGTH and code.isascii() and code.isupper()


def to_minor(amount, currency):
    """A decimal string as whole minor units of currency. ValueError when it isn't a plain decimal or has more
    decimal places than the currency allows."""
    if not isinstance(amount, str) or not PLAIN.fullmatch(amount):
        raise ValueError(f"not a plain decimal: {amount!r}")
    minor = Decimal(amount).scaleb(exponent(currency))
    if minor != minor.to_integral_value():
        raise ValueError(f"{amount} has more decimal places than {currency} allows")
    return int(minor)


def format_minor(minor, currency):
    """-123456 USD -> "-1,234.56 USD"."""
    places = exponent(currency)
    sign = "-" if minor < 0 else ""
    whole, part = divmod(abs(minor), 10 ** places)
    text = f"{whole:,}" + (f".{part:0{places}d}" if places else "")
    return f"{sign}{text} {currency}"
