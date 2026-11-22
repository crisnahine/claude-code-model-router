"""What happened to each call: how it was routed, whether its result came back, and what
that means for the same call issued again. The only module that reads or writes those
records.

Per session, `pending` holds fallback rewrites waiting for their result, `launched` holds
background runs waiting for their agent to stop, and `done` holds finished runs. The current request's runs, the model each call was routed to, and its strongest spawn live in that request's
`prompts` entry, so a new request starts them empty."""
from __future__ import annotations

import hashlib
import time

from . import registry, store
from .config import LEVELS, LEVEL_SOURCES

KEPT = 100
# Without a request id to compare, a call seen again within this long counts as a redo.
REDO_WINDOW = 30 * 60
# shortcut: a refused call re-issued within this many seconds is rewritten again; key on a message id if hooks get one
REFUSED_AFTER = 5


def identify(tool_input: dict) -> tuple[str, str]:
    """The call's agent, and a key that recognizes the same call issued again."""
    agent = tool_input.get("subagent_type")
    agent = agent if isinstance(agent, str) and agent else "general-purpose"
    text = agent + "\0" + str(tool_input.get("prompt") or "")
    return agent, hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def _put(records: dict, key: str, record: dict) -> None:
    records[key] = {**record, "at": time.time()}
    keys = [k for k, v in records.items() if isinstance(v, dict)]
    for old in sorted(keys, key=lambda k: records[k].get("at") or 0)[:-KEPT]:
        del records[old]


def _runs(entry: dict, name: str = "calls") -> dict:
    runs = entry.get(name)
    return runs if isinstance(runs, dict) else {}


def routed(config: dict, session_id: str | None, key: str, model: str | None, *, level: str | None = None,
           effort: str | None = None, source: str | None = None, awaiting: bool = False) -> str | None:
    """Record a spawned call and return the model the router gave it earlier in this request."""
    if awaiting:
        store.mutate_entry("pending", session_id, lambda pending: _put(
            pending, key, {"model": model, "level": level, "effort": effort}))
    stood = source in LEVEL_SOURCES and bool(level)
    earlier: list = []

    def update(entry: dict) -> None:
        # Under the lock: calls issued together record at the same moment.
        runs = _runs(entry)
        before = runs.get(key)
        if isinstance(before, dict) and before.get("source") == "router":
            earlier.append(before.get("model"))
        if stood:
            _put(runs, key, {"level": level, "model": model, "effort": effort, "source": source})
            entry["calls"] = runs
        # The model each call runs on, for its result: the result shows the call as issued.
        routes = _runs(entry, "routed")
        _put(routes, key, {"model": model})
        entry["routed"] = routes
        if registry.stronger(config, model, entry.get("top_spawn")):
            entry["top_spawn"] = model

    store.mutate_entry("prompts", session_id, update)
    return earlier[0] if earlier else None


def settled(session_id: str | None, tool_input: dict, finished: bool = False,
            launched_as: str | None = None) -> str | None:
    """A result or an error came back, so the call's rewrite was accepted. A result also
    marks the run finished, so the same call issued again is a redo. A background launch
    has no result yet: it waits under its agent's id until that agent stops. Returns the
    model the call was routed to in this request, when known."""
    key = identify(tool_input)[1]
    rewrite = store.load_entry("pending", session_id).get(key)
    if rewrite is not None:
        store.mutate_entry("pending", session_id, lambda pending: pending.pop(key, None))
    request = store.load_entry("prompts", session_id)
    route = _runs(request, "routed").get(key)
    model = route.get("model") if isinstance(route, dict) and isinstance(route.get("model"), str) else None
    ran = rewrite if isinstance(rewrite, dict) and rewrite.get("level") else _runs(request).get(key)
    if not isinstance(ran, dict) or ran.get("level") not in LEVELS:
        return model
    run = {"level": ran["level"], "model": ran.get("model"), "effort": ran.get("effort"),
           "prompt_id": request.get("prompt_id")}
    if finished:
        store.mutate_entry("done", session_id, lambda done: _put(done, key, run))
    elif launched_as:
        store.mutate_entry("launched", session_id, lambda launched: _put(launched, launched_as, {**run, "key": key}))
    return model


def stopped(session_id: str | None, agent_id: object, answered: bool) -> None:
    """An agent stopped. A background launch waiting under its id is finished now, when it
    stopped with an answer. Foreground agents finished at their result and aren't waiting."""
    if not isinstance(agent_id, str) or agent_id not in store.load_entry("launched", session_id):
        return
    run: dict = {}

    def take(launched: dict) -> None:
        waiting = launched.pop(agent_id, None)
        run.update(waiting if isinstance(waiting, dict) else {})

    store.mutate_entry("launched", session_id, take)
    if answered and run.get("level") in LEVELS and isinstance(run.get("key"), str):
        finished = {k: run.get(k) for k in ("level", "model", "effort", "prompt_id")}
        store.mutate_entry("done", session_id, lambda done: _put(done, run["key"], finished))


def finished_run(session_id: str | None, key: str) -> dict | None:
    """The run of this call that finished earlier in the current request: the same request
    id, or within REDO_WINDOW when there is none."""
    run = store.load_entry("done", session_id).get(key)
    if not isinstance(run, dict) or run.get("level") not in LEVELS:
        return None
    request = store.load_entry("prompts", session_id).get("prompt_id")
    same = run.get("prompt_id") == request if request else 0 <= _age(run) < REDO_WINDOW
    return run if same else None


def _age(record: dict) -> float:
    """Seconds since the record was written. A clock moved back makes it negative, which
    callers read as unknown rather than as recent."""
    return time.time() - (record.get("at") or 0)


def refused_rewrite(session_id: str | None, key: str) -> dict | None:
    """This call's fallback rewrite, when the call shows up again before its result: the
    rewrite didn't run. A rewrite only seconds old is a copy issued alongside it."""
    rewrite = store.load_entry("pending", session_id).get(key)
    if not isinstance(rewrite, dict) or 0 <= _age(rewrite) < REFUSED_AFTER:
        return None
    return rewrite


def named(session_id: str | None) -> str | None:
    """The model the user named in the current request's prompt."""
    return store.load_entry("prompts", session_id).get("named")


def strongest(session_id: str | None) -> str | None:
    """The strongest model any agent started on in the current request."""
    return store.load_entry("prompts", session_id).get("top_spawn")
