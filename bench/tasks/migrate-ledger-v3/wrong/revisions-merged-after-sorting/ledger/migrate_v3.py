"""Migrate a v2 export to the v3 format."""
from . import accounts as accounts_file
from . import kinds, money, timefmt, v2, v3
from .errors import MigrationError


def convert(number, record, accounts):
    account = accounts.get(record["account"])
    if account is None:
        raise MigrationError(f"account {record['account']!r} isn't in the accounts file", number)
    entry_type = record["kind"] if record["kind"] in kinds.TYPES else kinds.LEGACY.get(record["kind"])
    if entry_type is None:
        raise MigrationError(f"unknown kind {record['kind']!r}", number)
    currency = account.currency if record["currency"] is None else record["currency"]
    try:
        minor = money.to_minor(record["amount"], currency)
        stamp = timefmt.parse_ts(record["ts"])
    except ValueError as e:
        raise MigrationError(str(e), number) from None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=account.tz)
    return {"id": record["id"], "account": record["account"], "type": entry_type,
            "amount_minor": kinds.SIGN[entry_type] * minor, "currency": currency,
            "posted_at": timefmt.to_utc_z(stamp), "memo": record["memo"], "source_rev": record["rev"]}


def migrate(src, accounts, dst):
    held = accounts_file.load(accounts)
    rows = [(convert(n, r, held), n) for n, r in v2.read(src)]
    # Retries and edits of one entry sort next to each other; the last of each run is the current one.
    rows.sort(key=lambda x: (v3.order_key(x[0]), x[0]["source_rev"], x[1]))
    records = []
    for record, _ in rows:
        if records and records[-1]["id"] == record["id"]:
            records[-1] = record
        else:
            records.append(record)
    return v3.write(dst, records)
