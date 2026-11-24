"""One handler per hook event. Each takes the hook's JSON and returns the JSON to print,
or None. cli.py swallows anything a handler raises, so a hook can't break a session.

The plugin never approves a tool call. It rewrites two fields of agent calls, and it
denies only to hold the user's own cap when a lowering rewrite didn't take."""
from __future__ import annotations

from typing import Any

from . import calls, fallback, gates, health, intake, mod, profile, request, session, settings, upstream
from . import config as cfg
from .registry import Registry


def _context(event: str, text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def _rules(options: settings.Settings) -> str:
    """The level rule for Claude, written as facts: the Agent tool sets `effort` only when instructions ask."""
    redo = ("A redo after a failed check or a gap in the report keeps its `description` with \"Redo:\" in front, "
            "names {neither}, and runs one step higher.")
    if options.force:
        # Claude can't pass a model under FORCE (sub-agents.md), so only the effort half applies.
        return (f"CLAUDE_CODE_SUBAGENT_MODEL_FORCE runs every subagent on {options.subagent_model or 'one model'}, so a "
                "`model` on an Agent call has no effect. Model Router keeps the `effort` a call names, by the work: "
                "medium for finding, reading, listing and everyday work to a clear brief; high for work that is hard "
                "to check, high-stakes, ambiguous or broad, such as a bug's cause, a design, a review, a contract or "
                "financial figures. " + redo.format(neither="no effort"))
    if options.pins == "override":
        neither = "the conversation's model"
    elif options.subagent_model:
        neither = "its agent's own model or CLAUDE_CODE_SUBAGENT_MODEL"
    else:
        neither = "its agent's own model or the conversation's"
    return ("Model Router keeps the `model` and `effort` an Agent call names, within the user's cap, and fills in "
            "`effort` when a call names only a model. Calls name both by the work:\n"
            "- haiku at medium: finding, reading and listing\n"
            "- sonnet at medium: everyday work to a clear brief\n"
            "- opus at high: work that is hard to check, high-stakes, ambiguous or broad, such as a bug's cause, "
            "a design, a review, a contract or financial figures\n"
            "- fable at high: open-ended research\n"
            f"A call that names neither runs on {neither}. " + redo.format(neither="neither"))


def session_start(hook_input: dict) -> dict | None:
    """The level rule goes to Claude. The notices go to the user (`systemMessage`), only when a session
    opens, since a clear or a compaction continues one they already saw."""
    snap = settings.Snapshot(hook_input)
    options = snap.options
    settings.record(options)
    session.record(hook_input.get("session_id"), hook_input.get("model"))
    note, notices = None, []
    mode = options.mode
    name = cfg.PLUGIN_NAME
    opens = hook_input.get("source") not in ("clear", "compact")
    if mode == "auto":
        note = _rules(options)
        if options.force:
            notices.append(
                f"CLAUDE_CODE_SUBAGENT_MODEL_FORCE pins every subagent to {options.subagent_model or 'one model'}, "
                "so Model Router sets only their effort. Without it, an agent call would run on the model it names, "
                "within your cap.")
        elif options.subagent_model and options.pins != "override":
            notices.append(
                f"CLAUDE_CODE_SUBAGENT_MODEL sets the model of every agent without its own to {options.subagent_model}, "
                "so Model Router leaves those calls as they are and sets only the effort of agents that name their "
                "own model.")
        if options.workflow_model:
            notices.append(f"CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL runs every workflow agent on {options.workflow_model}, "
                           "so the router sets only their effort.")
    elif mode == "advise":
        note = ("Model Router is in advise mode: it logs the level it reads for each agent call and changes no call. "
                "Agents keep the model their definition sets, which a model passed on a call would override.")
    if options.update_check and opens:
        changes = upstream.pending()
        running = [hook_input.get("model")] + snap.health.seen_running()
        for model_id, latest in upstream.behind(running):
            notices.append(
                f"{model_id} is running here and {latest} is the newest of its family. A model setting that names a "
                "full version stays on it; the alias (opus, sonnet, haiku, fable) follows new versions once Claude "
                "Code is updated.")
        if changes and changes["new_model_families"]:
            notices.append("New Claude model alias in the docs that isn't in Model Router's registry yet: "
                           + ", ".join(changes["new_model_families"]) + f". /{name}:refresh reviews it.")
        if changes and changes.get("deprecated_in_use"):
            notices.append("Models this setup routes to are deprecated or retiring: " + "; ".join(
                f"{m['model']} retires {m['retires']}" for m in changes["deprecated_in_use"])
                + ". The model aliases move on by themselves; a pinned full ID doesn't.")
        if upstream.has_pending(changes):
            notices.append(
                f"Upstream: {len(changes['new_releases'])} Claude Code release(s) and "
                f"{len(changes['watched_pages_touched'])} plugin-relevant docs page change(s) since the last "
                f"review. /{name}:refresh reviews them.")
    out = _context("SessionStart", note) if note else {}
    if notices and opens:
        out["systemMessage"] = "Model Router: " + " ".join(notices)
    return out or None


def user_prompt(hook_input: dict) -> dict | None:
    """Record the person's new request. Says something only when the request names a model."""
    snap = settings.Snapshot(hook_input)
    reg, options = snap.registry, snap.options
    if options.mode == "off":
        return None
    prompt = hook_input.get("prompt")
    if not intake.is_user_prompt(prompt):
        return None  # a task notification or a delivered message continues the user's request
    wanted = request.named_model(reg, prompt)
    request.start(hook_input.get("session_id"), prompt_id=hook_input.get("prompt_id"))
    if not wanted or options.mode != "auto" or options.force or not snap.usable:
        return None  # with no registry model usable, Claude Code's allowlist decides where the call runs
    final = reg.clamp(wanted, snap.usable)
    if final == wanted:
        runs = "runs on it, and Model Router keeps it"
    elif options.cap and reg.stronger(wanted, options.cap):
        runs = (f"runs within the cap the user set, {options.cap}" if final == options.cap
                else f"runs on {final}, within the cap the user set ({options.cap})")
    else:
        runs = f"runs on {final}, since {wanted} isn't available here"
    return _context("UserPromptSubmit", f"The request mentions the {wanted} model. If it asks for that model, an "
                                        f"Agent call with model: \"{wanted}\" {runs}.")


def pre_agent(hook_input: dict) -> dict | None:
    if mod.routes(hook_input.get("tool_use_id")):
        return None  # the plugin's mod routes this call when it spawns
    return fallback.route(hook_input, settings.Snapshot(hook_input))


def _outcome(kind: str, hook_input: dict, reg: Registry, **fields: Any) -> calls.Outcome:
    """What came back for an Agent call, as the one record `calls.returned` reads."""
    tool_input = cfg.tool_input(hook_input)
    agent, key = calls.identify(tool_input)
    spawn_id = hook_input.get("tool_use_id")
    return calls.Outcome(kind, key=key, agent=agent, task=calls.task(tool_input), named=reg.family(tool_input.get("model")),
                         spawn_id=spawn_id if isinstance(spawn_id, str) and spawn_id else None, **fields)


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
                       models_used=() if launched else tuple(calls.models_used(response)),
                       cut_off=not launched and calls.cut_off(response))
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
    reason = gates.before_stop(hook_input, snap.options)
    return {"decision": "block", "reason": reason} if reason else None


def model_switch(hook_input: dict) -> dict | None:
    session.record(hook_input.get("session_id"), hook_input.get("to_model"))
    return None


def refresh_cache(hook_input: dict) -> dict | None:
    options = settings.resolve()
    if options.update_check:
        upstream.refresh(options, force=False)
    return None


HANDLERS = {
    "mod-start": mod.start, "mod-spawn": mod.spawn, "mod-usage": mod.usage,
    "session-start": session_start, "user-prompt": user_prompt, "pre-agent": pre_agent,
    "post-agent": post_agent, "agent-failed": agent_failed, "agent-stopped": agent_stopped,
    "post-edit": post_edit, "stop": stop,
    "model-switch": model_switch, "refresh-cache": refresh_cache,
}
