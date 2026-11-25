"""Migrate a v2 export to the v3 format.

migrate(src, accounts, dst) reads the v2 export at src (docs/FORMAT_V2.md) and the accounts file at accounts
(data/accounts.json shows the shape), writes the v3 file at dst (docs/FORMAT_V3.md, "Migrating from v2") and
returns the number of records written.
"""
from . import accounts as accounts_file
from . import kinds, money, timefmt, v2, v3
from .errors import MigrationError


def current_lines(lines):
    """The current line for each id: the highest rev, and between equal revs the later line."""
    current = {}
    for number, record in lines:
        held = current.get(record["id"])
        if held is None or record["rev"] >= held[1]["rev"]:
            current[record["id"]] = (number, record)
    return list(current.values())


def convert(number, record, accounts):
    account = accounts.get(record["account"])
    if account is None:
        raise MigrationError(f"account {record['account']!r} isn't in the accounts file", number)
    entry_type = record["kind"] if record["kind"] in kinds.TYPES else kinds.LEGACY.get(record["kind"])
    if entry_type is None:
        raise MigrationError(f"unknown kind {record['kind']!r}", number)
    currency = account.currency if record["currency"] is None else record["currency"]
    if not money.is_currency(currency):
        raise MigrationError(f"bad currency {currency!r}", number)
    try:
        minor = money.to_minor(record["amount"], currency)
        stamp = timefmt.parse_ts(record["ts"])
    except ValueError as e:
        raise MigrationError(str(e), number) from None
    if minor < 0:
        raise MigrationError(f"amount {record['amount']} is negative", number)
    if stamp.tzinfo is None:
        if stamp.year != 2020:
            raise MigrationError(f"{record['ts']} has no offset and isn't from the 2020 backlog", number)
        stamp = stamp.replace(tzinfo=account.tz)
    return {"id": record["id"], "account": record["account"], "type": entry_type,
            "amount_minor": kinds.SIGN[entry_type] * minor, "currency": currency,
            "posted_at": timefmt.to_utc_z(stamp), "memo": record["memo"], "source_rev": record["rev"]}


def migrate(src, accounts, dst):
    held = accounts_file.load(accounts)
    records = [convert(number, record, held) for number, record in current_lines(v2.read(src))]
    records.sort(key=v3.order_key)
    return v3.write(dst, records)
