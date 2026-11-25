"""Migrate a v2 export to the v3 format.

migrate(src, accounts, dst) reads the v2 export at src (docs/FORMAT_V2.md) and the accounts file at accounts
(data/accounts.json shows the shape), writes the v3 file at dst (docs/FORMAT_V3.md, "Migrating from v2") and
returns the number of records written.
"""
import os
from decimal import Decimal, InvalidOperation

# FORMAT_V3: two places for most currencies, none for JPY, three for KWD.
PLACES = {"JPY": 0, "KWD": 3}


def to_minor(amount, currency):
    try:
        minor = Decimal(amount) * 10 ** PLACES.get(currency, 2)
    except InvalidOperation:
        raise ValueError(f"not a decimal: {amount!r}") from None
    if minor != minor.to_integral_value():
        raise ValueError(f"{amount} has too many places for {currency}")
    return int(minor)

from . import accounts as accounts_file
from . import kinds, money, timefmt, v2, v3
from .errors import FormatError, MigrationError


def _record(number, line, held):
    kind = kinds.LEGACY.get(line["kind"], line["kind"])
    if kind not in kinds.TYPES:
        raise MigrationError(f"unknown kind {line['kind']!r}", number)
    account = held.get(line["account"])
    if account is None:
        raise MigrationError(f"unknown account {line['account']!r}", number)
    currency = line["currency"] if line["currency"] is not None else account.currency
    if not money.is_currency(currency):
        raise MigrationError(f"bad currency {currency!r}", number)
    try:
        minor = to_minor(line["amount"], currency)
        stamp = timefmt.parse_ts(line["ts"])
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=account.tz)
        posted = timefmt.to_utc_z(stamp)
    except ValueError as e:
        raise MigrationError(str(e), number) from None
    record = {"id": line["id"], "account": line["account"], "type": kind, "amount_minor": kinds.SIGN[kind] * minor,
              "currency": currency, "posted_at": posted, "memo": line["memo"], "source_rev": line["rev"]}
    try:
        v3.validate(record)
    except FormatError as e:
        raise MigrationError(str(e), number) from None
    return record


def migrate(src, accounts, dst):
    held = accounts_file.load(accounts)
    current = {}
    for number, line in v2.read(src):
        record = _record(number, line, held)
        kept = current.get(record["id"])
        # A later line with the same rev is the newer one.
        if kept is None or record["source_rev"] >= kept["source_rev"]:
            current[record["id"]] = record
    records = sorted(current.values(), key=v3.order_key)
    tmp = f"{dst}.tmp"
    try:
        count = v3.write(tmp, records)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return count
