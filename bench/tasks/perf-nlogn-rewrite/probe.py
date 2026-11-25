import hashlib
import importlib
import importlib.util
import io
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
    text = read(report) + "\0" + read(table)
    if payload.get("digest"):
        return text
    real, sys.stdout = sys.stdout, io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="\n")
    try:
        code = importlib.import_module("reports.__main__").main([sales, regions])
        sys.stdout.flush()
        printed = sys.stdout.buffer.getvalue().decode("utf-8", "replace")
    finally:
        sys.stdout = real
    if code != 0:
        raise RuntimeError(f"python3 -m reports without --out exited {code}")
    return text + "\0" + printed


def refusal(sales, regions):
    """For an input the report can't be built from: what render raises, or for the command its exit code, what it
    printed to stderr and which files it wrote, with this probe's temp folder named <tmp>."""
    def described(e):
        return f"{type(e).__name__}: {e}".replace(tmp, "<tmp>")[:300]

    if payload.get("via") != "command":
        try:
            importlib.import_module("reports").render(sales, regions)
        except Exception as e:
            return {"error": described(e)}
        return {"error": None}
    report, table = os.path.join(tmp, "report.txt"), os.path.join(tmp, "standings.csv")
    for path in (report, table):
        if os.path.exists(path):
            os.remove(path)
    real, sys.stderr = sys.stderr, io.StringIO()
    try:
        try:
            code, error = importlib.import_module("reports.__main__").main(
                [sales, regions, "--out", report, "--standings", table, "--verify"]), None
        except Exception as e:
            code, error = None, described(e)
        printed = sys.stderr.getvalue().replace(tmp, "<tmp>")
    finally:
        sys.stderr = real
    return {"error": error, "code": code, "stderr": printed, "wrote": [os.path.exists(report), os.path.exists(table)]}


try:
    render = importlib.import_module("reports").render if payload.get("via") != "command" else command
    # Every case reuses the same two paths, so an answer that caches by path hands back the last case's report.
    sales, regions = os.path.join(tmp, "sales.csv"), os.path.join(tmp, "regions.csv")
    for case in payload["cases"]:
        for path, text in ((sales, case["sales"]), (regions, case["regions"])):
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        if payload.get("invalid"):
            out.append(refusal(sales, regions))
            continue
        try:
            start = time.process_time()  # CPU time, so a busy machine doesn't fail a fast answer
            text = render(sales, regions)
            seconds = time.process_time() - start
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
