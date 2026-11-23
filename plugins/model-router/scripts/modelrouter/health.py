"""What the router has seen of each model: model errors, substitutions and refused rewrites
in; whether a model is healthy, what it last ran as and why it is routed around out.

The record lives in state.json under "health", keyed by model name."""
from __future__ import annotations

import math
import re
import time
from typing import Any, Callable

from . import store

# How long a model is routed around after each run of trouble: 1 day, then 3, then 7.
OUTAGE_DAYS = (1, 3, 7)
MODEL_ERROR = re.compile(
    r"model.{0,80}(?:not[ _]found|not available|unavailable|does not exist|not supported|no access|"
    r"not allowed|invalid|permission)|(?:not_found_error|invalid model|unknown model).{0,80}", re.I | re.S)
REFUSED = "a call rewritten to this model came back without having run"


def _num(value: Any) -> float:
    """A stored number, or 0 when it is not a finite float (junk, nan, inf, or an int too big for one)."""
    try:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return 0
    return value if ok else 0


def _outage_days(info: dict) -> int:
    return OUTAGE_DAYS[min(max(int(_num(info.get("outages"))), 1), len(OUTAGE_DAYS)) - 1]


class Health:
    def __init__(self, table: Any = None) -> None:
        self._table = table if isinstance(table, dict) else {}

    def _info(self, name: str) -> dict:
        info = self._table.get(name)
        return info if isinstance(info, dict) else {}

    def healthy(self, name: str) -> bool:
        info = self._info(name)
        now, until = time.time(), _num(info.get("unavailable_until"))
        latest = now + _outage_days(info) * 86400
        if until > latest:
            # A clock moved back would stretch the outage: it lasts at most its own days from now.
            try:
                info["unavailable_until"] = _clamp(name, latest)
            except OSError:
                info["unavailable_until"] = latest
            until = info["unavailable_until"]
        return until <= now

    def last_ran_as(self, name: str) -> str | None:
        last = self._info(name).get("last_model")
        return last if isinstance(last, str) and last else None

    def why_routed_around(self, name: str) -> str | None:
        if self.healthy(name):
            return None
        info = self._info(name)
        if info.get("substituted"):
            return f"ran as {info.get('substituted_by')} instead"
        return str(info.get("last_error") or "calls failed with a model error")

    def seen_running(self) -> list[str]:
        return [i["last_model"] for i in self._table.values()
                if isinstance(i, dict) and isinstance(i.get("last_model"), str)]


def load() -> Health:
    return Health(store.load_state().get("health"))


def is_model_error(error: object, interrupted: object = False) -> bool:
    return not interrupted and bool(MODEL_ERROR.search(str(error or "")))


def _edit(name: str, change: Callable[[dict], Any]) -> dict:
    result: dict = {}

    def apply(state: dict) -> None:
        table = state.get("health") if isinstance(state.get("health"), dict) else {}
        info = table.get(name) if isinstance(table.get(name), dict) else {}
        change(info)
        table[name] = info
        state["health"] = table
        result.update(info)

    store.update_state(apply)
    return result


def _clamp(name: str, latest: float) -> float:
    """Shorten a far-future outage to `latest`, if it is still that long once the lock is held."""
    kept: list = []

    def change(state: dict) -> None:
        table = state.get("health")
        info = table.get(name) if isinstance(table, dict) else None
        if isinstance(info, dict) and _num(info.get("unavailable_until")) > latest:
            info["unavailable_until"] = latest
        kept.append(_num(info.get("unavailable_until")) if isinstance(info, dict) else 0)

    store.update_state(change)
    return kept[0]


def _route_around(info: dict) -> None:
    if _num(info.get("unavailable_until")) > time.time():
        return  # parallel failures of one outage must not stack into the next, longer one
    info["outages"] = int(_num(info.get("outages"))) + 1
    info["unavailable_until"] = time.time() + _outage_days(info) * 86400


def _strike(info: dict, strikes: int, error: str) -> None:
    info["fail"] = int(_num(info.get("fail"))) + 1
    info["last_error"] = error[:200]
    if info["fail"] >= strikes:
        _route_around(info)


def failed(name: str, error: str, newest: bool = False) -> int:
    """A call on this model failed in a way that points at the model itself. The second
    time, it is routed around; a newest ID gets one strike. After an outage, one more is enough."""
    return int(_num(_edit(name, lambda info: _strike(info, 1 if newest else 2, error)).get("fail")))


def refused(name: str | None) -> None:
    """A rewrite to a model that has never once run here didn't run this time either."""
    if not name:
        return

    def change(info: dict) -> None:
        if not info.get("last_model"):
            _strike(info, 2, REFUSED)
    _edit(name, change)


def ran(name: str | None, ran_as: object, ran_family: str | None, forced: bool, swapped: bool = False) -> None:
    """We asked for `name`; Claude Code reports the model that really ran. Under the force
    setting every agent runs on the forced model, which isn't a substitution, and a run that
    swapped models mid-run says nothing about either model."""
    if forced or swapped or not name or not ran_family:
        return
    if ran_family == name:
        info = load()._info(name)
        if (info.get("last_model") != ran_as or info.get("fail") or info.get("substituted")
                or info.get("outages") or info.get("unavailable_until")):
            _edit(name, lambda i: i.update(last_model=ran_as, fail=0, substituted=0, outages=0, unavailable_until=0))
        return

    def change(info: dict) -> None:
        info["substituted"] = int(_num(info.get("substituted"))) + 1
        info["substituted_by"] = ran_as
        if info["substituted"] >= 2:
            _route_around(info)
    _edit(name, change)


def forget() -> tuple[bool, str]:
    """Drop the whole record. Returns whether one was removed and the state file's status."""
    removed: list = []
    status = store.try_update(store.state_path(), lambda state: removed.append(state.pop("health", None)))[1]
    return bool(removed and removed[0]), status
