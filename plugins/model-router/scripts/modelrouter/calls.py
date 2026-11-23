"""What happened to each call: how it was routed, whether its result came back, and what
that means for the same call issued again. The only module that reads or writes those
records.

Per session, `pending`, `launched` and `done` hold the per-call records, and the current
request's runs, routes and strongest spawn live in the request record (`request.py`)."""
from __future__ import annotations

import hashlib
import re

from . import request, store
from .config import LEVELS

# Without a request id to compare, a call seen again within this long counts as a redo.
REDO_WINDOW = 30 * 60

_MODEL_ID = re.compile(r"[A-Za-z0-9._:/\[\]-]{1,100}")
MAX_SWAPPED = 5


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


def settled(session_id: str | None, key: str, rewrite: dict | None, finished: bool = False,
            launched_as: str | None = None) -> str | None:
    """A result or an error came back, so the call's `rewrite` (taken from the fallback) was
    accepted. A result also marks the run finished, so the same call issued again is a redo.
    A background launch has no result yet: it waits under its agent's id until that agent
    stops. Returns the model the call was routed to in this request, when known."""
    now = request.load(session_id)
    model = now.routed_to(key)
    ran = rewrite if isinstance(rewrite, dict) and rewrite.get("level") else now.run(key)
    if not isinstance(ran, dict) or ran.get("level") not in LEVELS:
        return model
    run = {"level": ran["level"], "model": ran.get("model"), "effort": ran.get("effort"),
           "prompt_id": now.prompt_id}
    if finished:
        store.mutate_entry("done", session_id, lambda done: store.keep(done, key, run))
    elif launched_as:
        store.mutate_entry("launched", session_id, lambda launched: store.keep(launched, launched_as, {**run, "key": key}))
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
        store.mutate_entry("done", session_id, lambda done: store.keep(done, run["key"], finished))


def finished_run(session_id: str | None, key: str) -> dict | None:
    """The run of this call that finished earlier in the current request: the same request
    id, or within REDO_WINDOW when there is none."""
    run = store.load_entry("done", session_id).get(key)
    if not isinstance(run, dict) or run.get("level") not in LEVELS:
        return None
    current = request.load(session_id).prompt_id
    same = run.get("prompt_id") == current if current else 0 <= _age(run) < REDO_WINDOW
    return run if same else None


def _age(record: dict) -> float:
    """Seconds since the record was written. A clock moved back makes it negative, and each
    caller decides which side of its test that unknown age falls on."""
    return store.now() - (record.get("at") or 0)
