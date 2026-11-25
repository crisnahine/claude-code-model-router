"""Decide the model and effort for one call, from facts the spawn procedure gathers.
The agent never changes. Claude names a call's model and effort; the router holds what it names to the
user's cap and to what is usable here, raises it to a rule's level, and sets the effort nobody chose. The
order of authority is in docs/ARCHITECTURE.md. decide reads no state: every input arrives as a fact."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Optional, TypedDict

from . import ladder
from .config import _PLAIN_NAME, EFFORTS, Level
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
    no_step: bool  # a redo with nothing stronger usable and no effort to gain: it runs as the run it redoes did


@dataclass(frozen=True)
class Facts:
    """What decide needs to know about one call."""
    agent: str
    tool_input: dict
    usable: list  # registry.available for this project and cap
    definition: dict | None = None  # agents.find, or the engine's stand-in for its own agents
    parent: str | None = None  # the model an agent without one inherits, when known
    earlier: dict | None = None  # the run a marked redo climbs from; None when its own model and effort already ask for more
    on_record: bool = True  # False when `earlier` is what a marked redo would run at unmarked, not a finished run
    redoes: Level | None = None  # the level of the run a redo that runs as named redoes
    teammate: bool = False  # an agent-team teammate, which CLAUDE_CODE_SUBAGENT_EFFORT doesn't reach
    workflow: bool = False  # a workflow agent, which CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL pins
    session_effort: str | None = None  # the main conversation's level, for a call made from it
    effort_caps: Mapping[str, str] = field(default_factory=dict)  # maxEffortLevel per registry model, where set
    lagging: frozenset = frozenset()  # Snapshot.lagging: the router's own pick moves one family up from these


def decide(facts: Facts, reg: Registry, settings: Settings) -> Decision:
    tool_input, agent, definition, usable = facts.tool_input, facts.agent, facts.definition, facts.usable
    out: Decision = {"agent": agent, "action": "keep", "model": None, "effort": None, "level": None,
                     "source": None, "was": None, "reason": "", "repeat_of": None, "escalated_from": facts.redoes,
                     "version": None, "held": None, "no_step": False}
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
    redo = done_level is not None and reg.family(done.get("model")) is not None
    free = not _env_effort(settings, facts.teammate)

    if settings.force:
        model = env_default or facts.parent
        level = ladder.rung(reg, model, named_effort)
        effort = None if decided or tool_input.get("model") or not level else ladder.at_most(reg, model, ladder.effort(reg, level))
        step = ""
        if redo:
            # The model is pinned, so effort is the one lever a redo has.
            before = _ran_at(reg, done, done_level)
            effort = ladder.at_most(reg, model, _one_step(before)) if free else None
            level, out["escalated_from"] = ladder.rung(reg, model, effort) or done_level, done_level
            step = f"; a redo of the {done_level} run, " + (
                "one effort step up" if _above(effort, before) else f"and {model} gets no more effort")
        out["level"] = level
        return _pinned(out, "CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "subagent", model, level, effort, facts, step)
    if facts.workflow and settings.workflow_model and not tool_input.get("model"):
        # This pin wins over the model a mod sets, so it is kept like the one above.
        model = reg.family(settings.workflow_model)
        level = out["level"] = ladder.rung(reg, model, named_effort)
        effort = None if decided or not level else ladder.at_most(reg, model, ladder.effort(reg, level))
        return _pinned(out, "CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL", "workflow agent", model, level, effort, facts)

    floor = rule.level or rule.floor
    target, source, why, chosen = None, None, "", False  # chosen: the router picked the model, nobody named it
    unseen = False  # a project's floor moved a call whose inherited model and effort the router can't see
    if requested and not redo:  # a redo climbs past a model it names that asks for no more than the run it redoes
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
        elif reg.clamp(suggested, usable) == runs_on:
            pass  # the call runs on it anyway, and applying it would only reset the effort it inherits
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
            # An agent file can come with a repository, so only a plain name is shown back.
            shown = own_model if _PLAIN_NAME.fullmatch(str(own_model)) else "not a plain name"
            why = f"the agent names a model outside the registry ({shown})"
        elif pinned:
            target, source, why = pinned, "pin", "the agent names its own model"
        elif env_default and not redo:
            why = "CLAUDE_CODE_SUBAGENT_MODEL sets the model for agents without one"
    level = ladder.rung(reg, target or out["was"], named_effort)
    if target is None and why:
        out.update(level=level, reason=why + held)
        return out
    if redo:
        return _redo(out, facts, reg, done, done_level, free, held)
    if target is None:
        # Nothing names a model, so Claude Code's own order applies: the parent's model.
        runs_on = out["was"]
        lifts = bool(floor) and _floor_lifts(reg, rule, floor, level, runs_on, usable)
        if floor and not lifts and not rule.level and (level is None or level < floor):
            note = f"a project rule raises {agent} to {floor}, " + (
                f"whose model here is no stronger than the parent's {runs_on}" if runs_on
                else "and the model the call would run on isn't known")
            out["held"] = f"{out['held']}; {note}" if out["held"] else note
            held += f"; {note}"
        if lifts:
            level, target, source, chosen = floor, ladder.model_for(reg, floor), "rule", True
            why = f"a rule in model-router.json raises this agent to {floor}"
            unseen = runs_on is None and not rule.level and facts.session_effort is None
        elif facts.teammate and runs_on and reg.strength(runs_on) < reg.levels[Level.standard].needs:
            level, target, source, chosen = Level.standard, ladder.model_for(reg, Level.standard), "router", True
            why = f"a teammate starts no lower than {target}, since its model is fixed for all its tasks"
        elif runs_on and reg.clamp(runs_on, usable) != runs_on:
            # The parent's model was nobody's choice for this work, so the model the cap leaves runs at its own level.
            level = ladder.rung(reg, reg.clamp(runs_on, usable), named_effort)
            target, source, why = runs_on, "router", f"the call names no model and would run on the parent's {runs_on}"
        else:
            out.update(level=level, reason="no model is named for this call, so it runs on "
                       + (f"the parent's model, {runs_on}" if runs_on else "Claude Code's own choice") + held)
            return out
    elif floor and (level < floor or reg.stronger(ladder.model_for(reg, floor), target)):
        # A named model keeps its rung from the ladder without a project's file, which may still raise the floor's model.
        level, target, chosen = floor, ladder.model_for(reg, floor), True
        why += f"; a rule in model-router.json raises it to {floor}"

    named = tool_input.get("model") if source == "caller" else own_model if source == "pin" else None
    clamped = reg.clamp(target, usable)
    final = reg.past_lag(clamped, usable, facts.lagging) if chosen else clamped
    out["version"] = _version(reg, named, final)
    if clamped != target:
        why += f"; {target} is over the cap or unavailable here"
    if final != clamped:
        why += f"; {clamped}'s alias runs an older model here"
    # Effort is set whenever nobody chose it: on current models it is the cheaper lever for quality.
    effort = None if decided or unseen else ladder.effort_on(reg, level, final)
    effort, floor_note = _session_floor(effort, final, facts)
    effort, capped_note = capped(effort, facts.effort_caps.get(final))
    out.update(model=final, source=source, level=level, reason=why + floor_note + capped_note + held)
    if final == out["was"] and not effort:
        return out  # it would run on this model anyway
    out.update(action="rewrite", effort=effort)
    return out


def _redo(out: Decision, facts: Facts, reg: Registry, done: dict, done_level: Level, free: bool, held: str) -> Decision:
    """A marked redo climbs from the run it redoes: one effort step up on the same model while that run's
    effort was below high, else the next stronger usable model at its own rung's effort, else one more effort
    step, at most max or the model's `effort_stops_at`. A model or effort the call names that asks for no more
    than that run, the agent's own, a rule's model and CLAUDE_CODE_SUBAGENT_MODEL don't hold it back."""
    ran, before = reg.family(done.get("model")), _ran_at(reg, done, done_level)
    up = ladder.at_most(reg, ran, _one_step(before))
    gains = free and _above(capped(up, facts.effort_caps.get(ran))[0], before)
    stronger = next((n for n in facts.usable if reg.models[n].trusted and reg.stronger(n, ran)), None)
    # A nested run at its parent's effort (`spawn._inherited`) may already have run at the step up.
    if gains and not done.get("inherited") and EFFORTS.index(before) < EFFORTS.index("high"):
        model, effort, how = ran, up, f"one effort step up on {ran}"
    elif stronger:
        model, effort, how = stronger, ladder.effort(reg, ladder.rung(reg, stronger)), f"the next stronger model, {stronger}"
    else:
        model, effort = ran, up
        how = "no stronger model is usable" + (", so it runs one effort step up" if gains
                                               else f", and {ran} gets no more effort" if free else "")
    final = reg.clamp(model, facts.usable)
    out["no_step"] = not stronger and not gains and final == ran
    clamp_note = f"; {model} is over the cap or unavailable here" if final != model else ""
    if model != ran and reg.past_lag(final, facts.usable, facts.lagging) != final:
        # The next model is the router's own pick, so it passes over a family whose alias runs an older model.
        clamp_note += f"; {final}'s alias runs an older model here"
        final = reg.past_lag(final, facts.usable, facts.lagging)
        effort = ladder.effort(reg, ladder.rung(reg, final))
    effort, floor_note = _session_floor(ladder.at_most(reg, final, effort) if free else None, final, facts)
    effort, capped_note = capped(effort, facts.effort_caps.get(final))
    came = "the run that came back before" if facts.on_record else "the run it would get unmarked"
    at = "its parent's effort" if done.get("inherited") else before or "its default effort"
    # Left alone, the call runs on its own model, else the agent's.
    out["version"] = _version(reg, facts.tool_input.get("model") or (facts.definition or {}).get("model"), final)
    out.update(model=final, source="router", level=ladder.rung(reg, final, effort), escalated_from=done_level,
               reason=f"a redo of {came} ({ran} at {at}): {how}"
               + ("" if free else ", at the effort the environment sets") + clamp_note + floor_note + capped_note + held)
    if final == out["was"] and not effort:
        return out
    out.update(action="rewrite", effort=effort)
    return out


def _floor_lifts(reg: Registry, rule: Rule, floor: Level, level: Level | None, runs_on: str | None, usable: list) -> bool:
    """Whether a rule's level moves a call that names no model. The user's applies wherever the call stands below
    it. A project's only raises: it applies when its model is stronger than the one the call runs on, and, with
    that model unknown, only when no model in the registry is stronger, so it can't land below the inherited one."""
    if rule.level:
        return level is None or level < floor
    lift = reg.clamp(ladder.model_for(reg, floor), usable)
    if runs_on is None:
        return not any(reg.models[n].trusted and reg.stronger(n, lift) for n in reg.models)
    return level < floor and reg.stronger(lift, runs_on)


def _version(reg: Registry, named: object, final: str | None) -> str | None:
    """The caller's or the agent's exact ID, which only survives a cap that keeps its family."""
    return named if isinstance(named, str) and named.lower() != final and reg.family(named) == final else None


def _pinned(out: Decision, pin: str, scope: str, pinned: str | None, level: Level | None, effort: str | None,
            facts: Facts, redo: str = "") -> Decision:
    """The user pinned the model, so effort is the one lever left, and on a pinned strong model the one on cost."""
    out.update(was=pinned, reason=f"{pin} pins every {scope}'s model")
    if effort:
        effort, floor = _session_floor(effort, pinned, facts)
        effort, held = capped(effort, facts.effort_caps.get(pinned or ""))
        out.update(action="rewrite", effort=effort, source="router",
                   reason=f"{level} subtask; the model is pinned by {pin}, so only effort is set{redo}{floor}{held}")
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


def _ran_at(reg: Registry, done: dict, level: Level) -> str | None:
    """The effort the run a redo redoes ran at; one nothing on record set ran at its level's, as far as the router knows."""
    return done.get("effort") if done.get("effort") in EFFORTS else ladder.effort(reg, level)


def _one_step(effort: str | None) -> str | None:
    return EFFORTS[min(EFFORTS.index(effort) + 1, len(EFFORTS) - 1)] if effort in EFFORTS else None


def _above(effort: str | None, than: str | None) -> bool:
    return effort in EFFORTS and than in EFFORTS and EFFORTS.index(effort) > EFFORTS.index(than)


def runs_at(effort: str | None, tool_input: dict, definition: dict | None, settings: Settings,
            teammate: bool) -> str | None:
    """The effort a call runs at as far as the router can tell, in Claude Code's order
    (https://code.claude.com/docs/en/sub-agents.md#choose-an-effort-level): CLAUDE_CODE_EFFORT_LEVEL, the router's
    own `effort` (which the call then carries in place of one it named), the call's, the agent's, then
    CLAUDE_CODE_SUBAGENT_EFFORT. None when nothing it sees sets one."""
    env = str(settings.effort_level or "").lower()
    if env:
        return env if env in EFFORTS else None  # auto or unset sends no effort
    chosen = (effort, tool_input.get("effort"), (definition or {}).get("effort"),
              None if teammate else settings.subagent_effort)
    return next((e for e in (str(c).lower() for c in chosen if c) if e in EFFORTS), None)


def _env_effort(settings: Settings, teammate: bool) -> bool:
    """The user's environment sets the effort: CLAUDE_CODE_EFFORT_LEVEL, or CLAUDE_CODE_SUBAGENT_EFFORT, which skips teammates."""
    return bool(settings.effort_level or (settings.subagent_effort and not teammate))


def _effort_decided(tool_input: dict, definition: dict | None, settings: Settings, teammate: bool) -> bool:
    """Someone already chose this call's effort: the caller, the agent, or the user's environment."""
    return bool("effort" in tool_input or (definition or {}).get("effort") or _env_effort(settings, teammate))
