"""The subtask profiler against a labeled corpus of Agent briefs. Run: python3 -m unittest discover -s tests

Each brief in tests/fixtures/routing-corpus.json carries the lowest level it needs and the highest that is still a
sensible spend, set by the rules in that file before the profiler was run on it. A brief marked known_under routes
below its lowest today; the mark names the gap, and comes off when a fix closes it. A brief marked defect fixes or
hunts a bug, so it routes at complex or above unless its agent's name caps it.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "model-router" / "scripts"))
from modelrouter import profile  # noqa: E402
from modelrouter.config import LEVELS  # noqa: E402

CORPUS = json.loads((ROOT / "tests" / "fixtures" / "routing-corpus.json").read_text(encoding="utf-8"))["briefs"]
# 2 of 250 briefs over-routed on 2026-11-23. The margin leaves room for one-level over-routes that closing an
# under-route may cost; raise it only with the briefs that need it named in the commit.
OVER_ROUTE_BUDGET = 0.02


def routed(brief: dict) -> int:
    level = profile.profile_call({"description": brief["description"], "prompt": brief["prompt"]}, brief["agent"])
    return LEVELS.index(level["level"])


def show(briefs: list[dict]) -> str:
    return "\n".join(f"  {LEVELS[routed(b)]} for {b['lowest']}..{b['highest']}: {b['prompt']}" for b in briefs)


class RoutingCorpus(unittest.TestCase):
    def test_the_corpus_covers_every_level(self):
        self.assertGreaterEqual(len(CORPUS), 150)
        for level in LEVELS:
            self.assertGreaterEqual(sum(level in (b["lowest"], b["highest"]) for b in CORPUS), 8, level)
        self.assertGreaterEqual(sum(not b["prompt"].isascii() or "é" in b["prompt"] for b in CORPUS), 5)

    def test_no_brief_routes_below_its_lowest_level(self):
        under = [b for b in CORPUS if routed(b) < LEVELS.index(b["lowest"])]
        new = [b for b in under if not b.get("known_under")]
        fixed = [b for b in CORPUS if b.get("known_under") and b not in under]
        if new:
            self.fail("routed below their lowest level:\n" + show(new))
        if fixed:
            self.fail("route right now; remove their known_under mark:\n" + show(fixed))

    def test_defect_briefs_route_at_complex_or_above(self):
        marked = [b for b in CORPUS if b.get("defect")]
        self.assertGreaterEqual(len(marked), 12)
        low = [b for b in marked if routed(b) < LEVELS.index("complex")]
        if low:
            self.fail("fix or hunt a defect but route below complex:\n" + show(low))

    def test_over_routes_stay_rare_and_one_level(self):
        over = [b for b in CORPUS if routed(b) > LEVELS.index(b["highest"])]
        print(f"\nrouting corpus: {len(over)} of {len(CORPUS)} briefs over-routed ({len(over) / len(CORPUS):.1%}), "
              f"budget {OVER_ROUTE_BUDGET:.0%}; "
              f"{sum(bool(b.get('known_under')) for b in CORPUS)} known under-routes")
        far = [b for b in over if routed(b) > LEVELS.index(b["highest"]) + 1]
        if far:
            self.fail("routed two or more levels too high:\n" + show(far))
        if len(over) / len(CORPUS) > OVER_ROUTE_BUDGET:
            self.fail(f"over-route rate above {OVER_ROUTE_BUDGET:.0%}:\n" + show(over))


if __name__ == "__main__":
    unittest.main()
