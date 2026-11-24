"""Entry types."""

TYPES = ("credit", "debit", "fee", "refund", "chargeback")

# Exports before March 2021 wrote a two-letter code in place of these names.
LEGACY = {"CR": "credit", "DR": "debit", "CB": "chargeback"}

# Money in is positive, money out is negative.
SIGN = {"credit": 1, "refund": 1, "debit": -1, "fee": -1, "chargeback": -1}


def describe(entry_type):
    return {"credit": "Credit", "debit": "Debit", "fee": "Fee", "refund": "Refund",
            "chargeback": "Chargeback"}[entry_type]
