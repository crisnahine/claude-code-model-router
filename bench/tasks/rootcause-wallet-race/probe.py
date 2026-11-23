import contextlib
import importlib.util
import json
import os
import sys
import threading
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
LOCK_WAIT = 5
JOIN_WAIT = 20

# name: (call the barrier waits on, seconds it waits, one list of calls per thread)
PLANS = {
    "A": ("get", 1, [[("credit", "w", 30)], [("credit", "w", 50)]]),
    "B": ("get", 10, [[("credit", "x", 10)], [("credit", "y", 20)]]),
    "C": ("lock", 1, [[("transfer", "w1", "w2", 30)], [("transfer", "w2", "w1", 50)]]),
    "D": ("get", 1, [[("transfer", "w", "a", 70)], [("transfer", "w", "b", 70)]]),
    "E": (None, 0, [[("transfer", "w", "w", 40), ("transfer", "w", "w", 140)]]),
    "F": ("get", 1, [[("transfer", "w", "v", 60)], [("credit", "w", 25)]]),
}


class Store:
    # Slots, so work-dir code can't hang a shared lock on the store itself.
    __slots__ = ("data", "mutex", "locks", "kind", "seconds", "barrier", "seen", "met", "deadlock")

    def __init__(self, balances, kind, seconds):
        self.data = dict(balances)
        self.mutex = threading.Lock()
        self.locks = {}
        self.kind, self.seconds = kind, seconds
        self.barrier = threading.Barrier(2)
        self.seen = threading.local()
        self.met = self.deadlock = False

    def rendezvous(self, kind):
        if kind != self.kind or getattr(self.seen, "done", False):
            return
        self.seen.done = True
        try:
            self.barrier.wait(self.seconds)
            self.met = True
        except threading.BrokenBarrierError:
            pass

    def get(self, key):
        with self.mutex:
            value = self.data.get(key)
        self.rendezvous("get")  # the read is done, the caller doesn't have it yet
        return value

    def put(self, key, value):
        with self.mutex:
            self.data[key] = value

    def compare_and_put(self, key, expected, value):
        with self.mutex:
            if self.data.get(key) != expected:
                return False
            self.data[key] = value
            return True

    @contextlib.contextmanager
    def lock(self, key):
        with self.mutex:
            held = self.locks.setdefault(key, threading.Lock())
        if not held.acquire(timeout=LOCK_WAIT):
            self.deadlock = True
            raise TimeoutError(f"lock {key!r} still held after {LOCK_WAIT} s")
        try:
            self.rendezvous("lock")
            yield
        finally:
            held.release()


def fresh_copy(name):
    """ledger/wallet.py loaded again, with every other work-dir module dropped first so its helpers load again too."""
    for key, module in list(sys.modules.items()):
        if str(getattr(module, "__file__", None) or "").startswith(PREFIX):
            del sys.modules[key]
    spec = importlib.util.spec_from_file_location(name, Path(work) / "ledger" / "wallet.py")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "ledger"  # a relative import in the file still works
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run(name, balances, copies):
    kind, seconds, plans = PLANS[name]
    store = Store(balances, kind, seconds)
    raised = []
    go = threading.Event()

    def worker(module, calls):
        go.wait()
        for fn, *args in calls:
            try:
                getattr(module, fn)(store, *args)
            except BaseException as e:
                raised.append(type(e).__name__)

    threads = [threading.Thread(target=worker, args=(copies[i], calls), daemon=True) for i, calls in enumerate(plans)]
    for t in threads:
        t.start()
    go.set()  # no thread starts its calls before every thread exists
    deadline = time.monotonic() + JOIN_WAIT
    for t in threads:
        t.join(max(0, deadline - time.monotonic()))
    return {"error": None, "balances": {k: store.data.get(k) for k in balances}, "raised": sorted(raised),
            "deadlock": store.deadlock, "met": store.met, "hung": any(t.is_alive() for t in threads)}


out = {}
try:
    copies = [fresh_copy("wallet_1"), fresh_copy("wallet_2")]
    failure = None
except BaseException as e:
    copies, failure = None, f"{type(e).__name__}: {e}"
for phase in payload["phases"]:
    try:
        if failure:
            raise RuntimeError(failure)
        out[phase["name"]] = run(phase["name"], phase["balances"], copies)
    except BaseException as e:
        out[phase["name"]] = {"error": f"{type(e).__name__}: {e}", "balances": None, "raised": [], "deadlock": False,
                              "met": False, "hung": False}
# Written to the file descriptor and followed by _exit, so nothing the task code left behind can add a later line.
os.write(1, ("\n" + json.dumps(out) + "\n").encode())
os._exit(0)
