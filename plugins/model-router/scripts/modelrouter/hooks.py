"""One handler per hook event. Each takes the hook's JSON and returns the JSON to print,
or None. cli.py swallows anything a handler raises, so a hook can't break a session.

The plugin never approves a tool call. It rewrites two fields of agent calls, and it
denies only to hold the user's own cap when a lowering rewrite didn't take."""
from __future__ import annotations

import re
import time

from . import calls, gates, ladder, learn, registry, session, settings, spawn, store, upstream
from . import config as cfg
from .profile import _CHANGE, _LOOKUP, _REASONING, profile_prompt, signals

_NEGATION = re.compile(r"(?:\bnot|n't|\bnever|\bwithout|\binstead of|\bavoid|\bno)\s+(?:\w+\s+){0,3}$")


# Where the mod saw a turn's prompt come from (prompt.submit's origin.kind) when it wasn't
# the person. Left out: unclassified, which a same-user socket's typed prompt also gets, and
# a coordinator's hand-off, which is a worker's real task. An unknown kind counts as the person's.
MACHINE_ORIGINS = {"task-notification", "peer", "peer-send-message", "observer", "observer-activity"}
_DELIVERED = "Another Claude session sent a message:"


def is_user_prompt(prompt: str) -> bool:
    """UserPromptSubmit also fires for background-task notifications, teammates' reports
    and other sessions' messages. Those must not reset what is known about the user's
    request. Pasted text is the user's, though it arrives wrapped in a <pasted_content> tag."""
    prompt = (prompt or "").lstrip()
    first, _, rest = prompt.partition("\n")
    if first.strip() == _DELIVERED and rest.lstrip().startswith(("<teammate-message", "<cross-session-message")):
        return False
    return not prompt.startswith("<") or prompt.startswith("<pasted_content")


# A skill's body is standing instructions, not the request: only its opening counts, so its length
# never reads as scope and a !`cmd` output or checklist further down can't make every use hard.
_SKILL_BODY_CHARS = 1500


_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.S)


def _receives(body: str, frontmatter: str, count: int) -> bool:
    """Whether a raw skill file has a placeholder that receives one of `count` arguments: an unescaped
    $ARGUMENTS, $N or $ARGUMENTS[N] with N below count (0-based), or a declared name the body uses.
    A $ followed by an amount ($500, $1.00) is prose."""
    if re.search(r"(?<!\\)\$ARGUMENTS\b(?!\[)", body):
        return True
    for index in re.findall(r"(?<!\\)\$(?:ARGUMENTS\[(\d+)\]|(\d+)(?![\d.,]))", body):
        if int(index[0] or index[1]) < count:
            return True
    block = re.search(r"(?ms)^arguments\s*:(.*?)(?=^\S|\Z)", frontmatter)
    names = re.findall(r"[A-Za-z_]\w*", block.group(1)) if block else []
    return any(re.search(rf"(?<!\\)\${re.escape(name)}\b", body) for name in names)


def _carries_task(args: str) -> bool:
    """Whether a skill's arguments say what to do, not only name a target such as "482", "src/auth.py" or a
    URL: a change, reasoning or lookup verb, a signal word beside another word, or three words or more. A
    lone token counts only as a verb, so a path's "auth" or a class name's words don't make it a task."""
    text = args.lower()
    verb = bool(re.search(_CHANGE, text) or re.search(_REASONING, text) or re.search(_LOOKUP, text))
    words = len(args.split())
    if words == 1:
        return verb and not re.search(r"[/\\.:#]", args)
    return verb or bool(signals(text)) or words >= 3


def _profile_skill(args: object, body: object, rendered: bool) -> dict:
    """A typed skill's profile. A skill whose content takes its arguments (a $ARGUMENTS, $1 or named
    placeholder) and is given a task in them runs that task, so the arguments alone set the level: its own
    instructions, a pipeline's say, are the same on every use. Otherwise the body is the task, as it is when
    the arguments only name a target, and the higher of the arguments' level and the body's opening counts. `rendered` is the engine's expansion, where a skill that takes no
    placeholder gets the arguments appended as "ARGUMENTS: ..."; otherwise body is the raw file."""
    args = args.strip() if isinstance(args, str) else ""
    body = (body if isinstance(body, str) else "").lstrip("\ufeff")
    body = re.sub(r"\A\s*Base directory for this skill: [^\n]*\n", "", body)
    front = _FRONTMATTER.match(body)
    body = body[front.end():] if front else body
    if rendered:
        # The engine appends the trailer last, so only its last occurrence counts, and any trailer at all
        # means no placeholder took them: an unexpected format errs toward the higher level.
        head, trailer, _ = body.rpartition("\nARGUMENTS: ")
        takes_args = bool(args) and not trailer
        body = head if trailer else body
    else:
        takes_args = bool(args) and _receives(body, front.group(1) if front else "", len(args.split()))
    if takes_args and _carries_task(args):
        return profile_prompt(args)
    parts = [profile_prompt(text) for text in (args, body[:_SKILL_BODY_CHARS])]
    return max(parts, key=lambda p: cfg.LEVELS.index(p["level"]))


def _typed_skill(prompt: str, hook_input: dict) -> dict | None:
    """The profile behind "/name args" when name is a project or user skill or command on disk.
    UserPromptSubmit sees only the typed line; a plugin's skill isn't found this way."""
    match = re.match(r"/([A-Za-z0-9_.-]+)(?:\s+(.*))?\Z", prompt.strip(), re.S)
    if not match:
        return None
    name, args = match.group(1), match.group(2) or ""
    project, home = cfg.project_dir(hook_input) / ".claude", store.claude_home()
    for path in (project / "skills" / name / "SKILL.md", home / "skills" / name / "SKILL.md",
                 project / "commands" / f"{name}.md", home / "commands" / f"{name}.md"):
        try:
            if not path.is_file():  # a FIFO or device would hang or never end
                continue
            with path.open("rb") as fh:
                return _profile_skill(args, fh.read(64 * 1024).decode("utf-8", "replace"), rendered=False)
        except OSError:
            continue
    return None


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
    result = _typed_skill(prompt, hook_input) or profile_prompt(prompt)
    level = result["level"]
    need = ladder.pick(config, level, options.priority, usable, learn.raises()).model
    current = registry.family(config, session.model(session_id, hook_input.get("transcript_path")))
    below = bool(current and need and registry.strength(config, current) < registry.strength(config, need))
    wanted = named_model(config, prompt)
    store.save_entry("prompts", session_id, {
        "prompt_id": hook_input.get("prompt_id"), "level": level, "need": need, "below": below,
        "session_tier": current, "named": wanted})
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
        # Under the force setting every agent runs on the forced model, which isn't a substitution.
        if asked and registry.family(config, ran_as) and not settings.resolve(config).force:
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
    tool_input = cfg.tool_input(hook_input)
    routed = None
    try:
        routed = calls.settled(hook_input.get("session_id"), tool_input)
    except OSError:
        pass
    error = str(hook_input.get("error") or "")
    if hook_input.get("is_interrupt") or not registry.MODEL_ERROR.search(error):
        return None
    config = cfg.load_config(hook_input)
    asked = _asked_for(config, tool_input, routed)
    if not asked:
        return None
    named = tool_input.get("model")
    newest = None
    if mod_active(hook_input.get("session_id")) and (not named or str(named).lower() == asked):
        newest = registry.newest_id(config, asked, settings.resolve(config).provider, cfg.project_dir(hook_input))
    if newest:
        # The mod sent the family's newest ID, and this provider refused it: the alias runs
        # from now on, and the ID is tried again after the outage.
        registry.record_failure(newest, error, strikes=1)
        store.append_ledger({"event": "model_error", "session": hook_input.get("session_id"),
                             "agent": tool_input.get("subagent_type"), "model": newest})
        return _context("PostToolUseFailure", (
            f"{newest} isn't available here. Re-issue this agent call unchanged: it now runs on the "
            f"'{asked}' alias, as Claude Code resolves it for this provider."))
    fails = registry.record_failure(asked, error)
    store.append_ledger({"event": "model_error", "session": hook_input.get("session_id"),
                         "agent": tool_input.get("subagent_type"), "model": asked})
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
    return registry.newest_id(config, decision["model"], settings.resolve(config).provider, cfg.project_dir(hook_input))


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
    config = cfg.load_config(hook_input)
    options = settings.resolve(config)
    text = hook_input.get("text")
    text = text if isinstance(text, str) else ""
    # Only a turn that opens with the command's own line is that command's turn.
    command = hook_input.get("command")
    command = command if (isinstance(command, dict) and text.lstrip().startswith("<command-")
                          and f"<command-name>/{command.get('name')}</command-name>" in text) else None
    asked = command is not None or is_user_prompt(text)
    # The mod's live model, when it is an ID; a record can miss a fallback to another model.
    model = hook_input.get("model")
    model = model if upstream.version_of(model) else session.model(session_id)
    if (options.mode != "auto" or not options.main_effort or not asked
            or options.effort_level or hook_input.get("first_party") is False
            or str(hook_input.get("origin")) in MACHINE_ORIGINS
            or not _effort_keeps_cache(options, model)):
        return None
    level = (_profile_skill(command.get("args"), command.get("body"), rendered=True) if command
             else profile_prompt(text))["level"]
    effort = ((config.get("main") or {}).get("effort") or {}).get(level)
    if effort not in cfg.EFFORTS:
        return None
    store.append_ledger({"event": "turn", "session": hook_input.get("session_id"), "level": level,
                         "effort": effort, **({"command": command.get("name")} if command else {})})
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
