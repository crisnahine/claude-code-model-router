"""Pass when every formatter takes the currency through: symbol, decimals, and USD unchanged."""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

WORK = Path(sys.argv[1])
sys.path.insert(0, str(WORK))  # after the stdlib imports, so the work dir can't replace them

try:
    fmt = importlib.import_module("pricing.money").format_price
    Invoice = importlib.import_module("billing.invoice").Invoice
    summary_line = importlib.import_module("reports.summary").summary_line
    ok = (fmt(1234) == "$12.34" and fmt(5) == "$0.05" and fmt(1234, "EUR") == "€12.34"
          and fmt(1234, currency="GBP") == "£12.34" and fmt(1234, "JPY") == "¥1234"
          and Invoice([("a", 1000), ("b", 234)], "EUR").total_line() == "Total: €12.34"
          and Invoice([("a", 1234)]).total_line() == "Total: $12.34"
          and Invoice([("a", 500)], "JPY").total_line() == "Total: ¥500"
          and summary_line("Q1", 1234, "GBP") == "Q1: £12.34" and summary_line("Q1", 1234) == "Q1: $12.34")
except BaseException:  # a SystemExit from work-dir code is a failure
    ok = False
sys.exit(0 if ok else 1)
