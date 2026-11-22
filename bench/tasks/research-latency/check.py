"""Pass when RESEARCH.json names the first deploy whose /search p95 passes 300 ms, worked out here from the shipped data, with its commit and p95."""
import json
import re
import sys
from pathlib import Path


def answer(name):
    try:
        data = json.loads((Path(sys.argv[1]) / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def reasons(data):
    return isinstance(data.get("reasons"), list) and bool(data["reasons"]) and all(
        isinstance(r, str) and r.strip() for r in data["reasons"])


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def numeric(value):
    try:
        return float(value) if not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None
import csv
import math

SHIPPED = Path(__file__).resolve().parent / "repo"
truth = None
with open(SHIPPED / "deploys.csv", encoding="utf-8") as fh:
    for row in csv.DictReader(fh):
        values = sorted(json.loads((SHIPPED / "metrics" / f"{row['deploy']}.json").read_text())["/search"])
        p95 = values[math.ceil(0.95 * len(values)) - 1]
        if p95 > 300:
            truth = (row["deploy"], row["commit"], p95)
            break
d = answer("RESEARCH.json")
ok = (truth is not None and d.get("deploy") == truth[0] and d.get("commit") == truth[1]
      and numeric(d.get("p95_ms")) is not None and abs(numeric(d["p95_ms"]) - truth[2]) < 1)
sys.exit(0 if ok else 1)
