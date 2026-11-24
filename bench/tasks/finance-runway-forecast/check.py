"""Pass when RUNWAY.json gives the runway FORECAST.md's method yields on the shipped data, to two decimal places, and
the month cash runs out."""
import csv
import importlib.util
import re
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

SHIPPED = Path(__file__).resolve().parent / "repo"
# NOTES.md: October isn't closed, August's other income holds a one-off refund, the insurance falls due in January.
CLOSED, ONE_OFF, COMMITTED = "2026-09", {"2026-08": 150000}, {"2027-01": 48000}


def next_month(key):
    y, m = map(int, key.split("-"))
    return f"{y + m // 12}-{m % 12 + 1:02d}"


def forecast():
    with open(SHIPPED / "actuals.csv", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["month"] <= CLOSED]
    base = sum(float(r["revenue"]) - float(r["cost_of_revenue"]) - float(r["payroll"]) - float(r["other_opex"])
               + float(r["other_income"]) - ONE_OFF.get(r["month"], 0) for r in rows[-3:]) / 3
    cash = float(rows[-1]["closing_cash"])
    with open(SHIPPED / "hiring-plan.csv", encoding="utf-8") as fh:
        hires = [(r["start_date"][:7] if int(r["start_date"][8:]) <= 15 else next_month(r["start_date"][:7]),
                  float(r["annual_salary"]) * 1.25 / 12) for r in csv.DictReader(fh) if r["status"] == "approved"]
    month = next_month(CLOSED)
    for n in range(600):
        spend = -base + sum(cost for start, cost in hires if month >= start) + COMMITTED.get(month, 0)
        if cash < spend:
            return n + cash / spend, month
        cash -= spend
        month = next_month(month)
    raise RuntimeError("cash never runs out in 50 years")


MONTHS, OUT = forecast()
d = passcheck.answer(Path(sys.argv[1]), "RUNWAY.json")
months = passcheck.numeric(d.get("months_of_runway"))
month = d.get("cash_out_month")
if set(d) != {"months_of_runway", "cash_out_month"} or months is None or not isinstance(month, str):
    passcheck.finish(False, "RUNWAY.json is missing or doesn't hold exactly a number months_of_runway and a cash_out_month")
month = month.strip()
if not re.fullmatch(r"\d{4}-\d{2}", month):
    passcheck.finish(False, f"cash_out_month {month!r} isn't YYYY-MM")
reasons = [f"cash_out_month is {month}, expected {OUT}"] if month != OUT else []
# The brief asks for two decimals, so anything that doesn't round to the right hundredth is wrong.
if not abs(months - MONTHS) <= 0.005:
    reasons.append(f"months_of_runway is {months:g}, expected {MONTHS:.2f}")
passcheck.finish(not reasons, "; ".join(reasons))
