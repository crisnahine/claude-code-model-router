"""The mod path as its own adapter: hooks/router.ts forwards the engine's events here, and each
call answers with what the mod applies. A spawn goes through the one spawn procedure and answers
the model, effort and reason. Every call fails open like the settings hooks: cli.py swallows what a
handler raises."""
from __future__ import annotations

from . import allowlist, ledger, settings, store
from . import spawn as procedure


def active(session_id: str | None) -> bool:
    """Whether the mod has announced itself for this session, so the fallback stands down."""
    return bool(store.load_entry("mod", session_id).get("active"))


def _heard(hook_input: dict) -> None:
    """The mod's session.start fires once per process, never after /clear, so every turn and spawn
    says the mod is running. They also bring the settings layers the mod read."""
    session_id = hook_input.get("session_id")
    if session_id and not active(session_id):
        store.save_entry("mod", session_id, {"active": True, "at": store.now()})
    allowlist.remember(session_id, hook_input.get("settings"))


def start(hook_input: dict) -> None:
    """The session or a main-loop turn started."""
    _heard(hook_input)


def spawn(hook_input: dict) -> dict | None:
    """The model and effort for one agent about to start, or None to leave the call as it is."""
    _heard(hook_input)
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
    """One ledger row per turn, its cost the sum of its requests, each priced on its own."""
    found = hook_input.get("usage")
    if isinstance(found, dict) and isinstance(found.get("model"), str):
        stage = hook_input.get("subagent_type")
        ledger.usage(settings.Snapshot(hook_input).registry, hook_input.get("session_id"), hook_input.get("agent_id"),
                     found, hook_input.get("requests"), stage if isinstance(stage, str) and stage else None)
