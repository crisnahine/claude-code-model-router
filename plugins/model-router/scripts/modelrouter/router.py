"""Decide the model and effort for one call, from facts the spawn procedure gathers.
The agent never changes. Claude names a call's model and effort; the router holds what it names to the
user's cap and to what is usable here, raises it to a rule's level, and sets the effort nobody chose. The
order of authority is in docs/ARCHITECTURE.md. decide reads no state: every input arrives as a fact."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Optional, TypedDict

from . import ladder
from .config import EFFORTS, Level
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
    source: Optional[Source]
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
    earlier: dict | None = None  # the finished run a redo climbs from: this call's, or for a marked redo its agent's
    marked: bool = False  # calls.marked_redo: the description says the call redoes a weak result
    on_record: bool = True  # False when `earlier` is what a marked redo would run at unmarked, not a finished run
    teammate: bool = False  # an agent-team teammate, which CLAUDE_CODE_SUBAGENT_EFFORT doesn't reach
    workflow: bool = False  # a workflow agent, which CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL pins
    session_effort: str | None = None  # the main conversation's level, for a call made from it
    effort_caps: Mapping[str, str] = field(default_factory=dict)  # maxEffortLevel per registry model, where set
    lagging: frozenset = frozenset()  # Snapshot.lagging: the router's own pick moves one family up from these


def decide(facts: Facts, reg: Registry, settings: Settings) -> Decision:
    tool_input, agent, definition, usable = facts.tool_input, facts.agent, facts.definition, facts.usable
    out: Decision = {"agent": agent, "action": "keep", "model": None, "effort": None, "level": None,
                     "source": None, "was": None, "reason": "", "repeat_of": None, "escalated_from": None,
                     "version": None, "held": None}
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

    if settings.mode != "auto":
        out["reason"] = f"routing is {settings.mode}"
        return out
    if not usable:
        out["reason"] = "availableModels and deniedModels leave no registry model open here, so the call is left as it is"
        return out
    rule = reg.rules.get(agent) or Rule()
    if rule.route is False:
        out["reason"] = "a rule in model-router.json leaves this agent alone"
        return out
    if tool_input.get("model") and not requested:
        out.update(was=None, reason=f"the call names a model outside the registry ({tool_input['model']})")
        return out
    decided = _effort_decided(tool_input, definition, settings, facts.teammate)
    named_effort = next((str(e).lower() for e in (tool_input.get("effort"), (definition or {}).get("effort")) if e), None)
    done = facts.earlier
    done_level = Level.parse(done.get("level")) if done else None
    redo = bool(done_level and not tool_input.get("model"))

    def climb(level: Level | None) -> Level | None:
        """The same call again after its result came back: the first answer wasn't good enough.
        Re-running at the next level is cheaper than starting every call high."""
        if not redo:
            return level
        above = done_level.up()
        if above and (level is None or level < above):
            out["escalated_from"] = done_level
            return above
        if not above and (level is None or level < done_level):
            return done_level  # a redo of a top-rung run stays there and asks for more effort
        if facts.marked and level > done_level:
            out["escalated_from"] = done_level  # the call already stands above the run it redoes
        return level

    if settings.force:
        model = env_default or facts.parent
        level = out["level"] = climb(ladder.rung(reg, model, named_effort))
        effort = None if decided or tool_input.get("model") or not level else ladder.effort(reg, level)
        return _pinned(out, "CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "subagent", model, level, effort,
                       facts, reg, done if redo else None)
    if facts.workflow and settings.workflow_model and not tool_input.get("model"):
        # This pin wins over the model a mod sets, so it is kept like the one above.
        model = reg.family(settings.workflow_model)
        level = out["level"] = ladder.rung(reg, model, named_effort)
        effort = None if decided or not level else ladder.effort(reg, level)
        return _pinned(out, "CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL", "workflow agent", model, level, effort, facts, reg, None)

    floor = rule.level or rule.floor
    target, source, why, chosen = None, None, "", False  # chosen: the router picked the model, nobody named it
    if requested:
        target, source, why = requested, "caller", "the call names a model"
    elif reg.family(rule.model):
        target, source, why = reg.family(rule.model), "rule", "a rule in model-router.json sets this agent's model"
    elif reg.family(rule.suggested):
        # A project's model applies only when it isn't weaker than what the call would run on without it, and
        # never when the project added it: that model's strength is only the project's own claim.
        suggested, runs_on = reg.family(rule.suggested), pinned or out["was"]
        basis = ("the agent's own model" if pinned else "the CLAUDE_CODE_SUBAGENT_MODEL default"
                 if env_default and runs_on == env_default else "the parent's model")
        asked = f"a project rule names {suggested} for {agent}"
        if not reg.models[suggested].trusted:
            out["held"] = f"{asked}, a model only that file vouches for"
        elif runs_on and not reg.stronger(runs_on, suggested):
            target, source, why = suggested, "rule", "a rule in model-router.json sets this agent's model"
        else:
            out["held"] = (f"{asked}, weaker than {basis} {runs_on}" if runs_on
                           else f"{asked}, and the model the call would run on isn't known")
    held = f"; {out['held']}" if out["held"] else ""
    if target is None:
        if definition is None:
            why = "this agent's definition wasn't found, so it is left alone"
        elif own_model and not pinned:
            why = f"the agent names a model outside the registry ({own_model})"
        elif pinned:
            target, source, why = pinned, "pin", "the agent names its own model"
        elif env_default:
            why = "CLAUDE_CODE_SUBAGENT_MODEL sets the model for agents without one"
    level = ladder.rung(reg, target or out["was"], named_effort)
    if target is None and why:
        out.update(level=level, reason=why + held)
        return out
    if target is None:
        # Nothing names a model, so Claude Code's own order applies: the parent's model.
        runs_on = out["was"]
        if redo:
            level, source, chosen = climb(level), "router", True
            target = ladder.pick(reg, level, usable).model
            came = "run that came back before" if facts.on_record else "run it would get unmarked, as it is marked a redo"
            steps = "one level above" if level.rank - done_level.rank == 1 else "above"
            why = f"{level} subtask" + (f", {steps} the {out['escalated_from']} {came}" if out["escalated_from"]
                                        else f", redone with more effort than the {came}")
        elif floor and (level is None or level < floor):
            level, target, source, chosen = floor, ladder.model_for(reg, floor), "rule", True
            why = f"a rule in model-router.json raises this agent to {floor}"
        elif facts.teammate and runs_on and reg.strength(runs_on) < reg.levels[Level.standard].needs:
            level, target, source, chosen = Level.standard, ladder.model_for(reg, Level.standard), "router", True
            why = f"a teammate starts no lower than {target}, since its model is fixed for all its tasks"
        elif runs_on and reg.clamp(runs_on, usable) != runs_on:
            target, source, why = runs_on, "router", f"the call names no model and would run on the parent's {runs_on}"
        else:
            out.update(level=level, reason="no model is named for this call, so it runs on "
                       + (f"the parent's model, {runs_on}" if runs_on else "Claude Code's own choice") + held)
            return out
    else:
        if floor and level < floor:
            level, target, chosen = floor, ladder.model_for(reg, floor), True
            why += f"; a rule in model-router.json raises it to {floor}"
        level = climb(level)

    named = tool_input.get("model") if source == "caller" else own_model if source == "pin" else None
    clamped = reg.clamp(target, usable)
    final = reg.past_lag(clamped, usable, facts.lagging) if chosen else clamped
    # The caller's exact ID only survives a cap that keeps its family.
    out["version"] = named if isinstance(named, str) and named.lower() != final and reg.family(named) == final else None
    if clamped != target:
        why += f"; {target} is over the cap or unavailable here"
    if final != clamped:
        why += f"; {clamped}'s alias runs an older model here"
    # Effort is set whenever nobody chose it: on current models it is the cheaper lever for quality.
    effort = None if decided else ladder.effort_on(reg, level, final)
    if redo and not decided:
        effort = _above_earlier(reg, effort, final, done)
    effort, floor_note = _session_floor(effort, final, facts)
    effort, capped_note = capped(effort, facts.effort_caps.get(final))
    stronger = next((n for n in usable if reg.models[n].trusted and reg.stronger(n, final)), None)
    runs = runs_at(effort, tool_input, definition, settings, facts.teammate)
    if redo and source == "router" and stronger and (final, runs) == (done.get("model"), done.get("effort")):
        # The redo would repeat the run it replaces: quality comes first, so it takes the next model up.
        cause = f"maxEffortLevel holds {final} at {runs}" if capped_note else f"{final} would run at {runs} again"
        why += f"; {cause}, the same as the run that came back before, so it goes to {stronger}"
        final = stronger
        if not decided:
            effort, floor_note = _session_floor(ladder.effort_on(reg, level, final), final, facts)
            effort, capped_note = capped(effort, facts.effort_caps.get(final))
    out.update(model=final, source=source, level=level, reason=why + floor_note + capped_note + held)
    if final == out["was"] and not effort:
        return out  # it would run on this model anyway
    out.update(action="rewrite", effort=effort)
    return out


def _pinned(out: Decision, pin: str, scope: str, pinned: str | None, level: Level | None, effort: str | None,
            facts: Facts, reg: Registry, earlier: dict | None) -> Decision:
    """The user pinned the model, so effort is the one lever left, and on a pinned strong model the one on cost.
    `earlier` is the run a redo replaces: on the same pinned model it asks for more effort than that run."""
    out.update(was=pinned, reason=f"{pin} pins every {scope}'s model")
    if effort:
        if earlier:
            effort = _above_earlier(reg, effort, pinned, earlier)
        effort, floor = _session_floor(effort, pinned, facts)
        effort, held = capped(effort, facts.effort_caps.get(pinned or ""))
        out.update(action="rewrite", effort=effort, source="router",
                   reason=f"{level} subtask; the model is pinned by {pin}, so only effort is set{floor}{held}")
    return out


def _session_floor(effort: str | None, model: str | None, facts: Facts) -> tuple[str | None, str]:
    """On the session's own model an agent runs at the session's level unless told otherwise, so the router never sets less there."""
    floor = facts.session_effort
    if (effort in EFFORTS and floor in EFFORTS and model and model == facts.parent
            and EFFORTS.index(floor) > EFFORTS.index(effort)):
        return floor, f"; at least the session's {floor} on its own model"
    return effort, ""


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


def runs_at(effort: str | None, tool_input: dict, definition: dict | None, settings: Settings,
            teammate: bool) -> str | None:
    """The effort a call runs at as far as the router can tell, in Claude Code's order
    (https://code.claude.com/docs/en/sub-agents.md#choose-an-effort-level): CLAUDE_CODE_EFFORT_LEVEL, the call's,
    the agent's, the router's own `effort`, then CLAUDE_CODE_SUBAGENT_EFFORT. None when nothing it sees sets one."""
    env = str(settings.effort_level or "").lower()
    if env:
        return env if env in EFFORTS else None  # auto or unset sends no effort
    chosen = (tool_input.get("effort"), (definition or {}).get("effort"), effort,
              None if teammate else settings.subagent_effort)
    return next((e for e in (str(c).lower() for c in chosen if c) if e in EFFORTS), None)


def _effort_decided(tool_input: dict, definition: dict | None, settings: Settings, teammate: bool) -> bool:
    """Someone already chose this call's effort: the caller, the agent, or the user's environment."""
    return bool("effort" in tool_input or (definition or {}).get("effort")
                or settings.effort_level or (settings.subagent_effort and not teammate))
