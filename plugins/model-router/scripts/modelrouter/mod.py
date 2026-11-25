"""The mod path as its own adapter: hooks/router.ts forwards the engine's events here, and each
call answers with what the mod applies. A spawn goes through the one spawn procedure and answers
the model, effort and reason. Every call fails open like the settings hooks: cli.py swallows what a
handler raises."""
from __future__ import annotations

import re
import time
from pathlib import Path

from . import allowlist, calls, ladder, ledger, settings, store
from . import config as cfg
from . import spawn as procedure

# hooks/router.ts writes an empty file here per Agent call it routes at spawn, and `<id>.moved` for one a later mod moved
MARKS = "mod-calls"
MARK_KEPT = 86400
_PLAIN_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _marks() -> Path:
    return store.data_dir() / MARKS


def _take(tool_use_id: object, suffix: str = "") -> bool:
    """Whether the mark is there, taken so it answers one call. The id names a file, so only a plain one is looked up."""
    if not isinstance(tool_use_id, str) or not _PLAIN_ID.fullmatch(tool_use_id):
        return False
    mark = _marks() / f"{tool_use_id}{suffix}"
    try:
        mark.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return mark.is_file()
    return True


def routes(tool_use_id: object) -> bool:
    """Whether the mod's tool.call hook marked this Agent call, so its spawn hook routes it."""
    return _take(tool_use_id)


def moved(tool_use_id: object) -> bool:
    """Whether a mod later in the chain moved this call's spawn to another family than the one this mod sent."""
    return _take(tool_use_id, ".moved")


def _sweep() -> None:
    """Marks no settings hook took: the call never reached PreToolUse, such as one another mod refused."""
    try:
        marks = list(_marks().iterdir())
    except OSError:
        return
    for mark in marks:
        try:
            if time.time() - mark.stat().st_mtime > MARK_KEPT:
                mark.unlink()
        except OSError:
            pass


OK = {"ok": True}  # the launcher answers nothing when no Python runs, so this tells the mod the runtime ran


def start(hook_input: dict) -> dict:
    """A session started, or a /clear began one: keep the settings layers the mod read, and clear old marks."""
    allowlist.remember(hook_input.get("session_id"), hook_input.get("settings"))
    _sweep()
    return OK


def spawn(hook_input: dict) -> dict | None:
    """The model and effort for one agent about to start, or None to leave the call as it is."""
    # session.start doesn't fire after /clear, so a spawn may be the first to bring this session's layers.
    allowlist.remember(hook_input.get("session_id"), hook_input.get("settings"))
    # The spawn procedure reads agent_id as "the call comes from inside a subagent".
    snap = settings.Snapshot(hook_input)
    spawned = procedure.decide({**hook_input, "agent_id": hook_input.get("parent_agent_id")}, snap)
    decision = spawned.decision
    newest = _newest(snap, decision)
    sent = newest or exact(snap, decision)
    procedure.record(spawned, "mod", sent)
    same = procedure.redo_note(decision)
    note = {"note": same} if same else {}  # the mod adds it to the Agent call's result, where Claude reads it
    if decision["action"] != "rewrite" and not newest:
        return {**OK, **note} if note else None
    return {"model": sent or decision["model"], "effort": decision["effort"], "reason": decision["reason"], **note}


def exact(snap: settings.Snapshot, decision: dict) -> str | None:
    """The exact model the caller or the agent named, which a rewrite keeps in place of its family's alias:
    on some providers the alias runs an older model, and an alias drops a [1m] variant."""
    version = decision["version"]
    if decision["action"] != "rewrite" or not version:
        return None
    permitted = snap.allowed.permits_named(snap.registry, decision["model"], version) and snap.health.healthy(version)
    return version if permitted else None


def _newest(snap: settings.Snapshot, decision: dict) -> str | None:
    """The newest full ID to send in place of the decided family alias, on a provider whose
    alias lags. Only agent.spawn takes a full ID; the Agent tool's model takes an alias
    (https://code.claude.com/docs/en/sub-agents.md)."""
    if decision["action"] == "deny" or decision["version"]:
        return None
    return snap.registry.newest_id(decision["model"], snap.options.provider, snap.allowed, snap.health)


def usage(hook_input: dict) -> dict:
    """One ledger row per subagent turn, with the figures Claude Code keeps for that agent."""
    agent = hook_input.get("subagent_type")
    ledger.usage(hook_input.get("session_id"), hook_input.get("agent_id"), agent if isinstance(agent, str) and agent else None,
                 models=hook_input.get("models"), cost=hook_input.get("cost"),
                 subscription=hook_input.get("subscription") is True, turn=hook_input.get("usage"))
    _ran(hook_input)
    return OK


def _ran(hook_input: dict) -> None:
    """Keep what the agent's last request carried as what its run ran at, the fact a redo climbs from."""
    ran, spawn_id = hook_input.get("ran"), hook_input.get("tool_use_id")
    if not isinstance(ran, dict) or not isinstance(spawn_id, str) or not _PLAIN_ID.fullmatch(spawn_id):
        return
    reg = cfg.load(hook_input)
    model, effort = reg.family(ran.get("model")), ran.get("effort")
    if model not in reg.models or not (effort is None or effort in cfg.EFFORTS):
        return
    # No effort sent: the model takes none, so only a stronger model is a step up from it.
    run = {"level": ladder.rung(reg, model, effort).value, "model": model,
           **({"effort": effort} if effort else {"no_effort": True})}
    try:
        calls.observed(hook_input.get("session_id"), spawn_id, run)
    except OSError:
        pass
