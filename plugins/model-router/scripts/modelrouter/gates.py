"""The check before a turn ends: it asks once and then lets go."""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path

from . import session, store
from . import config as cfg
from .settings import Settings

# A script named for a check: test_api.py, _scratch_check.py, run_tests.sh, cart.spec.js.
_SCRIPT = re.compile(r"(?:\S*/)?(?:(?:tests?|checks?|spec)(?:[_.-][\w.-]*)?|[\w.-]*[_.-](?:tests?|check|spec))"
                     r"\.(?:py|[cm]?js|ts|rb|sh)")
_TASK = re.compile(r"(?:tests?|lint|type-?check|check|vet|verify|spec|clippy|nextest|analyze)(?:[:_-][\w:.-]*)?")
_RUNNERS = {"pytest", "py.test", "unittest", "tox", "nox", "jest", "vitest", "mocha", "rspec", "phpunit", "ctest",
            "tsc", "mypy", "pyright", "ruff", "eslint", "flake8", "pylint", "clippy", "golangci-lint", "rubocop"}
_SUBCOMMAND = {"playwright": "test", "cypress": "run"}  # `playwright install` and `cypress open` check nothing
_FORMATTERS = {"black", "prettier", "isort", "rustfmt", "cargo fmt", "ruff format", "terraform fmt", "dotnet format",
               "mix format", "deno fmt"}
_FORMAT_CHECKS = {"--check", "-check", "--check-only", "--check-formatted", "--verify-no-changes"}
_TASK_FIRST = {"cargo", "go", "dotnet", "swift", "mix", "deno", "bazel", "bazelisk", "flutter", "dart", "rails",
               "manage.py", "artisan"}  # the first argument names the task
_TASK_ANY = {"make", "gmake", "just", "mvn", "mvnw", "gradle", "gradlew", "rake", "sbt", "zig", "turbo", "nx", "lerna"}
_PACKAGE_MANAGERS = {"npm", "pnpm", "yarn", "bun"}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
# The short flag that takes inline code, last in a cluster as in `ruby -pe`, and its long forms.
_INLINE = {"python": ("c", ()), "node": ("ep", ("--eval", "--print")), "ruby": ("e", ()), "php": ("r", ())}
_ONLY_PRINTS = {"--version", "--help", "-h", "--collect-only", "--co", "--list", "--listTests", "--list-tests",
                "--fixtures", "--markers", "--showConfig", "--init", "--install-types", "--auto-gen-config",
                "--dry-run", "--just-print", "--recon"}
# Each wrapper's own arguments before the command it runs: a container or service, a time limit.
_WRAPPERS = {**dict.fromkeys(("sudo", "env", "time", "nice", "nohup", "stdbuf", "xargs", "npx", "pnpx", "bunx",
                              "uvx", "nyc", "c8", "if", "then", "else", "elif", "do", "while", "until", "!", "{",
                              "command", "exec", "eval", "watch", "xvfb-run", "cross-env", "caffeinate", "unbuffer",
                              "chronic", "ionice", "arch", "elixir -S", "devbox run",
                              "uv run", "uv tool run", "pipx run", "poetry run", "pipenv run", "pdm run", "hatch run",
                              "rye run", "conda run", "mamba run", "pixi run", "bundle exec", "rbenv exec",
                              "asdf exec"), 0),
             **dict.fromkeys(("timeout", "rvm", "nvm exec", "mise exec", "mise x", "docker exec", "docker run",
                              "docker compose run", "docker compose exec", "docker-compose run", "docker-compose exec",
                              "podman exec", "podman run", "kubectl exec", "direnv exec", "nix develop", "nix shell",
                              "flock", "script"), 1),
             **dict.fromkeys(("command -v", "command -V"), None)}  # these only say where a command is
# The flag whose value is a whole command line, read as `sh -c` reads one.
_LINE_FLAGS = {"nix-shell": ("--run", "--command"), "env": ("-S", "--split-string")}
_COMMANDS = (_RUNNERS | set(_SUBCOMMAND) | _TASK_FIRST | _TASK_ANY | _PACKAGE_MANAGERS | _SHELLS | set(_INLINE)
             | set(_LINE_FLAGS) | {key.split()[0] for key in (*_WRAPPERS, *_FORMATTERS)} | {"coverage"})
_PYTHON = re.compile(r"python[\d.]*")
_VARIABLE = re.compile(r"\$\{?\w+\}?")  # "$PY -m pytest": a variable that holds the interpreter
_ASSIGN = re.compile(r"[A-Za-z_]\w*\+?=")
_HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)[ \t]*\\?(['\"]?)([^\s'\"<>|&;()]+)\2")
_SEPARATORS = frozenset(";&|()`\n")
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
    """Does a part of this shell command, split as the shell splits it, run a check tool past any wrappers
    (`sudo`, `npx`, `uv run`, `docker exec`)? A tool named anywhere else, as in `pip install pytest`, a quoted
    message or a heredoc's lines, is only a mention."""
    return any(_runs_check(words, _find(words, 0, 0, _known)) for words in _parts(_without_heredocs(command)))


def _parts(command: str) -> list[list[str]]:
    """The command's simple commands as words, split at separators outside quotes, with quotes, comments and
    redirection targets removed."""
    parts: list[list[str]] = []
    words: list[str] = []
    word: str | None = None
    quote, i, target = None, 0, False
    while i < len(command):
        c = command[i]
        if quote:
            if c == quote:
                quote = None
            elif c == "\\" and quote == '"' and i + 1 < len(command):
                i += 1
                word += command[i]
            else:
                word += c
        elif c == "\\":
            i += 1
            if i < len(command) and command[i] != "\n":
                word = (word or "") + command[i]
        elif c in "'\"":
            quote, word = c, word or ""
        elif c == "#" and word is None:
            end = command.find("\n", i)
            i = len(command) if end < 0 else end
            continue
        elif c in " \t\r<>" or c in _SEPARATORS:
            if word is not None:
                if not target:
                    words.append(word)
                word, target = None, False
            if c in "<>":
                target = True
            elif c in _SEPARATORS:
                target = False
                if words:
                    parts.append(words)
                    words = []
        else:
            word = (word or "") + c
        i += 1
    if word is not None and not target:
        words.append(word)
    if words:
        parts.append(words)
    return parts


def _without_heredocs(command: str) -> str:
    """The command without the lines a heredoc feeds to a program, unless that program is a shell."""
    if "<<" not in command:
        return command
    kept: list[str] = []
    waiting: list[tuple[str, str]] = []
    open_: list[str] = []
    for line in command.split("\n"):
        if waiting:
            dash, delimiter = waiting[0]
            line = (line.lstrip("\t") if dash else line).rstrip("\r")
            # Inside `$(...)` the closing parenthesis can follow the delimiter, and the command goes on after it.
            if line != delimiter and not ("(" in open_ and line.startswith(delimiter + ")")):
                continue
            waiting.pop(0)
            line = line[len(delimiter):]
            if waiting or not line:
                continue
        kept.append(line)
        opened = _heredocs(line, open_)
        if not any(_base(words[j]) in _SHELLS for words in _parts(line)
                   for j in [_unwrap(words, _find(words, 0, 0, _known))] if j < len(words)):
            waiting = opened
    return "\n".join(kept)


def _heredocs(line: str, open_: list[str]) -> list[tuple[str, str]]:
    """The heredocs a line opens as (dash, delimiter): a `<<` outside quotes, comments and arithmetic. `open_` holds
    the quotes and parentheses still open, carried from line to line."""
    found: list[tuple[str, str]] = []
    j = 0
    while j < len(line):
        c, top = line[j], open_[-1] if open_ else ""
        if top == "'":
            if c == "'":
                open_.pop()
        elif c == "\\":
            j += 1
        elif top == '"' and c == '"':
            open_.pop()
        elif line.startswith("$((", j) or (top != '"' and line.startswith("((", j)):
            open_.append("((")
            j += 2 if c == "$" else 1
        elif line.startswith("$(", j):
            open_.append("(")
            j += 1
        elif top == "((":
            if line.startswith("))", j):
                open_.pop()
                j += 1
        elif top == '"':
            pass
        elif c in "'\"(":
            open_.append(c)
        elif c == ")" and top == "(":
            open_.pop()
        elif c == "#" and (j == 0 or line[j - 1] in " \t;&|()"):
            break
        elif m := _HEREDOC.match(line, j):
            found.append((m.group(1), m.group(3)))
            j = m.end()
            continue
        j += 1
    return found


def _base(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _named(word: str) -> bool:
    """A check tool, a wrapper or an interpreter, by its name."""
    return _base(word) in _COMMANDS or bool(_PYTHON.fullmatch(_base(word)))


def _known(word: str) -> bool:
    """A word that names a command this module reads: a check tool, a wrapper, an interpreter or a check script."""
    return _named(word) or bool(_VARIABLE.fullmatch(word) or _SCRIPT.fullmatch(word))


def _find(words: list[str], i: int, positionals: int, wanted) -> int:
    """The index of the next command word from `i`: past assignments, flags and `positionals` other words. A flag's
    next word is taken as its value unless it is `wanted`."""
    while i < len(words):
        word = words[i]
        if word.startswith(("-", "+")) and len(word) > 1:
            i += 1
            if "=" not in word and i < len(words) and not words[i].startswith(("-", "+")) and not wanted(words[i]):
                i += 1
        elif _ASSIGN.match(word):
            i += 1
        elif positionals and not wanted(word):
            positionals, i = positionals - 1, i + 1
        else:
            return i
    return i


def _unwrap(words: list[str], i: int) -> int:
    """The index of the command that runs once the wrappers from `i` are past."""
    while i < len(words):
        name, rest = _base(words[i]), words[i + 1:i + 3]
        key = next((k for k in (" ".join([name, *rest]), " ".join([name, *rest[:1]]), name) if k in _WRAPPERS), None)
        if key is None:
            # `op run -- pytest`: a command this module doesn't know runs the one named after its `--`.
            k = words.index("--", i + 1) if not _known(words[i]) and "--" in words[i + 1:] else len(words)
            if k + 1 < len(words) and _named(words[k + 1]):
                i = k + 1
                continue
            return i
        if _WRAPPERS[key] is None:
            return len(words)
        command = _find(words, i + key.count(" ") + 1, _WRAPPERS[key], _known)
        if any(w in _LINE_FLAGS.get(key, ()) for w in words[i + 1:command]):
            return i
        i = command
    return i


def _runs_check(words: list[str], i: int) -> bool:
    """Does the command at `words[i]` run a check?"""
    while (i := _unwrap(words, i)) < len(words):
        word = words[i]
        name = _base(word)
        pair = " ".join([name, *words[i + 1:i + 2]])
        python = bool(_PYTHON.fullmatch(name) or _VARIABLE.fullmatch(word)) or pair == "coverage run"
        if name in _LINE_FLAGS:
            k = next((k for k in range(i + 1, len(words)) if words[k] in _LINE_FLAGS[name]), len(words))
            return k + 1 < len(words) and is_check(words[k + 1])
        if name in _SHELLS:
            for k in range(i + 1, len(words)):
                flag = words[k]
                if flag.startswith("-") and not flag.startswith("--") and "c" in flag:
                    return k + 1 < len(words) and is_check(words[k + 1])
                if not flag.startswith(("-", "+")) and not re.fullmatch(r"[-+]\w*o", words[k - 1]):
                    return _script_runs_check(words, k)
            return False
        if python or name in _INLINE:
            k = i + (2 if pair == "coverage run" else 1)
            letters, longs = _INLINE["python" if python else name]
            while k < len(words) and words[k].startswith("-") and len(words[k]) > 1:
                flag, k = words[k], k + 1
                if flag in longs or (not flag.startswith("--") and flag[-1] in letters):
                    return False
                if name == "node" and flag == "--test":
                    return not _prints(words[k:])
                if python and flag == "-m":
                    break
                if k < len(words) and not words[k].startswith("-") and not _known(words[k]):
                    k += 1
            if k >= len(words) or not _known(words[k]):
                return False
            i = k
            continue
        if name in _PACKAGE_MANAGERS:
            i = _package_task(words, i, name)
            if i is None or i >= len(words):
                return False
            if _TASK.fullmatch(words[i]) or words[i] == "t":
                return not _prints(words[i + 1:])
            continue
        rest = words[i + 1:]
        if _prints(rest):
            return False
        if pair in _FORMATTERS or name in _FORMATTERS:
            return any(w in _FORMAT_CHECKS for w in rest)
        if name in _RUNNERS:
            return True
        if name in _SUBCOMMAND:
            k = _find(words, i + 1, 0, _known)
            return k < len(words) and words[k] == _SUBCOMMAND[name]
        if name in _TASK_FIRST:
            k = _find(words, i + 1, 0, _TASK.fullmatch)
            return k < len(words) and bool(_TASK.fullmatch(words[k]))
        if name in _TASK_ANY:
            if name.endswith("make") and "-n" in rest:  # make's dry run
                return False
            # A task may carry its project, as in `gradle :app:test` or `nx run web:lint`. Gradle's build task runs
            # its check task; any other bare build only compiles.
            return any(_TASK.fullmatch(w) or _TASK.fullmatch(task := w.rsplit(":", 1)[-1])
                       or (task == "build" and name.startswith("gradle")) for w in rest)
        return _script_runs_check(words, i)
    return False


def _script_runs_check(words: list[str], i: int) -> bool:
    """A script named for a check, or a shell script given a test file to run, as in `bash run.sh tests/test_a.py`."""
    script, args = words[i], words[i + 1:]
    # A test file may carry a test's line or id: spec/a_spec.rb:12, tests/test_a.py::test_b.
    named = _SCRIPT.fullmatch(script) or (script.endswith(".sh") and any(
        _SCRIPT.fullmatch(re.split(r"[:\[]", w, maxsplit=1)[0]) for w in args))
    return bool(named) and not _prints(args)


def _package_task(words: list[str], i: int, name: str) -> int | None:
    """The index of the task or command an npm, pnpm, yarn or bun command runs, or None when it runs neither."""
    runs = {"run", "run-script", "exec", "dlx", "x", "workspace", "t"}
    k = i + 1
    while k < len(words):
        word = words[k]
        if word.startswith("-") and len(word) > 1:
            k += 1
            if ("=" not in word and k < len(words) and not words[k].startswith("-") and words[k] not in runs
                    and not _TASK.fullmatch(words[k]) and not (name != "npm" and _known(words[k]))):
                k += 1
        elif word == "workspace":
            k += 2
        elif word in ("run", "run-script", "exec", "dlx", "x"):
            return k + 1
        elif _TASK.fullmatch(word) or word == "t" or name != "npm":
            return k  # yarn, pnpm and bun also run a tool by its name
        else:
            return None
    return None


def _prints(args: list[str]) -> bool:
    """Only a version, help or the list of tests: `--version`, `--help`, `--collect-only`."""
    return any(arg in _ONLY_PRINTS for arg in args)


def checks_ran_since(transcript_path: object, since: float) -> bool | None:
    """Did a check command run after `since`? None when the transcript can't be read."""
    tail = session.transcript_tail(transcript_path, 1024 * 1024)
    if tail is None:
        return None
    checks = set()
    for row, block in session.tool_calls(transcript_path, '"command"'):
        try:
            stamp = _dt.datetime.fromisoformat(str(row.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if stamp < since - _SAME_MESSAGE_SLACK:
            continue
        command = (block.get("input") or {}).get("command") if isinstance(block.get("input"), dict) else None
        if isinstance(command, str) and is_check(command):
            checks.add(block["id"] if isinstance(block.get("id"), str) else None)
    return bool(checks - _never_ran(tail, checks))


def _never_ran(tail: str, ids: set) -> set:
    """The calls among `ids` whose result is an error other than a non-zero exit: refused, denied by a
    hook or auto mode, or never started."""
    found = set()
    for line in tail.splitlines() if ids - {None} else []:
        if '"tool_result"' not in line:
            continue
        try:
            content = json.loads(line)["message"]["content"]
        except (ValueError, KeyError, TypeError):
            continue
        for block in content if isinstance(content, list) else []:
            if (not isinstance(block, dict) or not isinstance(block.get("tool_use_id"), str)
                    or block["tool_use_id"] not in ids or block.get("is_error") is not True):
                continue
            text = block.get("content")
            if isinstance(text, list):
                text = "".join(str(p.get("text", "")) for p in text if isinstance(p, dict))
            # Bash reports a command that ran and failed as "Exit code N".
            if not str(text or "").startswith("Exit code "):
                found.add(block["tool_use_id"])
    return found


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
    """Background work still in flight (hooks.md#stop-input) means the turn is paused, not done. Every entry
    counts, since Claude Code adds task types and passes an unknown one through as it is."""
    return any(isinstance(task, dict) for task in hook_input.get("background_tasks") or [])


def before_stop(hook_input: dict, settings: Settings) -> str | None:
    if hook_input.get("stop_hook_active") or settings.mode != "auto" or not settings.verify_gate \
            or still_running(hook_input):
        return None
    return unverified_edit(hook_input)
