"""Pass when the reply's last `<seconds> <file>` line says 25 seconds, decided in app/client.py."""
import re
import sys
from pathlib import Path

# The brief's `<seconds> <file path>` template is sometimes copied with its brackets.
lines = [l.replace("<", " ").replace(">", " ").strip().strip("`").strip()
         for l in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines() if l.strip()]
found = [re.fullmatch(r"`?(\d+(?:\.0+)?)`?\s*(?:s|seconds?)?\s*,?\s+`?([\w./\\-]+\.py)`?", l) for l in lines]
found = [m for m in found if m]
ok = bool(found) and float(found[-1].group(1)) == 25 and (
    "/" + found[-1].group(2).replace("\\", "/")).endswith("/app/client.py")
sys.exit(0 if ok else 1)
