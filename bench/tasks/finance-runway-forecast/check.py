"""Pass when RUNWAY.json gives 10.20 months of runway (within 0.03) and August 2027 as the month cash runs out."""
import importlib.util
import re
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

# 2,150,000 less 172,000 a month, the hires and the insurance leaves 43,250 after July 2027; August spends 217,000.
MONTHS, OUT = 10 + 43250 / 217000, "2027-08"

d = passcheck.answer(Path(sys.argv[1]), "RUNWAY.json")
months = passcheck.numeric(d.get("months_of_runway"))
month = d.get("cash_out_month")
if set(d) != {"months_of_runway", "cash_out_month"} or months is None or not isinstance(month, str):
    passcheck.finish(False, "RUNWAY.json is missing or doesn't hold exactly a number months_of_runway and a cash_out_month")
month = month.strip()
if not re.fullmatch(r"\d{4}-\d{2}", month):
    passcheck.finish(False, f"cash_out_month {month!r} isn't YYYY-MM")
reasons = [f"cash_out_month is {month}, expected {OUT}"] if month != OUT else []
if abs(months - MONTHS) > 0.03:
    reasons.append(f"months_of_runway is {months:g}, expected {MONTHS:.2f}")
passcheck.finish(not reasons, "; ".join(reasons))
