"""Format with symbols and decimals the task never shipped. It runs in a child on a copy of the work dir whose
pricing/symbols.py holds other tables, so code that hardcoded the shipped ones can't pass."""
import importlib
import json
import sys

sys.path.append(sys.argv[1])
money = importlib.import_module("pricing.money")
invoice = importlib.import_module("billing.invoice")
summary = importlib.import_module("reports.summary")
print(json.dumps([money.format_price(1234, "CHF"), money.format_price(1234, "XAF"), money.format_price(1234, "SEK"),
                  invoice.Invoice([("a", 1000), ("b", 234)], "SEK").total_line(), summary.summary_line("Q", 1234, "XAF"),
                  money.format_price(1234)]))
