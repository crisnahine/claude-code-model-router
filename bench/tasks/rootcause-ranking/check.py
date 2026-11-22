"""Pass when test_ranking.py, unchanged, passes, and scores order as numbers."""
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
    import ranking
    players = [{"name": n, "score": s} for n, s in (("a", 3), ("b", 100), ("c", 20), ("d", 1000))]
    ok = tests_pass("test_ranking.py") and [p["name"] for p in ranking.top(players, 4)] == ["d", "b", "c", "a"]
except BaseException:  # a SystemExit from work-dir code is a failure
    ok = False
sys.exit(0 if ok else 1)
