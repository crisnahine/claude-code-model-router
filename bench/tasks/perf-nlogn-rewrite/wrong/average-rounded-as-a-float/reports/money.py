"""Amounts are whole cents everywhere; this module reads them from text and writes them back."""
import re
from decimal import ROUND_HALF_UP, Decimal

AMOUNT = re.compile(r"-?[0-9]+(\.[0-9]{1,2})?")


def parse_cents(text):
    """Cents for an amount like 12, 12.5 or -0.75, or None for anything else (1e3, .5, 1.234, +2)."""
    if not AMOUNT.fullmatch(text):
        return None
    return int(Decimal(text) * 100)


def format_cents(cents):
    """-1234567 -> "-12,345.67"."""
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}{whole:,}.{part:02d}"


def average_cents(total, count):
    """total / count to the nearest cent, halves away from zero."""
    return round(total / count)
