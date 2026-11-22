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

This module only reads. hooks.py records what was decided."""
from __future__ import annotations

import hashlib
import os
import time

from . import agents, learn, registry, session, store
from . import config as cfg
from .profile import profile_call


# Without a request id to compare, a call seen again within this long counts as a redo.
REDO_WINDOW = 30 * 60


def call_key(agent: str, tool_input: dict) -> str:
    """Identifies a call by what it asks for, so the same call issued again is recognized."""
    text = agent + "\0" + str(tool_input.get("prompt") or "")
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def decide(hook_input: dict, config: dict) -> dict:
    tool_input = hook_input.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    agent = tool_input.get("subagent_type")
    agent = agent if isinstance(agent, str) and agent else "general-purpose"
    out = {"agent": agent, "action": "keep", "model": None, "effort": None, "level": None,
           "signals": [], "source": None, "pick": None, "was": None, "reason": "", "repeat_of": None,
           "escalated_from": None}
    if agent == "fork":
        out["reason"] = "a fork runs on its conversation's model"
        return out

    project = cfg.project_dir(hook_input)
    session_id = hook_input.get("session_id")
    usable = registry.available(config, project)
    # Inside a subagent, an agent without a model runs on that subagent's model, which
    # we can't see. Treat it as unknown so the pick is always written onto the call.
    nested = bool(hook_input.get("agent_id"))
    if hook_input.get("parent_model"):
        # A mod's spawn event names the parent's effective model exactly, nested or not.
        parent = registry.family(config, hook_input["parent_model"])
    else:
        parent = None if nested else registry.family(
            config, session.model(session_id, hook_input.get("transcript_path")))
    requested = registry.family(config, tool_input.get("model"))
    definition = agents.find(agent, project)
    if definition is None and hook_input.get("kind") in ("workflow", "teammate"):
        # The engine makes these agents: a workflow stage, or a teammate role with no file
        # of its own. Neither names a model unless the script or the call does.
        definition = {"name": agent, "source": hook_input["kind"], "model": None}
    own_model = (definition or {}).get("model")
    pinned = registry.family(config, own_model)
    env_default = registry.family(config, os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL"))
    forced = cfg.truthy(os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL_FORCE"))
    # The built-in Explore and Plan have had their own default model in some versions,
    # so what they would run on isn't assumed.
    special = bool(definition) and definition["source"] == "builtin" and agent in ("Explore", "Plan")
    if requested or pinned:
        out["was"] = requested or pinned
    elif definition is not None and not own_model and not special:
        out["was"] = env_default or parent

    rule = (config.get("agents") or {}).get(agent) or {}
    profile = profile_call(tool_input, agent)
    level = rule.get("level") or profile["level"]
    done = store.load_entry("done", session_id).get(call_key(agent, tool_input))
    request = store.load_entry("prompts", session_id).get("prompt_id")
    same_request = isinstance(done, dict) and (
        done.get("prompt_id") == request if request else time.time() - (done.get("at") or 0) < REDO_WINDOW)
    if same_request and done.get("level") in cfg.LEVELS and not tool_input.get("model"):
        # The same call again after its result came back: the first answer wasn't good
        # enough. Re-running at the next level is cheaper than starting every call high.
        above = cfg.LEVELS.index(done["level"]) + 1
        if above < len(cfg.LEVELS) and cfg.LEVELS.index(level) < above:
            level, out["escalated_from"] = cfg.LEVELS[above], done["level"]
    spec = (config.get("levels") or {}).get(level) or {}
    needs = float(spec.get("needs") or cfg.LEVELS.index(level) + 1) + learn.bump(level)
    priority = cfg.priority()
    pick = registry.select(config, needs, priority, usable)
    out.update(level=level, signals=profile["signals"], pick=pick)

    mode = cfg.mode(config)
    if mode != "auto" or not usable:
        out["reason"] = f"routing is {mode}"
        return out
    if rule.get("route") is False:
        out["reason"] = "a rule in model-router.json leaves this agent alone"
        return out
    if forced:
        # The user pinned every subagent's model. Effort is still free to set, and on a
        # pinned strong model it is the only lever left on cost.
        out.update(was=env_default or parent, reason="CLAUDE_CODE_SUBAGENT_MODEL_FORCE pins every subagent's model")
        effort = None if _effort_decided(tool_input, definition) else spec.get("effort")
        if priority == "fast" and effort != "low":
            effort = None
        if effort and not tool_input.get("model"):
            out.update(action="rewrite", effort=effort, source="router",
                       reason=f"{level} subtask; the model is pinned by CLAUDE_CODE_SUBAGENT_MODEL_FORCE, so only effort is set")
        return out

    rule_model = registry.family(config, rule.get("model"))
    override = cfg.pins() == "override"
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
    if source in ("router", "pin", "rule") and not _effort_decided(tool_input, definition):
        effort = spec.get("effort")
        if priority == "fast" and effort != "low":
            effort = None  # more thinking is slower; fast keeps only the reductions
    if out.get("escalated_from") and isinstance(done, dict):
        effort = _above_earlier(config, effort, final, done)
    out.update(model=final, source=source, reason=why)
    if final == out["was"] and not effort:
        return out  # it would run on this model anyway

    earlier = store.load_entry("pending", session_id).get(call_key(agent, tool_input))
    if isinstance(earlier, dict):
        # We rewrote this very call before and no result came back: the rewrite wasn't
        # accepted, or something else stopped the call. Rewriting it again could loop.
        limit = cfg.cap(config)
        if limit and registry.strength(config, target) > registry.strength(config, limit):
            out.update(action="deny", reason=(
                f"Model Router: subagents are capped at {limit} by the user, and this call would run on "
                f"{target}. Re-issue it with model: \"{final}\"."))
        else:
            out.update(action="keep", model=None, repeat_of=earlier.get("model"), reason=(
                "this call was rewritten once and came back without having run, so it is passed "
                "through unchanged"))
        return out
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


def _effort_decided(tool_input: dict, definition: dict | None) -> bool:
    """Someone already chose this call's effort: the caller, the agent, or the user's environment."""
    return bool("effort" in tool_input or (definition or {}).get("effort")
                or os.environ.get("CLAUDE_CODE_EFFORT_LEVEL") or os.environ.get("CLAUDE_CODE_SUBAGENT_EFFORT"))


def rewritten_input(tool_input: dict, decision: dict) -> dict:
    """The whole call as it should run: Claude Code replaces the input, it doesn't merge."""
    updated = dict(tool_input)
    if decision.get("model"):
        updated["model"] = decision["model"]
    if decision.get("effort"):
        updated["effort"] = decision["effort"]
    return updated


def note(decision: dict) -> str:
    """One line for Claude, delivered with the agent's result."""
    if not decision.get("model"):
        return (f"Model Router set this agent's effort to {decision['effort']} ({decision['reason']}). "
                "If the result falls short, re-issue the same call: it runs one level higher.")
    effort = f" at {decision['effort']} effort" if decision.get("effort") else ""
    return (f"Model Router set this agent's model to {decision['model']}{effort} ({decision['reason']}). "
            "If the result falls short, re-issue the same call: it runs one level higher.")
