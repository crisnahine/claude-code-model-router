"""Pass when DESIGN.json sizes partitions for peak and for catching up a 3-day replay after growth, as a
multiple of 6 inside the cluster's room, keys by order, replicates 3 times and keeps at least 3 days that fit 16 TB."""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

AVERAGE = 9000 * 2
PEAK = AVERAGE * 3
CATCH_UP = AVERAGE * 3 + AVERAGE  # 3 days of backlog worked off in 1 day, plus live traffic
LOW, HIGH = max(-(-PEAK // 2500), -(-CATCH_UP // 2500)), 2000 - 1960
d = passcheck.answer(Path(sys.argv[1]), "DESIGN.json")
rf = d.get("replication_factor")
hours = d.get("retention_hours")
ok = (set(d) == {"partitions", "key", "retention_hours", "replication_factor", "reasons"}
      and type(d.get("partitions")) is int and LOW <= d["partitions"] <= HIGH and d["partitions"] % 6 == 0
      and d.get("key") == "order_id" and type(rf) is int and rf == 3 and passcheck.number(hours)
      and 72 <= hours and AVERAGE * 1000 * rf * hours * 3600 <= 16 * 10 ** 12 and passcheck.reasons(d))
sys.exit(0 if ok else 1)
