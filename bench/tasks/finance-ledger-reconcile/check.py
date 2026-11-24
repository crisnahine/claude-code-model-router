"""Pass when MISMATCHES.json lists exactly the refs that don't reconcile, each once with its kind."""
import csv
import importlib.util
import re
import sys
from decimal import Decimal
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

SHIPPED = Path(__file__).resolve().parent / "repo"
ROUNDING = Decimal("0.50")


def kind(bank, rows):
    """The kind RECONCILE.md gives a bank row (None for no bank row) against its GL rows, or None if they reconcile."""
    if bank is None or not rows:
        return "missing"
    amount, total = Decimal(bank["amount_usd"]), sum(Decimal(r["amount_usd"]) for r in rows)
    if abs(total) > abs(amount) + ROUNDING and any(abs(Decimal(r["amount_usd"]) - amount) <= ROUNDING for r in rows):
        return "duplicate"
    if abs(total - amount) <= ROUNDING:
        return None if {r["posting_date"][:7] for r in rows} == {bank["value_date"][:7]} else "period"
    foreign = re.search(r"WIRE .* ([\d,.]+) FX ", bank["description"])
    if foreign and abs(abs(total) - Decimal(foreign.group(1).replace(",", ""))) <= ROUNDING:
        return "currency"
    return "sign" if abs(total + amount) <= ROUNDING else "amount"


def expected():
    with open(SHIPPED / "bank.csv", encoding="utf-8") as fh:
        bank = {r["ref"]: r for r in csv.DictReader(fh)}
    gl = {}
    with open(SHIPPED / "gl.csv", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            gl.setdefault(r["ref"], []).append(r)
    month = {r["value_date"][:7] for r in bank.values()}
    refs = set(bank) | {ref for ref, rows in gl.items() if any(r["posting_date"][:7] in month for r in rows)}
    kinds = {ref: kind(bank.get(ref), gl.get(ref)) for ref in refs}
    return {ref: k for ref, k in kinds.items() if k}


WANT = expected()


def verdict(d):
    if set(d) != {"mismatches"} or not isinstance(d["mismatches"], list):
        return "MISMATCHES.json is missing or isn't an object with only the key \"mismatches\" holding a list"
    got = {}
    for item in d["mismatches"]:
        if not isinstance(item, dict) or set(item) != {"ref", "kind"} or not all(isinstance(v, str) for v in item.values()):
            return f"{item!r} isn't an object with exactly a ref and a kind"
        ref = item["ref"].strip()
        if ref in got:
            return f"{ref} is listed more than once"
        got[ref] = item["kind"].strip()
    extra, missed = sorted(set(got) - set(WANT)), sorted(set(WANT) - set(got))
    if extra:
        return f"{', '.join(extra)} reconciles but is listed"
    if missed:
        return f"{', '.join(missed)} doesn't reconcile but isn't listed"
    wrong = [f"{r} is {got[r]}, expected {WANT[r]}" for r in sorted(WANT) if got[r] != WANT[r]]
    return "; ".join(wrong)


reason = verdict(passcheck.answer(Path(sys.argv[1]), "MISMATCHES.json"))
passcheck.finish(not reason, reason)
