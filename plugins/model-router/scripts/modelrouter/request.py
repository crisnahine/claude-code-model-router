"""The current request's record: its level and floor, the model the user named, the model each
call was routed to and the strongest spawn, whether the escalation was asked and whether code changed. The only
module that reads or writes the `prompts` entry for a session.

A new request replaces the whole record, so its runs, routes and strongest spawn start empty.
Order matters: `deliver` marks a message from someone else and never starts a request, and a
delivered message is never passed to `start`, so the user's request, and its level, stay.

Two readers set the level: UserPromptSubmit starts the request, and the mod's turn.start can
only raise it, from a typed command's rendered body that the first reader may not see."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

from . import ladder, store
from .config import LEVEL_SOURCES, Level
from .intake import Intake
from .registry import Registry

_NEGATION = re.compile(r"(?:\bnot|n't|\bnever|\bwithout|\binstead of|\bavoid|\bno)\s+(?:\w+\s+){0,3}$")


def named_model(reg: Registry, prompt: str) -> str | None:
    """A model the user may be asking for: "use opus", "run this on haiku". These are
    ordinary words too, so this only prompts Claude to decide; it forces nothing."""
    names = "|".join(re.escape(n) for n in reg.models)
    if not names:
        return None
    text = (prompt or "").lower()
    pattern = r"\b(?:use|using|with|on|via|to)\s+(?:the\s+)?(?:claude\s+)?(%s)\b(?!\s*(?:agent|subagent)s?\b)" % names
    for match in re.finditer(pattern, text):
        if not _NEGATION.search(text[max(0, match.start() - 40):match.start()]):
            return match.group(1)
    return None


@dataclass(frozen=True)
class Request:
    prompt_id: str | None = None
    level: Level | None = None
    need: str | None = None
    session_tier: str | None = None
    named: str | None = None
    floor_off: bool = False
    escalation_asked: bool = False
    edited: bool = False
    top_spawn: str | None = None
    calls: dict = field(default_factory=dict)
    routed: dict = field(default_factory=dict)

    @property
    def floor(self) -> Level | None:
        """The level strong role agents start no lower than, until a delivered message ends it."""
        return None if self.floor_off else self.level

    def below(self, reg: Registry, current_model: object) -> bool:
        """Whether the session's model is weaker than this request needs. The model the request
        started on stands in when the current one is unknown, as on a session's first prompt."""
        current = reg.family(current_model) or self.session_tier
        return bool(current and self.need and reg.strength(current) < reg.strength(self.need))

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
_FLAGS = ("floor_off", "escalation_asked", "edited")
_MAPS = ("calls", "routed")


def load(session_id: str | None) -> Request:
    entry = store.load_entry("prompts", session_id)
    fields: dict = {k: entry[k] for k in _TEXT if isinstance(entry.get(k), str)}
    fields["level"] = Level.parse(fields.get("level"))
    fields.update({k: entry[k] for k in _FLAGS if isinstance(entry.get(k), bool)})
    fields.update({k: entry[k] for k in _MAPS if isinstance(entry.get(k), dict)})
    return Request(**fields)


def begin(session_id: str | None, *, prompt_id: str | None, level: str | None, need: str | None,
          session_tier: str | None, named: str | None) -> None:
    store.save_entry("prompts", session_id, {
        "prompt_id": prompt_id, "level": level, "need": need, "session_tier": session_tier, "named": named})


def _need(reg: Registry, level: Level, priority: str, usable: list[str], raises: Mapping[str, int]) -> str | None:
    return ladder.pick(reg, level, priority, usable, raises).model


def start(session_id: str | None, reading: Intake, reg: Registry, session_model: object, usable: list[str],
          raises: Mapping[str, int], *, priority: str = "balanced", prompt_id: str | None = None,
          named: str | None = None) -> Request:
    """The person's new request, at the level of `reading`, with the model that level needs."""
    level = reading.level
    tier = reg.family(session_model)
    need = _need(reg, level, priority, usable, raises) if level else None
    begin(session_id, prompt_id=prompt_id, level=level, need=need, session_tier=tier, named=named)
    return Request(prompt_id=prompt_id, level=level, need=need, session_tier=tier, named=named)


def raise_to(session_id: str | None, reading: Intake, reg: Registry, usable: list[str],
             raises: Mapping[str, int], *, priority: str = "balanced") -> bool:
    """Lift the current request to the level of a turn's reading, and never lower it. A turn can't start a
    request, and only a typed command's reading counts: plain turn text may carry other hooks' context."""
    level = reading.level
    if not (reading.person and reading.command and level):
        return False
    held = load(session_id).level
    if held is None or held >= level:
        return False
    raised: list[bool] = []

    def update(entry: dict) -> None:
        held = Level.parse(entry.get("level"))
        if held is not None and held < level:
            entry["level"], entry["need"] = level, _need(reg, level, priority, usable, raises)
            raised.append(True)

    store.mutate_entry("prompts", session_id, update)
    return bool(raised)


def note_edit(session_id: str | None) -> None:
    """Code in the project changed during this request, so a diff of it can be saved."""
    store.update_entry("prompts", session_id, edited=True)


def deliver(session_id: str | None) -> None:
    """A message from someone else arrived: work for it isn't the user's request, so no floor."""
    store.update_entry("prompts", session_id, floor_off=True)


def note_spawn(reg: Registry, session_id: str | None, key: str, model: str | None, *, level: str | None = None,
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
        if reg.stronger(model, entry.get("top_spawn")):
            entry["top_spawn"] = model

    store.mutate_entry("prompts", session_id, update)
    return earlier[0] if earlier else None


def mark_escalation(session_id: str | None, session_tier: str) -> None:
    store.update_entry("prompts", session_id, escalation_asked=True, session_tier=session_tier)
