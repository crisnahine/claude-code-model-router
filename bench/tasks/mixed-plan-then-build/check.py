"""Pass when POST /payments replays a repeated key without a second charge and refuses a reused one, with requests
taking turns between two copies of the work dir (two hosts, each with its own temp and home folder) on one database;
DESIGN.json picks the option that keeps no state in a worker; the shipped tests still pass; and test_idempotency.py
passes on the work but fails on the code as shipped and on each build in mutants/ laid over the work. Requests run
one at a time, so a build that looks the key up, charges and then stores it with no lock passes, though two requests
with one key at the same moment would both charge."""
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
# Each keeps every name the solution has, so a test that only imports them can't tell the build is broken.
MUTANTS = {"charges-again": "charges a retry with the same key again",
           "no-body-check": "replays a reused key without comparing the body"}


def request(customer, cents, key=None):
    return {"headers": {"Idempotency-Key": key} if key else {}, "body": {"customer_id": customer, "cents": cents}}


def step(host, db, action, req=None):
    got = passcheck.probe(host / "work", HERE / "probe.py",
                          {"action": action, "db": db, "request": req, "host": str(host)})
    if not isinstance(got, dict):
        passcheck.finish(False, f"the {action} step gave no answer (a hang, a crash or a response that isn't JSON)")
    if got["error"]:
        passcheck.finish(False, f"the {action} step raised {got['error']}")
    return got["result"]


def behaviour(work, tmp):
    hosts = [Path(tmp) / name for name in ("host-a", "host-b")]
    for host in hosts:
        shutil.copytree(work, host / "work", symlinks=True)
    a, b = hosts
    db = str(Path(tmp) / "shop.db")
    step(a, db, "init")
    first = step(a, db, "post", request(1, 500, "k-1"))
    if not (isinstance(first, list) and first[0] == 201):
        return f"the first request with a key got {first!r}, expected a 201"
    again = step(b, db, "post", request(1, 500, "k-1"))
    if again != first:
        return f"a retry with the same key and body, sent to the other host, got {again!r}; expected {first!r}"
    for host, req in ((a, request(1, 700, "k-1")), (b, request(2, 500, "k-1"))):
        got = step(host, db, "post", req)
        if got != REUSED:
            return f"the key k-1 reused with the body {req['body']} got {got!r}, expected {REUSED!r}"
    plain = [step(host, db, "post", request(1, 500)) for host in hosts]
    if [p[0] for p in plain] != [201, 201] or plain[0] == plain[1]:
        return f"two requests without a key got {plain!r}, expected two different 201 payments"
    if step(a, db, "post", request(2, 500, "k-2"))[0] != 201:
        return "a new key for another customer wasn't a 201"
    counts = step(b, db, "count")
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
    wrong = behaviour(work, tmp)
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
    if runs(shipped):
        passcheck.finish(False, "test_idempotency.py also passes on the code as shipped, so it doesn't test the feature")
for name, broken in MUTANTS.items():
    with tempfile.TemporaryDirectory() as tmp:
        # Over a copy of the work, so the test's own helpers and modules are all still there.
        mutant = Path(tmp) / "work"
        shutil.copytree(work, mutant, symlinks=True)
        shutil.copytree(HERE / "mutants" / name, mutant, dirs_exist_ok=True)
        if runs(mutant):
            passcheck.finish(False, f"test_idempotency.py still passes on a build that {broken}, so it doesn't test "
                                    "the feature")
passcheck.finish(True)
