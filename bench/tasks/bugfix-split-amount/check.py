"""Pass when allocate splits any total by weights with the largest-remainder rule, negatives and validation
included, as its docstring says."""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)


def expected(total, weights):
    s, sign = sum(weights), (-1 if total < 0 else 1)
    parts = [divmod(abs(total) * w, s) for w in weights]
    shares = [q for q, _ in parts]
    for i in sorted(range(len(parts)), key=lambda i: (-parts[i][1], i))[:abs(total) - sum(shares)]:
        shares[i] += 1
    return [sign * x for x in shares]


try:
    allocate = passcheck.load(Path(sys.argv[1]), "money").allocate
    cases = [(100, [1, 1, 1]), (-100, [1, 1, 1]), (5, [3, 0, 2]), (10, [2, 1]), (1, [1, 1, 1]), (-1, [1, 1, 1]),
             (2, [1, 1, 1]), (0, [1, 2]), (7, [5]), (-7, [5]), (1000, [3, 3, 4]), (101, [1, 1, 1, 1]),
             (-101, [1, 1, 1, 1]), (99, [7, 0, 13, 2]), (3, [1, 2, 3]), (1, [0, 5, 0]), (17, [1, 3, 3, 1])]
    ok = all(allocate(t, list(w)) == expected(t, w) and all(type(x) is int for x in allocate(t, list(w)))
             for t, w in cases)
    ok = ok and all(passcheck.raises(allocate, 10, bad) for bad in ([], [0, 0], [1, -1], [1.5, 1], ["1"]))
except BaseException:  # a SystemExit from work-dir code is a failure
    ok = False
sys.exit(0 if ok else 1)
