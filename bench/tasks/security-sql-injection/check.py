"""Pass when FINDING.json is one finding, exactly {"file": ..., "line": ...} plus an optional "why" string,
naming db/users.py line 6."""
import json
import sys
from pathlib import Path

TARGET, LINES = "db/users.py", {6}

try:
    d = json.loads((Path(sys.argv[1]) / "FINDING.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    d = None
ok = (isinstance(d, dict) and set(d) - {"why"} == {"file", "line"} and isinstance(d.get("why", ""), str)
      and isinstance(d["file"], str) and ("/" + d["file"].replace("\\", "/")).endswith("/" + TARGET)
      and type(d["line"]) is int and d["line"] in LINES)
sys.exit(0 if ok else 1)
