"""Feedback from what Claude redoes. A level whose routed calls keep being redone or
overruled with a stronger model is raised one step, and a raise that keeps being
overruled downwards is undone. The votes are counted from outcomes; answer quality
itself is never measured."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from . import store
from .config import LEVELS, Level
from .registry import Registry

if TYPE_CHECKING:
    from .calls import Route

VOTES_NEEDED = 6
SHARE_NEEDED = 0.3
MAX_BUMP = 1


def _path() -> Path:
    return store.data_dir() / "learned.json"


def stats() -> dict:
    return store.load_state_file(_path())


def raises() -> dict[str, int]:
    """The learned raise of every level, read once."""
    data = stats()
    return {level: _raise(data.get(level)) for level in LEVELS}


def _raise(info: object) -> int:
    try:
        return max(0, min(MAX_BUMP, int(info.get("bump") or 0))) if isinstance(info, dict) else 0
    except (TypeError, ValueError):
        return 0


def _tally(data: dict, level: str, calls: int = 0, up: int = 0, down: int = 0) -> None:
    info = data.get(level) if isinstance(data.get(level), dict) else {}
    for field, amount in (("calls", calls), ("votes", up), ("relax", down)):
        info[field] = max(0, int(info.get(field) or 0) + amount)
    raised = int(info.get("bump") or 0)
    enough = max(VOTES_NEEDED, SHARE_NEEDED * info["calls"])
    if raised < MAX_BUMP and info["votes"] >= enough:
        info.update(bump=raised + 1, calls=0, votes=0, relax=0)
    elif raised > 0 and info["relax"] >= enough:
        info.update(bump=raised - 1, calls=0, votes=0, relax=0)
    data[level] = info


def _update(changes: list[tuple[object, dict[str, int]]]) -> None:
    """Apply every (level, {"calls" | "up" | "down": amount}) in one locked write."""
    found = []
    for level, amounts in changes:
        parsed = Level.parse(level)
        if parsed is not None:
            found.append((parsed.value, amounts))  # a plain str key: the state file sorts its keys

    def change(data: dict) -> None:
        for level, amounts in found:
            _tally(data, level, **amounts)

    if found:
        try:
            store.update_dict(_path(), change)
        except OSError:
            pass


SUBAGENT = "subagent"


def _source(decision: Mapping) -> str | None:
    """A call the repeat guard passed through untouched: neither the pick nor the level applied."""
    return None if decision["repeat_of"] else decision["source"]


def counted(decision: Mapping, kind: str) -> Level | None:
    """The level this spawn counts toward the vote threshold, or None. Only a subagent the router
    gave its model counts: a workflow stage or a teammate is never redone on Claude's judgment."""
    return Level.parse(decision["level"]) if kind == SUBAGENT and _source(decision) == "router" else None


def spawned(reg: Registry, decision: Mapping, kind: str, earlier: Route | None, user_named: bool,
            raised: Mapping[str, int] | None = None) -> Level | None:
    """Count one spawn and cast the votes its facts carry, and return the level it counted toward.
    `earlier` is the route this call had before in the request. A call on the model the user named in
    the prompt carries no caller votes: the user chose it, not Claude. A redo votes that its first
    level was too low only when the router chose the model: a pin, a rule or a named model decided
    it otherwise."""
    level = Level.parse(decision["level"])
    if kind != SUBAGENT or level is None:
        return None
    source = _source(decision)
    counts = counted(decision, kind)
    changes: list[tuple[object, dict[str, int]]] = [(counts, {"calls": 1})] if counts else []
    if source == "router" and decision["escalated_from"]:
        changes.append((decision["escalated_from"], {"up": 1}))
    if source == "caller" and not user_named:
        pick, runs_on = decision["pick"], decision["model"] or decision["was"]
        # The same call routed weaker earlier in this request counts double: it was redone.
        if earlier and earlier.source == "router" and reg.stronger(runs_on, earlier.model):
            changes.append((level, {"up": 2}))
        elif reg.stronger(runs_on, pick):
            changes.append((level, {"up": 1}))
        elif reg.stronger(pick, runs_on) and (raises() if raised is None else raised).get(level, 0) > 0:
            changes.append((level, {"down": 1}))
    _update(changes)
    return counts


def swapped(route: Route | None) -> None:
    """A run that swapped models mid-run is no result for its level: take back the count it made."""
    if route and route.counted:
        _update([(route.counted, {"calls": -1})])


def reset() -> str:
    """Forget what was learned: removed, missing, or left as newer or unreadable."""
    return store.remove_state_file(_path())
