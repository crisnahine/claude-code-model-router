"""Pass when test_pricing.py, unchanged, passes, a region is still read from the source once however often it
is priced, and pricing never changes the cached rows."""
import re
import subprocess
import sys
from pathlib import Path

WORK = Path(sys.argv[1])
SHIPPED = Path(__file__).resolve().parent / "repo"


def tests_pass(test):
    if (WORK / test).read_bytes() != (SHIPPED / test).read_bytes():
        return False  # the test is the spec; changing it doesn't count
    res = subprocess.run([sys.executable, "-m", "unittest", test[:-3]], cwd=WORK, capture_output=True, text=True,
                         timeout=60)
    return res.returncode == 0 and re.search(r"Ran [1-9]\d* tests?", res.stderr) is not None and "OK" in res.stderr


sys.path.insert(0, str(WORK))
try:
    import pricing
    import rates
    full = {"pen": 100, "book": 1500, "lamp": 4999}
    ok = tests_pass("test_pricing.py")
    for percent in (10, 25, 50, 10):
        ok = ok and pricing.sale_prices("eu", percent) == {k: v * (100 - percent) // 100 for k, v in full.items()}
        ok = ok and pricing.price_list("eu") == full
    ok = ok and [{"sku": r["sku"], "price": r["price"]} for r in rates.rate_table("eu")] == [
        {"sku": k, "price": v} for k, v in full.items()]
    ok = ok and rates.LOADS.count("eu") == 1 and pricing.price_list("us")["lamp"] == 5499
except BaseException:  # a SystemExit from work-dir code is a failure
    ok = False
sys.exit(0 if ok else 1)
