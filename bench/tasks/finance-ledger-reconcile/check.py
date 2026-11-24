"""Pass when MISMATCHES.json lists exactly the refs that don't reconcile, each once with its kind."""
import importlib.util
import json
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

WANT = {"TRX-109065": "period", "TRX-175986": "period", "TRX-200492": "amount", "TRX-221296": "missing",
        "TRX-629108": "duplicate", "TRX-749309": "currency", "TRX-761986": "missing", "TRX-835237": "amount",
        "TRX-869479": "amount", "TRX-978664": "sign"}


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
