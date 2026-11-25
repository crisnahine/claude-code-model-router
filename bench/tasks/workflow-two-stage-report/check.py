"""Pass when REPORT.json holds exactly the version and the upload limit, and every shipped file is unchanged."""
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
# The workflow script is shipped too, so a run can't pass by rewriting it.
for rel in sorted(p.relative_to(HERE / "repo") for p in (HERE / "repo").rglob("*") if p.is_file()):
    try:
        same = (work / rel).read_bytes() == (HERE / "repo" / rel).read_bytes()
    except OSError:
        same = False
    if not same:
        passcheck.finish(False, f"{rel.as_posix()} was changed or removed; the brief asks for no other change")

try:
    data = json.loads((work / "REPORT.json").read_text(encoding="utf-8-sig"))
except (OSError, ValueError, RecursionError):
    passcheck.finish(False, "REPORT.json is missing or isn't JSON")
if not isinstance(data, dict) or set(data) != {"version", "max_upload_mb"}:
    passcheck.finish(False, "REPORT.json must be an object with exactly version and max_upload_mb")
if not (isinstance(data["version"], str) and re.fullmatch(r"v?3\.4\.1", data["version"])):
    passcheck.finish(False, f"version is {data['version']!r}, expected 3.4.1")
ok = type(data["max_upload_mb"]) is int and data["max_upload_mb"] == 25
passcheck.finish(ok, f"max_upload_mb is {data['max_upload_mb']!r}, expected 25")
