"""What happened to each call: how it was routed, whether its result came back, and what
that means for the same call issued again. The only module that reads or writes those
records.

Per session, `pending` holds fallback rewrites waiting for their result and `done` holds
finished runs. The current request's runs and its strongest spawn live in that request's
`prompts` entry, so a new request starts them empty."""
from __future__ import annotations

import hashlib
import time

from . import registry, store
from .config import LEVELS, LEVEL_SOURCES

KEPT = 100
# Without a request id to compare, a call seen again within this long counts as a redo.
REDO_WINDOW = 30 * 60


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


def _runs(entry: dict) -> dict:
    runs = entry.get("calls")
    return runs if isinstance(runs, dict) else {}


def routed(config: dict, session_id: str | None, key: str, model: str | None, *, level: str | None = None,
           effort: str | None = None, source: str | None = None, awaiting: bool = False) -> str | None:
    """Record a spawned call running on `model`. `source` is who chose the model, None when
    the call was passed through untouched; `awaiting` marks a fallback rewrite that waits
    for its result. Returns the model the router picked for this call earlier in this request."""
    if awaiting:
        store.mutate_entry("pending", session_id, lambda pending: _put(
            pending, key, {"model": model, "level": level, "effort": effort}))
    stood = source in LEVEL_SOURCES and bool(level)
    entry = store.load_entry("prompts", session_id)
    before = _runs(entry).get(key)
    earlier = before.get("model") if isinstance(before, dict) and before.get("source") == "router" else None

    def power(name: object) -> float:
        return registry.strength(config, name)

    def update(entry: dict) -> None:
        # Re-read under the lock: calls issued together record at the same moment.
        if stood:
            runs = _runs(entry)
            _put(runs, key, {"level": level, "model": model, "effort": effort, "source": source})
            entry["calls"] = runs
        if power(model) > power(entry.get("top_spawn")):
            entry["top_spawn"] = model

    if session_id and (stood or power(model) > power(entry.get("top_spawn"))):
        store.mutate_entry("prompts", session_id, update)
    return earlier


def settled(session_id: str | None, tool_input: dict, finished: bool = False) -> None:
    """A result or an error came back, so the call's rewrite was accepted. A result also
    marks the run finished, so the same call issued again is a redo."""
    key = identify(tool_input)[1]
    rewrite = store.load_entry("pending", session_id).get(key)
    if rewrite is not None:
        store.mutate_entry("pending", session_id, lambda pending: pending.pop(key, None))
    request = store.load_entry("prompts", session_id)
    ran = rewrite if isinstance(rewrite, dict) and rewrite.get("level") else _runs(request).get(key)
    if finished and isinstance(ran, dict) and ran.get("level") in LEVELS:
        run = {"level": ran["level"], "model": ran.get("model"), "effort": ran.get("effort"),
               "prompt_id": request.get("prompt_id")}
        store.mutate_entry("done", session_id, lambda done: _put(done, key, run))


def finished_run(session_id: str | None, key: str) -> dict | None:
    """The run of this call that finished earlier in the current request: the same request
    id, or within REDO_WINDOW when there is none."""
    run = store.load_entry("done", session_id).get(key)
    if not isinstance(run, dict) or run.get("level") not in LEVELS:
        return None
    request = store.load_entry("prompts", session_id).get("prompt_id")
    same = run.get("prompt_id") == request if request else time.time() - (run.get("at") or 0) < REDO_WINDOW
    return run if same else None


def refused_rewrite(session_id: str | None, key: str) -> dict | None:
    """This call's fallback rewrite, when the call shows up again before its result: the
    rewrite didn't run."""
    rewrite = store.load_entry("pending", session_id).get(key)
    return rewrite if isinstance(rewrite, dict) else None


def strongest(session_id: str | None) -> str | None:
    """The strongest model any agent started on in the current request."""
    return store.load_entry("prompts", session_id).get("top_spawn")
