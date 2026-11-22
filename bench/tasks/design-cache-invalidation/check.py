"""Pass when DESIGN.json invalidates on the bus event and keeps staleness within 5 seconds."""
import json
import re
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


def numeric(value):
    try:
        return float(value) if not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None

d = answer("DESIGN.json")
ttl = d.get("ttl_seconds", "missing")
# 12 servers x 100,000 products / 2,000 reads per second: a safety TTL under 600 s alone outruns the database.
ok = (d.get("strategy") == "event_invalidation" and (ttl is None or (number(ttl) and ttl >= 600))
      and number(d.get("max_staleness_seconds")) and 0 <= d["max_staleness_seconds"] <= 5 and reasons(d))
sys.exit(0 if ok else 1)
