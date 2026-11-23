"""Pass when test_cart.py, unchanged, passes, and two new carts never share items."""
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
with passcheck.agent_code():
    import cart
    a, b = cart.add_item("a", 1), cart.add_item("b", 1)
    kept = cart.add_item("c", 1, items=a)
    shape = [len(b), kept is a, len(a)]
got = [passcheck.tests_pass(WORK, SHIPPED, "test_cart.py"), *shape]
passcheck.finish(got == [True, 1, True, 2], passcheck.differs(got, [True, 1, True, 2]))
