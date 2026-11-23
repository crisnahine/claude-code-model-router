"""Pass when FINDING.json is one finding, exactly {"file": ..., "line": ...} plus an optional "why" string,
naming gateway/router.py at the second decode (25 or 26) or gateway/guard.py at its prefix test (12)."""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

ROUTER, GUARD = {25, 26}, {12}
work = Path(sys.argv[1])
sys.exit(0 if passcheck.finding(work, "gateway/router.py", ROUTER) or passcheck.finding(work, "gateway/guard.py", GUARD) else 1)
