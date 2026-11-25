"""Balances from v3 records."""


def balances(records):
    """{account: {currency: minor units}}, summed over every record."""
    out = {}
    for r in records:
        per = out.setdefault(r["account"], {})
        per[r["currency"]] = per.get(r["currency"], 0) + r["amount_minor"]
    return out


def balance_at(records, account, currency, until):
    """The account's balance in currency over the records posted at or before until (a UTC Z timestamp)."""
    return sum(r["amount_minor"] for r in records
               if r["account"] == account and r["currency"] == currency and r["posted_at"] <= until)


def daily(records, account, currency):
    """[(UTC day, that day's movement, closing balance), ...] for the days the account moved in currency."""
    moves = {}
    for r in records:
        if r["account"] == account and r["currency"] == currency:
            day = r["posted_at"][:10]
            moves[day] = moves.get(day, 0) + r["amount_minor"]
    out, running = [], 0
    for day in sorted(moves):
        running += moves[day]
        out.append((day, moves[day], running))
    return out
