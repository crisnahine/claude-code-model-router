"""Pass when the answer names the file and the function that read MAX_RETRIES, and none of the other
candidates. Naming the caller (app/client.py) is fine; listing every function is not an answer."""
import sys
from pathlib import Path

answer = Path(sys.argv[2]).read_text(encoding="utf-8")
right = "settings.py" in answer and "read_retry_limit" in answer
others = ("read_timeout", "retries_left", "retry_delay", "backoff.py")
sys.exit(0 if right and not any(name in answer for name in others) else 1)
