"""Pass when RESEARCH.json gives 1.3, 2.0 and FETCHKIT_RETRIES, citing the two changelogs."""
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


def version(value):
    text = str(value or "").strip().lower().lstrip("v")
    return text[:-2] if text.endswith(".0") and text.count(".") == 2 else text


d = answer("RESEARCH.json")
sources = {str(s).replace("\\", "/").lstrip("./") for s in d.get("sources") or [] if isinstance(s, str)}
ok = (version(d.get("default_changed_in")) == "1.3" and version(d.get("configurable_in")) == "2.0"
      and d.get("variable") == "FETCHKIT_RETRIES"
      and {"docs/changelog/v1.3.md", "docs/changelog/v2.0.md"} <= sources)
sys.exit(0 if ok else 1)
