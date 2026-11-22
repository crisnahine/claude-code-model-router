"""Decide the model and reasoning effort for one agent call.

The agent the caller chose is never changed. Only `model` and `effort` on the call are,
and only when that makes a difference. Order of authority, strongest first:

  1. CLAUDE_CODE_SUBAGENT_MODEL_FORCE   the user pinned every subagent: set effort only
  2. a model named on the call          Claude, the user through Claude, or a skill: keep
  3. a per-agent rule in an override file
  4. the agent's own model              kept unless agent_pins is "override"
  5. CLAUDE_CODE_SUBAGENT_MODEL         the user's default for agents without a model
  6. the router's pick                  from the subtask's profile, priority and feedback

Whatever wins is then lowered, never raised, to fit the cap and what is available.

decide only decides: the spawn procedure gathers the facts and records the outcome."""
from __future__ import annotations

from dataclasses import dataclass

from . import ladder, registry
from . import config as cfg
from .profile import profile_call
from .settings import Settings


@dataclass(frozen=True)
class Facts:
    """What decide needs to know about one call."""
    agent: str
    tool_input: dict
    usable: list  # registry.available for this project and cap
    definition: dict | None = None  # agents.find, or the engine's stand-in for its own agents
    parent: str | None = None  # the model an agent without one inherits, when known
    earlier: dict | None = None  # calls.finished_run: this call's finished run in this request


def decide(facts: Facts, config: dict, settings: Settings) -> dict:
    tool_input, agent, definition, usable = facts.tool_input, facts.agent, facts.definition, facts.usable
    out = {"agent": agent, "action": "keep", "model": None, "effort": None, "level": None,
           "signals": [], "source": None, "pick": None, "was": None, "reason": "", "repeat_of": None,
           "escalated_from": None}
    if agent == "fork":
        out["reason"] = "a fork runs on its conversation's model"
        return out

    requested = registry.family(config, tool_input.get("model"))
    own_model = (definition or {}).get("model")
    pinned = registry.family(config, own_model)
    env_default = registry.family(config, settings.subagent_model)
    # The built-in Explore and Plan have had their own default model in some versions,
    # so what they would run on isn't assumed.
    special = bool(definition) and definition["source"] == "builtin" and agent in ("Explore", "Plan")
    if requested or pinned:
        out["was"] = requested or pinned
    elif definition is not None and not own_model and not special:
        out["was"] = env_default or facts.parent

    rule = (config.get("agents") or {}).get(agent) or {}
    profile = profile_call(tool_input, agent)
    level = rule.get("level") or profile["level"]
    done = facts.earlier
    if done and done.get("level") in cfg.LEVELS and not tool_input.get("model"):
        # The same call again after its result came back: the first answer wasn't good
        # enough. Re-running at the next level is cheaper than starting every call high.
        above = cfg.LEVELS.index(done["level"]) + 1
        if above < len(cfg.LEVELS) and cfg.LEVELS.index(level) < above:
            level, out["escalated_from"] = cfg.LEVELS[above], done["level"]
    pick, level_effort = ladder.pick(config, level, settings.priority, usable)
    out.update(level=level, signals=profile["signals"], pick=pick)

    if settings.mode != "auto" or not usable:
        out["reason"] = f"routing is {settings.mode}"
        return out
    if rule.get("route") is False:
        out["reason"] = "a rule in model-router.json leaves this agent alone"
        return out
    if settings.force:
        # The user pinned every subagent's model. Effort is still free to set, and on a
        # pinned strong model it is the only lever left on cost.
        out.update(was=env_default or facts.parent,
                   reason="CLAUDE_CODE_SUBAGENT_MODEL_FORCE pins every subagent's model")
        effort = None if _effort_decided(tool_input, definition, settings) else level_effort
        if effort and not tool_input.get("model"):
            out.update(action="rewrite", effort=effort, source="router",
                       reason=f"{level} subtask; the model is pinned by CLAUDE_CODE_SUBAGENT_MODEL_FORCE, so only effort is set")
        return out

    rule_model = registry.family(config, rule.get("model"))
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

    final = registry.clamp(config, target, usable)
    if final != target:
        why += f"; {target} is over the cap or unavailable here"
    effort = None
    # Effort is set whenever nobody chose it, a pinned agent included: the pin is about
    # the model, and on current models effort is the cheaper lever for quality.
    if source in cfg.LEVEL_SOURCES and not _effort_decided(tool_input, definition, settings):
        effort = level_effort
    if out.get("escalated_from") and done:
        effort = _above_earlier(config, effort, final, done)
    out.update(model=final, source=source, reason=why)
    if final == out["was"] and not effort:
        return out  # it would run on this model anyway
    out.update(action="rewrite", effort=effort)
    return out


def _above_earlier(config: dict, effort: str | None, model: str | None, done: dict) -> str | None:
    """A redo must ask for more than the run it replaces. When the cap or availability
    keeps it on the same model, that means more effort than last time."""
    if registry.strength(config, model) > registry.strength(config, done.get("model")):
        return effort
    earlier = done.get("effort")
    if earlier not in cfg.EFFORTS:
        return effort
    floor = cfg.EFFORTS[min(cfg.EFFORTS.index(earlier) + 1, len(cfg.EFFORTS) - 1)]
    return effort if effort in cfg.EFFORTS and cfg.EFFORTS.index(effort) >= cfg.EFFORTS.index(floor) else floor


def _effort_decided(tool_input: dict, definition: dict | None, settings: Settings) -> bool:
    """Someone already chose this call's effort: the caller, the agent, or the user's environment."""
    return bool("effort" in tool_input or (definition or {}).get("effort")
                or settings.effort_level or settings.subagent_effort)
