"""One handler per hook event. Each takes the hook's JSON and returns the JSON to print,
or None. cli.py swallows anything a handler raises, so a hook can't break a session.

The plugin never approves a tool call. It rewrites two fields of agent calls, and it
denies only to hold the user's own cap when a lowering rewrite didn't take."""
from __future__ import annotations

import re
import time

from . import allowlist, calls, gates, health, intake, ladder, learn, registry, request, session, settings, spawn, store, upstream
from . import config as cfg

_NEGATION = re.compile(r"(?:\bnot|n't|\bnever|\bwithout|\binstead of|\bavoid|\bno)\s+(?:\w+\s+){0,3}$")


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
        # Only what Claude must act on: every word here is in context for the whole session.
        parts.append(
            "Model Router sets every subagent's, teammate's and workflow agent's model and effort from its brief. "
            "Don't pass `model` or `effort` on Agent calls unless the user asked; a named one is kept. Say what "
            "makes a subtask hard (security, several modules, an unclear cause). If a result falls short, "
            "re-issue the same call unchanged: it runs one level higher."
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
        running = [hook_input.get("model")] + health.load().seen_running()
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
    if mode == "off":
        return None
    prompt = hook_input.get("prompt")
    prompt = prompt if isinstance(prompt, str) else ""
    session_id = hook_input.get("session_id")
    seen = intake.from_prompt(prompt, cfg.project_dir(hook_input))
    if seen.delivered:
        # Work for someone else's message isn't the user's request, so it doesn't get its floor.
        # A task notification continues the user's request and keeps it.
        try:
            request.deliver(session_id)
        except OSError:
            pass
    if not seen.person:
        return None
    usable = registry.available(config, options.cap, allowed=allowlist.read(session_id, cfg.project_dir(hook_input)))
    level = seen.level
    pick = ladder.pick(config, level, options.priority, usable, learn.raises())
    need = pick.model
    current = registry.family(config, session.model(session_id, hook_input.get("transcript_path")))
    below = bool(current and need and registry.strength(config, current) < registry.strength(config, need))
    wanted = named_model(config, prompt)
    request.start(session_id, prompt_id=hook_input.get("prompt_id"), level=level, need=need, below=below,
                  session_tier=current, named=wanted)
    why = f" (wording suggests: {', '.join(seen.signals)})" if seen.signals else ""
    notes = []
    if mode == "advise":
        if need:
            notes.append(f"Routing estimate for this request: {level}{why}, about {need}-level reasoning. "
                         "It reads only the wording; route by what the code shows.")
    elif below:
        agent = gates.strong_agent(config, need, level)
        asked = "; you will be asked to before finishing" if options.strong_agent_gate else ""
        hand = (f"Get the plan or decision (design, root cause, the risky choice) from {agent} rather than settling "
                f"it inline{asked}. It writes nothing, so make the change yourself." if gates.writes_nothing(agent)
                else f"Hand the hard thinking (design, root cause, the risky decision) to {agent} rather than "
                f"settling it inline{asked}.")
        notes.append(
            f"This request looks {level}{why}: about {need}-level reasoning, and this conversation runs on "
            f"{current}. {hand} If the code shows the task is simpler, say so instead."
        )
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
    try:
        health.refused(repeat_of)
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


def _asked_for(config: dict, tool_input: dict, routed: str | None) -> str | None:
    """The model a finished or failed call was meant to run on: the one it was routed to,
    or the one it named."""
    return routed or registry.family(config, tool_input.get("model"))


def post_agent(hook_input: dict) -> dict | None:
    config = cfg.load_config(hook_input)
    tool_input = cfg.tool_input(hook_input)
    response = hook_input.get("tool_response")
    response = response if isinstance(response, dict) else {}
    ran_as = response.get("resolvedModel")
    # A background agent's call returns as soon as the agent has launched. Its result
    # is back when the agent stops (agent_stopped), not here.
    launched = response.get("status") == "async_launched"
    agent_id = response.get("agentId")
    routed = None
    try:
        routed = calls.settled(hook_input.get("session_id"), tool_input, finished=not launched,
                               launched_as=agent_id if launched and isinstance(agent_id, str) else None)
    except OSError:
        pass
    asked = _asked_for(config, tool_input, routed)
    try:
        health.ran(asked, ran_as, registry.family(config, ran_as), settings.resolve(config).force)
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
    tool_input = cfg.tool_input(hook_input)
    routed = None
    try:
        routed = calls.settled(hook_input.get("session_id"), tool_input)
    except OSError:
        pass
    error = str(hook_input.get("error") or "")
    if not health.is_model_error(error, hook_input.get("is_interrupt")):
        return None
    config = cfg.load_config(hook_input)
    asked = _asked_for(config, tool_input, routed)
    if not asked:
        return None
    named = tool_input.get("model")
    newest = None
    if mod_active(hook_input.get("session_id")) and (not named or str(named).lower() == asked):
        newest = registry.newest_id(config, asked, settings.resolve(config).provider,
                                    allowlist.read(hook_input.get("session_id"), cfg.project_dir(hook_input)), health.load())
    if newest:
        # The mod sent the family's newest ID, and this provider refused it: the alias runs
        # from now on, and the ID is tried again after the outage.
        health.failed(newest, error, newest=True)
        store.append_ledger({"event": "model_error", "session": hook_input.get("session_id"),
                             "agent": tool_input.get("subagent_type"), "model": newest})
        return _context("PostToolUseFailure", (
            f"{newest} isn't available here. Re-issue this agent call unchanged: it now runs on the "
            f"'{asked}' alias, as Claude Code resolves it for this provider."))
    fails = health.failed(asked, error)
    store.append_ledger({"event": "model_error", "session": hook_input.get("session_id"),
                         "agent": tool_input.get("subagent_type"), "model": asked})
    cap = settings.resolve(config).cap
    allowed = allowlist.read(hook_input.get("session_id"), cfg.project_dir(hook_input))
    usable = [n for n in registry.available(config, cap, allowed=allowed) if n != asked]
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
    # A SubagentHandback report isn't in last_assistant_message, only the closing text is
    # (https://code.claude.com/docs/en/hooks.md#subagentstop).
    answered = not (isinstance(message, str) and not message.strip())
    if not answered:
        try:
            answered = session.handed_back(hook_input.get("agent_transcript_path"))
        except Exception:
            pass
    calls.stopped(hook_input.get("session_id"), hook_input.get("agent_id"), answered=answered)
    gates.note_agent_checks(hook_input)
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
    allowlist.remember(hook_input.get("session_id"), hook_input.get("settings"))
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
    newest = _newest(hook_input, decision)
    if decision["action"] != "rewrite" and not newest:
        return None
    return {"model": newest or decision["model"], "effort": decision["effort"], "reason": decision["reason"]}


def _newest(hook_input: dict, decision: dict) -> str | None:
    """The newest full ID to send in place of the decided family alias, on a provider whose
    alias lags. Only agent.spawn takes a full ID; the Agent tool's model takes an alias
    (https://code.claude.com/docs/en/sub-agents.md)."""
    if decision["action"] == "deny" or decision["version"]:
        return None
    config = cfg.load_config(hook_input)
    allowed = allowlist.read(hook_input.get("session_id"), cfg.project_dir(hook_input), hook_input.get("settings"))
    return registry.newest_id(config, decision["model"], settings.resolve(config).provider, allowed, health.load())


def _effort_keeps_cache(options: settings.Settings, model: object) -> bool:
    """Only these models, on Anthropic's API without a gateway or pre-release betas turned
    off, keep the prompt cache when effort changes. A model with no known version, as after
    /clear, gets the benefit of the doubt."""
    if options.cloud or options.base_url or options.no_betas:
        return False
    found = upstream.version_of(model)
    if not found:
        return True
    family, version = found
    return version >= (5, 1) if family == "fable" else family in ("opus", "sonnet", "haiku") and version == (5, 5)


def mod_turn(hook_input: dict) -> dict | None:
    """Effort for one main-loop turn, from the request. Effort is the lever that keeps the
    prompt cache on current models; switching the conversation's model would not."""
    session_id = hook_input.get("session_id")
    _mark_mod(session_id)
    allowlist.remember(session_id, hook_input.get("settings"))
    config = cfg.load_config(hook_input)
    options = settings.resolve(config)
    if (options.mode != "auto" or not options.main_effort or options.effort_level
            or hook_input.get("first_party") is False):
        return None
    seen = intake.from_turn(hook_input.get("text"), hook_input.get("command"), hook_input.get("origin"))
    if not seen.person:
        return None
    # The mod's live model, when it is an ID; a record can miss a fallback to another model.
    model = hook_input.get("model")
    model = model if upstream.version_of(model) else session.model(session_id)
    if not _effort_keeps_cache(options, model):
        return None
    level = seen.level
    effort = ((config.get("main") or {}).get("effort") or {}).get(level)
    if effort not in cfg.EFFORTS:
        return None
    store.append_ledger({"event": "turn", "session": hook_input.get("session_id"), "level": level,
                         "effort": effort, **({"command": seen.command} if seen.command else {})})
    return {"level": level, "effort": effort}


def _tokens(usage: dict) -> dict:
    tokens = {k: int(usage.get(k) or 0) for k in
              ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
    if "ephemeral_1h_input_tokens" in usage:
        tokens["ephemeral_1h_input_tokens"] = int(usage.get("ephemeral_1h_input_tokens") or 0)
    return tokens


def _usage(value: object) -> bool:
    return isinstance(value, dict) and isinstance(value.get("model"), str)


def mod_usage(hook_input: dict) -> dict | None:
    """One ledger row per turn, its cost the sum of its requests, each priced on its own."""
    usage = hook_input.get("usage")
    if not _usage(usage):
        return None
    config = cfg.load_config(hook_input)
    tokens = _tokens(usage)
    requests = hook_input.get("requests")
    requests = [r for r in requests if _usage(r)] if isinstance(requests, list) else []
    if requests:
        costs = [registry.cost(config, r["model"], _tokens(r)) for r in requests]
        spent = None if None in costs else round(sum(costs), 6)
    else:
        spent = registry.cost(config, usage["model"], tokens, whole_turn=True)
    store.append_ledger({"event": "usage", "session": hook_input.get("session_id"),
                         "agent_id": hook_input.get("agent_id"), "model": usage["model"], **tokens,
                         "cost_usd": spent})
    return None


HANDLERS = {
    "mod-start": mod_start, "mod-spawn": mod_spawn, "mod-turn": mod_turn, "mod-usage": mod_usage,
    "session-start": session_start, "user-prompt": user_prompt, "pre-agent": pre_agent,
    "post-agent": post_agent, "agent-failed": agent_failed, "agent-stopped": agent_stopped,
    "post-edit": post_edit, "stop": stop,
    "model-switch": model_switch, "refresh-cache": refresh_cache,
}
