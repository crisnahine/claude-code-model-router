import importlib.util
import json
import os
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

passcheck.use_work(Path(sys.argv[1]))
payload = json.load(sys.stdin)
log, orders, asks = payload["log"], payload["orders"], payload["refund_asks"]
upcast_inputs = payload["upcast_inputs"]
out = {"error": None, "order_total": None, "refundable": None, "can_refund": None, "lines": None, "csv": None,
       "fingerprints": None, "revenue": None, "recorded": None, "upcast": None, "upcast_inputs_after": None,
       "log_after": None}
try:
    from reports import daily
    from shop import audit, events, export, invoice, refunds, totals

    out["order_total"] = [[o, totals.order_total(log, o)] for o in orders]
    out["refundable"] = [[o, refunds.refundable(log, o)] for o in orders]
    out["can_refund"] = [[o, cents, refunds.can_refund(log, o, cents)] for o, cents in asks]
    out["lines"] = [[o, invoice.lines(log, o)] for o in orders]
    out["csv"] = export.csv_rows(log)
    out["fingerprints"] = [audit.fingerprint(e) for e in log]
    out["revenue"] = daily.revenue(log)
    recorded = []
    events.record(recorded, "charge", "o9", 1999)
    out["recorded"] = recorded
    out["upcast"] = [events.upcast(e) for e in upcast_inputs]
    out["upcast_inputs_after"] = upcast_inputs
except BaseException as e:  # whatever was read so far still goes out
    out["error"] = f"{type(e).__name__}: {e}"
try:
    out["log_after"] = json.loads(json.dumps(log))
except BaseException as e:
    out["error"] = out["error"] or f"{type(e).__name__}: {e}"
# Written to the file descriptor and followed by _exit, so nothing the code under test left behind can add a later line.
os.write(1, ("\n" + json.dumps(out) + "\n").encode())
os._exit(0)
