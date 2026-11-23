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
try:
    import pricing
    import rates
    full = {"pen": 100, "book": 1500, "lamp": 4999}
    ok = passcheck.tests_pass(WORK, SHIPPED, "test_pricing.py")
    for percent in (10, 25, 50, 10):
        ok = ok and pricing.sale_prices("eu", percent) == {k: v * (100 - percent) // 100 for k, v in full.items()}
        ok = ok and pricing.price_list("eu") == full
    ok = ok and [{"sku": r["sku"], "price": r["price"]} for r in rates.rate_table("eu")] == [
        {"sku": k, "price": v} for k, v in full.items()]
    ok = ok and rates.LOADS.count("eu") == 1 and pricing.price_list("us")["lamp"] == 5499
except BaseException:  # a SystemExit from work-dir code is a failure
    ok = False
sys.exit(0 if ok else 1)
