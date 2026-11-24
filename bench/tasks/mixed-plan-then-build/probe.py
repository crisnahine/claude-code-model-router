import importlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

passcheck.use_work(Path(sys.argv[1]))
payload = json.load(sys.stdin)
host = Path(payload["host"])
for name, folder in (("HOME", "home"), ("TMPDIR", "tmp"), ("TMP", "tmp"), ("TEMP", "tmp")):
    (host / folder).mkdir(exist_ok=True)
    os.environ[name] = str(host / folder)
tempfile.tempdir = None
out = {"error": None, "result": None}
try:
    if payload["action"] == "init":
        importlib.import_module("db").init(payload["db"])
        conn = sqlite3.connect(payload["db"])
        conn.executemany("INSERT INTO customers (id, email) VALUES (?, ?)", [(1, "ana@example.com"), (2, "ben@example.com")])
        conn.commit()
    elif payload["action"] == "post":
        conn = importlib.import_module("db").connect(payload["db"])
        status, body = importlib.import_module("api.payments").handle_post(conn, payload["request"])
        out["result"] = json.loads(json.dumps([status, body]))
    else:
        conn = sqlite3.connect(payload["db"])
        out["result"] = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("gateway_log", "payments")]
except BaseException as e:
    out["error"] = f"{type(e).__name__}: {e}"[:300]
# Written to the file descriptor and followed by _exit, so nothing the task code left behind can add a later line.
os.write(1, ("\n" + json.dumps(out) + "\n").encode())
os._exit(0)
