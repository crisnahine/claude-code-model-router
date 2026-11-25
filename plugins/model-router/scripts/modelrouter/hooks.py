"""One handler per hook event. Each takes the hook's JSON and returns the JSON to print,
or None. cli.py swallows anything a handler raises, so a hook can't break a session.

The plugin never approves a tool call. It rewrites two fields of agent calls, and it
denies only to hold the user's own cap when a lowering rewrite didn't take."""
from __future__ import annotations

from typing import Any

from . import calls, fallback, gates, health, intake, ladder, ledger, mod, request, session, settings
from . import config as cfg
from .registry import Registry


def _context(event: str, text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


_HARD = "work that is hard to check, high-stakes, ambiguous or broad"
_SUCH = "a bug's cause, a design, a review, a contract or financial figures"


def _rules(options: settings.Settings, reg: Registry) -> str:
    """The level rule for Claude, written as facts: the Agent tool sets `effort` only when instructions ask.
    Each level's model and effort come from the registry, so a rules file that moves a level moves the note."""
    at = {level: (ladder.model_for(reg, level), ladder.effort(reg, level)) for level in cfg.Level}
    redo = ("A redo after a failed check or a gap in the report keeps its `description` with \"Redo:\" in front "
            "and runs {above}.")
    # CLAUDE_CODE_EFFORT_LEVEL wins over a call's effort (sub-agents.md), and either variable keeps a redo's effort.
    fixed = ("CLAUDE_CODE_EFFORT_LEVEL sets every subagent's effort, so an `effort` on a call has no effect."
             if options.effort_level else "")
    if options.force:
        # Claude can't pass a model under FORCE (sub-agents.md), so only the effort half applies.
        model = options.subagent_model if cfg._PLAIN_NAME.fullmatch(options.subagent_model or "") else "one model"
        lead = (f"CLAUDE_CODE_SUBAGENT_MODEL_FORCE runs every subagent on {model}, so a `model` on an Agent call has "
                "no effect.")
        if fixed:
            return f"{lead} {fixed}"
        light, everyday, hard = (at[level][1] for level in (cfg.Level.lookup, cfg.Level.standard, cfg.Level.complex))
        plain = (f"{light} for finding, reading, listing and everyday work to a clear brief" if light == everyday
                 else f"{light} for finding, reading and listing; {everyday} for everyday work to a clear brief")
        return (lead + f" Model Router keeps the `effort` a call names, by the work: {plain}; {hard} for {_HARD}, "
                f"such as {_SUCH}."
                + ("" if options.subagent_effort else " " + redo.format(above="one effort step higher")))
    if options.subagent_model:
        # The cap doesn't lower CLAUDE_CODE_SUBAGENT_MODEL, only a model the agent names.
        neither = f"its agent's own model{', capped,' if options.cap else ''} or CLAUDE_CODE_SUBAGENT_MODEL"
    else:
        neither = f"its agent's own model or the conversation's{', within the cap' if options.cap else ''}"
    if fixed:
        lead, efforts = (f"Model Router keeps the `model` an Agent call names, within the user's cap. {fixed} "
                         "Here every Agent call names a model, by the work:\n"), False
    else:
        fills = "" if options.subagent_effort else ", and fills in `effort` when a call names only a model"
        lead, efforts = (f"Model Router keeps the `model` and `effort` an Agent call names, within the user's "
                         f"cap{fills}. Here every Agent call names both, by the work:\n"), True
    above = ("on a stronger model than the run it redoes" if fixed or options.subagent_effort
             else "above the run it redoes, effort before model")
    work = ((cfg.Level.lookup, "finding, reading and listing"), (cfg.Level.standard, "everyday work to a clear brief"),
            (cfg.Level.complex, f"{_HARD} ({_SUCH})"), (cfg.Level.frontier, "open-ended research"))
    rows = "".join(f"- {at[level][0]}{f' at {at[level][1]}' if efforts and at[level][1] else ''}: {what}\n"
                   for level, what in work if at[level][0])
    return f"{lead}{rows}A call that names neither runs on {neither}. " + redo.format(above=above)


def session_start(hook_input: dict) -> dict | None:
    """The level rule goes to Claude. The notices go to the user (`systemMessage`), only when a session
    opens, since a clear or a compaction continues one they already saw."""
    snap = settings.Snapshot(hook_input)
    options = snap.options
    session.record(hook_input.get("session_id"), hook_input.get("model"))
    note, notices = None, []
    mode = options.mode
    opens = hook_input.get("source") not in ("clear", "compact")
    if mode == "auto":
        note = _rules(options, snap.registry)
        if options.force:
            notices.append(
                f"CLAUDE_CODE_SUBAGENT_MODEL_FORCE pins every subagent to {options.subagent_model or 'one model'}, "
                "so Model Router sets only their effort. Without it, an agent call would run on the model it names, "
                "within your cap.")
        elif options.subagent_model:
            notices.append(
                f"CLAUDE_CODE_SUBAGENT_MODEL is {options.subagent_model}, so an agent call that names no model runs on "
                f"its agent's own model or {options.subagent_model}. Model Router keeps the model a call names, within "
                "your cap.")
        if options.workflow_model:
            notices.append(f"CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL runs every workflow agent on {options.workflow_model} "
                           "when its script names no model, so the router sets only their effort.")
    out = _context("SessionStart", note) if note else {}
    if notices and opens:
        out["systemMessage"] = "Model Router: " + " ".join(notices)
    return out or None


def user_prompt(hook_input: dict) -> dict | None:
    """Says something only when the person's request names a model."""
    snap = settings.Snapshot(hook_input)
    reg, options = snap.registry, snap.options
    if options.mode == "off":
        return None
    prompt = hook_input.get("prompt")
    if not intake.is_user_prompt(prompt):
        return None  # a task notification or a delivered message isn't the person asking for a model
    wanted = request.named_model(reg, prompt)
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


def mod_start(hook_input: dict) -> dict:
    """The mod's session start. The answer carries the session note, which the mod adds to the Agent tool's description."""
    answer = mod.start(hook_input)
    snap = settings.Snapshot(hook_input)
    return {**answer, "rules": _rules(snap.options, snap.registry)} if snap.options.mode == "auto" else answer


def pre_agent(hook_input: dict) -> dict | None:
    if mod.routes(hook_input.get("tool_use_id")):
        return None  # the plugin's mod routes this call when it spawns
    return fallback.route(hook_input, settings.Snapshot(hook_input, sends_ids=False))


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
    response = hook_input.get("tool_response")
    response = response if isinstance(response, dict) else {}
    ran_as = response.get("resolvedModel")
    # A background agent's call returns as soon as the agent has launched. Its result
    # is back when the agent stops (agent_stopped), not here.
    launched = response.get("status") == "async_launched"
    agent_id = response.get("agentId")
    session_id = hook_input.get("session_id")
    outcome = _outcome("result", hook_input, snap.registry, ran_as=ran_as, ran_family=snap.registry.family(ran_as),
                       forced=snap.options.force, status=response.get("status"),
                       moved=mod.moved(hook_input.get("tool_use_id")), launched=launched, agent_id=agent_id if launched and isinstance(agent_id, str) else None,
                       models_used=() if launched else tuple(calls.models_used(response)))
    _settled(session_id, outcome.key)
    returned = calls.returned(session_id, outcome)
    if returned.note != calls.SWAPPED:
        return None
    return _context("PostToolUse", (
        f"Model Router: this agent switched models mid-run ({outcome.models_used[0]} to {outcome.models_used[-1]}), "
        "so its result needs a check before it is relied on."))


def agent_failed(hook_input: dict) -> dict | None:
    """An agent call failed. If the error says the model itself isn't usable, count it,
    and route around that model once it has happened twice."""
    snap = settings.Snapshot(hook_input)
    reg = snap.registry
    session_id = hook_input.get("session_id")
    outcome = _outcome("failure", hook_input, reg, error=str(hook_input.get("error") or ""),
                       interrupted=bool(hook_input.get("is_interrupt")), moved=mod.moved(hook_input.get("tool_use_id")))
    _settled(session_id, outcome.key)
    returned = calls.returned(session_id, outcome)
    if returned.note == calls.ID_FAILED and returned.route:
        # The mod sent the family's newest ID, and this provider refused it: the alias runs
        # from now on, and the ID is tried again after the outage.
        return _context("PostToolUseFailure", (
            f"{returned.route.sent} isn't available here. The '{returned.asked}' alias runs from now on, as Claude Code "
            "resolves it for this provider; the same agent call re-issued unchanged uses it."))
    if snap.options.force or returned.note not in (calls.MODEL_FAILED, calls.ROUTED_AROUND) or not returned.asked:
        return None  # under FORCE neither a named model nor a route-around moves the call
    asked = returned.asked
    # snap.health may predate the strike just written, so the list reads the table again.
    usable = [n for n in reg.available(snap.options.cap, allowed=snap.allowed, table=health.load(),
                                       sendable=snap.sendable) if n != asked]
    if not usable:
        return None
    return _context("PostToolUseFailure", (
        f"The '{asked}' model looks unavailable here. This agent call re-issued with model: "
        f"\"{reg.clamp(asked, usable)}\" runs on a usable model."
        + (" Model Router will route around it for up to a day." if returned.note == calls.ROUTED_AROUND else "")))


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
    ask = gates.before_stop(hook_input, snap.options)
    if not ask:
        return None
    ledger.gate(hook_input.get("session_id"), "verify")
    return _context("Stop", ask)


def model_switch(hook_input: dict) -> dict | None:
    session.record(hook_input.get("session_id"), hook_input.get("to_model"))
    return None


HANDLERS = {
    # A spawn left as it is still answers OK, so an empty answer means the runtime didn't run.
    "mod-start": mod_start, "mod-spawn": lambda hook_input: mod.spawn(hook_input) or mod.OK, "mod-usage": mod.usage,
    "session-start": session_start, "user-prompt": user_prompt, "pre-agent": pre_agent,
    "post-agent": post_agent, "agent-failed": agent_failed, "agent-stopped": agent_stopped,
    "post-edit": post_edit, "stop": stop,
    "model-switch": model_switch,
}
