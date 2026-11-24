"""Pass when ANSWERS.json holds exactly the three answers, and every shipped file is still there unchanged."""
import importlib.util
import json
import re
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

HERE = Path(__file__).resolve().parent
work = Path(sys.argv[1])
shipped = sorted(p.relative_to(HERE / "repo") for p in (HERE / "repo").rglob("*") if p.is_file())
for rel in shipped:
    try:
        same = (work / rel).read_bytes() == (HERE / "repo" / rel).read_bytes()
    except OSError:
        same = False
    if not same:
        passcheck.finish(False, f"{rel.as_posix()} was changed or removed; the brief asks for no changes")

try:
    data = json.loads((work / "ANSWERS.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    passcheck.finish(False, "ANSWERS.json is missing or isn't JSON")
if not isinstance(data, dict) or set(data) != {"page_size", "refund_email", "webhook_attempts"}:
    passcheck.finish(False, "ANSWERS.json must be an object with exactly page_size, refund_email and webhook_attempts")
for key, want in (("page_size", 100), ("webhook_attempts", 4)):
    if type(data[key]) is not int or data[key] != want:
        passcheck.finish(False, f"{key} is {data[key]!r}, expected {want}")
where = data["refund_email"]
m = re.fullmatch(r"\s*`?(.+?):(\w+)(?:\(\))?`?\s*", where) if isinstance(where, str) else None
ok = bool(m) and ("/" + m.group(1).replace("\\", "/")).endswith("/shop/notify/emails.py") \
    and m.group(2) == "send_refund_receipt"
passcheck.finish(ok, f"refund_email is {where!r}, expected shop/notify/emails.py:send_refund_receipt")
