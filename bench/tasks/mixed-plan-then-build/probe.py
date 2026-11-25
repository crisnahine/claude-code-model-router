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
charged = []
try:
    if payload["action"] == "init":
        importlib.import_module("db").init(payload["db"])
        conn = sqlite3.connect(payload["db"])
        conn.executemany("INSERT INTO customers (id, email) VALUES (?, ?)", [(1, "ana@example.com"), (2, "ben@example.com")])
        conn.commit()
    elif payload["action"] in ("post", "crash"):
        if payload["action"] == "crash":
            # Only a commit after the charge, so a commit made before it can't use up the crash.
            class Crash(sqlite3.Connection):
                def commit(self):
                    super().commit()
                    if charged:
                        os.write(1, ("\n" + json.dumps(out) + "\n").encode())
                        os._exit(0)

            def crashing(*args, _connect=sqlite3.connect, **kwargs):
                kwargs.setdefault("factory", Crash)
                return _connect(*args, **kwargs)

            sqlite3.connect = crashing
        gateway = importlib.import_module("payments.gateway")
        real_charge = gateway.charge

        # The fake gateway logs inside the work's transaction, which a rollback erases; a real card stays charged.
        def charge(*args, **kwargs):
            got = real_charge(*args, **kwargs)
            with open(payload["db"] + ".charges", "a", encoding="utf-8") as log:
                log.write("charge\n")
            charged.append(True)
            return got

        gateway.charge = charge
        api = importlib.import_module("api.payments")
        for module in [m for m in list(sys.modules.values()) if m is not sys.modules["__main__"]]:
            for name, value in list(getattr(module, "__dict__", {}).items()):
                if value is real_charge:
                    setattr(module, name, charge)
        conn = importlib.import_module("db").connect(payload["db"])
        status, body = api.handle_post(conn, payload["request"])
        out["result"] = json.loads(json.dumps([status, body]))
    else:
        charges = Path(payload["db"] + ".charges")
        conn = sqlite3.connect(payload["db"])
        out["result"] = [len(charges.read_text(encoding="utf-8").split()) if charges.is_file() else 0,
                         conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]]
except BaseException as e:
    out["error"] = f"{type(e).__name__}: {e}"[:300]
# Written to the file descriptor and followed by _exit, so nothing the task code left behind can add a later line.
os.write(1, ("\n" + json.dumps(out) + "\n").encode())
os._exit(0)
