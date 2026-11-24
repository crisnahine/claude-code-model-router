"""Pass when TRIAGE.json gives each of the 30 messages the priority and owner the policy's rules settle on."""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

WANT = dict(zip([f"M{n:02d}" for n in range(1, 31)], (
    "P1 engineering", "P2 billing", "P4 none", "P3 support", "P1 security", "P3 security", "P1 security", "P1 legal",
    "P1 legal", "P2 legal", "P4 sales", "P3 sales", "P2 billing", "P2 engineering", "P2 engineering", "P3 support",
    "P1 security", "P3 support", "P4 none", "P3 legal", "P1 billing", "P2 engineering", "P1 security", "P2 billing",
    "P1 security", "P2 billing", "P4 sales", "P1 billing", "P1 security", "P4 none")))


def verdict(d):
    if set(d) != set(WANT):
        return f"TRIAGE.json is missing or its keys aren't exactly the {len(WANT)} message ids"
    wrong = []
    for mid, want in WANT.items():
        item = d[mid]
        if not isinstance(item, dict) or set(item) != {"priority", "owner"}:
            return f"{mid} isn't exactly a priority and an owner"
        got = f"{item['priority']} {item['owner']}"
        if got != want:
            wrong.append(f"{mid} is {got}, expected {want}")
    return "; ".join(wrong[:3]) + (f" and {len(wrong) - 3} more" if len(wrong) > 3 else "")


reason = verdict(passcheck.answer(Path(sys.argv[1]), "TRIAGE.json"))
passcheck.finish(not reason, reason)
