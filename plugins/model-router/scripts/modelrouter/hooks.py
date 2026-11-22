"""One handler per hook event. Each takes the hook's JSON and returns the JSON to print,
or None. cli.py swallows anything a handler raises, so a hook can't break a session.

The plugin never approves a tool call. It rewrites two fields of agent calls, and it
denies only to hold the user's own cap when a lowering rewrite didn't take."""
from __future__ import annotations

import re
import time

from . import agents, calls, gates, ladder, learn, registry, session, settings, spawn, store, upstream
from . import config as cfg
from .profile import profile_prompt

_NEGATION = re.compile(r"(?:\bnot|n't|\bnever|\bwithout|\binstead of|\bavoid|\bno)\s+(?:\w+\s+){0,3}$")


def is_user_prompt(prompt: str) -> bool:
    """UserPromptSubmit also fires for background-task notifications and similar
    machine-written turns. Those must not reset what is known about the user's request."""
    return not (prompt or "").lstrip().startswith("<")


def named_model(config: dict, prompt: str) -> str | None:
    """A model the user may be asking for: "use opus", "run this on haiku". These are
    ordinary words too, so this only prompts Claude to decide; it forces nothing."""
    names = "|".join(re.escape(n) for n in registry.models(config))
    if not names:
        return None
    text = (prompt or "").lower()
    pattern = r"\b(?:use|using|with|on|via|to)\s+(?:the\s+)?(?:claude\s+)?(%s)\b(?!\s*(?:agent|subagent)s?\b)" % names
    for match in re.finditer(pattern, text):
        if not _NEGATION.search(text[max(0, match.start() - 40):match.start()]):
            return match.group(1)
    return None


def _context(event: str, text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def session_start(hook_input: dict) -> dict | None:
    config = cfg.load_config(hook_input)
    options = settings.resolve(config)
    settings.record(options)
    session.record(hook_input.get("session_id"), hook_input.get("model"))
    parts = []
    mode = options.mode
    name = cfg.PLUGIN_NAME
    if mode == "auto":
        top = registry.ceiling(config, cfg.project_dir(hook_input), options.cap)
        crew = ", ".join(f"{name}:{agent} ({spec.get('role', '').lower()})"
                         for agent, spec in (config.get("crew") or {}).items())
        parts.append(
            "Model Router is on. It sets the model and reasoning effort of every subagent, teammate and "
            "workflow agent, from the subtask's brief, whichever agent, skill or plugin starts it (strongest "
            f"model in use: {top}). It starts each one on the cheapest model and effort that should do the job. "
            "So: don't pass `model` or `effort` on Agent calls unless the user asked for a specific model or "
            "you know something the brief doesn't show; what you name is kept. Write complete briefs and say "
            "what makes a subtask hard (security, several modules, an unclear root cause), since the brief is "
            "what the model is chosen from. Delegate reading-heavy and independent work, in parallel where it "
            "doesn't touch the same files, and don't delegate what takes you one or two tool calls. Check "
            "delegated results. If one isn't good enough, re-issue the same call unchanged: it runs one level "
            "higher (more effort, then a stronger model), and the router learns from it. Role agents exist "
            f"for when no better-fitting agent does: {crew}. /{name}:ship runs a full plan, build, verify and "
            "review loop."
        )
        if options.force:
            pinned = options.subagent_model or "one model"
            parts.append(
                f"CLAUDE_CODE_SUBAGENT_MODEL_FORCE pins every subagent to {pinned}, so Model Router sets only "
                "their effort. Tell the user once: removing CLAUDE_CODE_SUBAGENT_MODEL_FORCE from their settings "
                "lets the router put each subtask on the cheapest model strong enough for it."
            )
    elif mode == "advise":
        parts.append(
            "Model Router is in advise mode: it estimates the model each request needs and tells you, but "
            "changes nothing. Pass `model` on Agent calls to match the estimate."
        )
    if options.update_check:
        changes = upstream.pending()
        running = [hook_input.get("model")] + [info.get("last_model") for info in registry.health().values()
                                               if isinstance(info, dict)]
        for model_id, latest in upstream.behind(running):
            parts.append(
                f"{model_id} is running here and {latest} is the newest of its family. Tell the user once: a model "
                "setting naming a full version stays on it, so use the alias (opus, sonnet, haiku, fable) to follow "
                "new versions, and update Claude Code, which moves each alias to the newest version.")
        if changes and changes["new_model_families"]:
            parts.append("New Claude model alias in the docs that isn't in Model Router's registry yet: "
                         + ", ".join(changes["new_model_families"]) + f". Tell the user once; /{name}:refresh "
                         "reviews it.")
        if changes and changes.get("deprecated_in_use"):
            parts.append("Models this setup routes to are deprecated or retiring: " + "; ".join(
                f"{m['model']} retires {m['retires']}" for m in changes["deprecated_in_use"])
                + ". Tell the user once; the model aliases move on by themselves, but a pinned full ID won't.")
        if upstream.has_pending(changes):
            parts.append(
                f"Upstream: {len(changes['new_releases'])} Claude Code release(s) and "
                f"{len(changes['watched_pages_touched'])} plugin-relevant docs page change(s) since the last "
                f"review. Mention once to the user that /{name}:refresh reviews them."
            )
    return _context("SessionStart", " ".join(parts)) if parts else None


def user_prompt(hook_input: dict) -> dict | None:
    """Size up the request against the conversation's own model. Says nothing when there
    is nothing Claude needs to do differently, so an ordinary prompt costs no tokens."""
    config = cfg.load_config(hook_input)
    options = settings.resolve(config)
    mode = options.mode
    prompt = hook_input.get("prompt")
    prompt = prompt if isinstance(prompt, str) else ""
    if mode == "off" or not is_user_prompt(prompt):
        return None
    session_id = hook_input.get("session_id")
    usable = registry.available(config, cfg.project_dir(hook_input), options.cap)
    result = profile_prompt(prompt)
    level = result["level"]
    need = ladder.pick(config, level, options.priority, usable, learn.raises()).model
    current = registry.family(config, session.model(session_id, hook_input.get("transcript_path")))
    below = bool(current and need and registry.strength(config, current) < registry.strength(config, need))
    store.save_entry("prompts", session_id, {
        "prompt_id": hook_input.get("prompt_id"), "level": level, "need": need, "below": below,
        "session_tier": current})
    why = f" (wording suggests: {', '.join(result['signals'])})" if result["signals"] else ""
    notes = []
    if mode == "advise":
        notes.append(f"Routing estimate for this request: {level}{why}, about {need}-level reasoning. "
                     "It reads only the wording; route by what the code shows.")
    elif below:
        notes.append(
            f"This request looks {level}{why}: about {need}-level reasoning, and this conversation runs on "
            f"{current}. This conversation keeps its model, so this step is required: "
            f"hand the hard thinking (the design, the root cause, the risky decision) to "
            f"{gates.strong_agent(config, need)} rather than settling it inline. You will be asked to before "
            "finishing if you don't. If the code shows the task is simpler than its wording, say so instead."
        )
    wanted = named_model(config, prompt)
    if wanted and mode == "auto":
        notes.append(f"If the user is asking for the {wanted} model here, pass model: \"{wanted}\" on the "
                     "Agent calls for this request. A model named on a call is kept.")
    return _context("UserPromptSubmit", " ".join(notes)) if notes else None


def mod_active(session_id: str | None) -> bool:
    return bool(store.load_entry("mod", session_id).get("active"))


def pre_agent(hook_input: dict) -> dict | None:
    """The fallback path: the decision becomes the call's rewritten input and a note, or
    the one deny that holds the user's cap."""
    if mod_active(hook_input.get("session_id")):
        return None  # the plugin's mod routes this call when it spawns
    session_id = hook_input.get("session_id")
    decision = spawn.run(hook_input, "fallback", guard=lambda *args: _refused_rewrite(session_id, *args))
    if decision["action"] == "rewrite":
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "updatedInput": _rewritten_input(cfg.tool_input(hook_input), decision),
            "additionalContext": _note(decision)}}
    if decision["action"] == "deny":
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": decision["reason"]}}
    return None


def _refused_rewrite(session_id: str | None, decision: dict, key: str, config: dict,
                     options: settings.Settings) -> None:
    """We rewrote this very call before and no result came back: the rewrite wasn't
    accepted, or something else stopped the call. Rewriting it again could loop."""
    if decision["action"] != "rewrite" or not decision["model"]:
        return
    earlier = calls.refused_rewrite(session_id, key)
    if earlier is None:
        return
    cap, untouched = options.cap, decision["was"]
    if cap and registry.strength(config, untouched) > registry.strength(config, cap):
        decision.update(action="deny", reason=(
            f"Model Router: subagents are capped at {cap} by the user, and this call would run on "
            f"{untouched}. Re-issue it with model: \"{decision['model']}\"."))
        return
    decision.update(action="keep", model=None, repeat_of=earlier.get("model"), reason=(
        "this call was rewritten once and came back without having run, so it is passed through unchanged"))
    repeat_of = decision["repeat_of"]
    if repeat_of and not (registry.health().get(repeat_of) or {}).get("last_model"):
        # A rewrite to a model that has never once run here didn't run this time either.
        try:
            registry.record_failure(repeat_of, "a call rewritten to this model came back without having run")
        except OSError:
            pass


def _rewritten_input(tool_input: dict, decision: dict) -> dict:
    """The whole call as it should run: Claude Code replaces the input, it doesn't merge."""
    updated = dict(tool_input)
    if decision.get("model"):
        updated["model"] = decision["model"]
    if decision.get("effort"):
        updated["effort"] = decision["effort"]
    return updated


def _note(decision: dict) -> str:
    """One line for Claude, delivered with the agent's result."""
    if decision.get("model"):
        effort = f" at {decision['effort']} effort" if decision.get("effort") else ""
        what = f"model to {decision['model']}{effort}"
    else:
        what = f"effort to {decision['effort']}"
    return (f"Model Router set this agent's {what} ({decision['reason']}). "
            "If the result falls short, re-issue the same call: it runs one level higher.")


def _asked_for(config: dict, hook_input: dict) -> str | None:
    """The model a finished or failed call was meant to run on, if it named one."""
    tool_input = cfg.tool_input(hook_input)
    asked = registry.family(config, tool_input.get("model"))
    if asked:
        return asked
    agent = tool_input.get("subagent_type")
    if isinstance(agent, str) and agent:
        definition = agents.find(agent, cfg.project_dir(hook_input))
        return registry.family(config, (definition or {}).get("model"))
    return None


def post_agent(hook_input: dict) -> dict | None:
    config = cfg.load_config(hook_input)
    tool_input = cfg.tool_input(hook_input)
    response = hook_input.get("tool_response")
    response = response if isinstance(response, dict) else {}
    ran_as = response.get("resolvedModel")
    asked = _asked_for(config, hook_input)
    # A background agent's call returns as soon as the agent has launched. Its result
    # is back when the agent stops (agent_stopped), not here.
    launched = response.get("status") == "async_launched"
    agent_id = response.get("agentId")
    try:
        calls.settled(hook_input.get("session_id"), tool_input, finished=not launched,
                      launched_as=agent_id if launched and isinstance(agent_id, str) else None)
        if asked and registry.family(config, ran_as):
            registry.record_result(asked, ran_as, registry.family(config, ran_as))
    except OSError:
        pass
    store.append_ledger({
        "event": "result", "session": hook_input.get("session_id"),
        "agent": calls.identify(tool_input)[0], "asked": asked,
        "resolved_model": ran_as, "status": response.get("status"), "total_tokens": response.get("totalTokens")})
    return None


def agent_failed(hook_input: dict) -> dict | None:
    """An agent call failed. If the error says the model itself isn't usable, count it,
    and route around that model once it has happened twice."""
    try:
        calls.settled(hook_input.get("session_id"), cfg.tool_input(hook_input))
    except OSError:
        pass
    error = str(hook_input.get("error") or "")
    if hook_input.get("is_interrupt") or not registry.MODEL_ERROR.search(error):
        return None
    config = cfg.load_config(hook_input)
    asked = _asked_for(config, hook_input)
    if not asked:
        return None
    fails = registry.record_failure(asked, error)
    store.append_ledger({"event": "model_error", "session": hook_input.get("session_id"),
                         "agent": cfg.tool_input(hook_input).get("subagent_type"), "model": asked})
    cap = settings.resolve(config).cap
    usable = [n for n in registry.available(config, cfg.project_dir(hook_input), cap) if n != asked]
    if not usable:
        return None
    return _context("PostToolUseFailure", (
        f"The '{asked}' model looks unavailable here. Re-issue this agent call with model: "
        f"\"{registry.clamp(config, asked, usable)}\"."
        + (" Model Router will route around it for now." if fails >= 2 else "")))


def agent_stopped(hook_input: dict) -> dict | None:
    """A subagent stopped. For one launched in the background, this is when its result
    comes back, so its call counts as finished from here."""
    message = hook_input.get("last_assistant_message")
    calls.stopped(hook_input.get("session_id"), hook_input.get("agent_id"),
                  answered=not (isinstance(message, str) and not message.strip()))
    return None


def post_edit(hook_input: dict) -> dict | None:
    gates.note_edit(hook_input)
    return None


def stop(hook_input: dict) -> dict | None:
    config = cfg.load_config(hook_input)
    reason = gates.before_stop(hook_input, config, settings.resolve(config))
    return {"decision": "block", "reason": reason} if reason else None


def model_switch(hook_input: dict) -> dict | None:
    session.record(hook_input.get("session_id"), hook_input.get("to_model"))
    return None


def refresh_cache(hook_input: dict) -> dict | None:
    options = settings.resolve()
    if options.update_check:
        upstream.refresh(options, force=False)
    return None


def mod_start(hook_input: dict) -> dict | None:
    _mark_mod(hook_input.get("session_id"))
    return None


def _mark_mod(session_id: str | None) -> None:
    """The mod's session.start fires once per process, never after /clear, so every
    turn and spawn says the mod is running."""
    if session_id and not mod_active(session_id):
        store.save_entry("mod", session_id, {"active": True, "at": time.time()})


def mod_spawn(hook_input: dict) -> dict | None:
    _mark_mod(hook_input.get("session_id"))
    # The spawn procedure reads agent_id as "the call comes from inside a subagent".
    decision = spawn.run({**hook_input, "agent_id": hook_input.get("parent_agent_id")}, "mod")
    if decision["action"] != "rewrite":
        return None
    return {"model": decision["model"], "effort": decision["effort"], "reason": decision["reason"]}


def mod_turn(hook_input: dict) -> dict | None:
    """Effort for one main-loop turn, from the request. Effort is the lever that keeps the
    prompt cache on current models; switching the conversation's model would not."""
    _mark_mod(hook_input.get("session_id"))
    config = cfg.load_config(hook_input)
    options = settings.resolve(config)
    text = hook_input.get("text")
    text = text if isinstance(text, str) else ""
    # A cloud provider re-reads the whole context uncached whenever effort changes.
    if (options.mode != "auto" or not options.main_effort or not is_user_prompt(text)
            or options.effort_level or options.cloud):
        return None
    level = profile_prompt(text)["level"]
    effort = ((config.get("main") or {}).get("effort") or {}).get(level)
    if effort not in cfg.EFFORTS:
        return None
    store.append_ledger({"event": "turn", "session": hook_input.get("session_id"), "level": level,
                         "effort": effort})
    return {"level": level, "effort": effort}


def mod_usage(hook_input: dict) -> dict | None:
    usage = hook_input.get("usage")
    if not isinstance(usage, dict) or not isinstance(usage.get("model"), str):
        return None
    tokens = {k: int(usage.get(k) or 0) for k in
              ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
    store.append_ledger({"event": "usage", "session": hook_input.get("session_id"),
                         "agent_id": hook_input.get("agent_id"), "model": usage["model"], **tokens,
                         "cost_usd": registry.cost(cfg.load_config(hook_input), usage["model"], tokens)})
    return None


HANDLERS = {
    "mod-start": mod_start, "mod-spawn": mod_spawn, "mod-turn": mod_turn, "mod-usage": mod_usage,
    "session-start": session_start, "user-prompt": user_prompt, "pre-agent": pre_agent,
    "post-agent": post_agent, "agent-failed": agent_failed, "agent-stopped": agent_stopped,
    "post-edit": post_edit, "stop": stop,
    "model-switch": model_switch, "refresh-cache": refresh_cache,
}
