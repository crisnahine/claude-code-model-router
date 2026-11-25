"""Pass when the answer's count line gives 4, the number of '# TODO' comments in src/**/*.py, and no other number. The
count line is the first line with a number in it once per-file numbers are set aside: a line number or a count beside a
file name ("src/orders.py:7", "orders: 2", "`users.py` 1", "report.py has 1", "2 in orders.py"), "N files" and
"lines N". So a per-file breakdown doesn't count but a hedge such as "4 or 5" fails, as does a count line that names 4
only to deny it ("not 4"). Later lines may explain, and their numbers are not read."""
import re
import sys
from pathlib import Path

text = Path(sys.argv[2]).read_text(encoding="utf-8")
# Any .py path, or a file of this repo named without its folder or extension.
FILE = r"`?(?:[\w./\\-]+\.py|(?:[\w./\\-]+/)?(?:orders|users|report|notes|README)(?:\.\w+)?)\b`?"
PARTS = (rf"\d+\s+in\s+{FILE}", rf"{FILE}(?::\d+)?(?:\s*(?:\(\d+\)|:\s*\d+|\d+\b|\b(?:has|have)\s+\d+))?",
         r"\d+\s+(?:`?\.py`?\s+|python\s+)?files?", r"\blines?\s+\d+(?:\s*(?:,|and|to|-)\s*\d+)*")
count = []
for line in text.splitlines():
    for part in PARTS:
        line = re.sub(part, " ", line, flags=re.I)
    count = re.findall(r"\d+", line)
    if count:
        break
denied = re.search(r"\b(?:not|isn't|never)\s+\W*4\b", line, re.I)
ok = set(count) == {"4"} and not denied
print("the count line says 4" if ok else "the count line denies 4" if set(count) == {"4"} else
      f"the count line gives {sorted(set(count)) or 'no number'}, not just 4")
sys.exit(0 if ok else 1)
