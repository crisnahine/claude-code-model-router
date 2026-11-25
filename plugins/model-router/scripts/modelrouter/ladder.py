"""The ladder: each level is a rung, a model and an effort, from config/models.json.

A level's `needs` is a strength, and its model is the cheapest one at or above it. A model stands
for its lowest rung, or for the rung its named effort reaches. A model weaker than a level needs (the
cap, the allowlist or model health left nothing at or above it) gets one more effort step there,
which makes up part of the loss, up to xhigh."""
from __future__ import annotations

from typing import NamedTuple

from .config import EFFORTS, LEVELS, Level
from .registry import Registry


class Pick(NamedTuple):
    model: str | None
    effort: str | None


def effort(reg: Registry, level: Level) -> str | None:
    """The level's own effort."""
    return reg.levels[level].effort


def model_for(reg: Registry, level: Level) -> str | None:
    """The level's model in the whole registry, before the cap or anything else narrows it."""
    return reg.select(reg.levels[level].needs, reg.by_strength())


def rung(reg: Registry, model: str | None, named_effort: str | None = None) -> Level | None:
    """The level `model` stands for: its lowest rung, or the lowest whose effort reaches `named_effort`
    (Opus at xhigh or max is hardest). A model on no rung stands for the highest level it is strong enough for.
    Rungs are read from the ladder without a project's file, whose raised `needs` would move a model down one."""
    if not model:
        return None
    levels = reg.trusted_levels or reg.levels
    own = [level for level in LEVELS if reg.select(levels[level].needs, reg.by_strength()) == model]
    if not own:
        own = [level for level in LEVELS if levels[level].needs <= reg.strength(model)][-1:] or [LEVELS[0]]
    if named_effort in EFFORTS:
        wanted = EFFORTS.index(named_effort)
        return next((level for level in own if EFFORTS.index(effort(reg, level) or "medium") >= wanted), own[-1])
    return own[0]


def effort_on(reg: Registry, level: Level, model: str | None) -> str | None:
    """The effort `level` gets on `model`: its own, and one step more on a model weaker than it needs."""
    value = effort(reg, level)
    if model and value in EFFORTS and reg.strength(model) < reg.levels[level].needs:
        step = EFFORTS.index(value)
        value = EFFORTS[step + 1] if step < EFFORTS.index("xhigh") else value
    return value


def pick(reg: Registry, level: Level, usable: list[str], lagging: frozenset = frozenset()) -> Pick:
    """The cheapest usable model for `level`, one family up when its alias is in `lagging`, and the
    effort it runs that level at."""
    model = reg.past_lag(reg.select(reg.levels[level].needs, usable), usable, lagging)
    return Pick(model, effort_on(reg, level, model))
