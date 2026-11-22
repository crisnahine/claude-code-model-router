"""Feedback. When work routed at some level keeps being redone on a stronger model,
that level is raised one step from then on. When a raised level keeps being given a
weaker model than the raise picks, the raise is undone.

What counts as a vote that a level was routed too low:
  * Claude re-issues a call the router routed, this time naming a stronger model (2 votes)
  * Claude names a stronger model than the router would have picked (1 vote)
A level changes once it has 6 votes that amount to 30% of its routed calls. Feedback
never takes a level below what ships. This is a simple rule on real outcomes, not a
measurement of answer quality."""
from __future__ import annotations

from pathlib import Path

from . import store
from .config import LEVELS

VOTES_NEEDED = 6
SHARE_NEEDED = 0.3
MAX_BUMP = 1


def _path() -> Path:
    return store.data_dir() / "learned.json"


def stats() -> dict:
    return store.load_dict(_path())


def bump(level: str) -> int:
    info = stats().get(level)
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


def record_call(level: str) -> None:
    _update(level, calls=1)


def vote_up(level: str, weight: int = 1) -> None:
    _update(level, up=weight)


def vote_down(level: str) -> None:
    if bump(level) > 0:
        _update(level, down=1)


def reset() -> None:
    try:
        _path().unlink()
    except OSError:
        pass
