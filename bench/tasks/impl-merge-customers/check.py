"""Pass when store/migrations/m0002_merge_customers.py, run in a child on two generated databases, leaves exactly the
merged tables, view, indexes and pragmas the spec describes."""
import importlib.util
import json
import random
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

HERE = Path(__file__).resolve().parent
STAMPS = [f"2024-0{m}-{d:02d} 10:00:00" for m in (1, 2, 3) for d in (5, 20)]
NAMES = ["Ana", "Ben", "Cho", "Dee", "Eli", "Fay", "Gus", "Hal"]


def ascii_lower(text):
    return "".join(c.lower() if "A" <= c <= "Z" else c for c in text)


def scramble(rng, text):
    out = "".join(c.upper() if rng.random() < 0.5 else c for c in text)
    return " " * rng.randint(0, 2) + out + " " * rng.randint(0, 2)


def generate(seed):
    rng = random.Random(seed)
    ids = rng.sample(range(1, 400), 90)
    customers = []
    for n in range(22):
        base = f"user{n}.{rng.choice('abcdef')}@{rng.choice(['shop', 'mail', 'corp'])}.test"
        for _ in range(rng.randint(1, 4)):
            name = rng.choice([None, "", rng.choice(NAMES)])
            customers.append([ids.pop(), scramble(rng, base), name, rng.choice(STAMPS)])
    customers.append([ids.pop(), "Émile@shop.test", "Emile", rng.choice(STAMPS)])
    customers.append([ids.pop(), "émile@shop.test", None, rng.choice(STAMPS)])
    customers.append([ids.pop(), "zoë@shop.test", None, STAMPS[0]])
    customers.append([ids.pop(), "ZOë@SHOP.test", "Zoe", STAMPS[0]])
    customers.append([ids.pop(), "\ttab@shop.test", "Tab", rng.choice(STAMPS)])
    customers.append([ids.pop(), "tab@shop.test", "Tab too", rng.choice(STAMPS)])
    rng.shuffle(customers)
    drop_ambiguous_names(customers)
    orders, addresses = [], []
    for cid, *_ in customers:
        for _ in range(rng.randint(0, 3)):
            orders.append([len(orders) + 1, cid, rng.choice([0, rng.randint(1, 50000)])])
        for _ in range(rng.randint(0, 3)):
            addresses.append([len(addresses) + 1, cid, f"{rng.randint(1, 99)} Main St"])
    return {"customers": sorted(customers), "orders": orders, "addresses": addresses}


def key(email):
    return ascii_lower(email.strip(" "))


def groups(customers):
    out = {}
    for row in sorted(customers, key=lambda r: (r[3], r[0])):
        out.setdefault(key(row[1]), []).append(row)
    return out


def drop_ambiguous_names(customers):
    """The spec doesn't say which of two duplicates created at the same instant lends its name, so make it never matter."""
    for members in groups(customers).values():
        donors = [r for r in members[1:] if r[2]]
        for later in donors[1:]:
            if later[3] == donors[0][3]:
                later[2] = None


def expected(rows):
    keepers = {}
    for k, members in groups(rows["customers"]).items():
        keeper = list(members[0])
        keeper[1] = k
        if not keeper[2]:
            keeper[2] = next((r[2] for r in members[1:] if r[2]), keeper[2])
        keepers[k] = keeper
    owner = {r[0]: keepers[key(r[1])][0] for r in rows["customers"]}
    customers = sorted(keepers.values())
    orders = [[i, owner[c], cents] for i, c, cents in rows["orders"]]
    view = [[c[0], c[1], sum(o[1] == c[0] for o in orders), sum(o[2] for o in orders if o[1] == c[0])] for c in customers]
    return {"error": None, "in_transaction": False, "foreign_keys": 1, "fk_check": [], "integrity": "ok",
            "user_version": 2, "customers": customers, "orders": orders,
            "addresses": [[i, owner[c], line] for i, c, line in rows["addresses"]], "view": view,
            "email_index": ["u", "NOCASE"], "email_notnull": 1,
            "fks": {"orders": [["customers", "CASCADE"]], "addresses": [["customers", "CASCADE"]]},
            "duplicate_rejected": True}


def passes(work, schema, seed):
    rows = generate(seed)
    got = passcheck.probe(work, HERE / "probe.py", {"schema": schema, "rows": rows})
    if not isinstance(got, dict) or "orders_by_customer" not in (got.pop("indexes", None) or []):
        return False
    return json.dumps(got, sort_keys=True) == json.dumps(expected(rows), sort_keys=True)


work = Path(sys.argv[1])
schema = (HERE / "repo" / "store" / "schema.sql").read_text(encoding="utf-8")
sys.exit(0 if all(passes(work, schema, seed) for seed in (11, 12)) else 1)
