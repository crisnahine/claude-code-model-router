"""Pass when RESEARCH.json gives 1.3, 2.0 and FETCHKIT_RETRIES, citing the two changelogs."""
import importlib.util
import re
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)


def version(value):
    parts = [int(n) for n in re.findall(r"\d+", str(value or ""))]
    if not parts or any(parts[2:]):
        return str(value)
    return f"{parts[0]}.{parts[1] if len(parts) > 1 else 0}"


def cited(name):
    return any(re.sub(r"(?::\d+|#.*)$", "", str(s).replace("\\", "/")).endswith(f"changelog/{name}")
               for s in d.get("sources") or [])


d = passcheck.answer(Path(sys.argv[1]), "RESEARCH.json")
ok = (version(d.get("default_changed_in")) == "1.3" and version(d.get("configurable_in")) == "2.0"
      and d.get("variable") == "FETCHKIT_RETRIES" and cited("v1.3.md") and cited("v2.0.md"))
sys.exit(0 if ok else 1)
