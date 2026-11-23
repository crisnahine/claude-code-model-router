"""Pass when ledger/wallet.py, run by two worker copies against a store that forces each bad interleaving and times out
a lock held for 5 s, loses no credit, takes no wallet below zero, hangs nowhere and never makes unrelated wallets wait.
Each copy is a fresh load of every work-dir module, so a lock inside the module shares nothing across them."""
import importlib.util
import json
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

HERE = Path(__file__).resolve().parent
START = {"A": {"w": 0}, "B": {"x": 0, "y": 0}, "C": {"w1": 100, "w2": 100}, "D": {"w": 100, "a": 0, "b": 0},
         "E": {"w": 100}, "F": {"w": 100, "v": 0}}
# What each phase may end with: balances and the exceptions raised. Which of D's two transfers wins isn't fixed.
END = {"A": [({"w": 80}, [])], "B": [({"x": 10, "y": 20}, [])], "C": [({"w1": 120, "w2": 80}, [])],
       "D": [({"w": 30, "a": 70, "b": 0}, ["InsufficientFunds"]), ({"w": 30, "a": 0, "b": 70}, ["InsufficientFunds"])],
       "E": [({"w": 100}, ["InsufficientFunds"])], "F": [({"w": 65, "v": 60}, [])]}


def whole(error, balances, raised):
    return json.dumps({"error": error, "balances": balances, "raised": raised, "deadlock": False, "hung": False},
                      sort_keys=True)


def passes(work):
    got = passcheck.probe(work, HERE / "probe.py", {"phases": [{"name": n, "balances": b} for n, b in START.items()]})
    if not isinstance(got, dict) or set(got) != set(START) or not all(isinstance(g, dict) for g in got.values()):
        return False
    for name, ends in END.items():
        g = got[name]
        if type(g.get("met")) is not bool:
            return False
        rest = json.dumps({k: v for k, v in g.items() if k != "met"}, sort_keys=True)  # met differs with the fix
        if rest not in {whole(None, balances, raised) for balances, raised in ends}:
            return False
    return got["B"]["met"] is True


sys.exit(0 if passes(Path(sys.argv[1])) else 1)
