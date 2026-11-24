"""Pass when TERMS.json gives the terms in force: Amendment No. 1's cap and Customer notice, New York law (the draft
Amendment No. 2 was never signed), the Order Form's 24-month renewal term and Customer's risk in the Appliances."""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

WANT = {"liability_cap_usd": 1800000, "termination_notice_days": {"customer": 45, "provider": 90},
        "governing_law": "New York",
        "renewal": {"automatic": True, "renewal_term_months": 24, "non_renewal_notice_days": 60},
        "appliance_risk_at_site": "customer"}


def same(got, want):
    """Equal, with integers that are never booleans and booleans that are never integers."""
    if isinstance(want, dict):
        return isinstance(got, dict) and set(got) == set(want) and all(same(got[k], want[k]) for k in want)
    return type(got) is type(want) and got == want


d = passcheck.answer(Path(sys.argv[1]), "TERMS.json")
if set(d) != set(WANT):
    passcheck.finish(False, "TERMS.json is missing or its keys aren't exactly " + ", ".join(WANT))
wrong = [f"{k} is {d[k]!r}, expected {v!r}" for k, v in WANT.items() if not same(d[k], v)]
passcheck.finish(not wrong, "; ".join(wrong))
