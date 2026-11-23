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
# None: the wallet isn't in the store yet.
START = {"A": {"w": None}, "B": {"x": 0, "y": 0}, "C": {"w1": 100, "w2": 100}, "D": {"w": 100, "a": 0, "b": 0},
         "E": {"w": 100}, "F": {"w": 100, "v": None}, "G": {"w1": 100, "w2": 100, "w3": 100, "w4": 100},
         "H": {"w": 100, "a": None}}
# What each phase may end with: balances and the exceptions raised. Which of D's two transfers wins isn't fixed, and
# E's transfer of 140 from a wallet holding 100 to itself may raise or, as a no-op, return.
END = {"A": [({"w": 80}, [])], "B": [({"x": 10, "y": 20}, [])], "C": [({"w1": 120, "w2": 80}, [])],
       "D": [({"w": 30, "a": 70, "b": 0}, ["InsufficientFunds"]), ({"w": 30, "a": 0, "b": 70}, ["InsufficientFunds"])],
       "E": [({"w": 100}, ["InsufficientFunds"]), ({"w": 100}, [])], "F": [({"w": 65, "v": 60}, [])],
       "G": [({"w1": 70, "w2": 130, "w3": 80, "w4": 120}, [])], "H": [({"w": 70, "a": 55}, [])]}
MEET = ("B", "G")  # unrelated wallets must reach their reads together


def whole(error, balances, raised):
    return json.dumps({"error": error, "balances": balances, "raised": raised, "deadlock": False, "hung": False},
                      sort_keys=True)


def run_probe(work):
    return passcheck.probe(work, HERE / "probe.py", {"phases": [{"name": n, "balances": b} for n, b in START.items()]})


def wrong_phases(got):
    """The phases whose output isn't right; all of them when the probe's answer is unusable."""
    if not isinstance(got, dict) or set(got) != set(START) or not all(isinstance(g, dict) for g in got.values()):
        return list(START)
    wrong = []
    for name, ends in END.items():
        g = got[name]
        rest = json.dumps({k: v for k, v in g.items() if k != "met"}, sort_keys=True)  # met differs with the fix
        if (type(g.get("met")) is not bool or (name in MEET and g["met"] is not True)
                or rest not in {whole(None, balances, raised) for balances, raised in ends}):
            wrong.append(name)
    return wrong


if __name__ == "__main__":
    sys.exit(0 if wrong_phases(run_probe(Path(sys.argv[1]))) == [] else 1)
