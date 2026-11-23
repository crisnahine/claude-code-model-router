"""Decide the model and effort for one call, from facts the spawn procedure gathers.
The agent never changes. The order of authority (force, the call's own model, a rule,
the agent's pin, CLAUDE_CODE_SUBAGENT_MODEL, then the ladder's pick) and the cap are in
docs/ARCHITECTURE.md. decide reads no state: every input arrives as a fact."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Optional, TypedDict

from . import ladder
from .config import EFFORTS, LEVEL_SOURCES, Level
from .profile import profile_call
from .registry import Registry, Rule
from .settings import Settings


Action = Literal["keep", "rewrite", "deny"]
Source = Literal["caller", "rule", "pin", "router"]


class Decision(TypedDict):
    agent: str
    action: Action
    model: Optional[str]
    effort: Optional[str]
    level: Optional[str]
    signals: list
    source: Optional[Source]
    pick: Optional[str]
    was: Optional[str]
    reason: str
    repeat_of: Optional[str]
    escalated_from: Optional[str]
    version: Optional[str]  # an exact model ID the caller or the agent named, not just a family
    held: Optional[str]  # what a project's rule asked for that this call didn't take, and why


@dataclass(frozen=True)
class Facts:
    """What decide needs to know about one call."""
    agent: str
    tool_input: dict
    usable: list  # registry.available for this project and cap
    definition: dict | None = None  # agents.find, or the engine's stand-in for its own agents
    parent: str | None = None  # the model an agent without one inherits, when known
    earlier: dict | None = None  # calls.finished_run: this call's finished run in this request
    raises: dict = field(default_factory=dict)  # learn.raises: the learned raise of each level
    floor: Level | None = None  # the request's level, for a hand-off to a strong role agent
    teammate: bool = False  # an agent-team teammate, which CLAUDE_CODE_SUBAGENT_EFFORT doesn't reach
    effort_caps: Mapping[str, str] = field(default_factory=dict)  # maxEffortLevel per registry model, where set


def decide(facts: Facts, reg: Registry, settings: Settings) -> Decision:
    tool_input, agent, definition, usable = facts.tool_input, facts.agent, facts.definition, facts.usable
    out: Decision = {"agent": agent, "action": "keep", "model": None, "effort": None, "level": None,
           "signals": [], "source": None, "pick": None, "was": None, "reason": "", "repeat_of": None,
           "escalated_from": None, "version": None, "held": None}
    if agent == "fork":
        out["reason"] = "a fork runs on its conversation's model"
        return out

    requested = reg.family(tool_input.get("model"))
    own_model = (definition or {}).get("model")
    pinned = reg.family(own_model)
    env_default = reg.family(settings.subagent_model)
    # The built-in Explore and Plan have had their own default model in some versions,
    # so what they would run on isn't assumed.
    special = bool(definition) and definition["source"] == "builtin" and agent in ("Explore", "Plan")
    if requested or pinned:
        out["was"] = requested or pinned
    elif definition is not None and not own_model and not special:
        out["was"] = env_default or facts.parent

    rule = reg.rules.get(agent) or Rule()
    profile = profile_call(tool_input, agent)
    level = rule.level or profile["level"]
    # A project's rule can raise a level, never lower it; so can the request's own.
    for floor in (rule.floor, facts.floor):
        if floor and floor > level:
            level = floor
    done = facts.earlier
    done_level = Level.parse(done.get("level")) if done else None
    redo = bool(done_level and not tool_input.get("model"))
    if redo:
        # The same call again after its result came back: the first answer wasn't good
        # enough. Re-running at the next level is cheaper than starting every call high.
        above = done_level.up()
        if above and level < above:
            level, out["escalated_from"] = above, done_level
    pick, pick_effort = ladder.pick(reg, level, settings.priority, usable, facts.raises)
    level_effort = ladder.effort(reg, level, settings.priority)
    out.update(level=level, signals=profile["signals"], pick=pick)

    if settings.mode != "auto":
        out["reason"] = f"routing is {settings.mode}"
        return out
    if not usable:
        out["reason"] = "availableModels and deniedModels leave no registry model open here, so the call is left as it is"
        return out
    if rule.route is False:
        out["reason"] = "a rule in model-router.json leaves this agent alone"
        return out
    if tool_input.get("model") and not requested:
        out.update(was=None, reason=f"the call names a model outside the registry ({tool_input['model']})")
        return out
    if settings.force:
        # The user pinned every subagent's model. Effort is still free to set, and on a
        # pinned strong model it is the only lever left on cost.
        out.update(was=env_default or facts.parent,
                   reason="CLAUDE_CODE_SUBAGENT_MODEL_FORCE pins every subagent's model")
        effort = None if _effort_decided(tool_input, definition, settings, facts.teammate) else level_effort
        if effort and not tool_input.get("model"):
            effort, held = capped(effort, facts.effort_caps.get(out["was"] or ""))
            out.update(action="rewrite", effort=effort, source="router",
                       reason=f"{level} subtask; the model is pinned by CLAUDE_CODE_SUBAGENT_MODEL_FORCE, so only effort is set{held}")
        return out

    rule_model = reg.family(rule.model)
    suggested = reg.family(rule.suggested)
    if not rule_model and suggested and reg.stronger(pick, suggested):
        # A project's model applies only when it isn't weaker than the pick.
        out["held"] = f"a project rule names {suggested} for {agent}, weaker than the pick {pick}"
        suggested = None
    rule_model = rule_model or suggested
    override = settings.pins == "override"
    if requested:
        target, source, why = requested, "caller", "the call names a model"
    elif rule_model:
        target, source, why = rule_model, "rule", "a rule in model-router.json sets this agent's model"
    elif definition is None and not override:
        out["reason"] = "this agent's definition wasn't found, so it is left alone"
        return out
    elif own_model and not pinned and not override:
        out["reason"] = f"the agent names a model outside the registry ({own_model})"
        return out
    elif pinned and not override:
        target, source, why = pinned, "pin", "the agent names its own model"
    elif env_default and not override:
        out["reason"] = "CLAUDE_CODE_SUBAGENT_MODEL sets the model for agents without one"
        return out
    else:
        target, source, why = pick, "router", f"{level} subtask"
        if out.get("escalated_from"):
            why += f", one level above the {out['escalated_from']} run that came back before"
        elif redo:
            why += ", redone with more effort than the run that came back before"

    named = tool_input.get("model") if source == "caller" else own_model if source == "pin" else None
    out["version"] = named if isinstance(named, str) and named.lower() != target else None
    final = reg.clamp(target, usable)
    if final != target:
        why += f"; {target} is over the cap or unavailable here"
    effort = None
    # Effort is set whenever nobody chose it, a pinned agent included: the pin is about
    # the model, and on current models effort is the cheaper lever for quality.
    if source in LEVEL_SOURCES and not _effort_decided(tool_input, definition, settings, facts.teammate):
        effort = pick_effort if source == "router" else level_effort
    if redo:
        effort = _above_earlier(reg, effort, final, done)
    effort, held = capped(effort, facts.effort_caps.get(final))
    out.update(model=final, source=source, reason=why + held + (f"; {out['held']}" if out["held"] else ""))
    if final == out["was"] and not effort:
        return out  # it would run on this model anyway
    out.update(action="rewrite", effort=effort)
    return out


def capped(effort: str | None, cap: str | None) -> tuple[str | None, str]:
    """An effort held to the maxEffortLevel cap, and the reason to add when it was."""
    if effort in EFFORTS and cap in EFFORTS and EFFORTS.index(effort) > EFFORTS.index(cap):
        return cap, f"; effort held to {cap} by maxEffortLevel"
    return effort, ""


def _above_earlier(reg: Registry, effort: str | None, model: str | None, done: dict) -> str | None:
    """A redo must ask for more than the run it replaces. When the cap or availability
    keeps it on the same model, that means more effort than last time."""
    if reg.stronger(model, done.get("model")):
        return effort
    earlier = done.get("effort")
    if earlier not in EFFORTS:
        return effort
    floor = EFFORTS[min(EFFORTS.index(earlier) + 1, len(EFFORTS) - 1)]
    return effort if effort in EFFORTS and EFFORTS.index(effort) >= EFFORTS.index(floor) else floor


def _effort_decided(tool_input: dict, definition: dict | None, settings: Settings, teammate: bool) -> bool:
    """Someone already chose this call's effort: the caller, the agent, or the user's environment."""
    return bool("effort" in tool_input or (definition or {}).get("effort")
                or settings.effort_level or (settings.subagent_effort and not teammate))
