"""Pass when POST /payments, each request run in its own process on one database, replays a repeated key without a
second charge and refuses a reused one; DESIGN.json picks the option that keeps no state in a worker; the shipped tests
still pass; and test_idempotency.py passes on the work and fails on the code as shipped."""
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

HERE = Path(__file__).resolve().parent
REUSED = [409, {"error": "idempotency key reused"}]


def request(customer, cents, key=None):
    return {"headers": {"Idempotency-Key": key} if key else {}, "body": {"customer_id": customer, "cents": cents}}


def step(work, db, action, req=None):
    got = passcheck.probe(work, HERE / "probe.py", {"action": action, "db": db, "request": req})
    if not isinstance(got, dict):
        passcheck.finish(False, f"the {action} step gave no answer (a hang, a crash or a response that isn't JSON)")
    if got["error"]:
        passcheck.finish(False, f"the {action} step raised {got['error']}")
    return got["result"]


def behaviour(work, db):
    step(work, db, "init")
    first = step(work, db, "post", request(1, 500, "k-1"))
    if not (isinstance(first, list) and first[0] == 201):
        return f"the first request with a key got {first!r}, expected a 201"
    again = step(work, db, "post", request(1, 500, "k-1"))
    if again != first:
        return f"a retry with the same key and body, sent from another process, got {again!r}; expected {first!r}"
    for req in (request(1, 700, "k-1"), request(2, 500, "k-1")):
        got = step(work, db, "post", req)
        if got != REUSED:
            return f"the key k-1 reused with the body {req['body']} got {got!r}, expected {REUSED!r}"
    plain = [step(work, db, "post", request(1, 500)) for _ in range(2)]
    if [p[0] for p in plain] != [201, 201] or plain[0] == plain[1]:
        return f"two requests without a key got {plain!r}, expected two different 201 payments"
    if step(work, db, "post", request(2, 500, "k-2"))[0] != 201:
        return "a new key for another customer wasn't a 201"
    counts = step(work, db, "count")
    if counts != [4, 4]:
        return f"{counts[0]} charges and {counts[1]} payments, expected 4 of each"
    return None


def runs(folder):
    """Whether test_idempotency.py in folder runs at least one test and passes."""
    try:
        res = subprocess.run([sys.executable, "-I", "-c", passcheck.LAUNCHER, str(folder), "test_idempotency"],
                             cwd=folder, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        return False
    return res.returncode == 0 and re.search(r"Ran [1-9]\d* tests?", res.stderr) is not None and "OK" in res.stderr


work = Path(sys.argv[1])
with tempfile.TemporaryDirectory() as tmp:
    wrong = behaviour(work, str(Path(tmp) / "shop.db"))
if wrong:
    passcheck.finish(False, wrong)
design = passcheck.answer(work, "DESIGN.json")
choice = design.get("choice")
if set(design) != {"choice", "reasons"} or not passcheck.reasons(design) or not isinstance(choice, str):
    passcheck.finish(False, 'DESIGN.json must be {"choice": "<A or B>", "reasons": ["..."]}')
if choice.strip().upper() != "B":
    passcheck.finish(False, f"DESIGN.json picks {choice!r}, expected B")
if not passcheck.tests_pass(work, HERE / "repo", "test_payments.py"):
    passcheck.finish(False, "test_payments.py was changed or no longer passes")
if not (work / "test_idempotency.py").is_file():
    passcheck.finish(False, "test_idempotency.py is missing")
if not runs(work):
    passcheck.finish(False, "test_idempotency.py doesn't pass on the work")
with tempfile.TemporaryDirectory() as tmp:
    shipped = Path(tmp) / "shipped"
    shutil.copytree(HERE / "repo", shipped)
    shutil.copyfile(work / "test_idempotency.py", shipped / "test_idempotency.py")
    blind = runs(shipped)
passcheck.finish(not blind, "test_idempotency.py also passes on the code as shipped, so it doesn't test the feature")
