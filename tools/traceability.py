"""Count the requirement statuses in docs/TRACEABILITY.md and print the summary line it must carry.

Run: python3 tools/traceability.py
"""
from __future__ import annotations

import re
from pathlib import Path

STATUSES = ("PASS", "PARTIAL", "FAIL", "BLOCKED")
DOC = Path(__file__).resolve().parent.parent / "docs" / "TRACEABILITY.md"
ROW = re.compile(r"^\|\s*(S\d+\.\d+)\s*\|")


def count(text: str) -> dict[str, int]:
    """Count each requirement row (first cell an ID like S2.1) by the status in its last cell."""
    counts = dict.fromkeys(STATUSES, 0)
    seen = set()
    for line in text.splitlines():
        match = ROW.match(line)
        if not match:
            continue
        row_id = match.group(1)
        status = line.strip().rstrip("|").rsplit("|", 1)[1].strip().strip("*")
        if status not in counts:
            raise ValueError(f"{row_id}: unknown status {status!r}")
        if row_id in seen:
            raise ValueError(f"{row_id}: listed twice")
        seen.add(row_id)
        counts[status] += 1
    return counts


def summary(counts: dict[str, int]) -> str:
    return "Summary: " + ", ".join(f"{counts[s]} {s}" for s in STATUSES) + f", {sum(counts.values())} requirements."


if __name__ == "__main__":
    print(summary(count(DOC.read_text(encoding="utf-8"))))
