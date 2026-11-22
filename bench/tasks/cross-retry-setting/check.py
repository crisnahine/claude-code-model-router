"""Pass when fetch and upload both make `retries` attempts, read on every call, 3 by default."""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

WORK = Path(sys.argv[1])
sys.path.insert(0, str(WORK))  # after the stdlib imports, so the work dir can't replace them


def attempts(fn):
    calls = []
    fn("x", lambda arg: calls.append(arg))  # never succeeds: None from send, falsy from put
    return len(calls)


try:
    fetch = importlib.import_module("fetcher").fetch
    upload = importlib.import_module("uploader").upload
    os.environ.pop("APP_CONFIG", None)
    seen = [(attempts(fetch), attempts(upload))]
    with tempfile.TemporaryDirectory() as tmp:
        for retries in (5, 1):
            path = os.path.join(tmp, f"c{retries}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"retries": retries}, fh)
            os.environ["APP_CONFIG"] = path
            seen.append((attempts(fetch), attempts(upload)))
    ok = seen == [(3, 3), (5, 5), (1, 1)]
except BaseException:  # a SystemExit from work-dir code is a failure
    ok = False
sys.exit(0 if ok else 1)
