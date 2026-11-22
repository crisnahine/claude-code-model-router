"""Pass when the report names db/users.py line 6 and no other line of that file, cites no line
of the safe query in db/orders.py, and lists at most three lines in all."""
import re
import sys
from pathlib import Path

answer = Path(sys.argv[2]).read_text(encoding="utf-8").replace("\\", "/").replace("`", "").replace("*", "")
pairs = {(path.split("/")[-1], int(line)) for path, line in
         re.findall(r"([\w./-]+\.py)(?:#L|:|,?\s+(?:on\s+)?line\s+)(\d+)", answer)}


def verdict(file, targets, decoys):
    # One line of the vulnerable file, the vulnerable one; a caller elsewhere may be cited too.
    cited = {line for name, line in pairs if name == file}
    return len(cited) == 1 and cited <= targets and not decoys & pairs and len(pairs) <= 3


sys.exit(0 if verdict("users.py", {6}, {("orders.py", n) for n in range(1, 5)}) else 1)
