"""The ladder: a level, the priority and learned votes give the pick, a model and an effort.

A level's `needs` is a strength, raised one step when votes say the level runs too low.
registry.select turns it into the cheapest usable model at or above it, shifted by the
priority. The effort comes from the level, except that fast keeps only low effort, and a pick
weaker than the level's own `needs` (the cap, the allowlist or model health left nothing at or
above it) gets one more effort step, at most xhigh, so a missing model costs effort and not quality."""
from __future__ import annotations

from typing import Mapping, NamedTuple

from . import registry
from .config import EFFORTS, LEVELS


class Pick(NamedTuple):
    model: str | None
    effort: str | None


def effort(config: dict, level: str, priority: str) -> str | None:
    """The level's own effort, which a pin, a rule or a caller's model gets."""
    value = ((config.get("levels") or {}).get(level) or {}).get("effort")
    if priority == "fast" and value != "low":
        return None  # more thinking is slower; fast keeps only the reductions
    return value


def pick(config: dict, level: str, priority: str, usable: list[str], raises: Mapping[str, int]) -> Pick:
    """The pick for a call at `level`, before any pin, rule or caller's choice applies.
    `raises` is learn.raises(): the learned raise of each level."""
    spec = (config.get("levels") or {}).get(level) or {}
    needs = float(spec.get("needs") or LEVELS.index(level) + 1)
    model = registry.select(config, needs + raises.get(level, 0), priority, usable)
    value = effort(config, level, priority)
    if model and priority != "fast" and value in EFFORTS and registry.strength(config, model) < needs:
        step = EFFORTS.index(value)
        value = EFFORTS[step + 1] if step < EFFORTS.index("xhigh") else value
    return Pick(model, value)
