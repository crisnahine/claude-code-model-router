"""One procedure for every spawn, whichever path it came by, in two steps: decide (the facts
and the router's decision, writing nothing), then record (the ledger row and the route).
A path may adjust the decision between them."""
from __future__ import annotations

from dataclasses import dataclass, replace

from . import agents, calls, ledger, router, session, settings
from . import config as cfg
from .config import EFFORTS, Level
from .registry import Registry


@dataclass
class Spawn:
    hook_input: dict
    snap: settings.Snapshot
    key: str
    facts: router.Facts
    decision: router.Decision


def facts(hook_input: dict, snap: settings.Snapshot, call: tuple[str, str] | None = None) -> router.Facts:
    reg = snap.registry
    tool_input = cfg.tool_input(hook_input)
    agent = (call or calls.identify(tool_input))[0]
    definition = agents.find(agent, snap.project)
    if definition is None and hook_input.get("kind") in ("workflow", "teammate"):
        # The engine makes these agents: a workflow stage, or a teammate role with no file
        # of its own. Neither names a model unless the script or the call does.
        definition = {"name": agent, "source": hook_input["kind"], "model": None}
    if hook_input.get("parent_model"):
        # A mod's spawn event names the parent's effective model exactly, nested or not.
        parent = reg.family(hook_input["parent_model"])
    elif hook_input.get("agent_id"):
        # Inside a subagent, an agent without a model runs on that subagent's model, which
        # we can't see. Unknown means the pick is always written onto the call.
        parent = None
    else:
        parent = reg.family(session.model(hook_input.get("session_id"), hook_input.get("transcript_path")))
    # The mod says so. On the fallback, a named call from the main conversation without `isolation` launches
    # as a teammate only when agent teams are on (agent-teams.md); under -p it never does, which a hook can't see.
    name = tool_input.get("name")
    named = isinstance(name, str) and bool(name.strip()) and not tool_input.get("isolation") and not hook_input.get("agent_id")
    teammate = hook_input.get("kind") == "teammate" or (named and snap.options.agent_teams)
    caps = {n: c for n in reg.models for c in [snap.allowed.effort_cap(reg, n)] if c}
    # The main conversation's level: the mod reads it from the main loop's requests, a hook gets it as `effort`.
    # A nested call runs at its parent's effort instead, so it gets none.
    effort = hook_input.get("effort")
    level = hook_input.get("session_effort") or (effort.get("level") if isinstance(effort, dict) else None)
    session_effort = level if not hook_input.get("agent_id") and level in EFFORTS else None
    workflow = hook_input.get("kind") == "workflow"
    found = router.Facts(agent=agent, tool_input=tool_input, usable=snap.usable, definition=definition,
                         parent=parent, teammate=teammate, workflow=workflow, session_effort=session_effort,
                         effort_caps=caps, lagging=snap.lagging)
    # A workflow stage is never redone: its script decides what runs again, and a fan-out needs one model and effort.
    if workflow or not calls.marked_redo(tool_input):
        return found
    task = calls.task(tool_input)
    earlier = calls.last_run(hook_input.get("session_id"), task) if task else None
    earlier = _inherited(earlier, hook_input) if earlier else None
    named = _unmarked_run(found, snap)
    if earlier and _asks_more(reg, named, earlier):
        return replace(found, redoes=Level.parse(earlier.get("level")))  # the caller picked a stronger lever itself
    # Claude names a model on every call, the redo included, so the same one named again must not repeat the run.
    if earlier:
        return replace(found, earlier=earlier)
    return replace(found, earlier=_inherited(named, hook_input), on_record=False) if named["level"] else found


def _inherited(run: dict, hook_input: dict) -> dict:
    """A nested run with no effort on record ran at its parent's, which no hook input carries, so a redo
    can't climb one effort step from it: it moves to the next stronger model instead."""
    return {**run, "inherited": True} if hook_input.get("agent_id") and run.get("effort") not in EFFORTS else run


def _asks_more(reg: Registry, named: dict, earlier: dict) -> bool:
    """What a redo names for itself asks for more than the run it redoes: a stronger model, or the same at more
    effort on one that takes effort."""
    model, ran = reg.family(named.get("model")), reg.family(earlier.get("model"))
    levels = Level.parse(named.get("level")), Level.parse(earlier.get("level"))
    if not model or not ran or None in levels:
        return False
    if model != ran:
        return reg.stronger(model, ran)
    return not earlier.get("no_effort") and router._above(router._ran_at(named), router._ran_at(earlier))


def _runs_at(effort: str | None, model: str | None, found: router.Facts, snap: settings.Snapshot) -> str | None:
    """The effort a call runs at as far as the router can tell: `router.runs_at`, else the session's level on
    the session's own model (https://code.claude.com/docs/en/model-config.md#effort-level-of-subagents-and-teammates)."""
    chosen = router.runs_at(effort, found.tool_input, found.definition, snap.options, found.teammate)
    return chosen or (found.session_effort if model and model == found.parent else None)


def _unmarked_run(found: router.Facts, snap: settings.Snapshot) -> dict:
    """What the call would run at without its mark. A marked redo with no finished run on record redoes that
    run, so a cap, a pin or the session's floor can't leave it on the same model and effort."""
    plain = router.decide(found, snap.registry, snap.options)
    model = plain["model"] or plain["was"]
    return {"level": plain["level"], "model": model, "effort": _runs_at(plain["effort"], model, found, snap)}


def redo_note(decision: router.Decision) -> str | None:
    """What Claude reads after a redo that had no step left, so it doesn't send the same call again expecting more."""
    if not decision["no_step"]:
        return None
    at = f" at {decision['effort']}" if decision["effort"] else ""
    # It names the effort as the most usable, since the call may have named one the model doesn't take.
    return (f"This redo ran on {decision['model'] or decision['was']}{at}, the strongest model and the most effort "
            "usable here, the same as the run it redoes.")


def decide(hook_input: dict, snap: settings.Snapshot) -> Spawn:
    """Decide one spawn. Writes nothing."""
    call = calls.identify(cfg.tool_input(hook_input))
    found = facts(hook_input, snap, call)
    return Spawn(hook_input, snap, call[1], found, router.decide(found, snap.registry, snap.options))


def record(spawned: Spawn, via: str, newest: str | None = None) -> None:
    """Write the spawn's ledger row and, unless it is denied or a workflow stage, its route. `via` is "mod" or
    "fallback"; `newest` is the full ID the mod sends in place of the decided alias."""
    hook_input, decision = spawned.hook_input, spawned.decision
    session_id = hook_input.get("session_id")
    spawned.snap.settle()
    ledger.spawn(session_id, via, decision, kind=(hook_input.get("kind") or "subagent") if via == "mod" else None,
                 on_record=spawned.facts.on_record)
    # A stage has no Agent result to read its route, and a fan-out of them would push running calls' routes out.
    if decision["action"] != "deny" and not spawned.facts.workflow:
        try:
            _record(spawned, via, newest)
        except OSError:
            pass


def _record(spawned: Spawn, via: str, newest: str | None) -> None:
    hook_input, decision = spawned.hook_input, spawned.decision
    session_id = hook_input.get("session_id")
    runs_on = decision["model"] or decision["was"]
    # A call the repeat guard passed through untouched: neither the pick nor the level applied.
    source = None if decision["repeat_of"] else decision["source"]
    rewrote = decision["action"] == "rewrite"
    facts = spawned.facts
    # The effort it runs at, whoever chose it, so a redo climbs from what ran.
    effort = _runs_at(decision["effort"], runs_on, facts, spawned.snap)
    named = facts.tool_input.get("model") or (facts.definition or {}).get("model")
    reg = spawned.snap.registry
    # A full ID or a [1m] variant the call keeps is the caller's or the agent's own pick, not its family's.
    version = (named if not rewrote and runs_on and isinstance(named, str) and reg.family(named) == runs_on
               and named.lower() not in (runs_on, *[a for a, t in reg.aliases.items() if t == runs_on]) else None)
    route = calls.Route(model=runs_on, sent=newest or (decision["model"] if rewrote else None),
                        level=Level.parse(decision["level"]), effort=effort, source=source, path=via,
                        was=decision["was"], version=version)
    spawn_id = hook_input.get("tool_use_id")
    calls.routed(session_id, spawned.key, route, spawn_id if isinstance(spawn_id, str) and spawn_id else None)
