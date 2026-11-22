"""Pass when DESIGN.json invalidates on the bus event and keeps staleness within 5 seconds."""
import json
import sys
from pathlib import Path


def answer(name):
    try:
        data = json.loads((Path(sys.argv[1]) / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def reasons(data):
    return isinstance(data.get("reasons"), list) and bool(data["reasons"]) and all(
        isinstance(r, str) and r.strip() for r in data["reasons"])


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)

d = answer("DESIGN.json")
ttl = d.get("ttl_seconds", "missing")
ok = (d.get("strategy") == "event_invalidation" and (ttl is None or (number(ttl) and ttl > 0))
      and number(d.get("max_staleness_seconds")) and 0 <= d["max_staleness_seconds"] <= 5 and reasons(d))
sys.exit(0 if ok else 1)
