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


try:
    parse = load("durations").parse_duration
    good = {"1h30m": 5400, "45s": 45, "2d": 172800, "1d2h3m4s": 93784, "0m": 0, "10m5s": 605, "100h": 360000}
    bad = ["", "1x", "30m1h", "1h1h", "1.5h", "-1h", "1 h", "h", "90", "1h 30m"]
    ok = all(parse(text) == want for text, want in good.items()) and all(raises(parse, text) for text in bad)
except BaseException:  # a SystemExit from work-dir code is a failure
    ok = False
sys.exit(0 if ok else 1)
