"""Pass when the report names one line of files/server.py, the unresolved join (8) or the open
below it (9), cites no line of the checked join in files/thumbs.py, and lists at most three lines in all."""
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


sys.exit(0 if verdict("server.py", {8, 9}, {("thumbs.py", n) for n in range(6, 12)}) else 1)
