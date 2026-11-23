import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)


WORK = Path(sys.argv[1])
items = list(range(10))
with passcheck.agent_code():
    page_slice = passcheck.load(WORK, "pages").page_slice
    got = [page_slice(items, 1, 3), page_slice(items, 2, 3), page_slice(items, 4, 3), page_slice(items, 5, 3),
           page_slice(items, 1, 10), page_slice("abcdef", 2, 2), passcheck.raises(page_slice, items, 0, 3),
           passcheck.raises(page_slice, items, 1, 0)]
want = [[0, 1, 2], [3, 4, 5], [9], [], items, "cd", True, True]
passcheck.finish(got == want, passcheck.differs(got, want))
