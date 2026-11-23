"""Pass when every formatter takes the currency through: symbol, decimals, and USD unchanged."""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

WORK = Path(sys.argv[1])
passcheck.use_work(WORK)

with passcheck.agent_code():
    fmt = importlib.import_module("pricing.money").format_price
    Invoice = importlib.import_module("billing.invoice").Invoice
    summary_line = importlib.import_module("reports.summary").summary_line
    got = [fmt(1234), fmt(5), fmt(1234, "EUR"), fmt(1234, currency="GBP"), fmt(1234, "JPY"),
           Invoice([("a", 1000), ("b", 234)], "EUR").total_line(), Invoice([("a", 1234)]).total_line(),
           Invoice([("a", 500)], "JPY").total_line(), summary_line("Q1", 1234, "GBP"), summary_line("Q1", 1234)]
WANT = ["$12.34", "$0.05", "€12.34", "£12.34", "¥1234", "Total: €12.34", "Total: $12.34", "Total: ¥500",
        "Q1: £12.34", "Q1: $12.34"]
passcheck.finish(got == WANT, passcheck.differs(got, WANT))
