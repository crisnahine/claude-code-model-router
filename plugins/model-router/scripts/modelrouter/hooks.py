"""One handler per hook event. Each takes the hook's JSON and returns the JSON to print,
or None. cli.py swallows anything a handler raises, so a hook can't break a session.

The plugin never approves a tool call. It rewrites two fields of agent calls, and it
denies only to hold the user's own cap when a lowering rewrite didn't take."""
from __future__ import annotations

import re
from typing import Any

from . import allowlist, calls, fallback, gates, health, intake, ledger, profile, request, router, session, settings, spawn, store, upstream
from . import config as cfg
from .registry import Registry


def _context(event: str, text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def session_start(hook_input: dict) -> dict | None:
    snap = settings.Snapshot(hook_input)
    options = snap.options
    settings.record(options)
    session.record(hook_input.get("session_id"), hook_input.get("model"))
    parts = []
    mode = options.mode
    name = cfg.PLUGIN_NAME
    if mode == "auto":
        # Only what Claude must act on: every word here is in context for the whole session.
        parts.append(
            "Model Router sets each subagent's, teammate's and workflow agent's model and effort from its brief. "
            "Agent calls here omit `model` and `effort` unless the user named one; either overrides the router. "
            "A brief naming what makes it hard (security, several modules, an unclear cause) routes higher. "
            "A weak result's call, re-issued unchanged, runs one level higher."
        )
        if options.force:
            pinned = options.subagent_model or "one model"
            parts.append(
                f"CLAUDE_CODE_SUBAGENT_MODEL_FORCE pins every subagent to {pinned}, so Model Router sets only "
                "their effort. Without it in the user's settings, the router would put each subtask on the "
                "cheapest model strong enough for it. Worth telling the user once."
            )
        if options.workflow_model:
            parts.append(f"CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL runs every workflow agent on {options.workflow_model}. "
                         "Model Router still sets a workflow agent's model, and the docs don't say which of the two "
                         "wins. Worth telling the user once.")
    elif mode == "advise":
        parts.append(
            "Model Router is in advise mode: it estimates the model each request needs and changes no call. "
            "Agents keep the model their definition sets, which a model passed on a call would override."
        )
    if options.update_check:
        changes = upstream.pending()
        running = [hook_input.get("model")] + snap.health.seen_running()
        for model_id, latest in upstream.behind(running):
            parts.append(
                f"{model_id} is running here and {latest} is the newest of its family. A model setting that names a "
                "full version stays on it; the alias (opus, sonnet, haiku, fable) follows new versions once Claude "
                "Code is updated. Worth telling the user once.")
        if changes and changes["new_model_families"]:
            parts.append("New Claude model alias in the docs that isn't in Model Router's registry yet: "
                         + ", ".join(changes["new_model_families"]) + f". /{name}:refresh reviews it; "
                         "worth telling the user once.")
        if changes and changes.get("deprecated_in_use"):
            parts.append("Models this setup routes to are deprecated or retiring: " + "; ".join(
                f"{m['model']} retires {m['retires']}" for m in changes["deprecated_in_use"])
                + ". The model aliases move on by themselves; a pinned full ID doesn't. Worth telling the user once.")
        if upstream.has_pending(changes):
            parts.append(
                f"Upstream: {len(changes['new_releases'])} Claude Code release(s) and "
                f"{len(changes['watched_pages_touched'])} plugin-relevant docs page change(s) since the last "
                f"review. /{name}:refresh reviews them; worth mentioning to the user once."
            )
    return _context("SessionStart", " ".join(parts)) if parts else None


def user_prompt(hook_input: dict) -> dict | None:
    """Size up the request against the conversation's own model. Says nothing when there
    is nothing Claude needs to do differently, so an ordinary prompt costs no tokens."""
    snap = settings.Snapshot(hook_input)
    reg, options = snap.registry, snap.options
    mode = options.mode
    if mode == "off":
        return None
    prompt = hook_input.get("prompt")
    prompt = prompt if isinstance(prompt, str) else ""
    session_id = hook_input.get("session_id")
    seen = intake.from_prompt(prompt, snap.project)
    if seen.delivered:
        # Work for someone else's message isn't the user's request, so it doesn't get its floor.
        # A task notification continues the user's request and keeps it.
        try:
            request.deliver(session_id)
        except OSError:
            pass
    if not seen.person:
        return None
    model = session.model(session_id, hook_input.get("transcript_path"))
    started = request.start(session_id, seen, reg, model, snap.usable, snap.raises, priority=options.priority,
                            prompt_id=hook_input.get("prompt_id"), named=request.named_model(reg, prompt))
    level, need, current, wanted = started.level, started.need, started.session_tier, started.named
    below = started.below(reg, model)
    why = f" (wording suggests: {', '.join(seen.signals)})" if seen.signals else ""
    notes = []
    if mode == "advise":
        if need:
            notes.append(f"Routing estimate for this request: {level}{why}, about {need}-level reasoning, "
                         "from its wording alone.")
    elif below:
        agent = gates.strong_agent(reg, need, level)
        asked = " A stop before a stronger agent has looked is held once." if options.strong_agent_gate else ""
        writes = "; it writes nothing, so the change stays here" if gates.writes_nothing(agent) else ""
        notes.append(
            f"This request looks {level}{why}: about {need}-level reasoning, and this conversation runs on "
            f"{current}. If the design, root cause or risky choice can't be settled from the code in a few reads, "
            f"{agent} can give the plan or decision{writes}. Otherwise this conversation works directly.{asked}"
        )
    if wanted and mode == "auto":
        notes.append(f"The request mentions the {wanted} model. If it asks for that model, an Agent call with "
                     f"model: \"{wanted}\" runs on it, and Model Router keeps it.")
    return _context("UserPromptSubmit", " ".join(notes)) if notes else None


def mod_active(session_id: str | None) -> bool:
    return bool(store.load_entry("mod", session_id).get("active"))


def pre_agent(hook_input: dict) -> dict | None:
    if mod_active(hook_input.get("session_id")):
        return None  # the plugin's mod routes this call when it spawns
    return fallback.route(hook_input, settings.Snapshot(hook_input))


def _outcome(kind: str, hook_input: dict, reg: Registry, **fields: Any) -> calls.Outcome:
    """What came back for an Agent call, as the one record `calls.returned` reads."""
    tool_input = cfg.tool_input(hook_input)
    agent, key = calls.identify(tool_input)
    return calls.Outcome(kind, key=key, agent=agent, named=reg.family(tool_input.get("model")), **fields)


def _settled(session_id: str | None, key: str) -> None:
    try:
        fallback.forget(session_id, key)
    except OSError:
        pass


def post_agent(hook_input: dict) -> dict | None:
    snap = settings.Snapshot(hook_input)
    tool_input = cfg.tool_input(hook_input)
    response = hook_input.get("tool_response")
    response = response if isinstance(response, dict) else {}
    ran_as = response.get("resolvedModel")
    # A background agent's call returns as soon as the agent has launched. Its result
    # is back when the agent stops (agent_stopped), not here.
    launched = response.get("status") == "async_launched"
    agent_id = response.get("agentId")
    session_id = hook_input.get("session_id")
    outcome = _outcome("result", hook_input, snap.registry, ran_as=ran_as, ran_family=snap.registry.family(ran_as),
                       forced=snap.options.force, status=response.get("status"), tokens=response.get("totalTokens"),
                       launched=launched, agent_id=agent_id if launched and isinstance(agent_id, str) else None,
                       models_used=() if launched else tuple(calls.models_used(response)))
    _settled(session_id, outcome.key)
    returned = calls.returned(session_id, outcome)
    if returned.note != calls.SWAPPED:
        return None
    if "risk" in profile.profile_call(tool_input, outcome.agent)["signals"]:
        return _context("PostToolUse", (
            f"Model Router: this agent switched models mid-run ({outcome.models_used[0]} to {outcome.models_used[-1]}), "
            "so its result needs a check before it is relied on."))
    return None


def agent_failed(hook_input: dict) -> dict | None:
    """An agent call failed. If the error says the model itself isn't usable, count it,
    and route around that model once it has happened twice."""
    snap = settings.Snapshot(hook_input)
    reg = snap.registry
    session_id = hook_input.get("session_id")
    outcome = _outcome("failure", hook_input, reg, error=str(hook_input.get("error") or ""),
                       interrupted=bool(hook_input.get("is_interrupt")))
    _settled(session_id, outcome.key)
    returned = calls.returned(session_id, outcome)
    if returned.note == calls.ID_FAILED and returned.route:
        # The mod sent the family's newest ID, and this provider refused it: the alias runs
        # from now on, and the ID is tried again after the outage.
        return _context("PostToolUseFailure", (
            f"{returned.route.sent} isn't available here. The '{returned.asked}' alias runs from now on, as Claude Code "
            "resolves it for this provider; the same agent call re-issued unchanged uses it."))
    if returned.note not in (calls.MODEL_FAILED, calls.ROUTED_AROUND) or not returned.asked:
        return None
    asked = returned.asked
    # snap.health may predate the strike just written, so the list reads the table again.
    usable = [n for n in reg.available(snap.options.cap, allowed=snap.allowed, table=health.load()) if n != asked]
    if not usable:
        return None
    return _context("PostToolUseFailure", (
        f"The '{asked}' model looks unavailable here. This agent call re-issued with model: "
        f"\"{reg.clamp(asked, usable)}\" runs on the nearest usable one."
        + (" Model Router will route around it for now." if returned.note == calls.ROUTED_AROUND else "")))


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
    agent_id = hook_input.get("agent_id")
    calls.returned(hook_input.get("session_id"), calls.Outcome("stop", agent_id=agent_id if isinstance(agent_id, str) else None,
                                                               answered=answered))
    gates.note_agent_checks(hook_input)
    return None


def post_edit(hook_input: dict) -> dict | None:
    gates.note_edit(hook_input)
    return None


def stop(hook_input: dict) -> dict | None:
    snap = settings.Snapshot(hook_input)
    reason = gates.before_stop(hook_input, snap.registry, snap.options)
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
        store.save_entry("mod", session_id, {"active": True, "at": store.now()})


def mod_spawn(hook_input: dict) -> dict | None:
    _mark_mod(hook_input.get("session_id"))
    allowlist.remember(hook_input.get("session_id"), hook_input.get("settings"))
    # The spawn procedure reads agent_id as "the call comes from inside a subagent".
    snap = settings.Snapshot(hook_input)
    spawned = spawn.decide({**hook_input, "agent_id": hook_input.get("parent_agent_id")}, snap)
    decision = spawned.decision
    newest = _newest(snap, decision)
    spawn.record(spawned, "mod", newest)
    if decision["action"] != "rewrite" and not newest:
        return None
    return {"model": newest or decision["model"], "effort": decision["effort"], "reason": decision["reason"]}


def _newest(snap: settings.Snapshot, decision: dict) -> str | None:
    """The newest full ID to send in place of the decided family alias, on a provider whose
    alias lags. Only agent.spawn takes a full ID; the Agent tool's model takes an alias
    (https://code.claude.com/docs/en/sub-agents.md)."""
    if decision["action"] == "deny" or decision["version"]:
        return None
    return snap.registry.newest_id(decision["model"], snap.options.provider, snap.allowed, snap.health)


def _effort_keeps_cache(reg: Registry, options: settings.Settings, model: object) -> bool:
    """Only the models in `main.effort_keeps_cache`, on Anthropic's API without a gateway,
    Mantle, pre-release betas turned off or prompt caching turned off for them, keep the prompt
    cache when effort changes (https://code.claude.com/docs/en/prompt-caching.md#changing-effort-level).
    A model with no known version, as after /clear, gets the benefit of the doubt."""
    if options.cloud or options.mantle or options.base_url or options.no_betas or "" in options.cache_off:
        return False
    found = upstream.version_of(model)
    if not found:
        return True
    family, version = found
    if family in options.cache_off:
        return False
    rule = reg.keeps_cache.get(family)
    if not isinstance(rule, str) or not re.fullmatch(r"(?:>=)?\d+(?:\.\d+)*", rule):
        return False
    wanted = tuple(int(part) for part in rule.lstrip(">=").split("."))
    return version >= wanted if rule.startswith(">=") else version == wanted


def mod_turn(hook_input: dict) -> dict | None:
    """Effort for one main-loop turn, from the request. Effort is the lever that keeps the
    prompt cache on current models; switching the conversation's model would not."""
    session_id = hook_input.get("session_id")
    _mark_mod(session_id)
    allowlist.remember(session_id, hook_input.get("settings"))
    snap = settings.Snapshot(hook_input)
    reg, options = snap.registry, snap.options
    seen = intake.from_turn(hook_input.get("text"), hook_input.get("command"), hook_input.get("origin"))
    if options.mode != "off" and seen.person and seen.command:
        # The level is the request's, which the floor and the Stop check read, whatever the effort switches say.
        try:
            request.raise_to(session_id, seen, reg, snap.usable, snap.raises, priority=options.priority)
        except OSError:
            pass
    if (options.mode != "auto" or not options.main_effort or options.effort_level
            or hook_input.get("first_party") is False):
        return None
    if not seen.person:
        return None
    # The mod's live model, when it is an ID; a record can miss a fallback to another model.
    model = hook_input.get("model")
    model = model if upstream.version_of(model) else session.model(session_id)
    if not _effort_keeps_cache(reg, options, model):
        return None
    level = seen.level
    effort = reg.main_effort.get(level)
    if effort not in cfg.EFFORTS:
        return None
    effort, held = router.capped(effort, snap.allowed.effort_cap(reg, reg.family(model)))
    ledger.turn(session_id, level, effort, seen.command, reason=held.lstrip("; ") or None)
    return {"level": level, "effort": effort}


def mod_usage(hook_input: dict) -> dict | None:
    """One ledger row per turn, its cost the sum of its requests, each priced on its own."""
    usage = hook_input.get("usage")
    if isinstance(usage, dict) and isinstance(usage.get("model"), str):
        ledger.usage(settings.Snapshot(hook_input).registry, hook_input.get("session_id"), hook_input.get("agent_id"),
                     usage, hook_input.get("requests"))
    return None


HANDLERS = {
    "mod-start": mod_start, "mod-spawn": mod_spawn, "mod-turn": mod_turn, "mod-usage": mod_usage,
    "session-start": session_start, "user-prompt": user_prompt, "pre-agent": pre_agent,
    "post-agent": post_agent, "agent-failed": agent_failed, "agent-stopped": agent_stopped,
    "post-edit": post_edit, "stop": stop,
    "model-switch": model_switch, "refresh-cache": refresh_cache,
}
