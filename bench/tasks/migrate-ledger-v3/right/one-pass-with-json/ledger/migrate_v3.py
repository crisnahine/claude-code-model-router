"""Migrate a v2 export to the v3 format.

migrate(src, accounts, dst) reads the v2 export at src (docs/FORMAT_V2.md) and the accounts file at accounts
(data/accounts.json shows the shape), writes the v3 file at dst (docs/FORMAT_V3.md, "Migrating from v2") and
returns the number of records written.
"""
import datetime
import json
from decimal import Decimal

from .errors import MigrationError

TYPES = {"credit": 1, "refund": 1, "debit": -1, "fee": -1, "chargeback": -1}
CODES = {"CR": "credit", "DR": "debit", "CB": "chargeback"}
PLACES = {"JPY": 0, "KRW": 0, "CLP": 0, "KWD": 3, "BHD": 3, "OMR": 3}


def _zone(text):
    sign = -1 if text[0] == "-" else 1
    return datetime.timezone(sign * datetime.timedelta(hours=int(text[1:3]), minutes=int(text[4:6])))


def migrate(src, accounts, dst):
    with open(accounts, encoding="utf-8") as f:
        held = json.load(f)
    latest = {}
    with open(src, encoding="utf-8") as f:
        for number, text in enumerate(f, 1):
            if text.strip():
                r = json.loads(text)
                if r["id"] not in latest or r["rev"] >= latest[r["id"]][1]["rev"]:
                    latest[r["id"]] = (number, r)
    out = []
    for number, r in latest.values():
        account = held.get(r["account"])
        if account is None:
            raise MigrationError(f"no account {r['account']}", number)
        kind = CODES.get(r["kind"], r["kind"])
        if kind not in TYPES:
            raise MigrationError(f"unknown kind {r['kind']}", number)
        currency = r["currency"] or account["currency"]
        minor = Decimal(r["amount"]).scaleb(PLACES.get(currency, 2))
        if minor != minor.to_integral_value():
            raise MigrationError(f"too many decimal places in {r['amount']}", number)
        ts = r["ts"].replace("Z", "+00:00")
        when = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        when = when.replace(tzinfo=_zone(ts[19:] if len(ts) > 19 else account["utc_offset"]))
        out.append({"id": r["id"], "account": r["account"], "type": kind, "amount_minor": TYPES[kind] * int(minor),
                    "currency": currency, "memo": r["memo"], "source_rev": r["rev"],
                    "posted_at": when.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    out.sort(key=lambda r: (r["account"], r["posted_at"], r["id"]))
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        for r in out:
            f.write(json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
    return len(out)
