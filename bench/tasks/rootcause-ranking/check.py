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
try:
    import ranking
    players = [{"name": n, "score": s} for n, s in (("a", 3), ("b", 100), ("c", 20), ("d", 1000))]
    ok = passcheck.tests_pass(WORK, SHIPPED, "test_ranking.py") and [p["name"] for p in ranking.top(players, 4)] == ["d", "b", "c", "a"]
except BaseException:  # a SystemExit from work-dir code is a failure
    ok = False
sys.exit(0 if ok else 1)
