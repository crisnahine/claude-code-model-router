"""Run bench tasks under each configuration through headless Claude Code and append one JSON line per run.

Example:
  python3 bench/run.py --tasks lookup-retry-limit bugfix-pagination --configs plugin defaults haiku-high \\
      --runs 1 --budget 0.5 --max-total 3 --out bench/results/pilot/pilot-2026-11-22.jsonl
"""
from __future__ import annotations

import argparse
import atexit
import datetime
import filecmp
import hashlib
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent
PLUGIN = ROOT / "plugins" / "model-router"
sys.path.insert(0, str(PLUGIN / "scripts"))
from modelrouter import config as mrconfig  # noqa: E402
from modelrouter.registry import Registry  # noqa: E402
from modelrouter.ledger import read as read_ledger  # noqa: E402
from modelrouter.store import load_state_file  # noqa: E402
ROW_SCHEMA = 3  # a row's schema says which harness wrote it; analyze reads a missing key as old only below this
CLASSES = ("lookup", "implementation", "bugfix", "cross-module", "security-review", "root-cause", "design", "research",
           "document", "analysis", "operations")

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
STATE_FILES = ("state.json", "learned.json")
PLUGIN_OPTIONS = ("routing_mode", "max_tier", "verify_gate")
# Every run's scratch folder sits here, so one sandbox rule hides the others' work from it. Short and unresolved:
# the run's CLAUDE_CODE_TMPDIR is its scratch folder, and one over about 30 bytes falls back to the shared temp folder.
RUNS = (Path("/tmp") / f"mrb-{os.getuid()}" if hasattr(os, "getuid")
        else Path(tempfile.gettempdir()) / "mrb")
VAR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")  # what sandbox.credentials.envVars accepts
# The shell's directories can name the checkout, whose tasks hold the reference answers.
DROPPED = ("PWD", "OLDPWD")
PROMPTS = {
    "delegate": ("Hand the task below to one subagent with the Agent tool and wait for its answer; don't do the work "
                 "yourself.{pick} When it returns, reply with its final answer.\n\nTask: {brief}"),
    "direct": "{brief}",
}


def load_tasks() -> list[dict]:
    tasks = []
    for folder in sorted(p for p in (BENCH / "tasks").iterdir() if (p / "task.json").is_file()):
        task = json.loads((folder / "task.json").read_text(encoding="utf-8"))
        tasks.append({**task, "id": folder.name, "dir": folder})
    return tasks


def load_configs() -> list[dict]:
    return json.loads((BENCH / "configs.json").read_text(encoding="utf-8"))


def pins(config: dict) -> list[str] | None:
    """The inline agents a configuration defines with --agents, each on a fixed model and effort; None without any."""
    args = list(config.get("args", []))
    return sorted(json.loads(args[args.index("--agents") + 1])) if "--agents" in args else None


def prompt(task: dict, arm: str, config: dict | None = None) -> str:
    """The prompt for an arm: `delegate` hands the task to one subagent, naming a configuration's pinned agents,
    and `direct` is the brief as a person types it."""
    names = pins(config or {})
    pick = f" Use one of these agents: {', '.join(names[:-1])} or {names[-1]}." if names else ""
    return PROMPTS[arm].format(brief=task["brief"], pick=pick)


def _plugin_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")


def plugin_hash(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in _plugin_files(folder):
        digest.update(path.relative_to(folder).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:16]


def freeze_plugin(source: Path = PLUGIN) -> tuple[str, str]:
    """A copy of the plugin made once at the start of a batch, so an edit or a checkout during it can't reach later
    runs, and the hash of what was copied."""
    target = Path(tempfile.mkdtemp(prefix="mrbench-plugin-")) / "model-router"
    atexit.register(shutil.rmtree, target.parent, ignore_errors=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return str(target), plugin_hash(target)


def managed_settings() -> list[str]:
    """The managed settings sources on this machine; a run loads them whatever its own settings say."""
    return [p for p in MANAGED_SETTINGS if os.path.exists(p)]


_EXPORTED: dict[str, tuple[str, str]] = {}  # plugin_ref: (folder, hash)


def plugin_dir_for(config: dict, options: dict) -> str:
    """The plugin a run loads: the batch's frozen copy, or the one at a configuration's plugin_ref, exported once
    from git so a branch under test can't change while the bench runs."""
    ref = config.get("plugin_ref")
    if not ref:
        return options["plugin_dir"]
    if ref not in _EXPORTED:
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", ref, "plugins/model-router"],
                                 capture_output=True, check=True).stdout
        target = Path(tempfile.mkdtemp(prefix="mrbench-plugin-"))
        atexit.register(shutil.rmtree, target, ignore_errors=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(target, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
        folder = target / "plugins" / "model-router"
        _EXPORTED[ref] = (str(folder), plugin_hash(folder))
    return _EXPORTED[ref][0]


def loaded_hash(config: dict, options: dict) -> str | None:
    """The hash of the plugin a run loaded; None for a run with no plugin."""
    if not config.get("plugin"):
        return None
    ref = config.get("plugin_ref")
    return (_EXPORTED[ref][1] if ref in _EXPORTED else None) if ref else options.get("plugin_hash")


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


def settings(config: dict, env: dict, hidden: list[str] | None = None, own: str | None = None) -> dict:
    """The one --settings JSON a run gets: credentials, the answers and other runs' scratch folders kept from
    sandboxed commands, the answers kept from the file tools, and a router variant's plugin options."""
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
    out: dict = {"sandbox": sandbox, "permissions": {"deny": rules}}
    if config.get("plugin_options"):
        # A --plugin-dir plugin's id is <name>@inline; project settings can't set plugin options.
        out["pluginConfigs"] = {f"{_plugin_name()}@inline": {"options": config["plugin_options"]}}
    return out


def command(prompt: str, config: dict, options: dict, base_env: dict) -> tuple[list[str], dict]:
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
           "--model", config.get("model", options["model"]),
           "--max-budget-usd", str(options["budget"]), "--setting-sources", "project", "--strict-mcp-config",
           "--permission-mode", "dontAsk",
           "--settings", json.dumps(settings(config, env, options.get("hidden"), options.get("scratch")))]
    if config.get("plugin"):
        cmd += ["--plugin-dir", plugin_dir_for(config, options)]
    return cmd + list(config.get("args", [])) + ["--allowedTools", *TOOLS], env


LIMIT_RETRIES = 5
LIMIT_PAUSE = 60  # seconds before the first retry; each later pause is twice as long
API_ERROR_STOP = 3  # API errors before any spend, in a row, that stop the bench: the key or the account is wrong
# The messages Claude Code prints when the API refuses for a limit (https://code.claude.com/docs/en/errors.md).
_LIMIT_MESSAGE = (r"API Error: (?:Repeated )?(?:429|529)\b|API Error: Repeated 529 Overloaded|Request rejected \(429\)|"
                  r"Server is temporarily limiting requests|You've hit your (?:session|weekly|Opus|Sonnet) limit")


# What Claude Code printed when the network went away (2026-11-22): an API Error naming a network code, or a
# dropped connection.
_NETWORK_MESSAGE = (r"API Error: [^\n]*\((?:ENOTFOUND|ENETUNREACH|ECONNRESET|ECONNREFUSED|ETIMEDOUT|EAI_AGAIN)\)|"
                    r"API Error: (?:Can't reach the API server|No internet route)|Connection lost")


def refusal(row: dict) -> str | None:
    """Why the API never got to the task: "limit" or "network", "api_error" for any other error before a cent was
    spent (a bad key, no credit, a model the account can't use), else None. Only an errored run counts, and a limit
    or network only on the API's status or Claude Code's own message, at the start of the answer (which is then that
    message) or in stderr. Task text that talks about limits or network codes is never read as one."""
    if not (row.get("is_error") or row.get("exit_code")):
        return None
    if row.get("api_error_status") in (429, 529):
        return "limit"
    answer, stderr = str(row.get("answer") or ""), str(row.get("stderr") or "")
    for reason, message in (("limit", _LIMIT_MESSAGE), ("network", _NETWORK_MESSAGE)):
        if re.match(rf"\s*(?:{message})", answer) or re.search(message, stderr):
            return reason
    # No spend means no model reply, so no tool ran. error_during_execution's zero cost may have been zeroed.
    if row.get("total_cost_usd") == 0 and row.get("subtype") != "error_during_execution":
        return "api_error"
    return None


def rate_limited(row: dict) -> bool:
    """A run refused for a rate or usage limit or a lost network, which waiting can lift: no measurement, not a
    failed task."""
    return refusal(row) in ("limit", "network")


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


class Grade(NamedTuple):
    verdict: str  # "pass", "fail" or "broken"
    reason: str  # the last line the check printed, or for a broken check the last line of its error
    check_hash: str  # the check's code, so a row says which check graded it

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


def grade(check: Path, work: Path, answer: Path, timeout: int = 120) -> Grade:
    """The task's check run on a throwaway copy of a work dir, from a scratch folder, and an answer file, so code a
    run left behind can't write into the work dir, the checkout or the results. -I keeps the check's folder and the
    work dir off sys.path and ignores PYTHON* variables, so that code can't shadow what the check imports."""
    digest = check_hash(check)
    scratch = Path(tempfile.mkdtemp(prefix="mrbench-grade-"))
    try:
        copy = scratch / "work"
        shutil.copytree(work, copy, symlinks=True)
        res = subprocess.run([sys.executable, "-I", "-c", _LAUNCH, str(check), str(copy), str(answer)],
                             capture_output=True, cwd=scratch, env=grading_env(dict(os.environ)), timeout=timeout)
    except subprocess.TimeoutExpired:  # code the run left that hangs the check fails the task, not the bench
        return Grade("fail", f"the check timed out after {timeout} s", digest)
    except OSError as exc:
        return Grade("broken", f"the work dir could not be copied for the check: {exc}", digest)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if res.returncode == 0:
        return Grade("pass", _last_line(res.stdout), digest)
    if res.returncode == 2:
        return Grade("broken", _last_line(res.stderr) or _last_line(res.stdout), digest)
    return Grade("fail", _last_line(res.stdout) or f"the check exited {res.returncode}", digest)


def can_start(spent: float, budget: float, max_total: float) -> bool:
    # A run can pass --max-budget-usd by one response plus what running subagents spend before they stop, so
    # this leaves room for twice its cap. That is a margin, not a bound.
    return spent + 2 * budget <= max_total


def plugin_data_dir(env: dict) -> Path:
    """Where Claude Code keeps the --plugin-dir router's data (health, learned raises, the ledger):
    <config>/plugins/data/<id>, the id with anything but letters, digits, _ and - turned into -."""
    return Path(env["CLAUDE_CONFIG_DIR"]) / "plugins" / "data" / re.sub(r"[^A-Za-z0-9_-]", "-", f"{_plugin_name()}@inline")


def session_dir(env: dict) -> Path:
    """The run's transcripts: <config>/projects/<CLAUDE_CODE_PROJECT_DIR_NAME> (https://code.claude.com/docs/en/env-vars)."""
    return Path(env["CLAUDE_CONFIG_DIR"]) / "projects" / env["CLAUDE_CODE_PROJECT_DIR_NAME"]


def read_stream(stdout: str) -> tuple[dict, dict, dict[str, int], int]:
    """The system/init event, the last result line, the subagents started by type, and how many Agent calls set
    their own model or effort, which outranks a definition's (https://code.claude.com/docs/en/sub-agents), from
    stream-json output. A line that isn't a JSON object is skipped, as is a tool_use id seen before."""
    init, result, started, seen, overrides = {}, {}, {}, set(), 0
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
            for block in (event.get("message") or {}).get("content") or []:
                if (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in ("Agent", "Task")
                        and block.get("id") not in seen):
                    seen.add(block.get("id"))
                    call = block.get("input") or {}
                    kind = call.get("subagent_type") or "general-purpose"
                    started[kind] = started.get(kind, 0) + 1
                    overrides += bool(call.get("model") or call.get("effort"))
    return init, result, started, overrides


def plugin_loaded(init: dict) -> bool:
    """Whether system/init lists the router among the loaded plugins and reports no plugin errors."""
    name = _plugin_name()
    listed = any(isinstance(p, dict) and str(p.get("name", "")).split("@")[0] == name for p in init.get("plugins") or [])
    return listed and not init.get("plugin_errors")


def transcript_cost(folder: Path, registry_config: Registry) -> float | None:
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
        cost = registry_config.cost(message.get("model") or "", tokens)
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


def plugin_options(env: dict) -> dict:
    """The options the router saved to its state.json at SessionStart, as it resolved them; {} with no state."""
    state = load_state_file(plugin_data_dir(env) / "state.json")
    return {k: state[k] for k in PLUGIN_OPTIONS if k in state}


def options_applied(config: dict, seen: dict | None) -> bool | None:
    """Whether every plugin option a configuration sets reached the plugin; None when it sets none."""
    asked = config.get("plugin_options")
    if not asked:
        return None
    text = lambda v: str(v).lower() if isinstance(v, bool) else str(v)  # noqa: E731  the state keeps strings
    return all((seen or {}).get(k) == text(v) for k, v in asked.items())


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


def spend_by_agent(ledger: list[dict], types: dict[str, str]) -> dict[str, float]:
    """A router run's spend by agent type: usage rows carry the subagent's agent_id, or none for the main loop."""
    spend: dict = {}
    for row in ledger:
        if row.get("event") != "usage" or not isinstance(row.get("cost_usd"), (int, float)):
            continue
        who = "main" if not row.get("agent_id") else types.get(row["agent_id"], "unknown")
        spend[who] = round(spend.get(who, 0.0) + row["cost_usd"], 6)
    return spend


ROUTED_KEYS = ("agent", "level", "model", "effort", "escalated_from")


def spawns(ledger: list[dict]) -> list[dict]:
    """A router run's spawn decisions, in order. A key the router didn't log stays out, so an older router's
    missing escalated_from reads as unknown rather than as no escalation."""
    return [{k: row[k] for k in ROUTED_KEYS if k in row} for row in ledger if row.get("event") == "spawn"]


def state_hashes(folder: Path) -> dict:
    return {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()[:16] if (folder / name).is_file() else None
            for name in STATE_FILES}


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


def run_one(task: dict, config: dict, options: dict, registry_config: Registry) -> dict:
    # No task or configuration name: a brief that quotes its working directory would hand the router signal words.
    scratch = Path(tempfile.mkdtemp(dir=runs_folder()))
    work = scratch / "work"
    shutil.copytree(task["dir"] / "repo", work)
    if config.get("router_config"):  # a router variant, as the project's own model-router.json
        (work / ".claude").mkdir(exist_ok=True)
        (work / ".claude" / "model-router.json").write_text(json.dumps(config["router_config"]), encoding="utf-8")
    arm = options.get("arm", "delegate")
    cmd, env = command(prompt(task, arm, config), config,
                       {**options, "config_dir": str(scratch / "config"), "scratch": str(scratch)}, dict(os.environ))
    started = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    plugin_state = state_hashes(plugin_data_dir(env)) if config.get("plugin") else None
    clock = time.monotonic()
    stdout, stderr, code, wall_ms, result, init, started_agents, overrides = "", "interrupted", None, None, {}, {}, {}, 0
    ledger, types, final, graded, kept, stopped, priced, transcripts = None, {}, None, None, None, None, None, None
    seen_options = None
    # Once claude starts the run costs money: whatever stops it or the steps after it, its row is still built and its
    # scratch removed. With no result the row is priced from its transcripts, or charged the whole budget.
    try:
        try:
            proc = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, timeout=options["timeout"])
            stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, code = _text(exc.stdout), "timed out", None
        # The run's wall time: Claude Code's own duration_ms can fall short of it.
        wall_ms = round((time.monotonic() - clock) * 1000)
        init, result, started_agents, overrides = read_stream(stdout)
        answer = result.get("result") or ""
        (scratch / "answer.txt").write_text(answer, encoding="utf-8")
        if not isinstance(result.get("total_cost_usd"), (int, float)) or result.get("subtype") == "error_during_execution":
            priced = transcript_cost(session_dir(env), registry_config)
        # Read before grading: the check also runs the code the run left, which could rewrite these files.
        ledger = ledger_rows(env) if config.get("plugin") else None
        types = agent_types(env) if config.get("plugin") else {}
        seen_options = plugin_options(env) if config.get("plugin") else None
        final = final_answer(task["dir"] / "repo", work, answer)
        graded = grade(task["dir"] / "check.py", work, scratch / "answer.txt")
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
        graded = Grade("broken", f"the bench stopped before the check finished: {stopped!r}",
                       check_hash(task["dir"] / "check.py"))
    row = record(task, config, options, registry_config, result,
                 {"started_at": started, "wall_ms": wall_ms, "exit_code": code, "stderr": stderr,
                  "grade": graded._asdict(), "final_answer": final,
                  "plugin_state": plugin_state, "ledger": ledger, "agent_types": types,
                  "kept": kept and str(kept), "init": init, "agents_started": started_agents,
                  "agent_overrides": overrides, "plugin_options": seen_options,
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


def record(task: dict, config: dict, options: dict, registry_config: Registry, result: dict, facts: dict) -> dict:
    """The row for one run, from Claude Code's result line and what the runner saw around the process."""
    models = {name: {k: usage.get(k) for k in ("inputTokens", "outputTokens", "cacheReadInputTokens",
                                                "cacheCreationInputTokens", "costUSD", "costBasis")}
              for name, usage in (result.get("modelUsage") or {}).items()}
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
    # except when the API refused the run or it never started: nothing was spent.
    known = unstarted or isinstance(cost, (int, float)) and not (
        result.get("subtype") == "error_during_execution" and cost == 0 and not refused)
    priced = facts.get("transcript_cost")
    source = "result" if known else "transcripts" if priced is not None else "budget"
    arm, pinned = options.get("arm", "delegate"), pins(config)
    started, overrides = facts.get("agents_started") or {}, facts.get("agent_overrides") or 0
    # Told to use its pinned agents, a run that used none, or another, or set a model on the call, didn't run pinned.
    off_pin = None if pinned is None or arm != "delegate" else bool(not started or set(started) - set(pinned) or overrides)
    return {
        "schema": ROW_SCHEMA, "task": task["id"], "task_revision": task.get("revision", 1), "class": task["class"],
        "config": config["id"], "arm": arm,
        "batch": options.get("batch"), "order_seed": options.get("seed"),
        "started_at": facts["started_at"], "claude_version": options["claude_version"],
        "main_model": config.get("model", options["model"]),
        "budget_usd": options["budget"], "bench_commit": options["commit"], "bench_dirty": options.get("dirty"),
        "plugin_hash": loaded_hash(config, options), "managed_settings": options.get("managed"),
        "plugin_state": facts["plugin_state"],
        "claude_started": not unstarted, "startup_failure_reason": result.get("startup_failure_reason"),
        "plugin_loaded": plugin_loaded(facts.get("init") or {}) if config.get("plugin") else None,
        "plugin_options": facts.get("plugin_options"),
        "options_applied": options_applied(config, facts.get("plugin_options")),
        "agent_costs": None if ledger is None else spend_by_agent(ledger, facts["agent_types"]),
        "routed": None if ledger is None else spawns(ledger),
        "gate_events": sum(r.get("event") == "gate" for r in ledger or []),
        "plugin_ref": config.get("plugin_ref"), "plugin_data_own": True, "sessions_saved": True,
        "exit_code": code, "subtype": result.get("subtype"), "is_error": result.get("is_error"),
        "api_error_status": result.get("api_error_status"), "terminal_reason": result.get("terminal_reason"),
        "errors": result.get("errors"),
        # Without a cost from the result, the transcripts' list price, else the whole budget so totals never understate.
        "total_cost_usd": cost if known else priced if priced is not None else options["budget"],
        "cost_known": known, "cost_source": source,
        "duration_ms": result.get("duration_ms"), "duration_api_ms": result.get("duration_api_ms"),
        "wall_ms": facts["wall_ms"], "num_turns": result.get("num_turns"),
        "models": models, "prices": {name: registry_config.price(name) for name in models},
        "agents_started": started, "agent_overrides": overrides, "off_pin": off_pin,
        "permission_denials": [_denial(d) for d in result.get("permission_denials") or [] if isinstance(d, dict)],
        "passed": facts["grade"]["verdict"] == "pass", "grade": facts["grade"],
        "answer": (result.get("result") or "")[:2000], "final_answer": facts["final_answer"],
        **({"kept": facts["kept"]} if facts["kept"] else {}),
        **({"transcripts": facts["transcripts"]} if facts.get("transcripts") else {}),
        "stderr": stderr,
    }


def schedule(tasks: list[dict], configs: list[dict], runs: int, first_run: int = 0, seed: int = 0,
             arms: tuple[str, ...] = ("delegate",)) -> list[tuple[int, dict, dict, str]]:
    """Each run to make as (run number, task, configuration, arm), in an order shuffled by `seed`, so no
    configuration always goes first on a cold prompt cache or at one time of day."""
    out = [(run_index, task, config, arm) for run_index in range(first_run, first_run + runs)
           for task in tasks for config in configs for arm in arms]
    random.Random(seed).shuffle(out)
    return out


def _write(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def set_aside(row: dict) -> str | None:
    """Why a row stays out of the results: claude stopped before the session began (so no API call was made), the
    API never got to the task, a router run's plugin didn't load or didn't get its options, or a pinned run didn't
    use its pinned agents."""
    return (("no_start" if row.get("claude_started") is False else None) or refusal(row)
            or ("plugin_not_loaded" if row.get("plugin_loaded") is False else None)
            or ("options_not_applied" if row.get("options_applied") is False else None)
            or ("off_pin" if row.get("off_pin") else None))


# Set-aside reasons a pause can't fix; this many in a row stop the bench.
NO_RETRY = {"api_error": "API error before any spend", "no_start": "claude stopped before the session began",
            "plugin_not_loaded": "the plugin did not load",
            "options_not_applied": "the plugin's state shows other options than the configuration set",
            "off_pin": "the run didn't use its pinned agents"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tasks", nargs="*", help="task ids (default: all)")
    parser.add_argument("--configs", nargs="*", help="configuration ids (default: all)")
    parser.add_argument("--runs", type=int, default=1, help="runs of each task under each configuration")
    parser.add_argument("--first-run", type=int, default=0, help="number of the first run, to go on from an earlier round")
    parser.add_argument("--arms", nargs="+", choices=sorted(PROMPTS), default=["delegate"],
                        help="delegate: the prompt hands the task to one subagent; direct: the brief alone")
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

    tasks = [t for t in load_tasks() if not args.tasks or t["id"] in args.tasks]
    configs = [c for c in load_configs() if not args.configs or c["id"] in args.configs]
    for flag, asked, found in (("--tasks", args.tasks, tasks), ("--configs", args.configs, configs)):
        if asked and not found:
            parser.error(f"{flag} matches no id: {', '.join(asked)}")
    # Each run has its own config folder, and a Keychain login is keyed to the folder, so it can't sign in.
    if not (any(os.environ.get(k) for k in AUTH) or any(k.startswith("CLAUDE_CODE_USE_") and v for k, v in os.environ.items())):
        parser.error("set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY (or a CLAUDE_CODE_USE_* provider switch)")
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(2 ** 31)
    batch = args.batch or f"{datetime.datetime.now(datetime.timezone.utc):%Y%m%dT%H%M%SZ}-{seed}"
    version = subprocess.run([*args.claude, "--version"], capture_output=True, text=True).stdout.strip()
    registry_config = mrconfig.shipped()
    frozen, digest = freeze_plugin()
    options = {"claude": args.claude, "model": args.model, "budget": args.budget, "timeout": args.timeout,
               "plugin_dir": frozen, "plugin_hash": digest, "claude_version": version, "commit": _git_head(),
               "dirty": _git_dirty(), "managed": managed_settings(), "seed": seed, "batch": batch,
               "keep": str(args.keep) if args.keep else None,
               # Earlier rows and kept runs hold graded answers: the results folder holds transcripts/ and limited/.
               "hidden": answer_paths(str(args.out.parent), args.keep and str(args.keep)),
               "transcripts": str(args.out.parent / "transcripts" / args.out.stem)}
    print(f"batch {batch}, seed {seed}", file=sys.stderr)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    limited = args.out.parent / "limited" / args.out.name
    spent, errors = 0.0, 0
    for run_index, task, config, arm in schedule(tasks, configs, args.runs, args.first_run, seed, tuple(args.arms)):
        begin = time.time()
        for attempt in range(LIMIT_RETRIES + 1):
            if not can_start(spent, args.budget, args.max_total):  # a retry spends too
                print(f"stopped: ${spent:.4f} spent, cap ${args.max_total}", file=sys.stderr)
                return 1
            try:
                row = run_one(task, config, {**options, "arm": arm}, registry_config)
            except BaseException as exc:
                row = getattr(exc, "row", None)
                if row is not None:  # the run was paid for, so it goes on record where the loop would put it
                    row["run"], reason = run_index, refusal(row)
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
                      "model access, whether the sandbox can start, the plugin's load errors and the agents the "
                      "pinned runs used", file=sys.stderr)
                return 2
            continue
        errors = 0
        _write(args.out, row)
        print(f"{task['id']} {config['id']} {arm} run {run_index}: passed={row['passed']} "
              f"${row['total_cost_usd']:.4f} {time.time() - begin:.0f}s, total ${spent:.4f}")
        if row["grade"]["verdict"] == "broken":
            print(f"{task['id']}: its pass check broke ({row['grade']['reason']}); analyze sets this run aside",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
