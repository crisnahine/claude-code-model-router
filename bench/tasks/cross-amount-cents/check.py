"""Pass when the six shop modules and reports/daily.py, run in a child on a generated mixed log of old and new events,
give exactly the cents results the spec describes, leave the log as it was, and no module but shop/events.py still names the
old amount key."""
import ast
import importlib.util
import random
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

HERE = Path(__file__).resolve().parent
ORDERS = [f"o{n}" for n in range(1, 7)]
# Cents with the old event's dollar amount. int(dollars * 100) loses a cent on the first, second, third and fourth.
TRAPS = [(1999, 19.99), (29, 0.29), (435, 4.35), (57, 0.57), (110, 1.1), (500, 5), (10000, 100.0)]


def dollars(rng, cents):
    return cents // 100 if cents % 100 == 0 and rng.random() < 0.5 else cents / 100


def generate(seed):
    rng = random.Random(seed)
    rows = []  # kind, order, cents, old dollar amount or None for a new event
    for i, (cents, amount) in enumerate(TRAPS):
        rows.append(("charge", ORDERS[i % 3], cents, amount))
        rows.append(("refund", ORDERS[3 + i % 2], cents, amount))
    rows += [("charge", "o1", 1999, None), ("charge", "o2", 29, None), ("charge", "o3", 0, None),
             ("refund", "o4", 0, None), ("refund", "o5", 435, None), ("charge", "o1", 0, 0), ("refund", "o5", 0, 0.0),
             ("charge", "o6", 500, 5), ("refund", "o6", 1500, None)]
    for _ in range(14):
        cents = rng.choice([0, rng.randrange(1, 30000), rng.randrange(1, 100) * 100])
        rows.append((rng.choice(["charge", "charge", "refund"]), rng.choice(ORDERS[:5]), cents,
                     dollars(rng, cents) if rng.random() < 0.5 else None))
    rng.shuffle(rows)
    log = [{"kind": k, "order_id": o, "amount": a} if a is not None else {"kind": k, "order_id": o, "amount_cents": c}
           for k, o, c, a in rows]
    totals = {o: sum(c if k == "charge" else -c for k, oo, c, _ in rows if oo == o) for o in ORDERS}
    asks = []
    for o in ORDERS:
        left = max(0, totals[o])
        asks += [[o, left], [o, left + 1], [o, 0], [o, max(1, left - 1)]]
    return log, rows, totals, asks


OLD_EVENT = {"kind": "refund", "order_id": "o7", "amount": 0.29}
NEW_EVENT = {"kind": "charge", "order_id": "o8", "amount_cents": 0}


def expected(log, rows, totals, asks):
    left = {o: max(0, t) for o, t in totals.items()}
    return {"error": None,
            "order_total": [[o, totals[o]] for o in ORDERS],
            "refundable": [[o, left[o]] for o in ORDERS],
            "can_refund": [[o, c, 0 < c <= left[o]] for o, c in asks],
            "lines": [[o, [f"{k} {c // 100}.{c % 100:02d}" for k, oo, c, _ in rows if oo == o]] for o in ORDERS],
            "csv": ["kind,order_id,amount_cents"] + [f"{k},{o},{c}" for k, o, c, _ in rows],
            "fingerprints": [f"{k}|{o}|{c}" for k, o, c, _ in rows],
            "revenue": sum(c if k == "charge" else -c for k, _, c, _ in rows),
            "recorded": [{"kind": "charge", "order_id": "o9", "amount_cents": 1999}],
            "upcast": [{"kind": "refund", "order_id": "o7", "amount_cents": 29},
                       {"kind": "charge", "order_id": "o8", "amount_cents": 0}],
            "upcast_inputs_after": [OLD_EVENT, NEW_EVENT],
            "log_after": log}


def same(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def names_old_key(work):
    for path in sorted(work.rglob("*.py")):
        if path == work / "shop" / "events.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            return True
        if any(isinstance(n, ast.Constant) and n.value == "amount" for n in ast.walk(tree)):
            return True
    return False


def passes(work, seed):
    log, rows, totals, asks = generate(seed)
    got = passcheck.probe(work, HERE / "probe.py", {"log": log, "orders": ORDERS, "refund_asks": asks,
                                                     "upcast_inputs": [OLD_EVENT, NEW_EVENT]})
    return same(got, expected(log, rows, totals, asks)) and not names_old_key(work)


if __name__ == "__main__":
    sys.exit(0 if passes(Path(sys.argv[1]), 31) else 1)
