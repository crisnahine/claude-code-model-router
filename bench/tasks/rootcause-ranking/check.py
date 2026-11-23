"""Pass when test_ranking.py, unchanged, passes, and scores order as numbers."""
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
players = [{"name": n, "score": s} for n, s in (("a", 3), ("b", 100), ("c", 20), ("d", 1000))]
with passcheck.agent_code():
    import ranking
    tests = passcheck.tests_pass(WORK, SHIPPED, "test_ranking.py")
    order = [p["name"] for p in ranking.top(players, 4)]
passcheck.finish(tests and order == ["d", "b", "c", "a"],
                 "test_ranking.py no longer passes" if not tests else passcheck.differs(order, ["d", "b", "c", "a"]))
