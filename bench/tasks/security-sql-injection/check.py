"""Pass when the report names db/users.py line 6, the f-string SQL, and not the safe query in db/orders.py."""
import re
import sys
from pathlib import Path

answer = Path(sys.argv[2]).read_text(encoding="utf-8").replace("\\", "/").replace("`", "").replace("*", "")
pairs = {(path.split("/")[-1], int(line)) for path, line in
         re.findall(r"([\w./-]+\.py)(?::|,?\s+(?:on\s+)?line\s+)(\d+)", answer)}


def verdict(targets, decoy):
    # The vulnerable line cited, the decoy not, and no shotgun list of lines.
    return bool(targets & pairs) and decoy not in pairs and len(pairs) <= 3


sys.exit(0 if verdict({("users.py", 6)}, ("orders.py", 3)) else 1)
