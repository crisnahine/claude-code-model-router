"""Pass when billing.statement.balances, run in a child on three generated databases, returns the exact balances with
at most 3 SQL statements, leaves the schema alone, and its SQLite VM steps and Python line events grow at most 20 times
from 150 to 1,500 accounts; then, untraced on 15,000 accounts, returns them in at most 1 s of CPU time. Line events
don't see a loop that runs in C, such as `x in list`, so only that timed call bounds one."""
import importlib.util
import json
import random
import sqlite3
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

HERE = Path(__file__).resolve().parent
SEEDS = ((21, 150), (22, 150), (23, 1500))
GROWTH = 20
TIMED = (24, 15_000)
CPU = 1.0  # the reference takes about 0.03 s of CPU here, and `x in list` over the invoices about 10 s


def generate(seed, size):
    rng = random.Random(seed)
    ids = rng.sample(range(1, size * 4), size)
    accounts = [[i, f"acct {i}"] for i in ids]
    invoices, payments = [], []
    for account_id in ids:
        for _ in range(rng.randint(0, 5)):
            invoices.append([len(invoices) + 1, account_id, rng.randint(1, 90000), int(rng.random() < 0.25)])
            for _ in range(rng.randint(0, 4)):
                status = rng.choice(["settled", "settled", "pending", "failed"])
                cents = rng.randint(1, 30000)
                if status == "settled" and rng.random() < 0.2:
                    cents = -cents
                payments.append([len(payments) + 1, invoices[-1][0], cents, status, rng.choice(["card", "bank"])])
    return {"accounts": accounts, "invoices": invoices, "payments": payments}


def expected(data):
    live = {i[0]: i[1] for i in data["invoices"] if not i[3]}
    balance = {a[0]: 0 for a in data["accounts"]}
    for i in data["invoices"]:
        if not i[3]:
            balance[i[1]] += i[2]
    for _, invoice_id, cents, status, method in data["payments"]:
        if invoice_id in live and (status == "settled" or (status == "pending" and method == "card")):
            balance[live[invoice_id]] -= cents
    return sorted(balance.items())


def reference_schema(schema):
    conn = sqlite3.connect(":memory:")
    conn.executescript(schema)
    return [r[0] for r in conn.execute("SELECT sql FROM sqlite_master ORDER BY name")]


def count(value):
    return type(value) is int and value >= 0


def passes(work):
    schema = (HERE / "repo" / "billing" / "schema.sql").read_text(encoding="utf-8")
    datasets = [generate(seed, size) for seed, size in SEEDS]
    got = passcheck.probe(work, HERE / "probe.py", {"schema": schema, "datasets": datasets})
    if not isinstance(got, list) or len(got) != len(datasets) or not all(isinstance(g, dict) for g in got):
        return False
    schema_after = reference_schema(schema)
    for data, g in zip(datasets, got):
        if (g.get("error") is not None or json.dumps(g.get("pairs")) != json.dumps([list(p) for p in expected(data)])
                or not all(count(g.get(k)) for k in ("statements", "vm", "lines", "temp"))
                or not 1 <= g["statements"] <= 3 or g.get("sentinel") is not True
                or g["vm"] < 1 or g["lines"] < 1 or g["temp"] != 0
                or json.dumps(g.get("schema")) != json.dumps(schema_after)):
            return False
    small, large = got[0], got[2]
    if not all(large[k] <= GROWTH * max(1, small[k]) for k in ("vm", "lines")):
        return False
    data = generate(*TIMED)
    timed = passcheck.probe(work, HERE / "probe.py", {"schema": schema, "datasets": [data], "timed": True})
    return (isinstance(timed, list) and len(timed) == 1 and isinstance(timed[0], dict) and timed[0].get("error") is None
            and json.dumps(timed[0].get("pairs")) == json.dumps([list(p) for p in expected(data)])
            and type(timed[0].get("cpu")) is float and timed[0]["cpu"] <= CPU)


sys.exit(0 if passes(Path(sys.argv[1])) else 1)
