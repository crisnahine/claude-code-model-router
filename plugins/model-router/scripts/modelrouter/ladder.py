"""The ladder: a level, the priority and learned votes give the pick, a model and an effort.

A level's `needs` is a strength, raised one step when votes say the level runs too low.
Registry.select turns it into the cheapest usable model at or above it, shifted by the
priority. The effort comes from the level, except that fast keeps only low effort, and a pick
weaker than the level's own `needs` (the cap, the allowlist or model health left nothing at or
above it) gets one more effort step, at most xhigh, which makes up part of the loss."""
from __future__ import annotations

from typing import Mapping, NamedTuple

from .config import EFFORTS, Level
from .registry import Registry


class Pick(NamedTuple):
    model: str | None
    effort: str | None


def effort(reg: Registry, level: Level, priority: str) -> str | None:
    """The level's own effort, which a pin, a rule or a caller's model gets."""
    value = reg.levels[level].effort
    if priority == "fast" and value != "low":
        return None  # more thinking is slower; fast keeps only the reductions
    return value


def effort_on(reg: Registry, level: Level, priority: str, model: str | None) -> str | None:
    """The effort `level` gets on `model`: its own, and one step more (at most xhigh) on a model weaker than it needs."""
    value = effort(reg, level, priority)
    if model and priority != "fast" and value in EFFORTS and reg.strength(model) < reg.levels[level].needs:
        step = EFFORTS.index(value)
        value = EFFORTS[step + 1] if step < EFFORTS.index("xhigh") else value
    return value


def pick(reg: Registry, level: Level, priority: str, usable: list[str], raises: Mapping[str, int]) -> Pick:
    """The pick for a call at `level`, before any pin, rule or caller's choice applies.
    `raises` is learn.raises(): the learned raise of each level."""
    model = reg.select(reg.levels[level].needs + raises.get(level, 0), priority, usable)
    return Pick(model, effort_on(reg, level, priority, model))
