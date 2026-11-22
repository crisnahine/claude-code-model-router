"""Pass when the report names db/users.py line 6, the f-string SQL, and no other line."""
import re
import sys
from pathlib import Path

answer = Path(sys.argv[2]).read_text(encoding="utf-8")
pairs = [(path.replace("\\\\", "/").split("/")[-1], int(line)) for path, line in
         re.findall(r"([\w./\\\\-]+\.py)(?::|,?\s+(?:on\s+)?line\s+)(\d+)", answer)]
sys.exit(0 if pairs and all(p == ("users.py", 6) for p in pairs) else 1)
