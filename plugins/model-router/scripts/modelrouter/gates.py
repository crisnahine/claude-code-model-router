"""Checks before a turn ends. Each asks once and then lets go."""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time

from . import registry, session, store
from . import config as cfg

_NON_CODE = (".md", ".mdx", ".txt", ".rst", ".adoc", ".csv", ".log")
CHECK_COMMAND = re.compile(
    r"\b(?:pytest|unittest|tox|nox|jest|vitest|mocha|playwright|cypress|rspec|phpunit|ctest|tsc|mypy|pyright|"
    r"ruff|eslint|flake8|pylint|clippy|golangci-lint|rubocop)\b|"
    r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|lint|typecheck|check|build)\b|"
    r"\b(?:cargo|go|dotnet|mvn|gradle|gradlew|make|just|swift|mix|deno)\s+\S*\s*(?:test|check|build|vet|lint|verify)\b")
_CHECKERS = ("verifier", "reviewer")


def enabled(config: dict) -> bool:
    return cfg.mode(config) == "auto" and cfg.flag("VERIFY_GATE")


def is_checker(agent_type: object) -> bool:
    return str(agent_type or "").endswith(_CHECKERS)


def note_edit(hook_input: dict) -> None:
    tool_input = hook_input.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if path and not path.lower().endswith(_NON_CODE):
        store.update_entry("edits", hook_input.get("session_id"), edited_at=time.time())


def note_check(session_id: str | None) -> None:
    store.update_entry("edits", session_id, verified_at=time.time())


def checks_ran_since(transcript_path: object, since: float) -> bool:
    """Did a test, lint, type-check or build command run after `since`?"""
    if not isinstance(transcript_path, str) or not transcript_path:
        return False
    try:
        with open(os.path.expanduser(transcript_path), "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 1024 * 1024))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return False
    for line in tail.splitlines():
        if '"tool_use"' not in line or '"command"' not in line:
            continue
        try:
            row = json.loads(line)
            stamp = _dt.datetime.fromisoformat(str(row.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
            content = row["message"]["content"]
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if stamp < since:
            continue
        for block in content if isinstance(content, list) else []:
            command = (block.get("input") or {}).get("command") if isinstance(block, dict) else None
            if isinstance(command, str) and CHECK_COMMAND.search(command):
                return True
    return False


def strong_agent(config: dict, need: str) -> str:
    """The role agent that runs on `need`, or a plain instruction when there is none."""
    crew = config.get("crew") or {}
    for name in ("deep-solver", "architect"):
        if registry.family(config, (crew.get(name) or {}).get("model")) == need:
            return f"{cfg.PLUGIN_NAME}:{name}"
    return f'a subagent with model: "{need}"'


def escalation(config: dict, hook_input: dict) -> str | None:
    """A request estimated above the conversation's own model, answered without any
    agent of that strength: ask once for a stronger model to check the result."""
    session_id = hook_input.get("session_id")
    entry = store.load_entry("prompts", session_id)
    if entry.get("escalation_asked") or entry.get("level") not in ("complex", "hardest"):
        return None
    need = entry.get("need")
    # On a session's first prompt the model isn't known yet; by now the transcript has it.
    current = (registry.family(config, session.model(session_id, hook_input.get("transcript_path")))
               or entry.get("session_tier"))
    if not current or not need or registry.strength(config, current) >= registry.strength(config, need):
        return None
    if registry.strength(config, entry.get("top_spawn")) >= registry.strength(config, need):
        return None
    try:
        store.update_entry("prompts", session_id, escalation_asked=True, session_tier=current)
    except OSError:
        return None
    return (
        f"This request was estimated as {entry.get('level')}, which needs about {need}-level reasoning, and "
        f"this conversation runs on {current}. No {need}-level agent has looked at it. Before finishing, give "
        f"the task and your result to {strong_agent(config, need)} to check and improve, then give the user "
        "the reconciled answer. If the task turned out to be simple and the estimate was wrong, say so in "
        "one line and finish."
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
        f"Code changed in this turn and nothing has checked it since the last edit. Before finishing, run "
        f"{name}:verifier on the change (it finds and runs the project's tests, lint and type checks), and "
        f"for anything beyond a trivial edit also {name}:reviewer. If the project has no runnable checks, "
        "or the change can't affect behavior, say so in one line and finish."
    )


def before_stop(hook_input: dict, config: dict) -> str | None:
    if hook_input.get("stop_hook_active") or not enabled(config):
        return None
    return escalation(config, hook_input) or unverified_edit(hook_input)
