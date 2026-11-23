"""Pass when the answer names app/settings.py and read_retry_limit. read_timeout also reads the environment
(REQUEST_TIMEOUT), so a sentence that names it passes only when it rules it out: it says what read_timeout reads
instead of MAX_RETRIES, or says "not read_timeout". Hedging between the two, or listing both, fails."""
import re
import sys
from pathlib import Path

answer = Path(sys.argv[2]).read_text(encoding="utf-8")
RULED_OUT = (r"REQUEST_TIMEOUT", r"\b(?:not|isn't|never)\s+(?:the\s+|in\s+|about\s+)?\W?read_timeout")
hedged = [s for s in re.split(r"(?<=[.;!?])\s+|\n+", answer)
          if "read_timeout" in s and not any(re.search(rule, s) for rule in RULED_OUT)]
right = "settings.py" in answer and "read_retry_limit" in answer
print("names read_retry_limit in settings.py" if right and not hedged else
      "does not name read_retry_limit in settings.py" if not right else f"names read_timeout without ruling it out: {hedged[0][:80]!r}")
sys.exit(0 if right and not hedged else 1)
