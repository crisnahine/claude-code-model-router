"""Pass when DESIGN.json holds exactly the brief's four keys, invalidates on the bus event and states a staleness from
the bus's 1 second delivery to the spec's 5 seconds."""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

d = passcheck.answer(Path(sys.argv[1]), "DESIGN.json")
ttl = d.get("ttl_seconds", "missing")
# 12 servers x 100,000 products / 2,000 reads per second: a safety TTL under 600 s alone outruns the database.
# A server serves the old price until the bus's event reaches it, which can take 1 s, so less understates it.
ok = (set(d) == {"strategy", "ttl_seconds", "max_staleness_seconds", "reasons"}
      and d.get("strategy") == "event_invalidation" and (ttl is None or (passcheck.number(ttl) and ttl >= 600))
      and passcheck.number(d.get("max_staleness_seconds")) and 1 <= d["max_staleness_seconds"] <= 5
      and passcheck.reasons(d))
sys.exit(0 if ok else 1)
