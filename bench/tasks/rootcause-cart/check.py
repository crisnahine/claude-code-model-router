"""Pass when test_cart.py, unchanged, passes, and two new carts never share items."""
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
    return res.returncode == 0 and "Ran 0 tests" not in res.stderr


sys.path.insert(0, str(WORK))
try:
    import cart
    a, b = cart.add_item("a", 1), cart.add_item("b", 1)
    kept = cart.add_item("c", 1, items=a)
    ok = tests_pass("test_cart.py") and len(b) == 1 and kept is a and len(a) == 2
except Exception:
    ok = False
sys.exit(0 if ok else 1)
