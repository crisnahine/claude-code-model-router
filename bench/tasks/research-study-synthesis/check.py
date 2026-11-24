"""Pass when SYNTHESIS.json names the six supporting studies, excludes only the retracted one and the fluency one, and
reads the size-weighted pool as no clear effect."""
import importlib.util
import re
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

SUPPORTING = {"S01", "S02", "S06", "S08", "S09", "S10"}
EXCLUDED = {"S04": "retracted", "S05": "different_outcome"}
DIRECTION = "no_clear_effect"  # 223.5 / 5,650 pupils = +0.04


def study(value):
    m = re.fullmatch(r"S0?(\d{1,2})", value.strip().upper()) if isinstance(value, str) else None
    return f"S{int(m.group(1)):02d}" if m else None


def verdict(d):
    if (set(d) != {"supporting", "excluded", "direction"} or not isinstance(d["supporting"], list)
            or not isinstance(d["excluded"], dict)):
        return "SYNTHESIS.json is missing or doesn't hold exactly a supporting list, an excluded object and a direction"
    ids = [study(s) for s in [*d["supporting"], *d["excluded"]]]
    if None in ids:
        return "a study id isn't of the form S01"
    supporting = {study(s) for s in d["supporting"]}
    excluded = {study(s): r for s, r in d["excluded"].items()}
    if len(supporting) != len(d["supporting"]) or len(excluded) != len(d["excluded"]) or supporting & set(excluded):
        return "a study is listed twice"
    out = []
    if excluded != EXCLUDED:
        out.append(f"excluded is {dict(sorted(excluded.items()))}, expected {EXCLUDED}")
    if supporting != SUPPORTING:
        out.append(f"supporting is {sorted(supporting)}, expected {sorted(SUPPORTING)}")
    if d["direction"] != DIRECTION:
        out.append(f"direction is {d['direction']!r}, expected {DIRECTION!r}")
    return "; ".join(out)


reason = verdict(passcheck.answer(Path(sys.argv[1]), "SYNTHESIS.json"))
passcheck.finish(not reason, reason)
