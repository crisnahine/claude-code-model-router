"""Run bench tasks under each configuration through headless Claude Code and append one JSON line per run.

Example:
  python3 bench/run.py --tasks lookup-retry-limit bugfix-pagination --configs plugin defaults haiku-high \\
      --runs 1 --budget 0.5 --max-total 3 --batch pilot-1 --out bench/results/pilot/pilot-1.jsonl
"""
from __future__ import annotations

import argparse
import datetime
import filecmp
import functools
import hashlib
import json
import os
import random
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent
PLUGIN = ROOT / "plugins" / "model-router"
sys.path.insert(0, str(PLUGIN / "scripts"))
from modelrouter.ledger import read as read_ledger  # noqa: E402
ROW_SCHEMA = 3  # a row's schema says which harness wrote it; analyze refuses a row below this
CLASSES = ("lookup", "implementation", "bugfix", "cross-module", "security-review", "root-cause", "design", "research",
           "document", "analysis", "operations", "redo")

# Every run starts from the same environment: nothing a parent Claude Code session, its plugins or the shell's
# subagent and effort settings put there, except the provider switches and token auth. A configuration adds its
# own on top.
CLEARED = ("CLAUDE_CODE_", "CLAUDECODE", "CLAUDE_PLUGIN_", "CLAUDE_PROJECT_DIR")
KEPT = ("CLAUDE_CODE_USE_",)
KEPT_EXACT = ("CLAUDE_CODE_OAUTH_TOKEN",)
ISOLATION = {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
# dontAsk lets the file tools read the work dir without a rule and denies the rest; Edit rules cover Write too
# (https://code.claude.com/docs/en/permissions). A bare Read or Edit would approve them anywhere on the machine.
TOOLS = ("Bash", "Edit(./**)")
# A workflow task's script runs through the Workflow tool, which needs Read for a script given by path
# (https://code.claude.com/docs/en/workflows.md); scoped like Edit, so the file tools still reach only the work dir.
WORKFLOW_TOOLS = ("Workflow", "Read(./**)")
# Bash runs in the OS sandbox and is approved there without a prompt (https://code.claude.com/docs/en/sandboxing).
SANDBOX = {"enabled": True, "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False, "failIfUnavailable": True}
# The sandbox reads most of the machine by default and has no built-in credential list.
CREDENTIAL_FILES = ("~/.ssh", "~/.aws", "~/.azure", "~/.config/gcloud", "~/.config/gh", "~/.docker", "~/.gnupg",
                    "~/.kube", "~/.netrc", "~/.npmrc", "~/.pypirc", "~/.git-credentials", "~/.claude", "~/.claude.json")
# Where Claude Code reads managed settings from (https://code.claude.com/docs/en/managed-settings.md).
MANAGED_SETTINGS = ("/Library/Application Support/ClaudeCode/managed-settings.json",
                    "/Library/Application Support/ClaudeCode/managed-settings.d",
                    "/Library/Managed Preferences/com.anthropic.claudecode.plist",
                    "/etc/claude-code/managed-settings.json", "/etc/claude-code/managed-settings.d",
                    "C:\\Program Files\\ClaudeCode\\managed-settings.json", "C:\\Program Files\\ClaudeCode\\managed-settings.d")
AUTH = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
# Every run's scratch folder sits here, so one sandbox rule hides the others' work from it. Short and unresolved:
# the run's CLAUDE_CODE_TMPDIR is its scratch folder, and one over about 30 bytes falls back to the shared temp folder.
RUNS = (Path("/tmp") / f"mrb-{os.getuid()}" if hasattr(os, "getuid")
        else Path(tempfile.gettempdir()) / "mrb")
VAR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")  # what sandbox.credentials.envVars accepts
# A subagent_type is model-written and reaches the page, so anything else counts as "invalid".
AGENT_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
# The shell's directories can name the checkout, whose tasks hold the reference answers.
DROPPED = ("PWD", "OLDPWD")
PROMPT = ("Hand the task below to one subagent with the Agent tool and wait for its answer; don't do the work "
          "yourself. When it returns, reply with its final answer.\n\nTask: {brief}")
# A task.json "shape" replaces the delegate prompt with one that lets the session start more than one subagent.
SHAPES = {
    "redo": ("Hand the task below to a subagent with the Agent tool and wait for its answer; don't do the work "
             "yourself, but do run any check the task names and send the work back when the task says to. When "
             "the work is done, reply with its final answer.\n\nTask: {brief}"),
    "fanout": ("Give each question in the task below to its own subagent with the Agent tool, all started at once, and "
               "wait for their answers; don't look up the answers yourself. Then write the answer file the task "
               "asks for from what they found, and reply with its contents.\n\nTask: {brief}"),
    "plan-build": ("Hand each step of the task below to a subagent with the Agent tool, one step at a time and in "
                   "order, giving each one what the steps before it decided; don't do the work yourself. When "
                   "the last step returns, reply with its final answer.\n\nTask: {brief}"),
    "workflow": ("Do the task below with one Workflow tool call: write a short script with one `agent()` stage for "
                 "each step of the task, run it and wait for it to finish; don't do the work yourself. When it "
                 "returns, reply with what it returned.\n\nTask: {brief}"),
}


def load_tasks() -> list[dict]:
    tasks = []
    for folder in sorted(p for p in (BENCH / "tasks").iterdir() if (p / "task.json").is_file()):
        task = json.loads((folder / "task.json").read_text(encoding="utf-8"))
        tasks.append({**task, "id": folder.name, "dir": folder})
    return tasks


def load_configs() -> list[dict]:
    return json.loads((BENCH / "configs.json").read_text(encoding="utf-8"))


def prompt(task: dict) -> str:
    """The prompt that hands the task to one subagent, or as the task's `shape` says."""
    return (SHAPES[task["shape"]] if "shape" in task else PROMPT).format(brief=task["brief"])


def _plugin_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")


def plugin_hash(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in _plugin_files(folder):
        digest.update(path.relative_to(folder).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:16]


def freeze_plugin(source: Path = PLUGIN) -> tuple[str, str]:
    """A copy of the plugin made once at the start of a batch, so an edit or a checkout during it can't reach later
    runs, and the hash of what was copied. The caller removes the copy's folder, the parent of the path given."""
    target = Path(tempfile.mkdtemp(prefix="mrbench-plugin-")) / "model-router"
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return str(target), plugin_hash(target)


def managed_settings() -> list[str]:
    """The managed settings sources on this machine; a run loads them whatever its own settings say."""
    return [p for p in MANAGED_SETTINGS if os.path.exists(p)]


def _plugin_name() -> str:
    return json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]


def _git_lines(*args: str) -> list[str]:
    res = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    return res.stdout.splitlines() if res.returncode == 0 else []


def answer_paths(*extra: str | None) -> list[str]:
    """Where this machine keeps the tasks' answers or graded runs: this checkout, the repository's git data and main
    checkout, every worktree, Claude Code's shared temp folder (other sessions copy files there) and the one under the
    harness's own CLAUDE_CODE_TMPDIR, and `extra` (results and kept runs). Each path as given and resolved, so a
    symlink such as macOS's /tmp can't get round it."""
    found = [ROOT]
    common = _git_lines("rev-parse", "--git-common-dir")
    if common:
        git_dir = (ROOT / common[0]).resolve()
        found += [git_dir, git_dir.parent if git_dir.name == ".git" else git_dir]
    found += [Path(line[len("worktree "):]) for line in _git_lines("worktree", "list", "--porcelain")
              if line.startswith("worktree ")]
    if hasattr(os, "getuid"):
        found.append(Path("/tmp") / f"claude-{os.getuid()}")
        if os.environ.get("CLAUDE_CODE_TMPDIR"):
            found.append(Path(os.environ["CLAUDE_CODE_TMPDIR"]) / f"claude-{os.getuid()}")
    found += [Path(p) for p in extra if p]
    return list(dict.fromkeys(str(q) for p in found for q in (p.absolute(), p.resolve())))


def _both(path: Path) -> list[str]:
    return list(dict.fromkeys([str(path), str(path.resolve())]))


def _under(path: str, folder: str) -> bool:
    return Path(path) == Path(folder) or Path(folder) in Path(path).parents


def settings(env: dict, hidden: list[str] | None = None, own: str | None = None) -> dict:
    """The one --settings JSON a run gets: credentials, the answers and other runs' scratch folders kept from
    sandboxed commands, and the answers kept from the file tools."""
    hidden = answer_paths() if hidden is None else hidden
    sandbox = {**SANDBOX,
               # The narrower allowRead re-opens the run's own folder under RUNS (https://code.claude.com/docs/en/sandboxing).
               "filesystem": {"denyRead": [*hidden, *_both(RUNS)], "allowRead": _both(Path(own)) if own else []},
               "credentials": {
                   "files": [{"path": path, "mode": "deny"} for path in CREDENTIAL_FILES],
                   "envVars": [{"name": name, "mode": "deny"} for name in sorted(env)
                               if is_secret(name) and VAR_NAME.fullmatch(name)]}}
    # The sandbox doesn't cover the file tools; deny rules do, except over the run's own folder, since no rule
    # re-opens a denied path (https://code.claude.com/docs/en/permissions).
    rules = [f"{tool}(//{path.lstrip('/')}/**)" for path in hidden if not (own and _under(own, path))
             for tool in ("Read", "Edit")]
    return {"sandbox": sandbox, "permissions": {"deny": rules}}


def command(prompt: str, config: dict, options: dict, base_env: dict, shape: str = "single") -> tuple[list[str], dict]:
    env = {k: v for k, v in base_env.items()
           if k not in DROPPED and (k in KEPT_EXACT or k.startswith(KEPT) or not k.startswith(CLEARED))}
    env.update(ISOLATION)
    if options.get("config_dir"):
        # The run's own config folder: ~/.claude.json, settings, plugins and transcripts all live under it.
        env.update({"CLAUDE_CONFIG_DIR": options["config_dir"], "CLAUDE_CODE_PROJECT_DIR_NAME": "run"})
    if options.get("scratch"):
        # Else sandboxed commands get /tmp/claude-<uid>, which every session on the machine shares.
        env["CLAUDE_CODE_TMPDIR"] = options["scratch"]
    env.update(config.get("env", {}))
    cmd = [*options["claude"], "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--model", options["model"],
           "--max-budget-usd", str(options["budget"]), "--setting-sources", "project", "--strict-mcp-config",
           "--permission-mode", "dontAsk",
           "--settings", json.dumps(settings(env, options.get("hidden"), options.get("scratch")))]
    if config.get("plugin"):
        cmd += ["--plugin-dir", options["plugin_dir"]]
    tools = TOOLS + (WORKFLOW_TOOLS if shape == "workflow" else ())
    return cmd + ["--allowedTools", *tools], env


LIMIT_RETRIES = 5
LIMIT_PAUSE = 60  # seconds before the first retry; each later pause is twice as long
API_ERROR_STOP = 3  # account errors, in a row, that stop the bench: the key or the account is wrong
# The messages Claude Code prints when the API refuses for a limit (https://code.claude.com/docs/en/errors.md).
_LIMIT_MESSAGE = (r"API Error: (?:Repeated )?(?:429|529)\b|API Error: Repeated 529 Overloaded|Request rejected \(429\)|"
                  r"Server is temporarily limiting requests|You've hit your (?:session|weekly|Opus|Sonnet) limit")


# What Claude Code printed when the network went away (2026-11-22): an API Error naming a network code, or a
# dropped connection.
_NETWORK_MESSAGE = (r"API Error: [^\n]*\((?:ENOTFOUND|ENETUNREACH|ECONNRESET|ECONNREFUSED|ETIMEDOUT|EAI_AGAIN)\)|"
                    r"API Error: (?:Can't reach the API server|No internet route)|Connection lost")


# What ends a run when the account can't pay or may not use the model, before or part-way through the task
# (https://code.claude.com/docs/en/errors.md, https://platform.claude.com/docs/en/api/errors.md).
_ACCOUNT_MESSAGE = (r"(?:API Error: \d{3}\b[^\n]*?)?(?:Credit balance (?:is )?too low|Usage credits ran out|"
                    r"You've hit your [^\n·]*?(?:spend limit|usage limit|shared budget)|"
                    r"You have reached your (?:specified )?(?:workspace )?API usage limits|Invalid API key|"
                    r"Failed to authenticate|OAuth token (?:revoked|has expired)|Your account is on hold|"
                    r"Your ANTHROPIC_API_KEY belongs to a disabled organization|This organization has been disabled|"
                    r"Your organization has disabled|There's an issue with the selected model|Model '[^'\n]*' not found)")


def refusal(row: dict) -> str | None:
    """Why the API didn't serve the task: "limit" or "network", "api_error" for an account error whatever was spent (a
    bad key, no credit, a spend limit, a model the account may not use) or for any other error before a cent was
    spent, else None. Only an errored run counts, and a limit, network or account error only on the API's status or
    Claude Code's own message, at the start of the answer (which is then that message) or in stderr. Task text that
    talks about limits, credit or network codes is never read as one."""
    if not (row.get("is_error") or row.get("exit_code")):
        return None
    if row.get("api_error_status") in (429, 529):
        return "limit"
    if row.get("api_error_status") in (401, 402, 403):  # authentication, billing, permission
        return "api_error"
    answer, stderr = str(row.get("answer") or ""), str(row.get("stderr") or "")
    for reason, message in (("limit", _LIMIT_MESSAGE), ("network", _NETWORK_MESSAGE), ("api_error", _ACCOUNT_MESSAGE)):
        if re.match(rf"\s*(?:{message})", answer) or re.search(message, stderr):
            return reason
    # No spend means no model reply, so no tool ran. error_during_execution's zero cost may have been zeroed.
    if row.get("total_cost_usd") == 0 and row.get("subtype") != "error_during_execution":
        return "api_error"
    return None


SECRET_NAMES = ("TOKEN", "KEY", "SECRET", "PASSWORD", "AUTH", "CREDENTIAL")


def is_secret(name: str) -> bool:
    """Whether an environment variable's name marks a credential. PAT counts only as a whole word, so PATH stays."""
    upper = name.upper()
    return (any(w in upper for w in SECRET_NAMES) or "PAT" in re.split(r"[^A-Z0-9]+", upper)
            or upper.startswith(("ANTHROPIC_", "CLAUDE_CODE_OAUTH")))


def grading_env(env: dict) -> dict:
    """The environment a check runs in: the harness's own, minus credentials, since the check's process also runs
    the code a run left in the work dir."""
    return {k: v for k, v in env.items() if not is_secret(k)}


# A check exits 0 when the task was solved, 1 when it wasn't and 2 when the check itself broke. The launcher turns
# anything the check raises into 2, so a bug in a check can't read as a wrong answer.
# It writes UTF-8 on any locale, since grade() decodes the check's output as UTF-8.
_LAUNCH = ("import runpy, sys, traceback\nsys.stdout.reconfigure(encoding='utf-8')\nsys.stderr.reconfigure(encoding='utf-8')\n"
           "sys.argv = sys.argv[1:]\ntry:\n"
           "    runpy.run_path(sys.argv[0], run_name='__main__')\nexcept SystemExit:\n    raise\n"
           "except BaseException:\n    traceback.print_exc()\n    sys.exit(2)\n")


# The result subtype of a run that --max-budget-usd stopped; its cost is cut off at the cap.
BUDGET_STOP = "error_max_budget_usd"


class Grade(NamedTuple):
    verdict: str  # "pass", "fail", "broken", or "stopped" for a run the bench itself stopped before the check finished
    reason: str  # the last line the check printed, or for a broken check the last line of its error
    check_hash: str  # the check's code, so a row says which check graded it
    sandbox: str | None = None  # what the check ran in: "sandbox-exec", "bwrap", or None for no sandbox

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"


def check_hash(check: Path) -> str:
    """A hash of the code that decides a task: the check, any script beside it and the helper checks share."""
    files = sorted(Path(check).parent.glob("*.py")) + [Path(check).parent.parent / "passcheck.py"]
    digest = hashlib.sha256()
    for path in dict.fromkeys(files):
        digest.update(path.name.encode() + b"\0" + (path.read_bytes() if path.is_file() else b"") + b"\0")
    return digest.hexdigest()[:16]


def _last_line(data: bytes) -> str:
    lines = [line.strip() for line in data.decode("utf-8", "replace").splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""


def _uncopyable(folder: str, names: list[str]) -> list[str]:
    """The entries a check couldn't read anyway: a socket, a pipe, a file or folder the run made unreadable. Left out
    of the copy, they read as missing, so they fail the run instead of breaking the check."""
    out = []
    for name in names:
        path = os.path.join(folder, name)
        try:
            mode = os.lstat(path).st_mode
        except OSError:
            out.append(name)
            continue
        if stat.S_ISDIR(mode):
            readable = os.access(path, os.R_OK | os.X_OK)
        else:
            readable = stat.S_ISLNK(mode) or (stat.S_ISREG(mode) and os.access(path, os.R_OK))
        if not readable:
            out.append(name)
    return out


@functools.lru_cache(maxsize=None)
def _can_sandbox(tool: str) -> bool:
    """Whether `tool` can start a sandbox here: a host can bar it, as one that forbids unprivileged namespaces does."""
    probe = {"sandbox-exec": ["sandbox-exec", "-p", "(version 1)(allow default)", "true"],
             "bwrap": ["bwrap", "--ro-bind", "/", "/", "--unshare-net", "--die-with-parent", "--", "true"]}[tool]
    try:
        return shutil.which(tool) is not None and subprocess.run(probe, capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def grade_sandbox(scratch: Path) -> tuple[list[str], str | None]:
    """The argv prefix that runs a check in the OS sandbox, as a run's own commands are: no network, the credential
    files unreadable, writes only under `scratch`. With its name, or ([], None) where neither sandbox-exec (macOS)
    nor bwrap (Linux) can start."""
    own = os.path.realpath(scratch)
    hidden = [os.path.realpath(os.path.expanduser(p)) for p in CREDENTIAL_FILES]
    if sys.platform == "darwin" and _can_sandbox("sandbox-exec"):
        def paths(items: list[str]) -> str:
            return " ".join(f"(subpath {json.dumps(p)})" for p in items)
        return ["sandbox-exec", "-p", f"(version 1)(allow default)(deny network*)(deny file-read* {paths(hidden)})"
                f"(deny file-write* (require-not (require-any {paths([own, '/dev'])})))"], "sandbox-exec"
    if sys.platform.startswith("linux") and _can_sandbox("bwrap"):
        masks = [arg for p in hidden if os.path.lexists(p)
                 for arg in (("--tmpfs", p) if os.path.isdir(p) else ("--ro-bind", "/dev/null", p))]
        return ["bwrap", "--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev", "--bind", own, own, "--unshare-net",
                "--die-with-parent", *masks, "--"], "bwrap"
    return [], None


def grade(check: Path, work: Path, answer: Path, timeout: int = 120) -> Grade:
    """The task's check run on a throwaway copy of a work dir, from a scratch folder, and an answer file, so code a
    run left behind can't write into the work dir, the checkout or the results. -I keeps the check's folder and the
    work dir off sys.path and ignores PYTHON* variables, so that code can't shadow what the check imports. The
    scratch folder sits under RUNS and is the check's temp folder, so no other run's sandbox can read the copies.
    The check, and the work's code it runs, run in the OS sandbox where one can start (`grade_sandbox`)."""
    digest = check_hash(check)
    scratch = Path(tempfile.mkdtemp(prefix="mrbench-grade-", dir=runs_folder()))
    jail, sandbox = grade_sandbox(scratch)
    try:
        copy = scratch / "work"
        if not os.access(work, os.R_OK | os.X_OK):  # the ignore callback never sees the root, and the harness owns it
            os.chmod(work, stat.S_IRWXU)
        shutil.copytree(work, copy, symlinks=True, ignore=_uncopyable)
        res = subprocess.run([*jail, sys.executable, "-I", "-c", _LAUNCH, str(check), str(copy), str(answer)],
                             capture_output=True, cwd=scratch,
                             env={**grading_env(dict(os.environ)), "TMPDIR": str(scratch)}, timeout=timeout)
    except subprocess.TimeoutExpired:  # code the run left that hangs the check fails the task, not the bench
        return Grade("fail", f"the check timed out after {timeout} s", digest, sandbox)
    except OSError as exc:
        return Grade("broken", f"the work dir could not be copied for the check: {exc}", digest, sandbox)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if res.returncode == 0:
        return Grade("pass", _last_line(res.stdout), digest, sandbox)
    if res.returncode == 2:
        return Grade("broken", _last_line(res.stderr) or _last_line(res.stdout), digest, sandbox)
    return Grade("fail", _last_line(res.stdout) or f"the check exited {res.returncode}", digest, sandbox)


def can_start(spent: float, budget: float, max_total: float) -> bool:
    # A run can pass --max-budget-usd by one response plus what running subagents spend before they stop, so
    # this leaves room for twice its cap. That is a margin, not a bound.
    return spent + 2 * budget <= max_total


def plugin_data_dir(env: dict) -> Path:
    """Where Claude Code keeps the --plugin-dir router's data (health, the ledger):
    <config>/plugins/data/<id>, the id with anything but letters, digits, _ and - turned into -."""
    return Path(env["CLAUDE_CONFIG_DIR"]) / "plugins" / "data" / re.sub(r"[^A-Za-z0-9_-]", "-", f"{_plugin_name()}@inline")


def session_dir(env: dict) -> Path:
    """The run's transcripts: <config>/projects/<CLAUDE_CODE_PROJECT_DIR_NAME> (https://code.claude.com/docs/en/env-vars)."""
    return Path(env["CLAUDE_CONFIG_DIR"]) / "projects" / env["CLAUDE_CODE_PROJECT_DIR_NAME"]


def read_stream(stdout: str) -> tuple[dict, dict, dict[str, int], list[dict], list[dict]]:
    """The system/init event, the last result line, the subagents started by type, each Agent call in order:
    its agent, the model and effort the call named itself, which outrank a definition's
    (https://code.claude.com/docs/en/sub-agents), and the model of the first message the stream shows the subagent
    sending (`parent_tool_use_id`), None when it shows none; and each Workflow call in order: the saved workflow it
    named (None for a script or a path, `invalid` for a name that isn't a plain one) and its tool_result block's
    `is_error`, None when none came back. A line that isn't a JSON object is skipped, as is a tool_use id seen
    before."""
    init, result, started, calls, workflows = {}, {}, {}, {}, {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "system" and event.get("subtype") == "init" and not init:
            init = event
        elif event.get("type") == "result":
            result = event
        elif event.get("type") == "assistant":
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            parent = event.get("parent_tool_use_id")
            mine = calls.get(parent) if isinstance(parent, str) else None
            if mine and mine["ran_on"] is None and isinstance(message.get("model"), str):
                mine["ran_on"] = message["model"]
            for block in message.get("content") or []:
                if (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Workflow"
                        and block.get("id") not in workflows):
                    named = (block.get("input") if isinstance(block.get("input"), dict) else {}).get("name")
                    workflows[block.get("id")] = {
                        "name": None if named is None else named if isinstance(named, str) and AGENT_NAME.fullmatch(named)
                        else "invalid", "is_error": None}
                if (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in ("Agent", "Task")
                        and block.get("id") not in calls):
                    call = block.get("input") if isinstance(block.get("input"), dict) else {}
                    kind = call.get("subagent_type") or "general-purpose"
                    if not isinstance(kind, str) or not AGENT_NAME.fullmatch(kind):
                        kind = "invalid"
                    started[kind] = started.get(kind, 0) + 1
                    calls[block.get("id")] = {"agent": kind, "named_model": call.get("model") or None,
                                              "named_effort": call.get("effort") or None, "ran_on": None}
        elif event.get("type") == "user":
            # What a workflow's result holds isn't documented, so only the block's own error flag is read.
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            content = message.get("content") if isinstance(message.get("content"), list) else []
            for block in content:
                mine = workflows.get(block.get("tool_use_id")) if isinstance(block, dict) else None
                if mine and block.get("type") == "tool_result":
                    mine["is_error"] = block.get("is_error") is True
    return init, result, started, list(calls.values()), list(workflows.values())


def plugin_loaded(init: dict) -> bool:
    """Whether system/init lists the router among the loaded plugins and reports no plugin errors."""
    name = _plugin_name()
    listed = any(isinstance(p, dict) and str(p.get("name", "")).split("@")[0] == name for p in init.get("plugins") or [])
    return listed and not init.get("plugin_errors")


PRICES = json.loads((BENCH / "prices.json").read_text(encoding="utf-8"))


def price(model_id: str) -> dict | None:
    """List prices for a full model ID, or None for a version the table doesn't list. A near match never counts: a new
    version is often priced differently."""
    model_id = re.sub(r"-\d{8}$", "", (model_id or "").lower().split("[")[0])
    if model_id in PRICES:
        return PRICES[model_id]
    families = [k for k in PRICES if k.endswith("-*") and model_id.startswith(k[:-1])]
    return PRICES[max(families, key=len)] if families else None


def list_cost(model_id: str, tokens: dict) -> float | None:
    """USD for one request's tokens at list price, or None for a model with no price."""
    rates = price(model_id)
    if not rates:
        return None
    long = rates.get("long_prompt")
    prompt = sum(tokens.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    if isinstance(long, dict) and prompt > long.get("over", float("inf")):
        rates = long
    # The rest of the writes count as five minutes.
    hour = max(0, min(tokens.get("ephemeral_1h_input_tokens", 0), tokens.get("cache_creation_input_tokens", 0)))
    total = (tokens.get("input_tokens", 0) * rates.get("input", 0)
             + tokens.get("output_tokens", 0) * rates.get("output", 0)
             + tokens.get("cache_read_input_tokens", 0) * rates.get("cache_read", 0)
             + (tokens.get("cache_creation_input_tokens", 0) - hour) * rates.get("cache_write", 0)
             + hour * rates.get("cache_write_1h", 2 * rates.get("input", 0)))
    return round(total / 1_000_000, 6)


def transcript_cost(folder: Path) -> float | None:
    """A run's spend at list price from its saved transcripts, main and subagents, each message once (streamed lines
    repeat its usage); None when no transcript holds usage or a message's model has no price, so the budget
    stands in rather than an understated sum."""
    if not folder.is_dir():
        return None
    messages = {}
    for path in folder.rglob("*.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            message = event.get("message") if isinstance(event, dict) else None
            if isinstance(message, dict) and isinstance(message.get("usage"), dict) and message.get("id"):
                messages[message["id"]] = message
    if not messages:
        return None
    total = 0.0
    for message in messages.values():
        usage = message["usage"]
        tokens = {k: v for k, v in usage.items() if isinstance(v, (int, float))}
        tokens.update({k: v for k, v in (usage.get("cache_creation") or {}).items() if isinstance(v, (int, float))})
        cost = list_cost(message.get("model") or "", tokens)
        if cost is None and any(tokens.values()):  # a message with no tokens, like an error notice, costs nothing
            return None
        total += cost or 0.0
    return round(total, 6)


def ledger_rows(env: dict) -> list[dict] | None:
    """The router's ledger rows for a run, in order; None when it kept no ledger or the file can't be read. Lines that
    aren't objects are skipped."""
    path = plugin_data_dir(env) / "ledger.jsonl"
    if not path.is_file():
        return None
    try:
        path.read_text(encoding="utf-8")  # read_ledger gives [] for an unreadable file and raises on invalid UTF-8
    except (OSError, ValueError):
        return None
    return read_ledger(path=path)


def agent_types(env: dict) -> dict[str, str]:
    """Each subagent's type by id, from the saved session's subagents/agent-<id>.meta.json, a layout no page documents
    (Claude Code 2.1.330 writes it). Only the ledger spend join reads it."""
    types = {}
    for meta in session_dir(env).glob("*/subagents/agent-*.meta.json"):
        try:
            types[meta.name[len("agent-"):-len(".meta.json")]] = json.loads(meta.read_text(encoding="utf-8"))["agentType"]
        except (OSError, ValueError, KeyError):
            continue
    return types


def spend_by_agent(ledger: list[dict], types: dict[str, str], total: object) -> dict[str, float]:
    """A router run's spend by agent type, from Claude Code's estimate per subagent: each one's newest usage row
    holds all of its turns, and names its type, else the transcripts do. `main` is the rest of the run's total: the
    main loop, and agents with no estimate."""
    last: dict[str, tuple[float, object]] = {}
    for row in ledger:
        if row.get("event") == "usage" and row.get("agent_id") and isinstance(row.get("cost_usd"), (int, float)):
            last[row["agent_id"]] = (row["cost_usd"], row.get("agent"))
    spend: dict = {}
    for agent_id, (usd, agent) in last.items():
        who = agent if isinstance(agent, str) and agent else types.get(agent_id, "unknown")
        spend[who] = round(spend.get(who, 0.0) + usd, 6)
    if isinstance(total, (int, float)):
        spend["main"] = round(max(0.0, total - sum(usd for usd, _ in last.values())), 6)
    return spend


ROUTED_KEYS = ("agent", "kind", "level", "model", "effort", "source", "escalated_from", "on_record")  # on_record only when false


def spawns(ledger: list[dict]) -> list[dict]:
    """A router run's spawn decisions, in order. A key the router didn't log stays out, so an older router's
    missing escalated_from reads as unknown rather than as no escalation."""
    return [{k: row[k] for k in ROUTED_KEYS if k in row} for row in ledger if row.get("event") == "spawn"]


def _text(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")


def _git_head() -> str | None:
    res = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
    return res.stdout.strip() or None


def _git_dirty() -> bool:
    """Whether bench/ or the plugin has changes HEAD doesn't hold, so bench_commit isn't the code that ran. The
    bench's own output under bench/results/ isn't code, so a results file written earlier in a run doesn't count."""
    res = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "bench", "plugins",
                          ":(exclude)bench/results"], capture_output=True, text=True)
    return bool(res.stdout.strip())


FINAL_FILE_CHARS = 20_000  # kept per file
FINAL_ROW_CHARS = 80_000  # kept per row, the reply and every file together


def final_answer(shipped: Path, work: Path, reply: str) -> dict:
    """What the check graded, kept in the row because the work dir is deleted after the run: the full reply, each
    text file the run added or changed against the task's repo/, the files it removed, and the names of files left out
    (binary, unreadable, a link out of the work dir, caches, or over the caps). The reply alone is cut to the row cap."""
    reply = reply[:FINAL_ROW_CHARS]
    room, files, omitted, root = FINAL_ROW_CHARS - len(reply), {}, [], work.resolve()
    for path in sorted(p for p in work.rglob("*") if p.is_file()):
        rel = path.relative_to(work).as_posix()
        if "__pycache__" in path.parts or rel.startswith((".claude/", ".pytest_cache/")) or path.suffix == ".pyc":
            continue
        before = shipped / rel
        try:  # the money for the run is spent by now, so a file that can't be read must not lose the row
            if root not in path.resolve().parents:
                omitted.append(rel)
                continue
            # A UTF-8 character is at most 4 bytes, so a bigger file can't fit the cap; reading it could exhaust memory.
            if path.stat().st_size > 4 * FINAL_FILE_CHARS:
                if not (before.is_file() and filecmp.cmp(before, path, shallow=False)):
                    omitted.append(rel)
                continue
            data = path.read_bytes()
        except OSError:
            omitted.append(rel)
            continue
        if before.is_file() and before.read_bytes() == data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            omitted.append(rel)
            continue
        if len(text) > FINAL_FILE_CHARS or len(text) > room:
            omitted.append(rel)
            continue
        files[rel], room = text, room - len(text)
    removed = sorted(p.relative_to(shipped).as_posix() for p in shipped.rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts and not (work / p.relative_to(shipped)).exists())
    return {"reply": reply, "files": files, "removed": removed, "omitted": omitted}


def runs_folder() -> Path:
    """RUNS, made private to this user; on a shared /tmp another user could have made it first."""
    RUNS.mkdir(mode=0o700, exist_ok=True)
    if hasattr(os, "getuid") and RUNS.stat().st_uid != os.getuid():
        raise RuntimeError(f"{RUNS} belongs to another user")
    return RUNS


def run_claude(cmd: list[str], work: Path, env: dict, timeout: float) -> tuple[str, str, int]:
    """claude's output and exit code. It runs in a session of its own, so a timeout or an interrupt also stops what it
    started, which killing claude alone leaves running. A timeout raises with the output read by then."""
    proc = subprocess.Popen(cmd, cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except BaseException as exc:
        if hasattr(os, "killpg"):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:  # the group has already gone
                pass
            proc.wait()  # the timeout already holds the output read so far
        else:  # Windows has no process group to kill, so this stops claude alone
            proc.kill()
            output = proc.communicate()
            if isinstance(exc, subprocess.TimeoutExpired):
                exc.stdout, exc.stderr = output
        raise
    return out, err, proc.returncode


def run_one(task: dict, config: dict, options: dict) -> dict:
    # No task or configuration name: a brief that quotes its working directory would tell the model what it is graded on.
    scratch = Path(tempfile.mkdtemp(dir=runs_folder()))
    work = scratch / "work"
    shutil.copytree(task["dir"] / "repo", work)
    cmd, env = command(prompt(task), config,
                       {**options, "config_dir": str(scratch / "config"), "scratch": str(scratch)}, dict(os.environ),
                       task.get("shape", "single"))
    started = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    clock = time.monotonic()
    stdout, stderr, code, wall_ms, result, init, started_agents, calls = "", "interrupted", None, None, {}, {}, {}, []
    workflows: list[dict] = []
    ledger, types, final, graded, kept, stopped, priced, transcripts = None, {}, None, None, None, None, None, None
    stopped_by = None
    # Once claude starts the run costs money: whatever stops it or the steps after it, its row is still built and its
    # scratch removed. With no result the row is priced from its transcripts, or charged the whole budget.
    try:
        try:
            stdout, stderr, code = run_claude(cmd, work, env, options["timeout"])
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, code, stopped_by = _text(exc.stdout), "timed out", None, "timeout"
        # The run's wall time: Claude Code's own duration_ms can fall short of it.
        wall_ms = round((time.monotonic() - clock) * 1000)
        init, result, started_agents, calls, workflows = read_stream(stdout)
        if result.get("subtype") == BUDGET_STOP:
            stopped_by = "budget"
        answer = result.get("result") or ""
        (scratch / "answer.txt").write_text(answer, encoding="utf-8")
        if not isinstance(result.get("total_cost_usd"), (int, float)) or result.get("subtype") == "error_during_execution":
            priced = transcript_cost(session_dir(env))
        # Read before grading: the check also runs the code the run left, which could rewrite these files.
        ledger = ledger_rows(env) if config.get("plugin") else None
        types = agent_types(env) if config.get("plugin") else {}
        final = final_answer(task["dir"] / "repo", work, answer)
        # A run the harness cut short isn't solved, whatever it had written by then.
        graded = (Grade("fail", f"the harness stopped the run ({stopped_by}), so it isn't solved",
                        check_hash(task["dir"] / "check.py")) if stopped_by
                  else grade(task["dir"] / "check.py", work, scratch / "answer.txt"))
        denied = bool(result.get("permission_denials"))
        if options.get("transcripts") and (not graded.passed or denied) and session_dir(env).is_dir():
            transcripts = Path(options["transcripts"]) / scratch.name
            shutil.copytree(session_dir(env), transcripts, symlinks=True)
        if options.get("keep"):
            # The work the run left, its answer, the router's own data and ledger, and the session's transcripts.
            target = Path(options["keep"]) / scratch.name
            shutil.copytree(scratch, target, symlinks=True)
            if session_dir(env).is_dir():
                shutil.copytree(session_dir(env), target / "session", symlinks=True)
            kept = target
    except BaseException as exc:
        stopped = exc
    if wall_ms is None:
        wall_ms = round((time.monotonic() - clock) * 1000)
    shutil.rmtree(scratch, ignore_errors=True)
    if graded is None:
        graded = Grade("stopped", f"the bench stopped before the check finished: {stopped!r}",
                       check_hash(task["dir"] / "check.py"))
    row = record(task, config, options, result,
                 {"started_at": started, "wall_ms": wall_ms, "exit_code": code, "stderr": stderr, "stopped_by": stopped_by,
                  "grade": graded._asdict(), "final_answer": final,
                  "ledger": ledger, "agent_types": types,
                  "kept": kept and str(kept), "init": init, "agents_started": started_agents,
                  "agent_calls": calls, "workflow_calls": workflows,
                  "agent_overrides": sum(bool(c["named_model"] or c["named_effort"]) for c in calls),
                  "transcript_cost": priced, "transcripts": transcripts and str(transcripts)})
    if stopped is not None:
        stopped.row = row  # main writes it before the bench stops
        raise stopped
    return row


def _denial(d: dict) -> dict:
    tool_input = d.get("tool_input")
    text = tool_input.get("command") if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str) \
        else json.dumps(tool_input, sort_keys=True)
    return {"tool": d.get("tool_name"), "input": text[:200]}


def never_started(init: dict, result: dict, exited: bool) -> bool:
    """Whether Claude Code stopped before the session began. It writes an error_during_execution result with
    `startup_failure_reason` for a known startup failure, the sandbox among them, and for others prints only to
    stderr (https://code.claude.com/docs/en/agent-sdk/typescript); without an init event, an error result with
    0 turns and no API status is one too. A run killed for a timeout or an interrupt may have spent, so it isn't."""
    if result.get("startup_failure_reason"):
        return True
    return exited and not init and (not result or (result.get("subtype") == "error_during_execution"
                                                   and result.get("num_turns") == 0
                                                   and result.get("api_error_status") is None))


def record(task: dict, config: dict, options: dict, result: dict, facts: dict) -> dict:
    """The row for one run, from Claude Code's result line and what the runner saw around the process."""
    models = {name: {k: usage.get(k) for k in ("inputTokens", "outputTokens", "cacheReadInputTokens",
                                                "cacheCreationInputTokens", "costUSD", "costBasis")}
              for name, usage in (result.get("modelUsage") or {}).items()}
    # modelUsage counts subagents and workflow agents too; result.usage counts only the main loop.
    usage = result.get("modelUsage")
    cache = {field: sum(u.get(key) or 0 for u in usage.values() if isinstance(u, dict)) if isinstance(usage, dict) else None
             for field, key in (("cache_read_tokens", "cacheReadInputTokens"),
                                ("cache_write_tokens", "cacheCreationInputTokens"), ("fresh_input_tokens", "inputTokens"))}
    cost = result.get("total_cost_usd")
    ledger, code = facts["ledger"], facts["exit_code"]
    stderr = facts["stderr"][-300:] if code else ""
    unstarted = never_started(facts.get("init") or {}, result, code is not None)
    if unstarted:
        cost = 0  # the start never reached the API
    refused = refusal({"is_error": result.get("is_error"), "exit_code": code, "stderr": stderr,
                       "api_error_status": result.get("api_error_status"), "answer": result.get("result"),
                       "subtype": result.get("subtype"), "total_cost_usd": cost})
    # A cost of 0 on error_during_execution "may be zeroed" (https://code.claude.com/docs/en/agent-sdk/cost-tracking.md),
    # except when a limit or the network stopped the run or it never started: nothing was spent. An account error can
    # come part-way.
    known = unstarted or isinstance(cost, (int, float)) and not (
        result.get("subtype") == "error_during_execution" and cost == 0 and refused not in ("limit", "network"))
    priced = facts.get("transcript_cost")
    source = "result" if known else "transcripts" if priced is not None else "budget"
    started, overrides = facts.get("agents_started") or {}, facts.get("agent_overrides") or 0
    return {
        "schema": ROW_SCHEMA, "task": task["id"], "task_revision": task.get("revision", 1), "class": task["class"],
        "shape": task.get("shape", "single"), "config": config["id"],
        "batch": options.get("batch"), "order_seed": options.get("seed"),
        "started_at": facts["started_at"], "claude_version": options["claude_version"],
        "main_model": options["model"],
        "budget_usd": options["budget"], "bench_commit": options["commit"], "bench_dirty": options.get("dirty"),
        "plugin_hash": options.get("plugin_hash") if config.get("plugin") else None,
        "managed_settings": options.get("managed"),
        "claude_started": not unstarted, "startup_failure_reason": result.get("startup_failure_reason"),
        # With no init event the stream never began, so it can't say whether the plugin loaded.
        "plugin_loaded": (plugin_loaded(facts["init"]) if facts.get("init") else None) if config.get("plugin") else None,
        "agent_costs": None if ledger is None else spend_by_agent(ledger, facts["agent_types"], cost if known else None),
        "routed": None if ledger is None else spawns(ledger),
        # shortcut: a fork leaves no spawn row and isn't told apart; key on its fork flag if a fork-only run shows up.
        "router_silent": bool(config.get("plugin"))
        and (any(c.get("ran_on") for c in facts.get("agent_calls") or []) or bool(facts.get("agent_types")))
        and not any(r.get("event") == "spawn" for r in ledger or []),
        "gate_events": sum(r.get("event") == "gate" for r in ledger or []),
        "exit_code": code, "stopped_by": facts.get("stopped_by"), "subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
        "api_error_status": result.get("api_error_status"), "terminal_reason": result.get("terminal_reason"),
        "errors": result.get("errors"),
        # Without a cost from the result, the transcripts' list price, else the whole budget so totals never understate.
        "total_cost_usd": cost if known else priced if priced is not None else options["budget"],
        "cost_known": known, "cost_source": source,
        "duration_ms": result.get("duration_ms"), "duration_api_ms": result.get("duration_api_ms"),
        "wall_ms": facts["wall_ms"], "num_turns": result.get("num_turns"),
        "models": models, "prices": {name: price(name) for name in models}, **cache,
        "agents_started": started, "agent_calls": facts.get("agent_calls") or [], "agent_overrides": overrides,
        "workflow_calls": facts.get("workflow_calls") or [],
        "permission_denials": [_denial(d) for d in result.get("permission_denials") or [] if isinstance(d, dict)],
        "passed": facts["grade"]["verdict"] == "pass", "grade": facts["grade"],
        "answer": (result.get("result") or "")[:2000], "final_answer": facts["final_answer"],
        **({"kept": facts["kept"]} if facts["kept"] else {}),
        **({"transcripts": facts["transcripts"]} if facts.get("transcripts") else {}),
        "stderr": stderr,
    }


def schedule(tasks: list[dict], configs: list[dict], runs: int, first_run: int = 0,
             seed: int = 0) -> list[tuple[int, dict, dict]]:
    """Each run to make as (run number, task, configuration), in an order shuffled by `seed`, so no
    configuration always goes first on a cold prompt cache or at one time of day."""
    out = [(run_index, task, config) for run_index in range(first_run, first_run + runs)
           for task in tasks for config in configs]
    random.Random(seed).shuffle(out)
    return out


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if path.is_file() else []


def _write(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def set_aside(row: dict) -> str | None:
    """Why a row stays out of the results: claude stopped before the session began (so no API call was made), the
    API never got to the task, a router run's plugin didn't load, or its router logged nothing for a subagent that
    ran."""
    return (("no_start" if row.get("claude_started") is False else None) or refusal(row)
            or ("plugin_not_loaded" if row.get("plugin_loaded") is False else None)
            or ("router_silent" if row.get("router_silent") else None))


# Set-aside reasons a pause can't fix; this many in a row stop the bench.
NO_RETRY = {"api_error": "the API refused the account", "no_start": "claude stopped before the session began",
            "plugin_not_loaded": "the plugin did not load",
            "router_silent": "the router logged no spawn though a subagent ran"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tasks", nargs="+", help="task ids (default: all)")
    parser.add_argument("--configs", nargs="+", help="configuration ids (default: all)")
    parser.add_argument("--runs", type=int, default=1, help="runs of each task under each configuration")
    parser.add_argument("--first-run", type=int, default=0, help="number of the first run, to go on from an earlier round")
    parser.add_argument("--seed", type=int, help="the run order's shuffle seed (default: a random one, recorded)")
    parser.add_argument("--batch", help="the batch name every row records; processes started together share one")
    parser.add_argument("--model", default="sonnet", help="the session model, the same for every configuration that sets none")
    parser.add_argument("--budget", type=float, default=0.5, help="--max-budget-usd for each run")
    parser.add_argument("--max-total", type=float, required=True, help="stop before total spend could pass this")
    parser.add_argument("--timeout", type=int, default=900, help="seconds before a run is killed")
    parser.add_argument("--claude", nargs="+", default=["claude"], help="the claude command as an argv prefix")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--keep", type=Path, help="copy each run's scratch folder and transcripts here")
    args = parser.parse_args(argv)
    if hasattr(signal, "SIGTERM"):  # else Python exits on it at once, leaving claude running and the paid run unwritten
        signal.signal(signal.SIGTERM, signal.default_int_handler)

    tasks = [t for t in load_tasks() if not args.tasks or t["id"] in args.tasks]
    configs = [c for c in load_configs() if not args.configs or c["id"] in args.configs]
    for flag, asked, found in (("--tasks", args.tasks, tasks), ("--configs", args.configs, configs)):
        known = {item["id"] for item in found}
        unknown = [i for i in asked or [] if i not in known]
        repeated = sorted({i for i in asked or [] if asked.count(i) > 1})
        if unknown:
            parser.error(f"{flag} matches no id: {', '.join(unknown)}")
        if repeated:
            parser.error(f"{flag} names an id more than once: {', '.join(repeated)}")
    # Each run has its own config folder, and a Keychain login is keyed to the folder, so it can't sign in.
    if not (any(os.environ.get(k) for k in AUTH) or any(k.startswith("CLAUDE_CODE_USE_") and v for k, v in os.environ.items())):
        parser.error("set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY (or a CLAUDE_CODE_USE_* provider switch)")
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(2 ** 31)
    batch = args.batch or f"{datetime.datetime.now(datetime.timezone.utc):%Y%m%dT%H%M%SZ}-{seed}"
    # The file the installer's symlink names now: an update during the round moves the link, never this.
    claude = [os.path.realpath(shutil.which(args.claude[0]) or args.claude[0]), *args.claude[1:]]
    version = subprocess.run([*claude, "--version"], capture_output=True, text=True).stdout.strip()
    frozen, digest = freeze_plugin()
    try:
        options = {"claude": claude, "model": args.model, "budget": args.budget, "timeout": args.timeout,
                   "plugin_dir": frozen, "plugin_hash": digest, "claude_version": version, "commit": _git_head(),
                   "dirty": _git_dirty(), "managed": managed_settings(), "seed": seed, "batch": batch,
                   "keep": str(args.keep) if args.keep else None,
                   # Earlier rows and kept runs hold graded answers: the results folder holds transcripts/ and limited/.
                   "hidden": answer_paths(str(args.out.parent), args.keep and str(args.keep)),
                   "transcripts": str(args.out.parent / "transcripts" / args.out.stem)}
        print(f"batch {batch}, seed {seed}", file=sys.stderr)
        limited = args.out.parent / "limited" / args.out.name
        # A run is paid for before its row is written, so every place a row or a kept run goes must take a write first.
        try:
            for target in (args.out, limited):
                target.parent.mkdir(parents=True, exist_ok=True)
                new = not target.exists()
                with target.open("a", encoding="utf-8"):
                    pass
                if new:  # an empty results file would read as a batch with no runs
                    target.unlink()
            if args.keep:
                args.keep.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            parser.error(f"can't write the results or --keep: {exc}")
        try:
            before, aside = (_rows(path) for path in (args.out, limited))
        except ValueError as exc:
            parser.error(f"can't read the rows already in the results: {exc}")
        # The same command after a stop goes on from there: a run on record is skipped, but not one the bench stopped
        # or one on an earlier revision of its task, which the page leaves out.
        revision = {t["id"]: t.get("revision", 1) for t in tasks}
        done = {(r.get("run"), r.get("task"), r.get("config")) for r in before
                if (r.get("grade") or {}).get("verdict") != "stopped"
                and r.get("task_revision") == revision.get(r.get("task"))}
        spent, errors = sum(r.get("total_cost_usd") or 0 for r in before + aside), 0
        todo = [(n, task, config) for n, task, config in schedule(tasks, configs, args.runs, args.first_run, seed)
                if (n, task["id"], config["id"]) not in done]
        skipped = args.runs * len(tasks) * len(configs) - len(todo)
        if skipped:
            print(f"skipped {skipped} runs already in the results", file=sys.stderr)
        for run_index, task, config in todo:
            begin = time.time()
            for attempt in range(LIMIT_RETRIES + 1):
                if not can_start(spent, args.budget, args.max_total):  # a retry spends too
                    print(f"stopped: ${spent:.4f} spent, cap ${args.max_total}", file=sys.stderr)
                    return 1
                try:
                    row = run_one(task, config, options)
                except BaseException as exc:
                    row = getattr(exc, "row", None)
                    if row is not None:  # the run was paid for, so it goes on record where the loop would put it
                        row["run"], reason = run_index, set_aside(row)
                        if reason:
                            row["limited_reason"] = reason
                        _write(limited if reason else args.out, row)
                    raise
                row["run"] = run_index
                spent += row["total_cost_usd"]
                reason = set_aside(row)
                if reason is None:
                    break
                row["limited_reason"] = reason
                # Kept apart, outside results/*.jsonl, so the spend is on record but never a failed task.
                _write(limited, row)
                if reason in NO_RETRY:
                    break
                if attempt == LIMIT_RETRIES:
                    print(f"stopped: still rate or usage limited after {LIMIT_RETRIES} pauses, "
                          f"${spent:.4f} spent", file=sys.stderr)
                    return 2
                print(f"{task['id']} {config['id']}: rate or usage limited, pausing", file=sys.stderr)
                time.sleep(LIMIT_PAUSE * 2 ** attempt)
            if row.get("limited_reason") in NO_RETRY:
                errors += 1
                print(f"{task['id']} {config['id']}: {NO_RETRY[row['limited_reason']]}, set aside: "
                      f"{(row['answer'] or row['stderr'])[:200]}", file=sys.stderr)
                if errors == API_ERROR_STOP:
                    print(f"stopped: {errors} runs in a row set aside; check the key, the credit balance, the account's "
                          "model access, whether the sandbox can start and the plugin's load errors", file=sys.stderr)
                    return 2
                continue
            errors = 0
            _write(args.out, row)
            print(f"{task['id']} {config['id']} run {run_index}: passed={row['passed']} "
                  f"${row['total_cost_usd']:.4f} {time.time() - begin:.0f}s, total ${spent:.4f}")
            if row["grade"]["verdict"] == "broken":
                print(f"{task['id']}: its pass check broke ({row['grade']['reason']}); analyze sets this run aside",
                      file=sys.stderr)
        return 0
    finally:
        shutil.rmtree(Path(frozen).parent, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
