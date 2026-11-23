"""Pass when test_pricing.py, unchanged, passes, a region is still read from the source once however often it
is priced, and pricing never changes the cached rows."""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

WORK = Path(sys.argv[1])
SHIPPED = Path(__file__).resolve().parent / "repo"

passcheck.use_work(WORK)
FULL = {"pen": 100, "book": 1500, "lamp": 4999}
with passcheck.agent_code():
    import pricing
    import rates
    sales = []
    for percent in (10, 25, 50, 10):
        sales.append((dict(pricing.sale_prices("eu", percent)), dict(pricing.price_list("eu"))))
    cached = [{"sku": r["sku"], "price": r["price"]} for r in rates.rate_table("eu")]
    loads, us_lamp = rates.LOADS.count("eu"), pricing.price_list("us")["lamp"]
tests = passcheck.tests_pass(WORK, SHIPPED, "test_pricing.py")
want_sales = [({k: v * (100 - percent) // 100 for k, v in FULL.items()}, FULL) for percent in (10, 25, 50, 10)]
if not tests:
    passcheck.finish(False, "test_pricing.py no longer passes")
if sales != want_sales:
    passcheck.finish(False, passcheck.differs(sales, want_sales))
if cached != [{"sku": k, "price": v} for k, v in FULL.items()]:
    passcheck.finish(False, "pricing changed the cached rows")
passcheck.finish(loads == 1 and us_lamp == 5499, f"a region was read {loads} times, or the us lamp is {us_lamp}")
