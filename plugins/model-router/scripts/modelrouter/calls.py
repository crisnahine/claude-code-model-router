"""What happened to each call: where it was routed and what came back. A spawn writes a Route
on either path; a result, a failure or a stop comes back as an Outcome, and one rule set turns
it into the done record, the health evidence, the ledger row and the count taken back.
The only module that reads or writes those records.

Per session, `routes` holds a Route per spawn, and `launched` and `done` hold the per-call
records of runs waiting for their agent and of finished runs. The request's own record is
`request.py`'s."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from . import health, learn, ledger, request, store
from .config import LEVEL_SOURCES, Level
from .registry import Registry

# Without a request id to compare, a call seen again within this long counts as a redo.
REDO_WINDOW = 30 * 60

_MODEL_ID = re.compile(r"[A-Za-z0-9._:/\[\]-]{1,100}")
MAX_SWAPPED = 5

# What a returned outcome says Claude should be told about.
SWAPPED = "swapped"  # the run changed models mid-run
ID_FAILED = "id-failed"  # the full model ID the mod sent was refused
MODEL_FAILED = "model-failed"  # a model error struck the model
ROUTED_AROUND = "routed-around"  # ... and it is now routed around


@dataclass(frozen=True)
class Route:
    """Where one spawn went, written by `spawn.record` on either path. `sent` is the name or ID the
    path put on the call, a full ID where the alias lags; `was` is the model the call would have run on
    untouched (the parent's, or the agent's pin); `rewrote` says the decision changed the call; `counted` is the level the spawn counted toward (`learn.counted`), so a run that swapped models
    can take its own count back."""
    model: str | None
    sent: str | None
    level: Level | None
    effort: str | None
    source: str | None
    path: str
    counted: Level | None = None
    prompt_id: str | None = None
    at: float = 0.0
    rewrote: bool = False
    was: str | None = None

    @property
    def leveled(self) -> bool:
        """The call ran at the level the router gave it: the router, an agent's pin or a rule chose its model."""
        return self.level is not None and self.source in LEVEL_SOURCES

    @property
    def redoable(self) -> bool:
        """A result finishes the call for a redo to climb from when the run was leveled, or when the router
        rewrote the call, as it does for a model the caller named that the cap lowered."""
        return self.leveled or (self.level is not None and self.rewrote)

    @property
    def run(self) -> dict:
        return {"level": self.level.value if self.level else None, "model": self.model, "effort": self.effort}


@dataclass(frozen=True)
class Outcome:
    """A result, a failure or a stop came back. `named` is the model the call itself named, as a
    registry name; `models_used` is empty for a run that didn't swap and for a background launch."""
    kind: Literal["result", "failure", "stop"]
    key: str = ""
    agent: str = ""
    spawn_id: str | None = None
    agent_id: str | None = None
    named: str | None = None
    ran_as: object = None
    ran_family: str | None = None
    forced: bool = False
    status: object = None
    tokens: object = None
    launched: bool = False
    models_used: tuple = ()
    error: str = ""
    interrupted: bool = False
    answered: bool = True


@dataclass(frozen=True)
class Returned:
    """What an outcome came to: the model the call was meant to run on, its route, whether it
    finished the call, and what Claude should be told (`note`, one of the constants above)."""
    asked: str | None
    route: Route | None
    finished: bool
    note: str | None = None


def identify(tool_input: dict) -> tuple[str, str]:
    """The call's agent, and a key that recognizes the same call issued again."""
    agent = tool_input.get("subagent_type")
    agent = agent if isinstance(agent, str) and agent else "general-purpose"
    text = agent + "\0" + str(tool_input.get("prompt") or "")
    return agent, hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def models_used(response: object) -> list[str]:
    """The model ids a run switched between, in order, or [] when it didn't switch. The engine's
    list is untrusted: only well-formed ids count, and a swap needs two different ones."""
    used = response.get("modelsUsed") if isinstance(response, dict) else None
    if not isinstance(used, list):
        return []
    ids = [m for m in used if isinstance(m, str) and _MODEL_ID.fullmatch(m)][:MAX_SWAPPED]
    return ids if len(set(ids)) >= 2 else []


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _route(record: object) -> Route | None:
    if not isinstance(record, dict):
        return None
    at = record.get("at")
    return Route(model=_text(record.get("model")), sent=_text(record.get("sent")),
                 level=Level.parse(record.get("level")), effort=_text(record.get("effort")),
                 source=_text(record.get("source")), path=_text(record.get("path")) or "",
                 counted=Level.parse(record.get("counted")), prompt_id=_text(record.get("prompt_id")),
                 at=float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else 0.0,
                 rewrote=record.get("rewrote") is True, was=_text(record.get("was")))


def _record(route: Route, key: str) -> dict:
    record = {"model": route.model, "sent": route.sent, "level": route.level.value if route.level else None,
              "effort": route.effort, "source": route.source, "path": route.path,
              "counted": route.counted.value if route.counted else None, "prompt_id": route.prompt_id,
              "rewrote": route.rewrote or None, "was": route.was, "call": key}
    return {k: v for k, v in record.items() if v is not None}


def _latest(routes: dict, key: str) -> list[dict]:
    """The records of this call, newest first."""
    found = [r for r in routes.values() if isinstance(r, dict) and r.get("call") == key]
    return sorted(found, key=lambda r: store.stamp(r.get("at")), reverse=True)


def routed(session_id: str | None, key: str, route: Route, spawn_id: str | None = None) -> Route | None:
    """Write where a spawn went, under its own id when the path has one (a copy of the same call can't
    overwrite it) and else under the call's key. Returns the route this call had earlier in the request
    and ran at its level, which the votes compare with."""
    earlier: list[Route] = []

    def update(entry: dict) -> None:
        # Under the lock: calls issued together record at the same moment.
        for record in _latest(entry, key):
            before = _route(record)
            if before and before.leveled and _same_request(before.prompt_id, before.at, route.prompt_id):
                earlier.append(before)
                break
        store.keep(entry, spawn_id or key, _record(route, key))

    store.mutate_entry("routes", session_id, update)
    return earlier[0] if earlier else None


def route_of(session_id: str | None, key: str, spawn_id: str | None = None) -> Route | None:
    """The route of this spawn, else the call's latest one: a path that sent no spawn id still finds it."""
    entry = store.load_entry("routes", session_id)
    record = entry.get(spawn_id) if spawn_id else None
    if not isinstance(record, dict):
        found = _latest(entry, key)
        record = found[0] if found else None
    return _route(record)


def strongest(reg: Registry, session_id: str | None) -> str | None:
    """The strongest model any spawn of the current request ran on, for the Stop check. The routes
    already hold every spawn's model and request, so it needs no record of its own."""
    current = request.load(session_id)
    ran = [r.model for r in map(_route, store.load_entry("routes", session_id).values())
           if r and r.model and _in(current, r)]
    return max(ran, key=reg.strength, default=None)


def _in(current: request.Request, route: Route) -> bool:
    """The route was written during this request: the same request id, else after it started."""
    return route.prompt_id == current.prompt_id if current.prompt_id else route.at >= current.started


def returned(session_id: str | None, outcome: Outcome) -> Returned:
    """A result, a failure or a stop came back. One rule set, whichever path routed the call:
    a result that is neither a background launch nor a swapped run finishes the call; a launch waits
    for its agent to stop; a stop with an answer finishes it; a model error strikes the ID the spawn
    sent, or else the model it was routed to."""
    if outcome.kind == "stop":
        return _stopped(session_id, outcome)
    route = route_of(session_id, outcome.key, outcome.spawn_id)
    asked = route.model if route and route.model else outcome.named
    if outcome.kind == "failure":
        return _failed(session_id, outcome, route, asked)
    swapped = bool(outcome.models_used)
    finished = not outcome.launched and not swapped
    try:
        if route and route.redoable and (finished or (outcome.launched and outcome.agent_id)):
            _wait_or_finish(session_id, outcome, route, finished)
    except OSError:
        pass
    try:
        health.ran(asked, outcome.ran_as, outcome.ran_family, outcome.forced, swapped=swapped,
                   untouched=route.was if route and route.path == "mod" else None)
    except OSError:
        pass
    ledger.result(session_id, outcome.agent, asked, outcome.ran_as, outcome.status, outcome.tokens,
                  models_used=list(outcome.models_used) or None)
    if swapped:
        learn.swapped(route)
    return Returned(asked, route, finished, SWAPPED if swapped else None)


def _wait_or_finish(session_id: str | None, outcome: Outcome, route: Route, finished: bool) -> None:
    if finished:
        run = {**route.run, "prompt_id": request.load(session_id).prompt_id}
        store.mutate_entry("done", session_id, lambda done: store.keep(done, outcome.key, run))
    else:
        run = {**route.run, "prompt_id": request.load(session_id).prompt_id, "key": outcome.key}
        store.mutate_entry("launched", session_id, lambda waiting: store.keep(waiting, outcome.agent_id, run))


def _failed(session_id: str | None, outcome: Outcome, route: Route | None, asked: str | None) -> Returned:
    if not asked or not health.is_model_error(outcome.error, outcome.interrupted):
        return Returned(asked, route, False)
    sent = route.sent if route and route.model == asked else None
    if sent and sent.lower() != asked:
        # The path sent the family's newest ID and the provider refused it: the ID is routed around,
        # and the alias runs until it is tried again.
        health.failed(sent, outcome.error, newest=True)
        ledger.model_error(session_id, outcome.agent, sent)
        return Returned(asked, route, False, ID_FAILED)
    fails = health.failed(asked, outcome.error)
    ledger.model_error(session_id, outcome.agent, asked)
    return Returned(asked, route, False, ROUTED_AROUND if fails >= 2 else MODEL_FAILED)


def _stopped(session_id: str | None, outcome: Outcome) -> Returned:
    """An agent stopped. A background launch waiting under its id is finished now, when it stopped
    with an answer. Foreground agents finished at their result and aren't waiting."""
    agent_id = outcome.agent_id
    if not isinstance(agent_id, str) or agent_id not in store.load_entry("launched", session_id):
        return Returned(None, None, False)
    run: dict = {}

    def take(launched: dict) -> None:
        waiting = launched.pop(agent_id, None)
        run.update(waiting if isinstance(waiting, dict) else {})

    store.mutate_entry("launched", session_id, take)
    finished = outcome.answered and Level.parse(run.get("level")) is not None and isinstance(run.get("key"), str)
    if finished:
        done = {k: run.get(k) for k in ("level", "model", "effort", "prompt_id")}
        store.mutate_entry("done", session_id, lambda records: store.keep(records, run["key"], done))
    return Returned(run.get("model") if isinstance(run.get("model"), str) else None, None, finished)


def finished_run(session_id: str | None, key: str) -> dict | None:
    """The run of this call that finished earlier in the current request: the same request
    id, or within REDO_WINDOW when there is none."""
    run = store.load_entry("done", session_id).get(key)
    if not isinstance(run, dict) or Level.parse(run.get("level")) is None:
        return None
    return run if _same_request(run.get("prompt_id"), run.get("at"), request.load(session_id).prompt_id) else None


def _same_request(prompt_id: object, at: object, current: object) -> bool:
    """A record belongs to the current request when their ids match, and with no id to compare,
    when it is younger than REDO_WINDOW. A clock moved back makes its age negative, which is unknown."""
    if current:
        return prompt_id == current
    return 0 <= store.now() - (at if isinstance(at, (int, float)) else 0) < REDO_WINDOW
