import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)


WORK = Path(sys.argv[1])
values = [4, 1, 3, 2]
with passcheck.agent_code():
    median = passcheck.load(WORK, "stats").median
    got = [median([3, 1, 2]), median(values), values, median([1, 2]), median([5]), median([1, 1, 2, 2]),
           median([7, 3, 5, 1, 9]), passcheck.raises(median, [])]
want = [2, 2.5, [4, 1, 3, 2], 1.5, 5, 1.5, 5, True]
passcheck.finish(got == want, passcheck.differs(got, want))
