"""The mod path as its own adapter: hooks/router.ts forwards the engine's events here, and each
call answers with what the mod applies. A spawn goes through the one spawn procedure and answers
the model, effort and reason. Every call fails open like the settings hooks: cli.py swallows what a
handler raises."""
from __future__ import annotations

import re
import time
from pathlib import Path

from . import allowlist, ledger, settings, store
from . import spawn as procedure

MARKS = "mod-calls"  # hooks/router.ts writes one empty file here per Agent call it routes at spawn
MARK_KEPT = 86400
_PLAIN_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _marks() -> Path:
    return store.data_dir() / MARKS


def routes(tool_use_id: object) -> bool:
    """Whether the mod's tool.call hook marked this Agent call, so its spawn hook routes it. The mark is
    taken: it answers one call. The id names a file, so only a plain one is looked up."""
    if not isinstance(tool_use_id, str) or not _PLAIN_ID.fullmatch(tool_use_id):
        return False
    mark = _marks() / tool_use_id
    try:
        mark.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return mark.is_file()
    return True


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


def start(hook_input: dict) -> None:
    """The session started: keep the settings layers the mod read, and clear old marks."""
    allowlist.remember(hook_input.get("session_id"), hook_input.get("settings"))
    _sweep()


def spawn(hook_input: dict) -> dict | None:
    """The model and effort for one agent about to start, or None to leave the call as it is."""
    # session.start doesn't fire after /clear, so a spawn may be the first to bring this session's layers.
    allowlist.remember(hook_input.get("session_id"), hook_input.get("settings"))
    # The spawn procedure reads agent_id as "the call comes from inside a subagent".
    snap = settings.Snapshot(hook_input)
    spawned = procedure.decide({**hook_input, "agent_id": hook_input.get("parent_agent_id")}, snap)
    decision = spawned.decision
    newest = _newest(snap, decision)
    procedure.record(spawned, "mod", newest)
    if decision["action"] != "rewrite" and not newest:
        return None
    return {"model": newest or decision["model"], "effort": decision["effort"], "reason": decision["reason"]}


def _newest(snap: settings.Snapshot, decision: dict) -> str | None:
    """The newest full ID to send in place of the decided family alias, on a provider whose
    alias lags. Only agent.spawn takes a full ID; the Agent tool's model takes an alias
    (https://code.claude.com/docs/en/sub-agents.md)."""
    if decision["action"] == "deny" or decision["version"]:
        return None
    return snap.registry.newest_id(decision["model"], snap.options.provider, snap.allowed, snap.health)


def usage(hook_input: dict) -> None:
    """One ledger row per subagent turn, with the figures Claude Code keeps for that agent."""
    agent = hook_input.get("subagent_type")
    ledger.usage(hook_input.get("session_id"), hook_input.get("agent_id"), agent if isinstance(agent, str) and agent else None,
                 models=hook_input.get("models"), cost=hook_input.get("cost"),
                 subscription=hook_input.get("subscription") is True, turn=hook_input.get("usage"))
