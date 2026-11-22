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


median = load("stats").median
values = [4, 1, 3, 2]
ok = (median([3, 1, 2]) == 2 and median(values) == 2.5 and values == [4, 1, 3, 2]
      and median([1, 2]) == 1.5 and median([5]) == 5 and median([1, 1, 2, 2]) == 1.5
      and median([7, 3, 5, 1, 9]) == 5 and raises(median, []))
sys.exit(0 if ok else 1)
