"""Pass when POST /payments replays a repeated key without a second charge and refuses a reused one, with requests
taking turns between two copies of the work dir (two hosts, each with its own temp and home folder) on one database;
DESIGN.json picks the option that keeps no state in a worker (its reasons need only be non-empty text; what they
argue is not graded); the shipped tests still pass; and test_idempotency.py passes on the work but fails on the code
as shipped and on each broken build in mutants.py. A charge counts when the work calls the gateway, even if it then
rolls back, as a real card stays charged. A mutant wraps the work's own
handle_post, so the test is judged on the requests it sends, not on the names it imports. Requests run one at a
time, so a build that looks the key up, charges and then stores it with no lock passes, though two requests with one
key at the same moment would both charge. A request that dies right after its first commit after the charge,
as a host can, is retried once on the other host and may be charged once in all, so a build that stores the key in a
commit of its own after the payment's fails."""
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
mutants = passcheck.load(HERE, "mutants")
REUSED = [409, {"error": "idempotency key reused"}]


def request(customer, cents, key=None):
    return {"headers": {"Idempotency-Key": key} if key else {}, "body": {"customer_id": customer, "cents": cents}}


def step(host, db, action, req=None, strict=True):
    got = passcheck.probe(host / "work", HERE / "probe.py",
                          {"action": action, "db": db, "request": req, "host": str(host)})
    if not strict:
        return got
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
    # The process dies right after its first commit after the charge, as a host can; whatever the retry answers, one
    # charge at most.
    db = str(Path(tmp) / "crash.db")
    step(a, db, "init")
    step(a, db, "crash", request(1, 500, "k-3"), strict=False)
    step(b, db, "post", request(1, 500, "k-3"), strict=False)
    charges = step(b, db, "count")[0]
    if charges > 1:
        return (f"a retry after the request died at its first commit after the charge was charged again "
                f"({charges} charges)")
    return None


def runs(folder, mutant=None):
    """Whether test_idempotency.py in folder runs at least one test and passes, with that mutant installed if named."""
    argv = ["-c", passcheck.LAUNCHER, str(folder), "test_idempotency"] if mutant is None else \
        ["-c", mutants.LAUNCHER, str(folder), "test_idempotency", str(HERE / "mutants.py"), mutant]
    try:
        res = subprocess.run([sys.executable, "-I", *argv], cwd=folder, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        return False
    return res.returncode == 0 and re.search(r"Ran [1-9]\d* tests?", res.stderr) is not None and "OK" in res.stderr

work = Path(sys.argv[1])
with tempfile.TemporaryDirectory() as tmp:
    wrong = behaviour(work, tmp)
if wrong:
    passcheck.finish(False, wrong)
design = passcheck.answer(work, "DESIGN.json", "utf-8-sig")
choice = design.get("choice")
if set(design) != {"choice", "reasons"} or not passcheck.reasons(design) or not isinstance(choice, str):
    passcheck.finish(False, 'DESIGN.json must be {"choice": "<A or B>", "reasons": ["..."]}')
picked = re.match(r"\s*(?:option\s+)?([AB])\b", choice, re.I)
if not picked or picked.group(1).upper() != "B":
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
for name, broken in mutants.MUTANTS.items():
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "work"
        shutil.copytree(work, copy, symlinks=True)
        if runs(copy, name):
            passcheck.finish(False, f"test_idempotency.py still passes on a build that {broken}, so it doesn't test "
                                    "the feature")
passcheck.finish(True)
