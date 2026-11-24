"""The check before a turn ends: it asks once and then lets go."""
from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path

from . import session, store
from . import config as cfg
from .settings import Settings

# A script named for a check: test_api.py, _scratch_check.py, run_tests.sh, cart.spec.js.
_SCRIPT = r"(?:\S*/)?(?:(?:test|check|spec)[\w.-]*|[\w.-]*[_.-](?:tests?|check|spec))\.(?:py|[cm]?js|ts|rb|sh)\b"
CHECK_COMMAND = re.compile(
    r"\b(?:pytest|unittest|tox|nox|jest|vitest|mocha|playwright|cypress|rspec|phpunit|ctest|tsc|mypy|pyright|"
    r"ruff|eslint|flake8|pylint|clippy|golangci-lint|rubocop)\b|"
    r"\b(?:npm|pnpm|yarn|bun)(?:\s+-\S+(?:\s+[^-\s]\S*)?)*\s+(?:run\s+)?(?:test|lint|typecheck|check)\b|"
    r"\b(?:cargo|go|dotnet|mvn|gradle|gradlew|make|just|swift|mix|deno)\s+\S*\s*(?:test|check|vet|lint|verify)\b|"
    # Gradle's build task runs its check task; any other bare build only compiles.
    r"\bgradlew?\s+build\b|"
    r"\b(?:manage\.py|rails|artisan|bazel|sbt|zig(?:\s+build)?)\s+test\b|\brake\s+(?:test|spec)\b|\bnode\s+--test\b|"
    rf"\b(?:python3?|node|ruby|bash|sh)\s+(?:-\S+\s+)*{_SCRIPT}|(?:^|\s)\./{_SCRIPT}")
# A command part led by one of these only installs, finds, reads, opens, copies or mentions a tool, as in
# "pip install pytest", "vim pytest.ini" or "git commit -m 'Run mypy'", or is a heredoc line ("import pytest").
# Separators inside quotes still split the command.
_PARTS = re.compile(r"&&|\|\||[;|\n]")
_LEAD = re.compile(r"[\s({`$]*(?:(?:\w+=\S*|sudo)\s+)*")
_MENTION = re.compile(
    r"(?:pip3?|pipx|brew|apt(?:-get)?|which|type|whereis|command\s+-[vV]|e?grep|rg|ag|cat|less|more|head|tail|sed|"
    r"awk|echo|printf|git|ls|find|wc|open|vim?|nvim|nano|emacs|code|cp|mv|cd|touch|mkdir|ln|chmod|import|from|"
    r"(?:npm|pnpm|yarn|bun)\s+(?:install|i|add|ci|remove|uninstall|view|info|ls)|"
    r"uv\s+(?:pip|add|tool\s+install))(?:\s|$)")
# A message's tool_use rows are stamped before its Edit's PostToolUse records the edit.
_SAME_MESSAGE_SLACK = 5


def note_edit(hook_input: dict) -> None:
    tool_input = cfg.tool_input(hook_input)
    path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if path and _in_project(hook_input, path):
        store.update_entry("edits", hook_input.get("session_id"), edited_at=store.now())


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
    store.update_entry("edits", session_id, verified_at=store.now())


def note_agent_checks(hook_input: dict) -> None:
    """A subagent's commands are in its own transcript, which the Stop check doesn't read."""
    session_id = hook_input.get("session_id")
    entry = store.load_entry("edits", session_id)
    edited = entry.get("edited_at") or 0
    if edited > (entry.get("verified_at") or 0) and checks_ran_since(hook_input.get("agent_transcript_path"), edited):
        note_check(session_id)


def is_check(command: str) -> bool:
    """A check command: CHECK_COMMAND in a part of it no mention word leads."""
    return any(CHECK_COMMAND.search(part) and not _MENTION.match(part, _LEAD.match(part).end())
               for part in _PARTS.split(command))


def checks_ran_since(transcript_path: object, since: float) -> bool | None:
    """Did a check command run after `since`? None when the transcript can't be read."""
    if session.transcript_tail(transcript_path, 0) is None:
        return None
    for row, block in session.tool_calls(transcript_path, '"command"'):
        try:
            stamp = _dt.datetime.fromisoformat(str(row.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if stamp < since - _SAME_MESSAGE_SLACK:
            continue
        command = (block.get("input") or {}).get("command") if isinstance(block.get("input"), dict) else None
        if isinstance(command, str) and is_check(command):
            return True
    return False


def unverified_edit(hook_input: dict) -> str | None:
    session_id = hook_input.get("session_id")
    entry = store.load_entry("edits", session_id)
    edited = entry.get("edited_at") or 0
    if not edited or edited <= (entry.get("verified_at") or 0):
        return None
    ran = checks_ran_since(hook_input.get("transcript_path"), edited)
    if ran is None:
        return None  # no transcript, as under --no-session-persistence, so no telling whether a check ran
    note_check(session_id)  # either a check ran, or we ask once for this batch of edits
    if ran:
        return None
    return (
        "Model Router: project files changed since the last check and nothing has checked them yet. The work's own "
        "check comes next: for code, the project's tests, build or linter, and "
        f"{cfg.PLUGIN_NAME}:verifier for a check that takes more than one command; for figures or a spreadsheet, a "
        "recompute or a diff against known values; for a document or an analysis, a reread against the request and "
        "its sources. Work that isn't finished, that waits on the user, or that has no check to run ends the turn "
        "with one line saying so."
    )


def still_running(hook_input: dict) -> bool:
    """Background work still in flight (hooks.md#stop-input) means the turn is paused, not done."""
    return any(isinstance(task, dict) and task.get("type") in ("workflow", "subagent", "shell", "teammate")
               for task in hook_input.get("background_tasks") or [])


def before_stop(hook_input: dict, settings: Settings) -> str | None:
    if hook_input.get("stop_hook_active") or settings.mode != "auto" or not settings.verify_gate \
            or still_running(hook_input):
        return None
    return unverified_edit(hook_input)
