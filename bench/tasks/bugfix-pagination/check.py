import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)


try:
    page_slice = passcheck.load(Path(sys.argv[1]), "pages").page_slice
    items = list(range(10))
    ok = (page_slice(items, 1, 3) == [0, 1, 2] and page_slice(items, 2, 3) == [3, 4, 5]
          and page_slice(items, 4, 3) == [9] and page_slice(items, 5, 3) == []
          and page_slice(items, 1, 10) == items and page_slice("abcdef", 2, 2) == "cd"
          and passcheck.raises(page_slice, items, 0, 3) and passcheck.raises(page_slice, items, 1, 0))
except BaseException:  # a SystemExit from work-dir code is a failure
    ok = False
sys.exit(0 if ok else 1)
