"""Pass when RESEARCH.json names 2.3.1, 2.4.2 and 2.4.3, with the yank notice, the errata and SA-2025-01 among its
sources, and every source a file under docs/."""
import json
import re
import sys
from pathlib import Path

SHIPPED = Path(__file__).resolve().parent / "repo"


def version(value):
    m = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", str(value).strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def source(value):
    if not isinstance(value, str):
        return None
    path = re.sub(r"(?:#L?\d+|:\d+)$", "", value.strip().replace("\\", "/"))
    tail = path[path.find("docs/"):] if "docs/" in path else "docs/" + path.lstrip("./")
    return tail if (SHIPPED / tail).is_file() else None


try:
    d = json.loads((Path(sys.argv[1]) / "RESEARCH.json").read_text(encoding="utf-8"))
except (OSError, ValueError, RecursionError):
    d = None
ok = False
if (isinstance(d, dict) and set(d) == {"stream_since", "timeout_since", "safe_since", "sources"}
        and isinstance(d["sources"], list)):
    found = [source(s) for s in d["sources"]]
    ok = (version(d["stream_since"]) == (2, 3, 1) and version(d["timeout_since"]) == (2, 4, 2)
          and version(d["safe_since"]) == (2, 4, 3) and None not in found
          and {"docs/changelog/v2.3.1.md", "docs/errata.md", "docs/security/SA-2025-01.md"} <= set(found))
sys.exit(0 if ok else 1)
