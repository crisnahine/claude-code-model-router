"""Pass when the first number in the answer is the count of '# TODO' comments in src/**/*.py (4)."""
import re
import sys
from pathlib import Path

numbers = re.findall(r"\d+", Path(sys.argv[2]).read_text(encoding="utf-8"))
sys.exit(0 if numbers and int(numbers[0]) == 4 else 1)
