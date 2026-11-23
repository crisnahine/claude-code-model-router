"""Pass when compare orders, ties and rejects versions as Semantic Versioning 2.0.0 says, and max_satisfying
picks the highest version a range allows, carets and the prerelease rule included."""
import importlib.util
import itertools
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

ORDER = ["0.9.9", "1.0.0-0.3.7", "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
         "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "1.0.1-0A", "1.0.1-x-y-z.--", "1.0.1",
         "1.2.0", "1.10.0", "2.0.0-1", "2.0.0-1.a", "2.0.0-a", "2.0.0", "10.0.0"]
EQUAL = [("1.0.0+build.1", "1.0.0+build.2"), ("1.0.0-alpha+001", "1.0.0-alpha"), ("1.0.0+0010", "1.0.0")]
INVALID = ["01.0.0", "1.01.0", "1.0", "1.0.0.0", "1.0.0-", "1.0.0+", "1.0.0-01", "1.0.0-alpha..1", "v1.0.0",
           " 1.0.0", "1.0.0-al_pha", "1.0.0+a..b", "", "1.0.0-é"]
V = ["1.2.3", "1.2.4", "1.3.0", "2.0.0", "2.0.0-alpha", "1.2.4-rc.1", "0.2.3", "0.2.9", "0.3.0", "0.0.3", "0.0.4",
     "1.2.3-beta.2", "1.2.3-beta.10", "bogus", "1.3.0+build", "01.4.0"]
RANGES = {"^1.2.3": "1.3.0", "^0.2.3": "0.2.9", "^0.0.3": "0.0.3", "^1.2.3-beta.2": "1.3.0",
          ">=1.2.3-beta.2 <1.2.3": "1.2.3-beta.10", "<1.0.0 || >=2.0.0": "2.0.0", ">=2.0.0-alpha": "2.0.0",
          "=2.0.0-alpha": "2.0.0-alpha", "2.0.0-alpha": "2.0.0-alpha", ">1.2.3 <1.3.0": "1.2.4", ">3.0.0": None,
          "  <0.1.0   ||  ^0.2.3 ": "0.2.9", "<=1.2.4-rc.1": "1.2.4-rc.1", ">=1.2.4-rc.0 <1.2.4": "1.2.4-rc.1",
          "<2.0.0": "1.3.0", ">=0.0.3 <0.0.4 || =9.9.9": "0.0.3"}
BAD_RANGES = ["", "||", ">=1.2.3 ||", "^1.2", "~1.2.3", ">= 1.2.3", ">=1.2.3 <01.0.0", "1.2", "=>1.2.3"]


try:
    module = passcheck.load(Path(sys.argv[1]), "semver")
    compare, best = module.compare, module.max_satisfying
    ok = all(compare(ORDER[i], ORDER[j]) == (i > j) - (i < j)
             for i, j in itertools.product(range(len(ORDER)), repeat=2))
    ok = ok and all(compare(a, b) == 0 == compare(b, a) for a, b in EQUAL)
    ok = ok and all(passcheck.raises(compare, bad, "1.0.0") and passcheck.raises(compare, "1.0.0", bad) for bad in INVALID)
    ok = ok and all(best(V, r) == want for r, want in RANGES.items())
    ok = ok and best(["1.3.0+b", "1.3.0+a"], "^1.0.0") == "1.3.0+b" and best([], "^1.0.0") is None
    ok = ok and all(passcheck.raises(best, V, r) for r in BAD_RANGES)
except BaseException:  # a SystemExit from work-dir code is a failure
    ok = False
sys.exit(0 if ok else 1)
