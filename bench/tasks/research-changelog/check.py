"""Pass when RESEARCH.json gives 1.3, 2.0 and FETCHKIT_RETRIES, citing the two changelogs."""
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


def version(value):
    parts = [int(n) for n in re.findall(r"\d+", str(value or ""))]
    if not parts or any(parts[2:]):
        return str(value)
    return f"{parts[0]}.{parts[1] if len(parts) > 1 else 0}"


def cited(name):
    return any(str(s).replace("\\", "/").endswith(f"changelog/{name}") for s in d.get("sources") or [])


d = answer("RESEARCH.json")
ok = (version(d.get("default_changed_in")) == "1.3" and version(d.get("configurable_in")) == "2.0"
      and d.get("variable") == "FETCHKIT_RETRIES" and cited("v1.3.md") and cited("v2.0.md"))
sys.exit(0 if ok else 1)
