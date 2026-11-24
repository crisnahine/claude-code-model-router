"""Reading and writing v3 ledger files (docs/FORMAT_V3.md)."""
import json

from . import kinds, money, timefmt
from .errors import FormatError

FIELDS = ("id", "account", "type", "amount_minor", "currency", "posted_at", "memo", "source_rev")


def validate(record, line=None):
    if not isinstance(record, dict) or set(record) != set(FIELDS):
        raise FormatError(f"a v3 record has exactly the fields {', '.join(FIELDS)}", line)
    for field in ("id", "account", "memo"):
        if not isinstance(record[field], str):
            raise FormatError(f"{field} must be a string", line)
    if not record["id"] or not record["account"]:
        raise FormatError("id and account can't be empty", line)
    if record["type"] not in kinds.TYPES:
        raise FormatError(f"unknown type {record['type']!r}", line)
    if type(record["amount_minor"]) is not int:
        raise FormatError("amount_minor must be a whole number", line)
    if record["amount_minor"] and (record["amount_minor"] > 0) != (kinds.SIGN[record["type"]] > 0):
        raise FormatError(f"a {record['type']} can't have amount_minor {record['amount_minor']}", line)
    if not money.is_currency(record["currency"]):
        raise FormatError(f"bad currency {record['currency']!r}", line)
    if not timefmt.is_utc_z(record["posted_at"]):
        raise FormatError(f"posted_at must be UTC, YYYY-MM-DDTHH:MM:SSZ, not {record['posted_at']!r}", line)
    if type(record["source_rev"]) is not int or record["source_rev"] < 1:
        raise FormatError("source_rev must be a whole number from 1", line)


def dump_line(record):
    """One record as its line in a v3 file: compact JSON, keys sorted, non-ASCII kept, newline-terminated."""
    validate(record)
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def order_key(record):
    """Records in a v3 file are sorted by account, then posted_at, then id."""
    return record["account"], record["posted_at"], record["id"]


def write(path, records):
    """Write records, which must already be in order_key order; return how many were written."""
    keys = [order_key(r) for r in records]
    if keys != sorted(keys):
        raise FormatError("v3 records must be sorted by account, posted_at, id")
    if len({r["id"] for r in records}) != len(records):
        raise FormatError("an id appears more than once")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(dump_line(record))
    return len(records)


def read(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for number, text in enumerate(f, 1):
            try:
                record = json.loads(text)
            except ValueError as e:
                raise FormatError(f"not JSON ({e})", number) from None
            validate(record, number)
            out.append(record)
    return out
