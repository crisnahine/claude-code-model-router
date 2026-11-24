"""What happened to each call: where it was routed and what came back. A spawn writes a Route
on either path; a result, a failure or a stop comes back as an Outcome, and one rule set turns
it into the done record, the health evidence and the ledger row.
The only module that reads or writes those records.

Per session, `routes` holds a Route per spawn, `launched` the runs waiting for their agent, and
`done` the latest finished run of each task, which a marked redo climbs from."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from . import health, ledger, store
from .config import Level

# A marked redo climbs from a run of its task that finished at most this long ago.
REDO_WINDOW = 30 * 60
# A session that wrote no route, launch or finished run for this long is dropped from those files: each spawn
# rewrites them whole under a lock it waits 1 s for, so old sessions there cost lost writes.
KEPT_FOR = 24 * 3600

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
    untouched (the parent's, or the agent's pin)."""
    model: str | None
    sent: str | None
    level: Level | None
    effort: str | None
    source: str | None
    path: str
    at: float = 0.0
    was: str | None = None

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
    task: str | None = None  # calls.task: what a marked redo finds this run by
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


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def identify(tool_input: dict) -> tuple[str, str]:
    """The call's agent, and a key that recognizes the same call issued again."""
    agent = tool_input.get("subagent_type")
    agent = agent if isinstance(agent, str) and agent else "general-purpose"
    return agent, _digest(agent + "\0" + str(tool_input.get("prompt") or ""))


_REDO_MARK = re.compile(r"\s*redo:", re.IGNORECASE)


def marked_redo(tool_input: dict) -> bool:
    """Claude rewrites a brief it sends again, so it marks the redo where the text stays its own:
    a `description` that starts with "Redo:"."""
    return bool(_REDO_MARK.match(str(tool_input.get("description") or "")))


def task(tool_input: dict) -> str | None:
    """A key for the work a call does, whatever its prompt says: a hash of its agent and its description
    without the redo mark, so no description is kept. None when the description is empty, since nothing
    then tells one task from another."""
    described = str(tool_input.get("description") or "")
    while _REDO_MARK.match(described):
        described = described[_REDO_MARK.match(described).end():]
    described = " ".join(described.lower().split())
    return _digest(identify(tool_input)[0] + "\0" + described) if described else None


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
                 at=float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else 0.0,
                 was=_text(record.get("was")))


def _record(route: Route, key: str) -> dict:
    record = {"model": route.model, "sent": route.sent, "level": route.level.value if route.level else None,
              "effort": route.effort, "source": route.source, "path": route.path, "was": route.was, "call": key}
    return {k: v for k, v in record.items() if v is not None}


def _latest(routes: dict, key: str) -> list[dict]:
    """The records of this call, newest first."""
    found = [r for r in routes.values() if isinstance(r, dict) and r.get("call") == key]
    return sorted(found, key=lambda r: store.stamp(r.get("at")), reverse=True)


def _mutate(name: str, session_id: str | None, change) -> None:
    store.mutate_entry(name, session_id, change, max_age=KEPT_FOR)


def routed(session_id: str | None, key: str, route: Route, spawn_id: str | None = None) -> None:
    """Write where a spawn went, under its own id when the path has one (a copy of the same call can't
    overwrite it) and else under the call's key."""
    _mutate("routes", session_id, lambda entry: store.keep(entry, spawn_id or key, _record(route, key)))


def route_of(session_id: str | None, key: str, spawn_id: str | None = None) -> Route | None:
    """The route of this spawn, else the call's latest one: a path that sent no spawn id still finds it."""
    entry = store.load_entry("routes", session_id)
    record = entry.get(spawn_id) if spawn_id else None
    if not isinstance(record, dict):
        found = _latest(entry, key)
        record = found[0] if found else None
    return _route(record)


def returned(session_id: str | None, outcome: Outcome) -> Returned:
    """A result, a failure or a stop came back. One rule set, whichever path routed the call:
    a result that is neither a background launch nor a swapped run finishes the call, and a run at a
    level is kept as its task's latest; a launch waits for its agent to stop; a stop with an answer
    finishes it; a model error strikes the ID the spawn sent, or else the model it was routed to."""
    if outcome.kind == "stop":
        return _stopped(session_id, outcome)
    route = route_of(session_id, outcome.key, outcome.spawn_id)
    asked = route.model if route and route.model else outcome.named
    if outcome.kind == "failure":
        return _failed(session_id, outcome, route, asked)
    swapped = bool(outcome.models_used)
    finished = not outcome.launched and not swapped
    try:
        if route and route.level is not None and outcome.task and (finished or (outcome.launched and outcome.agent_id)):
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
    return Returned(asked, route, finished, SWAPPED if swapped else None)


def _wait_or_finish(session_id: str | None, outcome: Outcome, route: Route, finished: bool) -> None:
    if finished:
        _mutate("done", session_id, lambda done: store.keep(done, outcome.task, route.run))
    else:
        run = {**route.run, "task": outcome.task}
        _mutate("launched", session_id, lambda waiting: store.keep(waiting, outcome.agent_id, run))


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

    _mutate("launched", session_id, take)
    finished = outcome.answered and Level.parse(run.get("level")) is not None and isinstance(run.get("task"), str)
    if finished:
        done = {k: run.get(k) for k in ("level", "model", "effort")}
        _mutate("done", session_id, lambda records: store.keep(records, run["task"], done))
    return Returned(run.get("model") if isinstance(run.get("model"), str) else None, None, finished)


def last_run(session_id: str | None, task: str) -> dict | None:
    """The latest finished run of this task in the session, within REDO_WINDOW: what a marked redo climbs
    from. The request doesn't matter here, since a redo often follows the user turning the result down."""
    run = store.load_entry("done", session_id).get(task)
    if not isinstance(run, dict) or Level.parse(run.get("level")) is None:
        return None
    return run if 0 <= store.now() - store.stamp(run.get("at")) < REDO_WINDOW else None
