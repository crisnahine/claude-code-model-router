import importlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

passcheck.use_work(Path(sys.argv[1]))
payload = json.load(sys.stdin)
tmp = tempfile.mkdtemp()
out = {"error": None, "count": None, "text": None}
try:
    src, accounts, dst = (os.path.join(tmp, name) for name in ("export_v2.jsonl", "accounts.json", "ledger_v3.jsonl"))
    for path, text in ((src, payload["v2"]), (accounts, payload["accounts"])):
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    migrate = importlib.import_module("ledger.migrate_v3").migrate
    count = migrate(src, accounts, dst)
    out["count"] = count if type(count) is int else repr(count)
    if os.path.exists(dst):
        with open(dst, encoding="utf-8", newline="") as f:
            out["text"] = f.read()
except BaseException as e:
    out["error"] = f"{type(e).__name__}: {e}"[:300]
shutil.rmtree(tmp, ignore_errors=True)
# Written to the file descriptor and followed by _exit, so nothing the task code left behind can add a later line.
os.write(1, ("\n" + json.dumps(out) + "\n").encode())
os._exit(0)
