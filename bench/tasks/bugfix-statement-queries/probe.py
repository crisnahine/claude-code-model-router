import importlib
import importlib.util
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

work = str(Path(sys.argv[1]))
passcheck.use_work(Path(work))
payload = json.load(sys.stdin)
PREFIX = work + os.sep
LINE_CAP = 3_000_000  # a linear answer stays far below this
VM_CAP = 2_000_000  # progress-handler calls, 100 VM steps each
OVER = 10 ** 9
clock = time.process_time  # bound before the work's code loads, so it can't replace the clock


def run(data):
    out = {"error": None, "pairs": None, "statements": 0, "vm": 0, "lines": 0, "sentinel": False, "schema": None,
           "temp": None, "cpu": None}
    seen = {"statements": 0, "vm": 0, "lines": 0, "sentinel": False, "counting": True, "traced": True}

    def on_statement(sql):
        if seen["counting"]:
            seen["statements"] += 1
        if "sentinel" in sql:
            seen["sentinel"] = True

    def on_progress():
        if seen["counting"]:
            seen["vm"] += 1
            if seen["vm"] > VM_CAP:
                return 1
        return 0

    def local(frame, event, arg):
        if event == "line":
            seen["lines"] += 1
            if seen["lines"] > LINE_CAP:
                seen["lines"] = OVER
                sys.settrace(None)
                return None
        return local

    def tracer(frame, event, arg):
        return local if frame.f_code.co_filename.startswith(PREFIX) else None

    try:
        kwargs = {"autocommit": sqlite3.LEGACY_TRANSACTION_CONTROL} if hasattr(sqlite3, "LEGACY_TRANSACTION_CONTROL") else {}
        conn = sqlite3.connect(":memory:", **kwargs)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(payload["schema"])
        conn.executemany("INSERT INTO accounts VALUES (?, ?)", data["accounts"])
        conn.executemany("INSERT INTO invoices VALUES (?, ?, ?, ?)", data["invoices"])
        conn.executemany("INSERT INTO payments VALUES (?, ?, ?, ?, ?)", data["payments"])
        conn.commit()
        balances = importlib.import_module("billing.statement").balances
        if payload.get("timed"):
            start = clock()
            result = balances(conn)
            out["cpu"] = clock() - start
        else:
            conn.set_trace_callback(on_statement)
            conn.set_progress_handler(on_progress, 100)
            sys.settrace(tracer)
            try:
                result = balances(conn)
            finally:
                if sys.gettrace() is not tracer:
                    seen["traced"] = False  # tracing was replaced or removed, so the line count means nothing
                sys.settrace(None)
                seen["counting"] = False
        if isinstance(result, dict):
            try:
                pairs = sorted(result.items())
            except TypeError:
                pairs = list(result.items())
            json.dumps(pairs)
            out["pairs"] = [list(p) for p in pairs]
        conn.execute("SELECT 'sentinel'").fetchall()
        out["schema"] = [r[0] for r in conn.execute("SELECT sql FROM sqlite_master ORDER BY name")]
        out["temp"] = conn.execute("SELECT count(*) FROM sqlite_temp_master").fetchone()[0]
    except BaseException as e:  # whatever was counted so far still goes out
        out["error"] = f"{type(e).__name__}: {e}"
    out.update(statements=seen["statements"], vm=seen["vm"], lines=seen["lines"], sentinel=seen["sentinel"] and seen["traced"])
    return out


results = [run(data) for data in payload["datasets"]]
# Written to the file descriptor and followed by _exit, so nothing the task code left behind can add a later line.
os.write(1, ("\n" + json.dumps(results) + "\n").encode())
os._exit(0)
