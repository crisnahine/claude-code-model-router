"""Checks before a turn ends. Each asks once and then lets go."""
from __future__ import annotations

import datetime as _dt
import os
import re
import time
from pathlib import Path

from . import registry, request, session, store
from . import config as cfg
from .settings import Settings

_NON_CODE = (".md", ".mdx", ".txt", ".rst", ".adoc", ".csv", ".log")
CHECK_COMMAND = re.compile(
    r"\b(?:pytest|unittest|tox|nox|jest|vitest|mocha|playwright|cypress|rspec|phpunit|ctest|tsc|mypy|pyright|"
    r"ruff|eslint|flake8|pylint|clippy|golangci-lint|rubocop)\b|"
    r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|lint|typecheck|check|build)\b|"
    r"\b(?:cargo|go|dotnet|mvn|gradle|gradlew|make|just|swift|mix|deno)\s+\S*\s*(?:test|check|build|vet|lint|verify)\b")
_CHECKERS = ("verifier", "reviewer")
# A message's tool_use rows are stamped before its Edit's PostToolUse records the edit.
_SAME_MESSAGE_SLACK = 5


def is_checker(agent_type: object) -> bool:
    return str(agent_type or "").endswith(_CHECKERS)


def note_edit(hook_input: dict) -> None:
    tool_input = cfg.tool_input(hook_input)
    path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if path and not path.lower().endswith(_NON_CODE) and _in_project(hook_input, path):
        store.update_entry("edits", hook_input.get("session_id"), edited_at=time.time())


def _in_project(hook_input: dict, path: str) -> bool:
    """Under the hook's cwd or the session's starting project root, since cwd can move into
    a subfolder. Hook input doesn't list --add-dir directories, so files there count only
    while cwd is inside one."""
    cwd = cfg.project_dir(hook_input)
    roots = [cwd, *([Path(os.environ["CLAUDE_PROJECT_DIR"])] if os.environ.get("CLAUDE_PROJECT_DIR") else [])]
    try:
        target = (cwd / Path(path).expanduser()).resolve()
        return any(target.is_relative_to(root.resolve()) for root in roots)
    except (OSError, RuntimeError, ValueError):
        return True


def note_check(session_id: str | None) -> None:
    store.update_entry("edits", session_id, verified_at=time.time())


def note_agent_checks(hook_input: dict) -> None:
    """A subagent's commands are in its own transcript, which the Stop check doesn't read."""
    session_id = hook_input.get("session_id")
    entry = store.load_entry("edits", session_id)
    edited = entry.get("edited_at") or 0
    if edited > (entry.get("verified_at") or 0) and checks_ran_since(hook_input.get("agent_transcript_path"), edited):
        note_check(session_id)


def checks_ran_since(transcript_path: object, since: float) -> bool:
    """Did a test, lint, type-check or build command run after `since`?"""
    for row, block in session.tool_calls(transcript_path, '"command"'):
        try:
            stamp = _dt.datetime.fromisoformat(str(row.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if stamp < since - _SAME_MESSAGE_SLACK:
            continue
        command = (block.get("input") or {}).get("command") if isinstance(block.get("input"), dict) else None
        if isinstance(command, str) and CHECK_COMMAND.search(command):
            return True
    return False


def strong_agent(config: dict, need: str, level: str | None = None) -> str:
    """The role agent to hand hard thinking to, by name only: the router routes its brief with the
    role's floor and a redo still climbs, which a model named on the call would stop. Only with no
    role agent does the needed model go on the call."""
    name = "deep-solver" if level == "frontier" else "architect"
    if name not in (config.get("crew") or {}):
        return f'a subagent with model: "{need}"'
    return f"{cfg.PLUGIN_NAME}:{name}"


def writes_nothing(agent: str) -> bool:
    """The architect has no Write or Edit tool, so a task whose deliverable is a file fails there."""
    return agent == f"{cfg.PLUGIN_NAME}:architect"


def escalation(config: dict, hook_input: dict) -> str | None:
    """A request estimated above the conversation's own model, answered without any
    agent of that strength: ask once for a stronger model to check the result."""
    session_id = hook_input.get("session_id")
    entry = request.load(session_id)
    if entry.escalation_asked or entry.level not in cfg.LEVELS[cfg.LEVELS.index("complex"):]:
        return None
    need = entry.need
    # On a session's first prompt the model isn't known yet; by now the transcript has it.
    current = (registry.family(config, session.model(session_id, hook_input.get("transcript_path")))
               or entry.session_tier)
    if not current or not need or registry.strength(config, current) >= registry.strength(config, need):
        return None
    if registry.strength(config, entry.top_spawn) >= registry.strength(config, need):
        return None
    try:
        request.mark_escalation(session_id, current)
    except OSError:
        return None
    agent = strong_agent(config, need, entry.level)
    act = ("to check; it writes nothing, so apply what it finds yourself" if writes_nothing(agent)
           else "to check and improve")
    return (
        f"This request was estimated as {entry.level}, which needs about {need}-level reasoning, and "
        f"this conversation runs on {current}. No {need}-level agent has looked at it. Before finishing, give "
        f"the task and your result to {agent} {act}, then give the user the reconciled answer. If the task "
        "turned out to be simple and the estimate was wrong, say so in one line and finish."
    )


def unverified_edit(hook_input: dict) -> str | None:
    session_id = hook_input.get("session_id")
    entry = store.load_entry("edits", session_id)
    edited = entry.get("edited_at") or 0
    if not edited or edited <= (entry.get("verified_at") or 0):
        return None
    for task in hook_input.get("background_tasks") or []:
        if isinstance(task, dict) and is_checker(task.get("agent_type")):
            return None  # a check is still running; its report will bring Claude back
    note_check(session_id)  # either a check ran, or we ask once for this batch of edits
    if checks_ran_since(hook_input.get("transcript_path"), edited):
        return None
    name = cfg.PLUGIN_NAME
    return (
        f"Code changed since the last check and nothing has checked it yet. Before finishing, run "
        f"{name}:verifier on the change (it finds and runs the project's tests, lint and type checks), and "
        f"for a risky or cross-module change also {name}:reviewer. If the project has no runnable checks, "
        "or the change can't affect behavior, say so in one line and finish."
    )


def before_stop(hook_input: dict, config: dict, settings: Settings) -> str | None:
    if hook_input.get("stop_hook_active") or settings.mode != "auto":
        return None
    return ((escalation(config, hook_input) if settings.strong_agent_gate else None)
            or (unverified_edit(hook_input) if settings.verify_gate else None))
