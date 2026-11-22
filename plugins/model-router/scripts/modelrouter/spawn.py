"""One procedure for every spawn, whichever path it came by: gather the facts, decide,
write the ledger row, and record the call and the votes it carries."""
from __future__ import annotations

from typing import Callable

from . import agents, calls, gates, learn, registry, router, session, settings, store
from . import config as cfg

# Adjusts a decision before it is logged and recorded: (decision, call key, config, settings).
Guard = Callable[[dict, str, dict, settings.Settings], None]


def _tool_input(hook_input: dict) -> dict:
    tool_input = hook_input.get("tool_input")
    return tool_input if isinstance(tool_input, dict) else {}


def facts(hook_input: dict, config: dict, options: settings.Settings) -> router.Facts:
    tool_input = _tool_input(hook_input)
    agent, key = calls.identify(tool_input)
    project = cfg.project_dir(hook_input)
    definition = agents.find(agent, project)
    if definition is None and hook_input.get("kind") in ("workflow", "teammate"):
        # The engine makes these agents: a workflow stage, or a teammate role with no file
        # of its own. Neither names a model unless the script or the call does.
        definition = {"name": agent, "source": hook_input["kind"], "model": None}
    if hook_input.get("parent_model"):
        # A mod's spawn event names the parent's effective model exactly, nested or not.
        parent = registry.family(config, hook_input["parent_model"])
    elif hook_input.get("agent_id"):
        # Inside a subagent, an agent without a model runs on that subagent's model, which
        # we can't see. Unknown means the pick is always written onto the call.
        parent = None
    else:
        parent = registry.family(config, session.model(hook_input.get("session_id"),
                                                       hook_input.get("transcript_path")))
    return router.Facts(agent=agent, tool_input=tool_input, usable=registry.available(config, project, options.cap),
                        definition=definition, parent=parent,
                        earlier=calls.finished_run(hook_input.get("session_id"), key))


def run(hook_input: dict, via: str, guard: Guard | None = None) -> dict:
    """Decide one spawn and record it. `via` is "mod" or "fallback"."""
    config = cfg.load_config(hook_input)
    options = settings.resolve(config)
    found = facts(hook_input, config, options)
    key = calls.identify(found.tool_input)[1]
    decision = router.decide(found, config, options)
    if guard:
        guard(decision, key, config, options)
    session_id = hook_input.get("session_id")
    action = decision["action"]
    row = {"event": "spawn", "via": via, "session": session_id, "agent": decision["agent"], "action": action,
           "level": decision["level"], "signals": decision["signals"],
           "model": None if action == "deny" else decision["model"] or decision["was"],
           "was": decision["was"], "effort": decision["effort"], "source": decision["source"],
           "reason": decision["reason"], "description": str(found.tool_input.get("description") or "")[:120]}
    if via == "mod":
        row["kind"] = hook_input.get("kind") or "subagent"
    store.append_ledger(row)
    if action != "deny":
        try:
            _record(config, session_id, key, decision, awaiting=via == "fallback" and action == "rewrite")
        except OSError:
            pass
    return decision


def _record(config: dict, session_id: str | None, key: str, decision: dict, awaiting: bool) -> None:
    level, source = decision["level"], decision["source"]
    runs_on = decision["model"] or decision["was"]
    if decision["repeat_of"]:
        source = None  # passed through untouched: neither the pick nor the level applied
    earlier = calls.routed(config, session_id, key, runs_on, level=level, effort=decision["effort"],
                           source=source, awaiting=awaiting)

    def power(name: object) -> float:
        return registry.strength(config, name)

    if source == "router":
        learn.record_call(level)
    if source in cfg.LEVEL_SOURCES and level and decision["escalated_from"]:
        learn.vote_up(decision["escalated_from"])
    if source == "caller" and level:
        if earlier and power(runs_on) > power(earlier):
            learn.vote_up(level, 2)  # the same call again, on a stronger model
        elif power(runs_on) > power(decision["pick"]):
            learn.vote_up(level, 1)
        elif power(runs_on) < power(decision["pick"]):
            learn.vote_down(level)
    if gates.is_checker(decision["agent"]):
        gates.note_check(session_id)
