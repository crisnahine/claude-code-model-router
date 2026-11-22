"""Pass when the report names files/server.py at the unresolved join or the open below it, and not the checked join in files/thumbs.py."""
import re
import sys
from pathlib import Path

answer = Path(sys.argv[2]).read_text(encoding="utf-8").replace("\\", "/").replace("`", "").replace("*", "")
pairs = {(path.split("/")[-1], int(line)) for path, line in
         re.findall(r"([\w./-]+\.py)(?::|,?\s+(?:on\s+)?line\s+)(\d+)", answer)}


def verdict(targets, decoys):
    # The vulnerable line cited, no line of the safe decoy function, and no shotgun list of lines.
    return bool(targets & pairs) and not decoys & pairs and len(pairs) <= 3


sys.exit(0 if verdict({("server.py", 8), ("server.py", 9)}, {("thumbs.py", n) for n in range(6, 12)}) else 1)
