"""Pass when the answer names app/settings.py and gives read_retry_limit as the function: it comes before the caller
and the backoff helpers, so an answer that leads with the caller fails. read_timeout also reads the environment
(REQUEST_TIMEOUT), so a sentence that names it passes only when it rules it out: it says read_timeout reads
REQUEST_TIMEOUT, or says "not read_timeout". Hedging between the two, or listing both, fails, as does an answer that
names read_retry_limit or settings.py only to deny it ("not read_retry_limit", "settings.py isn't it")."""
import re
import sys
from pathlib import Path

answer = Path(sys.argv[2]).read_text(encoding="utf-8")
# read_timeout is left out: an answer may rule it out before naming the function, and `hedged` judges it.
FUNCTIONS = r"\b(?:read_retry_limit|fetch|retries_left|retry_delay)\b"
RULED_OUT = (r"\bread_timeout\b.{0,60}?\b(?:reads|uses|is\s+for)\s+(?:the\s+)?\W?REQUEST_TIMEOUT",
             r"\bread_timeout\b.{0,60}?\b(?:is\s+not|isn't)\s+(?:it|the\s+one)\b",
             r"\b(?:not|isn't|never)\s+(?:the\s+|in\s+|about\s+)?\W?read_timeout")
OFFERED = r"\bor\s+\W?read_timeout\b|\bread_timeout\W*\s+or\b"
DENIED = (r"\b(?:not|isn't|never)\s+(?:the\s+|in\s+|from\s+)?\W?(?:app/)?(?:read_retry_limit|settings\.py)\b|"
          r"\b(?:read_retry_limit|settings\.py)\W*\s+(?:is\s+not|isn't)\s+(?:it|the\s+one)\b")
hedged = [s for s in re.split(r"(?<=[.;!?])\s+|\n+", answer) if "read_timeout" in s
          and (re.search(OFFERED, s) or not any(re.search(rule, s) for rule in RULED_OUT))]
first = re.search(FUNCTIONS, answer)
right = "settings.py" in answer and first is not None and first.group() == "read_retry_limit"
led = first is not None and first.group() != "read_retry_limit" and "read_retry_limit" in answer
denied = re.search(DENIED, answer, re.I)
right = right and not denied
print(f"denies the answer it names: {denied.group()!r}" if denied else
      "names read_retry_limit in settings.py" if right and not hedged else
      f"names {first.group()} before read_retry_limit" if led else
      "does not name read_retry_limit in settings.py" if not right else f"names read_timeout without ruling it out: {hedged[0][:80]!r}")
sys.exit(0 if right and not hedged else 1)
