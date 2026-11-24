import hashlib
import importlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True  # the check also runs this on the task's own repo/, which must stay clean

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

passcheck.use_work(Path(sys.argv[1]))
payload = json.load(sys.stdin)
tmp = tempfile.mkdtemp()
out = []


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def command(sales, regions):
    """python3 -m reports with every flag the README lists: (exit code, the report, the standings CSV)."""
    report, table = os.path.join(tmp, "report.txt"), os.path.join(tmp, "standings.csv")
    for path in (report, table):
        if os.path.exists(path):
            os.remove(path)
    code = importlib.import_module("reports.__main__").main(
        [sales, regions, "--out", report, "--standings", table, "--verify"])
    if code != 0:
        raise RuntimeError(f"python3 -m reports exited {code}")
    return read(report) + "\0" + read(table)


try:
    render = importlib.import_module("reports").render if payload.get("via") != "command" else command
    for n, case in enumerate(payload["cases"]):
        sales, regions = os.path.join(tmp, f"sales{n}.csv"), os.path.join(tmp, f"regions{n}.csv")
        for path, text in ((sales, case["sales"]), (regions, case["regions"])):
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        try:
            start = time.perf_counter()
            text = render(sales, regions)
            seconds = time.perf_counter() - start
        except Exception as e:
            out.append({"error": f"{type(e).__name__}: {e}"[:300]})
            continue
        if not isinstance(text, str):
            out.append({"error": f"render returned {type(text).__name__}, not str"})
        elif payload.get("digest"):
            out.append({"error": None, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "lines": text.count("\n"), "seconds": seconds})
        else:
            out.append({"error": None, "text": text})
except BaseException as e:
    out = {"error": f"{type(e).__name__}: {e}"[:300]}
shutil.rmtree(tmp, ignore_errors=True)
# Written to the file descriptor and followed by _exit, so nothing the task code left behind can add a later line.
os.write(1, ("\n" + json.dumps(out) + "\n").encode())
os._exit(0)
