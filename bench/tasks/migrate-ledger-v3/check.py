"""Pass when test_migrate.py, unchanged, passes and ledger.migrate_v3.migrate, run in a child on a generated export
that holds every v2 quirk many times over (revisions out of file order, repeated revisions, edits that move an
entry, legacy kind codes, timestamps without an offset or at a negative half-hour one, null and foreign currencies,
every currency money.py gives other than two decimals, amounts written without their trailing zeros), writes
exactly the v3 file the formats describe and returns its record count, migrates an export of 80,000 lines in a
few seconds, keeps amounts too big for a float exact, and refuses an amount with more places than its currency."""
import datetime
import importlib.util
import json
import random
import sys
from decimal import Decimal
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

HERE = Path(__file__).resolve().parent
ACCOUNTS = {"acc-ny": ("USD", "-05:00"), "acc-ber": ("EUR", "+01:00"), "acc-tok": ("JPY", "+09:00"),
            "acc-kwi": ("KWD", "+03:00"), "acc-del": ("INR", "+05:30"), "acc-sp": ("BRL", "-03:00"),
            "acc-lon": ("GBP", "+00:00"), "acc-seo": ("KRW", "+09:00"), "acc-stj": ("CAD", "-03:30")}
# Every currency ledger/money.py lists, so a table copied from FORMAT_V3's two examples is off by 100 or 1000.
PLACES = {"JPY": 0, "KRW": 0, "CLP": 0, "KWD": 3, "BHD": 3, "OMR": 3}
FOREIGN = sorted({c for c, _ in ACCOUNTS.values()} | set(PLACES))
BIG = 60_000
BIG_SECONDS = 15
LEGACY = {"credit": "CR", "debit": "DR", "chargeback": "CB"}
SIGN = {"credit": 1, "refund": 1, "debit": -1, "fee": -1, "chargeback": -1}
FIELDS = ("source_rev", "type", "amount_minor", "currency", "posted_at", "memo", "account")
MEMOS = ["", "rent", "Gebühr", "café supplies", "東京 office", "refund #12", "wire", "fees: card"]


def offset(text):
    sign, hours, minutes = text[0], int(text[1:3]), int(text[4:6])
    delta = datetime.timedelta(hours=hours, minutes=minutes)
    return datetime.timezone(-delta if sign == "-" else delta)


def amount_text(rng, currency):
    places = PLACES.get(currency, 2)
    minor = rng.choice([1, 5, 99, 100, 1250, 7000, rng.randint(1, 500000)])
    whole, part = divmod(minor, 10 ** places)
    text = f"{whole}.{part:0{places}d}" if places else str(whole)
    # Some exporters dropped trailing zeros: "12.5" USD is 1250 cents, "0.5" KWD is 500 fils.
    return text.rstrip("0").rstrip(".") if places and rng.random() < 0.3 else text


def entry(rng, n, account):
    currency, utc = ACCOUNTS[account]
    kind = rng.choice(["credit", "debit", "fee", "refund", "chargeback"])
    roll = rng.random()
    own = currency if roll > 0.12 else rng.choice([c for c in FOREIGN if c != currency])
    local = datetime.datetime(2020, 12, 31, 18) + datetime.timedelta(minutes=rng.randint(0, 60 * 24 * 20))
    if rng.random() < 0.25:
        stamp = local.strftime("%Y-%m-%dT%H:%M:%S")
    else:
        zone = rng.choice([utc, utc, "+00:00", "+02:00", "-08:00", "-09:30", "Z"])
        stamp = local.strftime("%Y-%m-%dT%H:%M:%S") + zone
    return {"id": f"t-{n}", "rev": 1, "account": account,
            "kind": LEGACY[kind] if kind in LEGACY and rng.random() < 0.3 else kind,
            "amount": amount_text(rng, own), "currency": None if own == currency and rng.random() < 0.25 else own,
            "ts": stamp, "memo": rng.choice(MEMOS), "exported_at": "2023-01-05T01:00:00Z"}


def edited(rng, record, rev):
    """A later revision. Some move the entry to another time, kind or account, so the revisions of one id don't
    sort next to each other."""
    new = dict(record, rev=rev, memo=f"{record['memo']} (edit {rev})".strip())
    if rng.random() < 0.4:
        moved = datetime.datetime.strptime(new["ts"][:19], "%Y-%m-%dT%H:%M:%S")
        moved += datetime.timedelta(hours=rng.choice([-50, -7, 3, 26, 75]))
        new["ts"] = moved.strftime("%Y-%m-%dT%H:%M:%S") + new["ts"][19:]
    if rng.random() < 0.2:
        new["kind"] = rng.choice(["credit", "debit", "fee", "refund", "chargeback"])
    if rng.random() < 0.15:
        new["currency"] = new["currency"] or ACCOUNTS[new["account"]][0]
        new["account"] = rng.choice(sorted(ACCOUNTS))
    if rng.random() < 0.5:
        currency = new["currency"] or ACCOUNTS[new["account"]][0]
        new["amount"] = amount_text(rng, currency)
    return new


def generate(seed, count=240):
    """v2 lines in file order: each entry once, or as one of the ways retries and edits repeat it."""
    rng = random.Random(seed)
    groups = []
    for n in range(1, count + 1):
        first = entry(rng, n, rng.choice(sorted(ACCOUNTS)))
        shape = rng.random()
        if shape < 0.06:
            group = [first, dict(first)]  # a plain retry
        elif shape < 0.12:
            group = [first, edited(rng, first, 2)]  # edited between attempts
        elif shape < 0.20:
            # An older batch retried after a newer one, so it carries the later exported_at.
            group = [edited(rng, first, 2), dict(first, exported_at="2023-01-06T03:00:00Z")]
        elif shape < 0.26:
            late = dict(first, memo=first["memo"] + " (late edit)")
            # Same rev: the later line wins, even when it drops memo text or carries an earlier exported_at.
            group = [first, late] if n % 2 else [late, dict(first, exported_at="2023-01-04T22:00:00Z")]
        elif shape < 0.30:
            group = [first, edited(rng, first, 3), edited(rng, first, 2)]
        else:
            group = [first]
        groups.append(group)
    # Same account and posted_at as entry 1, with ids that sort one way as text and the other way as numbers.
    for n in (2000, 300) if count < 300 else ():
        groups.append([dict(groups[0][0], id=f"t-{n}", rev=1, memo="same time")])
    lines = [record for group in groups for record in group]
    rng.shuffle(lines)
    # The shuffle spreads a group's lines over the file; each group's own order is what it tests, so keep it.
    queue = {group[0]["id"]: list(group) for group in groups}
    return [queue[record["id"]].pop(0) for record in lines]


def reference(lines):
    current = {}
    for record in lines:
        held = current.get(record["id"])
        if held is None or record["rev"] >= held["rev"]:
            current[record["id"]] = record
    out = []
    for r in current.values():
        currency, utc = ACCOUNTS[r["account"]]
        kind = {v: k for k, v in LEGACY.items()}.get(r["kind"], r["kind"])
        own = r["currency"] or currency
        minor = int(Decimal(r["amount"]).scaleb(PLACES.get(own, 2)))
        stamp = r["ts"]
        if stamp.endswith("Z"):
            stamp = stamp[:-1] + "+00:00"
        when = datetime.datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")
        when = when.replace(tzinfo=offset(stamp[19:]) if len(stamp) > 19 else offset(utc))
        posted = when.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append({"id": r["id"], "account": r["account"], "type": kind, "amount_minor": SIGN[kind] * minor,
                    "currency": own, "posted_at": posted, "memo": r["memo"], "source_rev": r["rev"]})
    out.sort(key=lambda r: (r["account"], r["posted_at"], r["id"]))
    return out


def dump(record):
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def wrong(got, want):
    """Why the probe's result isn't the expected file, or None."""
    if got is None or got.get("error"):
        return f"migrate raised {(got or {}).get('error')}"
    text = got.get("text")
    expected = "".join(dump(r) for r in want)
    if text is None:
        return "migrate wrote no file"
    if text != expected:
        records = []
        for n, line in enumerate(text.splitlines(), 1):
            try:
                records.append(json.loads(line))
            except ValueError:
                return f"line {n} of the v3 file isn't JSON: {line[:80]!r}"
        if not all(isinstance(r, dict) and "id" in r for r in records):
            return "a line of the v3 file isn't a record with an id"
        by_id = {}
        for r in records:
            if r["id"] in by_id:
                return f"{r['id']} appears more than once"
            by_id[r["id"]] = r
        for r in want:
            mine = by_id.get(r["id"])
            if mine is None:
                return f"{r['id']} is missing"
            for field in FIELDS:
                if mine.get(field) != r[field]:
                    return f"{r['id']}: {field} is {mine.get(field)!r}, expected {r[field]!r}"
            if set(mine) != set(r):
                return f"{r['id']} has the fields {sorted(mine)}"
        extra = sorted(set(by_id) - {r["id"] for r in want})
        if extra:
            return f"{extra[0]} shouldn't be there"
        for n, (mine, r) in enumerate(zip(records, want), 1):
            if mine["id"] != r["id"]:
                return f"the records are in the wrong order: line {n} is {mine['id']}, expected {r['id']}"
        for n, (line, w) in enumerate(zip(text.splitlines(True), expected.splitlines(True)), 1):
            if line != w:
                return f"line {n} is written {line!r}, expected {w!r}"[:300]
        return "the file's last line or its ending differs"
    if got.get("count") != len(want):
        return f"migrate returned {got.get('count')!r}, expected {len(want)}"
    return None


work = Path(sys.argv[1])
if not passcheck.tests_pass(work, HERE / "repo", "test_migrate.py"):
    passcheck.finish(False, "test_migrate.py doesn't pass unchanged")
accounts = json.dumps({a: {"name": a.upper(), "currency": c, "utc_offset": o} for a, (c, o) in ACCOUNTS.items()})


def migrated(lines, timeout=45):
    return passcheck.probe(work, HERE / "probe.py", {"v2": "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines),
                                                    "accounts": accounts}, timeout=timeout)


lines = generate(23)
missing = set(PLACES) - {r["currency"] or ACCOUNTS[r["account"]][0] for r in lines}
if missing:
    raise RuntimeError(f"the generated export has no entry in {sorted(missing)}")
reason = wrong(migrated(lines), reference(lines))
if reason:
    passcheck.finish(False, reason[:300])
lines = generate(29, BIG)
got = migrated(lines, BIG_SECONDS)
if got is None:
    passcheck.finish(False, f"too slow: {len(lines):,} lines took more than {BIG_SECONDS} seconds")
reason = wrong(got, reference(lines))
if reason:
    passcheck.finish(False, f"on {len(lines):,} lines, {reason}"[:300])


def one(n, account, amount, currency=None):
    return {"id": f"x-{n}", "rev": 1, "account": account, "kind": "credit", "amount": amount, "currency": currency,
            "ts": "2021-01-02T03:04:05Z", "memo": "", "exported_at": "2023-01-05T01:00:00Z"}


# Past 2**53 minor units a float can't hold every whole number.
lines = [one(1, "acc-ny", "123456789012345.67"), one(2, "acc-seo", "12345678901234567"),
         one(3, "acc-kwi", "9007199254740.993")]
reason = wrong(migrated(lines), reference(lines))
if reason:
    passcheck.finish(False, f"on amounts too big for a float, {reason}"[:300])
got = migrated([one(1, "acc-ny", "5.00"), one(2, "acc-ny", "1.000000001")])
if not (got and str(got.get("error")).startswith("MigrationError")):
    passcheck.finish(False, f"1.000000001 USD has more places than USD allows, but migrate gave {got!r}"[:300])
passcheck.finish(True, "")
