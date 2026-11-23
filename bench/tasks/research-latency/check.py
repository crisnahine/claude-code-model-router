"""Pass when RESEARCH.json names the first deploy whose /search p95 passes 300 ms, worked out here from the shipped data, with its commit and p95."""
import csv
import importlib.util
import json
import math
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

SHIPPED = Path(__file__).resolve().parent / "repo"
truth = None
with open(SHIPPED / "deploys.csv", encoding="utf-8") as fh:
    for row in csv.DictReader(fh):
        values = sorted(json.loads((SHIPPED / "metrics" / f"{row['deploy']}.json").read_text())["/search"])
        p95 = values[math.ceil(0.95 * len(values)) - 1]
        if p95 > 300:
            truth = (row["deploy"], row["commit"], p95)
            break
d = passcheck.answer(Path(sys.argv[1]), "RESEARCH.json")
ok = (truth is not None and d.get("deploy") == truth[0] and d.get("commit") == truth[1]
      and passcheck.numeric(d.get("p95_ms")) is not None and abs(passcheck.numeric(d["p95_ms"]) - truth[2]) < 1)
sys.exit(0 if ok else 1)
