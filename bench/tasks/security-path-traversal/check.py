"""Pass when FINDING.json is one finding, exactly {"file": ..., "line": ...} plus an optional "why" string,
naming files/server.py at the unresolved join (8) or the open below it (9)."""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

TARGET, LINES = "files/server.py", {8, 9}
sys.exit(0 if passcheck.finding(Path(sys.argv[1]), TARGET, LINES) else 1)
