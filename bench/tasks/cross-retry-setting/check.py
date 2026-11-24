"""Pass when fetch and upload both make `retries` attempts, read on every call, 3 by default."""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

WORK = Path(sys.argv[1])
passcheck.use_work(WORK)


def attempts(fn):
    calls = []
    fn("x", lambda arg: calls.append(arg))  # never succeeds: None from send, falsy from put
    return len(calls)


with tempfile.TemporaryDirectory() as tmp:
    configs = [os.path.join(tmp, f"c{retries}.json") for retries in (5, 1)]
    for path, retries in zip(configs, (5, 1)):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"retries": retries}, fh)
    with passcheck.agent_code():
        fetch = importlib.import_module("fetcher").fetch
        upload = importlib.import_module("uploader").upload
        os.environ.pop("APP_CONFIG", None)
        seen = [(attempts(fetch), attempts(upload))]
        for path in configs:
            os.environ["APP_CONFIG"] = path
            seen.append((attempts(fetch), attempts(upload)))
passcheck.finish(seen == [(3, 3), (5, 5), (1, 1)],
                 f"(fetch, upload) attempts by setting (none, 5, 1) are {seen}, expected [(3, 3), (5, 5), (1, 1)]")
