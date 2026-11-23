"""One procedure for every spawn, whichever path it came by, in two steps: decide (the facts
and the router's decision, writing nothing), then record (the ledger row, the route and the
votes it carries). A path may adjust the decision between them."""
from __future__ import annotations

from dataclasses import dataclass

from . import agents, calls, gates, learn, ledger, profile, request, router, session, settings
from . import config as cfg
from .config import Level


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
    agent, key = call or calls.identify(tool_input)
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
    # A hand-off brief often reads simpler than the request it serves, so the strong role
    # agents start no lower than the request's level; a redo still climbs from there.
    floor = request.load(hook_input.get("session_id")).floor if profile.role(agent).strong else None
    # The mod says so. On the fallback, a named call from the main conversation without `isolation` launches
    # as a teammate when agent teams are on (sub-agents.md, Subagent names); that is the one hook-visible sign.
    name = tool_input.get("name")
    named = isinstance(name, str) and bool(name.strip()) and not tool_input.get("isolation") and not hook_input.get("agent_id")
    teammate = hook_input.get("kind") == "teammate" or named
    caps = {n: c for n in reg.models for c in [snap.allowed.effort_cap(reg, n)] if c}
    return router.Facts(agent=agent, tool_input=tool_input, usable=snap.usable,
                        definition=definition, parent=parent,
                        earlier=calls.finished_run(hook_input.get("session_id"), key), raises=snap.raises,
                        floor=floor, teammate=teammate, effort_caps=caps)


def decide(hook_input: dict, snap: settings.Snapshot) -> Spawn:
    """Decide one spawn. Writes nothing."""
    call = calls.identify(cfg.tool_input(hook_input))
    found = facts(hook_input, snap, call)
    return Spawn(hook_input, snap, call[1], found, router.decide(found, snap.registry, snap.options))


def record(spawned: Spawn, via: str, newest: str | None = None) -> None:
    """Write the spawn's ledger row and, unless it is denied, its route and votes. `via` is "mod" or "fallback";
    `newest` is the full ID the mod sends in place of the decided alias."""
    hook_input, decision = spawned.hook_input, spawned.decision
    session_id = hook_input.get("session_id")
    spawned.snap.settle()
    ledger.spawn(session_id, via, decision, spawned.facts.tool_input.get("description"),
                 kind=(hook_input.get("kind") or "subagent") if via == "mod" else None)
    if decision["action"] != "deny":
        try:
            _record(spawned, via, newest)
        except OSError:
            pass


def _record(spawned: Spawn, via: str, newest: str | None) -> None:
    hook_input, decision = spawned.hook_input, spawned.decision
    # The fallback's input has no kind; a named call it flags as a teammate must not vote as a subagent.
    session_id = hook_input.get("session_id")
    kind = hook_input.get("kind") or ("teammate" if spawned.facts.teammate else learn.SUBAGENT)
    reg, current = spawned.snap.registry, request.load(session_id)
    runs_on = decision["model"] or decision["was"]
    source = None if decision["repeat_of"] else decision["source"]  # passed through untouched: no pick, no level
    rewrote = decision["action"] == "rewrite"
    route = calls.Route(model=runs_on, sent=newest or (decision["model"] if rewrote else None),
                        level=Level.parse(decision["level"]), effort=decision["effort"], source=source, path=via,
                        counted=learn.counted(decision, kind), prompt_id=current.prompt_id, rewrote=rewrote,
                        was=decision["was"])
    spawn_id = hook_input.get("tool_use_id")
    earlier = None
    try:  # a route that can't be written mustn't cost the spawn's votes
        earlier = calls.routed(session_id, spawned.key, route, spawn_id if isinstance(spawn_id, str) and spawn_id else None)
    except OSError:
        pass
    user_named = source == "caller" and decision["was"] == current.named
    learn.spawned(reg, decision, kind, earlier, user_named, spawned.facts.raises)
    if gates.is_checker(decision["agent"]):
        gates.note_check(session_id)
