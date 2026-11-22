"""Pass when the answer names the file and the function that read MAX_RETRIES. read_timeout also reads the
environment, so an answer that names it without ruling it out is hedging between candidates and fails."""
import re
import sys
from pathlib import Path

answer = Path(sys.argv[2]).read_text(encoding="utf-8")
right = "settings.py" in answer and "read_retry_limit" in answer
hedged = "read_timeout" in answer and not re.search(r"\b(?:not|instead|rather|doesn't|does not)\b", answer)
sys.exit(0 if right and not hedged else 1)
