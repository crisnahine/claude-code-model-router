"""The current request's record: its level and floor, the model the user named, the model each
call was routed to and the strongest spawn, whether the escalation was asked and whether code changed. The only
module that reads or writes the `prompts` entry for a session.

A new request replaces the whole record, so its runs, routes and strongest spawn start empty.
Order matters: `deliver` marks a message from someone else and never starts a request, and a
delivered message is never passed to `start`, so the user's request, and its level, stay."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import registry, store
from .config import LEVEL_SOURCES


@dataclass(frozen=True)
class Request:
    prompt_id: str | None = None
    level: str | None = None
    need: str | None = None
    below: bool = False
    session_tier: str | None = None
    named: str | None = None
    floor_off: bool = False
    escalation_asked: bool = False
    edited: bool = False
    top_spawn: str | None = None
    calls: dict = field(default_factory=dict)
    routed: dict = field(default_factory=dict)

    @property
    def floor(self) -> str | None:
        """The level strong role agents start no lower than, until a delivered message ends it."""
        return None if self.floor_off else self.level

    def routed_to(self, key: str) -> str | None:
        route = self.routed.get(key)
        model = route.get("model") if isinstance(route, dict) else None
        return model if isinstance(model, str) else None

    def sent_to(self, key: str) -> str | None:
        """The model name or ID the path put on the call, which can be a full ID where the alias lags."""
        route = self.routed.get(key)
        sent = route.get("sent") if isinstance(route, dict) else None
        return sent if isinstance(sent, str) else None

    def routed_by(self, key: str) -> str | None:
        route = self.routed.get(key)
        source = route.get("source") if isinstance(route, dict) else None
        return source if isinstance(source, str) else None

    def run(self, key: str) -> dict | None:
        run = self.calls.get(key)
        return run if isinstance(run, dict) else None


_TEXT = ("prompt_id", "level", "need", "session_tier", "named", "top_spawn")
_FLAGS = ("below", "floor_off", "escalation_asked", "edited")
_MAPS = ("calls", "routed")


def load(session_id: str | None) -> Request:
    entry = store.load_entry("prompts", session_id)
    fields: dict = {k: entry[k] for k in _TEXT if isinstance(entry.get(k), str)}
    fields.update({k: entry[k] for k in _FLAGS if isinstance(entry.get(k), bool)})
    fields.update({k: entry[k] for k in _MAPS if isinstance(entry.get(k), dict)})
    return Request(**fields)


def start(session_id: str | None, *, prompt_id: str | None, level: str | None, need: str | None, below: bool,
          session_tier: str | None, named: str | None) -> None:
    store.save_entry("prompts", session_id, {
        "prompt_id": prompt_id, "level": level, "need": need, "below": below,
        "session_tier": session_tier, "named": named})


def note_edit(session_id: str | None) -> None:
    """Code in the project changed during this request, so a diff of it can be saved."""
    store.update_entry("prompts", session_id, edited=True)


def deliver(session_id: str | None) -> None:
    """A message from someone else arrived: work for it isn't the user's request, so no floor."""
    store.update_entry("prompts", session_id, floor_off=True)


def note_spawn(config: dict, session_id: str | None, key: str, model: str | None, *, level: str | None = None,
               effort: str | None = None, source: str | None = None, sent: str | None = None) -> str | None:
    """Record a spawned call and return the model the router gave it earlier in this request."""
    stood = source in LEVEL_SOURCES and bool(level)
    earlier: list = []

    def update(entry: dict) -> None:
        # Under the lock: calls issued together record at the same moment.
        runs = entry.get("calls") if isinstance(entry.get("calls"), dict) else {}
        before = runs.get(key)
        if isinstance(before, dict) and before.get("source") == "router":
            earlier.append(before.get("model"))
        if stood:
            store.keep(runs, key, {"level": level, "model": model, "effort": effort, "source": source})
            entry["calls"] = runs
        # The model each call runs on, for its result: the result shows the call as issued.
        routes = entry.get("routed") if isinstance(entry.get("routed"), dict) else {}
        store.keep(routes, key, {"model": model, "source": source, **({"sent": sent} if sent else {})})
        entry["routed"] = routes
        if registry.stronger(config, model, entry.get("top_spawn")):
            entry["top_spawn"] = model

    store.mutate_entry("prompts", session_id, update)
    return earlier[0] if earlier else None


def mark_escalation(session_id: str | None, session_tier: str) -> None:
    store.update_entry("prompts", session_id, escalation_asked=True, session_tier=session_tier)
