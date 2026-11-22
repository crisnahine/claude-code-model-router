"""The ladder: a level, the priority and learned votes give the pick, a model and an effort.

A level's `needs` is a strength, raised one step when votes say the level runs too low.
registry.select turns it into the cheapest usable model at or above it, shifted by the
priority. The effort comes from the level, except that fast keeps only low effort."""
from __future__ import annotations

from typing import Mapping, NamedTuple

from . import registry
from .config import LEVELS


class Pick(NamedTuple):
    model: str | None
    effort: str | None


def pick(config: dict, level: str, priority: str, usable: list[str], raises: Mapping[str, int]) -> Pick:
    """The pick for a call at `level`, before any pin, rule or caller's choice applies.
    `raises` is learn.raises(): the learned raise of each level."""
    spec = (config.get("levels") or {}).get(level) or {}
    needs = float(spec.get("needs") or LEVELS.index(level) + 1) + raises.get(level, 0)
    effort = spec.get("effort")
    if priority == "fast" and effort != "low":
        effort = None  # more thinking is slower; fast keeps only the reductions
    return Pick(registry.select(config, needs, priority, usable), effort)
