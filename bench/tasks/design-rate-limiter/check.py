"""Pass when DESIGN.json picks a shared token bucket per API key at 5/s with a burst of 20."""
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
ok = (d.get("algorithm") == "token_bucket" and d.get("store") == "redis" and d.get("key") == "api_key"
      and number(d.get("rate_per_second")) and d["rate_per_second"] == 5
      and number(d.get("burst")) and d["burst"] == 20 and reasons(d))
sys.exit(0 if ok else 1)
