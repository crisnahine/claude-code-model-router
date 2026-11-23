"""Pass when the answer's count line gives 4, the number of '# TODO' comments in src/**/*.py, and no other number. The
count line is the first line with a number in it once file names (with a line number or a count beside them), "N files"
and "lines N" are set aside, so a per-file breakdown doesn't count but a hedge such as "4 or 5" fails. Later lines
may explain, and their numbers are not read."""
import re
import sys
from pathlib import Path

text = Path(sys.argv[2]).read_text(encoding="utf-8")
PATH = r"`?[\w./\\-]+\.py`?"
PARTS = (rf"\d+\s+in\s+{PATH}", rf"{PATH}(?::\d+)?(?:\s*(?:\(\d+\)|:\s*\d+|\b(?:has|have)\s+\d+))?",
         r"\d+\s+(?:\.py\s+|python\s+)?files?", r"\blines?\s+\d+(?:\s*(?:,|and|to|-)\s*\d+)*")
count = []
for line in text.splitlines():
    for part in PARTS:
        line = re.sub(part, " ", line, flags=re.I)
    count = re.findall(r"\d+", line)
    if count:
        break
ok = set(count) == {"4"}
print("the count line says 4" if ok else f"the count line gives {sorted(set(count)) or 'no number'}, not just 4")
sys.exit(0 if ok else 1)
