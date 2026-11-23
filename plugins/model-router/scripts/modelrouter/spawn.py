"""One procedure for every spawn, whichever path it came by, in two steps: decide (the facts
and the router's decision, writing nothing), then record (the ledger row, the call and the
votes it carries). A path may adjust the decision between them."""
from __future__ import annotations

from dataclasses import dataclass

from . import agents, calls, gates, learn, ledger, registry, request, router, session, settings
from . import config as cfg


@dataclass
class Spawn:
    hook_input: dict
    snap: settings.Snapshot
    key: str
    facts: router.Facts
    decision: router.Decision


def facts(hook_input: dict, snap: settings.Snapshot, call: tuple[str, str] | None = None) -> router.Facts:
    config = snap.config
    tool_input = cfg.tool_input(hook_input)
    agent, key = call or calls.identify(tool_input)
    definition = agents.find(agent, snap.project)
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
    # A hand-off brief often reads simpler than the request it serves, so the strong role
    # agents start no lower than the request's level; a redo still climbs from there.
    strong = agent in tuple(f"{cfg.PLUGIN_NAME}:{role}" for role in ("architect", "deep-solver", "reviewer"))
    floor = request.load(hook_input.get("session_id")).floor if strong else None
    # The mod says so. On the fallback, a named call from the main conversation without `isolation` launches
    # as a teammate when agent teams are on (sub-agents.md, Subagent names); that is the one hook-visible sign.
    name = tool_input.get("name")
    named = isinstance(name, str) and bool(name.strip()) and not tool_input.get("isolation") and not hook_input.get("agent_id")
    teammate = hook_input.get("kind") == "teammate" or named
    return router.Facts(agent=agent, tool_input=tool_input, usable=snap.usable,
                        definition=definition, parent=parent,
                        earlier=calls.finished_run(hook_input.get("session_id"), key), raises=learn.raises(),
                        floor=floor, teammate=teammate)


def decide(hook_input: dict, snap: settings.Snapshot) -> Spawn:
    """Decide one spawn. Writes nothing."""
    call = calls.identify(cfg.tool_input(hook_input))
    found = facts(hook_input, snap, call)
    return Spawn(hook_input, snap, call[1], found, router.decide(found, snap.config, snap.options))


def record(spawned: Spawn, via: str, newest: str | None = None) -> None:
    """Write the spawn's ledger row and, unless it is denied, its call and votes. `via` is "mod" or "fallback";
    `newest` is the full ID the mod sends in place of the decided alias."""
    hook_input, decision = spawned.hook_input, spawned.decision
    session_id = hook_input.get("session_id")
    ledger.spawn(session_id, via, decision, spawned.facts.tool_input.get("description"),
                 kind=(hook_input.get("kind") or "subagent") if via == "mod" else None)
    if decision["action"] != "deny":
        try:
            sent = newest or (decision["model"] if decision["action"] == "rewrite" else None)
            _record(spawned.snap.config, session_id, spawned.key, decision, spawned.facts.raises, sent)
        except OSError:
            pass


def _record(config: dict, session_id: str | None, key: str, decision: dict, raises: dict, sent: str | None) -> None:
    level, source = decision["level"], decision["source"]
    runs_on = decision["model"] or decision["was"]
    if decision["repeat_of"]:
        source = None  # passed through untouched: neither the pick nor the level applied
    earlier = request.note_spawn(config, session_id, key, runs_on, level=level, effort=decision["effort"],
                                 source=source, sent=sent)
    user_named = source == "caller" and decision["was"] == request.load(session_id).named
    learn.decided(config, source=source, level=level, pick=decision["pick"], runs_on=runs_on, raised=raises,
                  earlier=earlier, escalated_from=decision["escalated_from"], user_named=user_named)
    if gates.is_checker(decision["agent"]):
        gates.note_check(session_id)
