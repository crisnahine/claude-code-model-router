import importlib.util
import sys
from pathlib import Path


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(sys.argv[1]) / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def raises(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return True
    except Exception:
        return False
    return False


page_slice = load("pages").page_slice
items = list(range(10))
ok = (page_slice(items, 1, 3) == [0, 1, 2] and page_slice(items, 2, 3) == [3, 4, 5]
      and page_slice(items, 4, 3) == [9] and page_slice(items, 5, 3) == []
      and page_slice(items, 1, 10) == items and page_slice("abcdef", 2, 2) == "cd"
      and raises(page_slice, items, 0, 3) and raises(page_slice, items, 1, 0))
sys.exit(0 if ok else 1)
