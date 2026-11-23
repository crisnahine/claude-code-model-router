import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)


try:
    median = passcheck.load(Path(sys.argv[1]), "stats").median
    values = [4, 1, 3, 2]
    ok = (median([3, 1, 2]) == 2 and median(values) == 2.5 and values == [4, 1, 3, 2]
          and median([1, 2]) == 1.5 and median([5]) == 5 and median([1, 1, 2, 2]) == 1.5
          and median([7, 3, 5, 1, 9]) == 5 and passcheck.raises(median, []))
except BaseException:  # a SystemExit from work-dir code is a failure
    ok = False
sys.exit(0 if ok else 1)
