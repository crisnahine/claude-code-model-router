"""Feedback from what Claude redoes. A level whose routed calls keep being redone or
overruled with a stronger model is raised one step, and a raise that keeps being
overruled downwards is undone. The votes are counted from outcomes; answer quality
itself is never measured."""
from __future__ import annotations

from pathlib import Path

from . import registry, store
from .config import LEVEL_SOURCES, LEVELS

VOTES_NEEDED = 6
SHARE_NEEDED = 0.3
MAX_BUMP = 1


def _path() -> Path:
    return store.data_dir() / "learned.json"


def stats() -> dict:
    return store.load_dict(_path())


def raises() -> dict[str, int]:
    """The learned raise of every level, read once."""
    data = stats()
    return {level: _raise(data.get(level)) for level in LEVELS}


def _raise(info: object) -> int:
    try:
        return max(0, min(MAX_BUMP, int(info.get("bump") or 0))) if isinstance(info, dict) else 0
    except (TypeError, ValueError):
        return 0


def _update(level: str, calls: int = 0, up: int = 0, down: int = 0) -> None:
    if level not in LEVELS:
        return

    def change(data: dict) -> None:
        info = data.get(level) if isinstance(data.get(level), dict) else {}
        for field, amount in (("calls", calls), ("votes", up), ("relax", down)):
            info[field] = int(info.get(field) or 0) + amount
        raised = int(info.get("bump") or 0)
        enough = max(VOTES_NEEDED, SHARE_NEEDED * info["calls"])
        if raised < MAX_BUMP and info["votes"] >= enough:
            info.update(bump=raised + 1, calls=0, votes=0, relax=0)
        elif raised > 0 and info["relax"] >= enough:
            info.update(bump=raised - 1, calls=0, votes=0, relax=0)
        data[level] = info

    try:
        store.update_dict(_path(), change)
    except OSError:
        pass


def decided(config: dict, *, source: str | None, level: str | None, pick: str | None, runs_on: str | None,
            raised: dict | None = None, earlier: str | None = None, escalated_from: str | None = None,
            user_named: bool = False) -> None:
    """Count one spawn and cast the votes its facts carry. `source` is None for a call
    that ran untouched. A call on the model the user named in the prompt carries no
    caller votes: the user chose it, not Claude."""
    if not level:
        return
    if source == "router":
        _update(level, calls=1)
    if source in LEVEL_SOURCES and escalated_from:
        _update(escalated_from, up=1)
    if source != "caller" or user_named:
        return
    # The same call routed weaker earlier in this request counts double: it was redone.
    if earlier and registry.stronger(config, runs_on, earlier):
        _update(level, up=2)
    elif registry.stronger(config, runs_on, pick):
        _update(level, up=1)
    elif registry.stronger(config, pick, runs_on) and (raises() if raised is None else raised).get(level, 0) > 0:
        _update(level, down=1)


def reset() -> None:
    try:
        _path().unlink()
    except OSError:
        pass
