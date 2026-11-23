"""Pass when DESIGN.json picks a shared token bucket per API key at 5/s with a burst of 20."""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

d = passcheck.answer(Path(sys.argv[1]), "DESIGN.json")
ok = (d.get("algorithm") == "token_bucket" and d.get("store") == "redis" and d.get("key") == "api_key"
      and passcheck.number(d.get("rate_per_second")) and d["rate_per_second"] == 5
      and passcheck.number(d.get("burst")) and d["burst"] == 20 and passcheck.reasons(d))
sys.exit(0 if ok else 1)
