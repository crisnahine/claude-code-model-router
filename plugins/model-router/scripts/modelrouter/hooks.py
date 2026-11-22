"""One handler per hook event. Each takes the hook's JSON and returns the JSON to print,
or None. cli.py swallows anything a handler raises, so a hook can't break a session.

The plugin never approves a tool call. It rewrites two fields of agent calls, and it
denies only to hold the user's own cap when a lowering rewrite didn't take."""
from __future__ import annotations

import re
import time

from . import agents, gates, learn, registry, router, session, store, upstream
from . import config as cfg
from .profile import profile_prompt

_NEGATION = re.compile(r"(?:\bnot|n't|\bnever|\bwithout|\binstead of|\bavoid|\bno)\s+(?:\w+\s+){0,3}$")
PENDING_KEPT = 100


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


def _tool_input(hook_input: dict) -> dict:
    tool_input = hook_input.get("tool_input")
    return tool_input if isinstance(tool_input, dict) else {}


def session_start(hook_input: dict) -> dict | None:
    config = cfg.load_config(hook_input)
    options = {"max_tier": cfg.cap(config) or "", "priority": cfg.priority(), "agent_pins": cfg.pins()}
    if any(store.load_state().get(k) != v for k, v in options.items()):
        store.update_state(lambda state: state.update(options))
    session.record(hook_input.get("session_id"), hook_input.get("model"))
    parts = []
    mode = cfg.mode(config)
    name = cfg.PLUGIN_NAME
    if mode == "auto":
        top = registry.ceiling(config, cfg.project_dir(hook_input))
        crew = ", ".join(f"{name}:{agent} ({spec.get('role', '').lower()})"
                         for agent, spec in (config.get("crew") or {}).items())
        parts.append(
            "Model Router is on. It sets the model and reasoning effort of every subagent you start, from "
            f"the subtask's brief, whichever agent, skill or plugin starts it (strongest model in use: {top}). "
            "So: don't pass `model` on Agent calls unless the user asked for a specific model or you know "
            "something the brief doesn't show; a model you name is kept. Write complete briefs and say what "
            "makes a subtask hard (security, several modules, an unclear root cause), since the brief is "
            "what the model is chosen from. Delegate reading-heavy and independent work, in parallel where "
            "it doesn't touch the same files, and don't delegate what takes you one or two tool calls. If a "
            "delegated result isn't good enough, re-issue the call with a stronger `model`; the router "
            f"learns from that. Role agents exist for when no better-fitting agent does: {crew}. "
            f"/{name}:ship runs a full plan, build, verify and review loop."
        )
    elif mode == "advise":
        parts.append(
            "Model Router is in advise mode: it estimates the model each request needs and tells you, but "
            "changes nothing. Pass `model` on Agent calls to match the estimate."
        )
    if cfg.flag("UPDATE_CHECK"):
        changes = upstream.pending()
        if changes and changes["new_model_families"]:
            parts.append("New Claude model alias in the docs that isn't in Model Router's registry yet: "
                         + ", ".join(changes["new_model_families"]) + f". Tell the user once; /{name}:refresh "
                         "reviews it.")
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
    mode = cfg.mode(config)
    prompt = hook_input.get("prompt")
    prompt = prompt if isinstance(prompt, str) else ""
    if mode == "off" or not is_user_prompt(prompt):
        return None
    session_id = hook_input.get("session_id")
    usable = registry.available(config, cfg.project_dir(hook_input))
    result = profile_prompt(prompt)
    level = result["level"]
    needs = float(((config.get("levels") or {}).get(level) or {}).get("needs") or 1)
    need = registry.select(config, needs, cfg.priority(), usable)
    current = registry.family(config, session.model(session_id, hook_input.get("transcript_path")))
    below = bool(current and need and registry.strength(config, current) < registry.strength(config, need))
    store.save_entry("prompts", session_id, {
        "prompt_id": hook_input.get("prompt_id"), "level": level, "need": need, "below": below,
        "session_tier": current, "top_spawn": None, "routed": {}})
    why = f" (wording suggests: {', '.join(result['signals'])})" if result["signals"] else ""
    notes = []
    if mode == "advise":
        notes.append(f"Routing estimate for this request: {level}{why}, about {need}-level reasoning. "
                     "It reads only the wording; route by what the code shows.")
    elif below:
        notes.append(
            f"This request looks {level}{why}: about {need}-level reasoning, and this conversation runs on "
            f"{current}. A plugin can't switch the conversation's model, so this is required, not optional: "
            f"hand the hard thinking (the design, the root cause, the risky decision) to "
            f"{gates.strong_agent(config, need)} rather than settling it inline. You will be asked to before "
            "finishing if you don't. If the code shows the task is simpler than its wording, say so instead."
        )
    wanted = named_model(config, prompt)
    if wanted and mode == "auto":
        notes.append(f"If the user is asking for the {wanted} model here, pass model: \"{wanted}\" on the "
                     "Agent calls for this request. A model named on a call is kept.")
    return _context("UserPromptSubmit", " ".join(notes)) if notes else None


def pre_agent(hook_input: dict) -> dict | None:
    config = cfg.load_config(hook_input)
    tool_input = _tool_input(hook_input)
    decision = router.decide(hook_input, config)
    session_id = hook_input.get("session_id")
    action = decision["action"]
    store.append_ledger({
        "event": "spawn", "session": session_id, "agent": decision["agent"], "action": action,
        "level": decision["level"], "signals": decision["signals"],
        "model": None if action == "deny" else decision["model"] or decision["was"],
        "was": decision["was"], "effort": decision["effort"], "source": decision["source"],
        "reason": decision["reason"], "description": str(tool_input.get("description") or "")[:120]})
    try:
        _record(config, session_id, tool_input, decision)
    except OSError:
        pass
    if action == "rewrite":
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "updatedInput": router.rewritten_input(tool_input, decision),
            "additionalContext": router.note(decision)}}
    if action == "deny":
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": decision["reason"]}}
    return None


def _record(config: dict, session_id: str | None, tool_input: dict, decision: dict) -> None:
    """What the feedback loop, the repeat guard and the gates need to know afterwards."""
    agent, level, source, action = decision["agent"], decision["level"], decision["source"], decision["action"]
    if action == "deny":
        return
    key = router.call_key(agent, tool_input)
    runs_on = decision["model"] or decision["was"]

    def power(name: object) -> float:
        return registry.strength(config, name)

    if action == "rewrite":
        def remember(pending: dict) -> None:
            pending[key] = {"model": decision["model"], "at": time.time()}
            calls = [k for k in pending if isinstance(pending[k], dict) and k != "_at"]
            for old in sorted(calls, key=lambda k: pending[k].get("at", 0))[:-PENDING_KEPT]:
                del pending[old]
        store.mutate_entry("pending", session_id, remember)
    repeat_of = decision.get("repeat_of")
    if repeat_of and not (registry.health().get(repeat_of) or {}).get("last_model"):
        # A rewrite to a model that has never once run here didn't run this time either.
        registry.record_failure(repeat_of, "a call rewritten to this model came back without having run")

    picked = source == "router" and not repeat_of
    if picked:
        learn.record_call(level)
    current = store.load_entry("prompts", session_id)
    routed_before = current.get("routed") if isinstance(current.get("routed"), dict) else {}
    earlier = routed_before.get(key)

    def update(entry: dict) -> None:
        # Re-read under the lock: calls issued together record at the same moment.
        routed = entry.get("routed") if isinstance(entry.get("routed"), dict) else {}
        if picked:
            routed[key] = runs_on
            entry["routed"] = dict(list(routed.items())[-100:])
        if power(runs_on) > power(entry.get("top_spawn")):
            entry["top_spawn"] = runs_on

    if session_id and (picked or power(runs_on) > power(current.get("top_spawn"))):
        store.mutate_entry("prompts", session_id, update)
    if source == "caller" and level:
        if earlier and power(runs_on) > power(earlier):
            learn.vote_up(level, 2)  # the same call again, on a stronger model
        elif power(runs_on) > power(decision["pick"]):
            learn.vote_up(level, 1)
        elif power(runs_on) < power(decision["pick"]):
            learn.vote_down(level)
    if gates.is_checker(agent):
        gates.note_check(session_id)


def _settle(hook_input: dict) -> None:
    """A result or an error came back for this call, so its rewrite was accepted."""
    session_id = hook_input.get("session_id")
    tool_input = _tool_input(hook_input)
    agent = tool_input.get("subagent_type")
    key = router.call_key(agent if isinstance(agent, str) and agent else "general-purpose", tool_input)
    if key in store.load_entry("pending", session_id):
        store.mutate_entry("pending", session_id, lambda pending: pending.pop(key, None))


def _asked_for(config: dict, hook_input: dict) -> str | None:
    """The model a finished or failed call was meant to run on, if it named one."""
    tool_input = _tool_input(hook_input)
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
    tool_input = _tool_input(hook_input)
    response = hook_input.get("tool_response")
    response = response if isinstance(response, dict) else {}
    ran_as = response.get("resolvedModel")
    asked = _asked_for(config, hook_input)
    try:
        _settle(hook_input)
        if asked and registry.family(config, ran_as):
            registry.record_result(asked, ran_as, registry.family(config, ran_as))
    except OSError:
        pass
    store.append_ledger({
        "event": "result", "session": hook_input.get("session_id"),
        "agent": tool_input.get("subagent_type") or "general-purpose", "asked": asked,
        "resolved_model": ran_as, "status": response.get("status"), "total_tokens": response.get("totalTokens")})
    return None


def agent_failed(hook_input: dict) -> dict | None:
    """An agent call failed. If the error says the model itself isn't usable, count it,
    and route around that model once it has happened twice."""
    try:
        _settle(hook_input)
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
                         "agent": _tool_input(hook_input).get("subagent_type"), "model": asked})
    usable = [n for n in registry.available(config, cfg.project_dir(hook_input)) if n != asked]
    if not usable:
        return None
    return _context("PostToolUseFailure", (
        f"The '{asked}' model looks unavailable here. Re-issue this agent call with model: "
        f"\"{registry.clamp(config, asked, usable)}\"."
        + (" Model Router will route around it for now." if fails >= 2 else "")))


def post_edit(hook_input: dict) -> dict | None:
    gates.note_edit(hook_input)
    return None


def stop(hook_input: dict) -> dict | None:
    reason = gates.before_stop(hook_input, cfg.load_config(hook_input))
    return {"decision": "block", "reason": reason} if reason else None


def model_switch(hook_input: dict) -> dict | None:
    session.record(hook_input.get("session_id"), hook_input.get("to_model"))
    return None


def refresh_cache(hook_input: dict) -> dict | None:
    if cfg.flag("UPDATE_CHECK"):
        upstream.refresh(force=False)
    return None


HANDLERS = {
    "session-start": session_start, "user-prompt": user_prompt, "pre-agent": pre_agent,
    "post-agent": post_agent, "agent-failed": agent_failed, "post-edit": post_edit, "stop": stop,
    "model-switch": model_switch, "refresh-cache": refresh_cache,
}
