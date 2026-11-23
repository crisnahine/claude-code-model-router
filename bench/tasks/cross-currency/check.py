"""Pass when every formatter takes the currency through: symbol, decimals, and USD unchanged, and when it reads
them from pricing/symbols.py: on a copy of the work dir with other tables the output follows them."""
import importlib.util
import shutil
import sys
import tempfile
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
if got != WANT:
    passcheck.finish(False, passcheck.differs(got, WANT))
# Appended and swapped in place, so a helper the run added to symbols.py stays and still reads the tables.
OTHER_TABLES = ('\nSYMBOLS.clear()\nSYMBOLS.update({"USD": "$", "CHF": "Fr", "XAF": "F", "SEK": "kr"})\n'
                'DECIMALS.clear()\nDECIMALS.update({"CHF": 0, "XAF": 0})\n')
FOLLOWS = ["Fr1234", "F1234", "kr12.34", "Total: kr12.34", "Q: F1234", "$12.34"]
with tempfile.TemporaryDirectory() as tmp:
    copy = Path(tmp) / "work"
    shutil.copytree(WORK, copy, ignore=shutil.ignore_patterns("__pycache__"))
    with open(copy / "pricing" / "symbols.py", "a", encoding="utf-8") as tables:
        tables.write(OTHER_TABLES)
    seen = passcheck.probe(copy, Path(__file__).resolve().parent / "probe.py", None)
passcheck.finish(seen == FOLLOWS, "with other tables in pricing/symbols.py the output is "
                 f"{seen!r}, expected {FOLLOWS!r}: the symbols and decimals must come from SYMBOLS and DECIMALS")
