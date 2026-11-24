"""An account statement from v3 records: one line per entry, oldest first, with the running balance."""
from . import kinds
from .money import format_minor


def lines(records, account, currency):
    mine = [r for r in records if r["account"] == account and r["currency"] == currency]
    out = [f"Statement for {account} in {currency}", ""]
    if not mine:
        return out + ["No entries."]
    running = 0
    rows = []
    for r in mine:
        running += r["amount_minor"]
        rows.append([r["posted_at"].replace("T", " ").rstrip("Z"), kinds.describe(r["type"]),
                     format_minor(r["amount_minor"], currency), format_minor(running, currency), r["memo"]])
    widths = [max(len(row[n]) for row in rows) for n in range(4)]
    for row in rows:
        out.append("  ".join([row[0].ljust(widths[0]), row[1].ljust(widths[1]), row[2].rjust(widths[2]),
                              row[3].rjust(widths[3]), row[4]]).rstrip())
    out += ["", f"Closing balance: {format_minor(running, currency)}"]
    return out
