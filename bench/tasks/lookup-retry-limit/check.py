"""Pass when the answer names the file and the function that read MAX_RETRIES."""
import sys
from pathlib import Path

answer = Path(sys.argv[2]).read_text(encoding="utf-8")
sys.exit(0 if "settings.py" in answer and "read_retry_limit" in answer else 1)
