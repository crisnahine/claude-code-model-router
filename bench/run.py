"""Run bench tasks under each configuration through headless Claude Code and append one JSON line per run.

Example (the pilot):
  python3 bench/run.py --tasks lookup-retry-limit bugfix-pagination --configs plugin defaults haiku \\
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
ROW_SCHEMA = 2  # a row's schema says which harness wrote it; analyze reads a missing key as old only below this
CLASSES = ("lookup", "implementation", "bugfix", "cross-module", "security-review", "root-cause", "design", "research")

# Every run starts from the same environment: nothing a parent Claude Code session, its plugins or the shell's
# subagent and effort settings put there, except the provider switches and token auth. A configuration adds its
# own on top.
CLEARED = ("CLAUDE_CODE_", "CLAUDECODE", "CLAUDE_PLUGIN_", "CLAUDE_PROJECT_DIR")
KEPT = ("CLAUDE_CODE_USE_",)
KEPT_EXACT = ("CLAUDE_CODE_OAUTH_TOKEN",)
ISOLATION = {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
# tail, head and echo let a subagent pipe its test output; every part of a compound command must be allowed.
TOOLS = ("Read", "Edit", "Write", "Glob", "Grep", "Bash(python3 *)", "Bash(python *)", "Bash(tail *)",
         "Bash(head *)", "Bash(echo *)")
STATE_FILES = ("state.json",)
PROMPT = ("Hand the task below to one subagent with the Agent tool and wait for its answer; don't do the work "
          "yourself. When it returns, reply with its final answer.\n\nTask: {brief}")


def load_tasks() -> list[dict]:
    tasks = []
    for folder in sorted(p for p in (BENCH / "tasks").iterdir() if (p / "task.json").is_file()):
        task = json.loads((folder / "task.json").read_text(encoding="utf-8"))
        tasks.append({**task, "id": folder.name, "dir": folder})
    return tasks


def load_configs() -> list[dict]:
    return json.loads((BENCH / "configs.json").read_text(encoding="utf-8"))


_EXPORTED: dict = {}


def plugin_dir_for(config: dict, options: dict) -> str:
    """The plugin a run loads: the checkout's, or the one at a configuration's plugin_ref, exported once from git
    so a branch under test can't change while the bench runs."""
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
        _EXPORTED[ref] = str(target / "plugins" / "model-router")
    return _EXPORTED[ref]


def command(prompt: str, config: dict, options: dict, base_env: dict) -> tuple[list[str], dict]:
    env = {k: v for k, v in base_env.items() if k in KEPT_EXACT or k.startswith(KEPT) or not k.startswith(CLEARED)}
    env.update(ISOLATION)
    if options.get("plugins_root"):
        # The run's own plugins root, so the router's data never lands in the folder other sessions share.
        env["CLAUDE_CODE_PLUGIN_CACHE_DIR"] = options["plugins_root"]
    env.update(config.get("env", {}))
    cmd = [*options["claude"], "-p", prompt, "--output-format", "json",
           "--model", config.get("model", options["model"]),
           "--max-budget-usd", str(options["budget"]), "--setting-sources", "project", "--strict-mcp-config",
           "--permission-mode", "dontAsk"]
    if config.get("plugin"):
        cmd += ["--plugin-dir", plugin_dir_for(config, options)]
    if config.get("plugin_options"):
        # A --plugin-dir plugin's id is <name>@inline; project settings can't set plugin options.
        name = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
        cmd += ["--settings", json.dumps({"pluginConfigs": {f"{name}@inline": {"options": config["plugin_options"]}}})]
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
    """The task's check run on a work dir and an answer file. -I keeps the check's folder and the work dir off
    sys.path and ignores PYTHON* variables, so code a run left behind can't shadow what the check imports."""
    digest = check_hash(check)
    try:
        res = subprocess.run([sys.executable, "-I", "-c", _LAUNCH, str(check), str(work), str(answer)],
                             capture_output=True, env=grading_env(dict(os.environ)), timeout=timeout)
    except subprocess.TimeoutExpired:  # code the run left that hangs the check fails the task, not the bench
        return Grade("fail", f"the check timed out after {timeout} s", digest)
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
    """Where Claude Code keeps the --plugin-dir router's data (health, the ledger):
    <plugins root>/data/<id>, the id with anything but letters, digits, _ and - turned into -."""
    name = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    home = Path(env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    root = Path(env.get("CLAUDE_CODE_PLUGIN_CACHE_DIR") or home / "plugins")
    return root / "data" / re.sub(r"[^A-Za-z0-9_-]", "-", f"{name}@inline")


def session_dir(work: Path, env: dict) -> Path:
    """The folder Claude Code keeps a session's transcripts in for this cwd: projects/<the resolved path with
    anything but letters and digits turned into ->, as Claude Code 2.1.330 names it."""
    home = Path(env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    return home / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(work.resolve()))


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


def agent_types(work: Path, env: dict) -> dict[str, str]:
    """Each subagent's type by id, from the saved session's subagents/agent-<id>.meta.json (as Claude Code 2.1.330
    writes it)."""
    types = {}
    for meta in session_dir(work, env).glob("*/subagents/agent-*.meta.json"):
        try:
            types[meta.name[len("agent-"):-len(".meta.json")]] = json.loads(meta.read_text(encoding="utf-8"))["agentType"]
        except (OSError, ValueError, KeyError):
            continue
    return types


def spend_by_agent(ledger: list[dict], types: dict[str, str], total: object) -> dict[str, float]:
    """A router run's spend by agent type, from Claude Code's estimate per subagent: each one's newest usage row
    holds all of its turns. `main` is the rest of the run's total: the main loop, and agents with no estimate."""
    last: dict[str, float] = {}
    for row in ledger:
        if row.get("event") == "usage" and row.get("agent_id") and isinstance(row.get("cost_usd"), (int, float)):
            last[row["agent_id"]] = row["cost_usd"]
    spend: dict = {}
    for agent_id, usd in last.items():
        who = types.get(agent_id, "unknown")
        spend[who] = round(spend.get(who, 0.0) + usd, 6)
    if isinstance(total, (int, float)):
        spend["main"] = round(max(0.0, total - sum(last.values())), 6)
    return spend


ROUTED_KEYS = ("agent", "level", "model", "effort", "escalated_from")


def spawns(ledger: list[dict]) -> list[dict]:
    """A router run's spawn decisions, in order. A key the router didn't log stays out, so an older router's
    missing escalated_from reads as unknown rather than as no escalation."""
    return [{k: row[k] for k in ROUTED_KEYS if k in row} for row in ledger if row.get("event") == "spawn"]


def _forget_session(folder: Path) -> bool:
    # Only a folder named after one of the bench's own scratch copies is ever removed.
    if "-mrbench-" not in folder.name or not folder.is_dir():
        return False
    shutil.rmtree(folder)
    return True


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


def run_one(task: dict, config: dict, options: dict, registry_config: Registry) -> dict:
    # No task or configuration name: a brief that quotes its working directory would tell the model what it is graded on.
    scratch = Path(tempfile.mkdtemp(prefix="mrbench-"))
    work = scratch / "work"
    shutil.copytree(task["dir"] / "repo", work)
    if config.get("router_config"):  # a router variant, as the project's own model-router.json
        (work / ".claude").mkdir(exist_ok=True)
        (work / ".claude" / "model-router.json").write_text(json.dumps(config["router_config"]), encoding="utf-8")
    cmd, env = command(PROMPT.format(brief=task["brief"]), config,
                       {**options, "plugins_root": str(scratch / "plugins")}, dict(os.environ))
    started = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    plugin_state = state_hashes(plugin_data_dir(env)) if config.get("plugin") else None
    clock = time.monotonic()
    stdout, stderr, code, wall_ms, result = "", "interrupted", None, None, {}
    ledger, types, final, graded, kept, stopped = None, {}, None, None, None, None
    # Once claude starts the run costs money: whatever stops it or the steps after it, its row is still built and its
    # scratch removed. With no result yet the cost is unknown, so the row is charged the whole budget.
    try:
        try:
            proc = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, timeout=options["timeout"])
            stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, code = _text(exc.stdout), "timed out", None
        # The run's wall time: Claude Code's own duration_ms can fall short of it.
        wall_ms = round((time.monotonic() - clock) * 1000)
        try:
            result = json.loads(stdout)
        except ValueError:
            result = {}
        answer = result.get("result") or ""
        (scratch / "answer.txt").write_text(answer, encoding="utf-8")
        # Read before grading: the check also runs the code the run left, which could rewrite these files.
        ledger = ledger_rows(env) if config.get("plugin") else None
        types = agent_types(work, env) if config.get("plugin") else {}
        final = final_answer(task["dir"] / "repo", work, answer)
        graded = grade(task["dir"] / "check.py", work, scratch / "answer.txt")
        if options.get("keep"):
            # The work the run left, its answer, the router's own data and ledger, and the session's transcripts.
            target = Path(options["keep"]) / scratch.name
            shutil.copytree(scratch, target, symlinks=True)
            if session_dir(work, env).is_dir():
                shutil.copytree(session_dir(work, env), target / "session", symlinks=True)
            kept = target
    except BaseException as exc:
        stopped = exc
    if wall_ms is None:
        wall_ms = round((time.monotonic() - clock) * 1000)
    session_removed = _forget_session(session_dir(work, env))
    shutil.rmtree(scratch, ignore_errors=True)
    if graded is None:
        graded = Grade("broken", f"the bench stopped before the check finished: {stopped!r}",
                       check_hash(task["dir"] / "check.py"))
    row = record(task, config, options, registry_config, result,
                 {"started_at": started, "wall_ms": wall_ms, "exit_code": code, "stderr": stderr,
                  "grade": graded._asdict(), "final_answer": final,
                  "plugin_state": plugin_state, "ledger": ledger, "agent_types": types,
                  "session_dir_removed": session_removed, "kept": kept and str(kept)})
    if stopped is not None:
        stopped.row = row  # main writes it before the bench stops
        raise stopped
    return row


def record(task: dict, config: dict, options: dict, registry_config: Registry, result: dict, facts: dict) -> dict:
    """The row for one run, from Claude Code's parsed result and what the runner saw around the process."""
    models = {name: {k: usage.get(k) for k in ("inputTokens", "outputTokens", "cacheReadInputTokens",
                                                "cacheCreationInputTokens", "costUSD", "costBasis")}
              for name, usage in (result.get("modelUsage") or {}).items()}
    agents = result.get("subagent_stats") or {}
    cost = result.get("total_cost_usd")
    ledger, code = facts["ledger"], facts["exit_code"]
    stderr = facts["stderr"][-300:] if code else ""
    refused = refusal({"is_error": result.get("is_error"), "exit_code": code, "stderr": stderr,
                       "api_error_status": result.get("api_error_status"), "answer": result.get("result"),
                       "subtype": result.get("subtype"), "total_cost_usd": cost})
    # A cost of 0 on error_during_execution "may be zeroed" (https://code.claude.com/docs/en/agent-sdk/cost-tracking.md),
    # except when the API refused the run: it never started, so nothing was spent.
    known = isinstance(cost, (int, float)) and not (
        result.get("subtype") == "error_during_execution" and cost == 0 and not refused)
    return {
        "schema": ROW_SCHEMA, "task": task["id"], "task_revision": task.get("revision", 1), "class": task["class"],
        "config": config["id"],
        "started_at": facts["started_at"], "claude_version": options["claude_version"],
        "main_model": config.get("model", options["model"]),
        "budget_usd": options["budget"], "bench_commit": options["commit"], "bench_dirty": options.get("dirty"),
        "plugin_state": facts["plugin_state"],
        "agent_costs": None if ledger is None else spend_by_agent(ledger, facts["agent_types"], cost if known else None),
        "routed": None if ledger is None else spawns(ledger),
        "plugin_ref": config.get("plugin_ref"), "plugin_data_own": True, "sessions_saved": True,
        "session_dir_removed": facts["session_dir_removed"],
        "exit_code": code, "subtype": result.get("subtype"), "is_error": result.get("is_error"),
        "api_error_status": result.get("api_error_status"),
        # Without a known cost, count the whole budget so totals never understate spend.
        "total_cost_usd": cost if known else options["budget"],
        "cost_known": known,
        "duration_ms": result.get("duration_ms"), "duration_api_ms": result.get("duration_api_ms"),
        "wall_ms": facts["wall_ms"], "num_turns": result.get("num_turns"),
        "models": models, "prices": {name: registry_config.price(name) for name in models},
        "subagents": {k: agents.get(k) for k in ("spawned", "completed", "failed", "by_type")},
        "permission_denials": [d.get("tool_name") for d in result.get("permission_denials") or []],
        "passed": facts["grade"]["verdict"] == "pass", "grade": facts["grade"],
        "answer": (result.get("result") or "")[:2000], "final_answer": facts["final_answer"],
        **({"kept": facts["kept"]} if facts["kept"] else {}),
        "stderr": stderr,
    }


def schedule(tasks: list[dict], configs: list[dict], runs: int, first_run: int = 0) -> list[tuple[int, dict, dict]]:
    """Each run to make as (run number, task, configuration). The configuration order rotates with the run number
    so none always goes first on a cold prompt cache."""
    out = []
    for run_index in range(first_run, first_run + runs):
        shift = run_index % len(configs)
        out += [(run_index, task, config) for task in tasks for config in configs[shift:] + configs[:shift]]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tasks", nargs="*", help="task ids (default: all)")
    parser.add_argument("--configs", nargs="*", help="configuration ids (default: all)")
    parser.add_argument("--runs", type=int, default=1, help="runs of each task under each configuration")
    parser.add_argument("--first-run", type=int, default=0, help="number of the first run, to go on from an earlier round")
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
    version = subprocess.run([*args.claude, "--version"], capture_output=True, text=True).stdout.strip()
    registry_config = mrconfig.shipped()
    options = {"claude": args.claude, "model": args.model, "budget": args.budget, "timeout": args.timeout,
               "plugin_dir": str(PLUGIN), "claude_version": version, "commit": _git_head(), "dirty": _git_dirty(),
               "keep": str(args.keep) if args.keep else None}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    spent, errors = 0.0, 0
    for run_index, task, config in schedule(tasks, configs, args.runs, args.first_run):
        begin = time.time()
        for attempt in range(LIMIT_RETRIES + 1):
            if not can_start(spent, args.budget, args.max_total):  # a retry spends too
                print(f"stopped: ${spent:.4f} spent, cap ${args.max_total}", file=sys.stderr)
                return 1
            try:
                row = run_one(task, config, options, registry_config)
            except BaseException as exc:
                row = getattr(exc, "row", None)
                if row is not None:  # the run was paid for, so it goes on record where the loop would put it
                    row["run"], reason = run_index, refusal(row)
                    if reason:
                        row["limited_reason"] = reason
                    path = args.out.parent / "limited" / args.out.name if reason else args.out
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(row, sort_keys=True) + "\n")
                raise
            row["run"] = run_index
            spent += row["total_cost_usd"]
            reason = refusal(row)
            if reason is None:
                break
            row["limited_reason"] = reason
            # Kept apart, outside results/*.jsonl, so the spend is on record but never a failed task.
            limited = args.out.parent / "limited" / args.out.name
            limited.parent.mkdir(parents=True, exist_ok=True)
            with limited.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
            if reason == "api_error":  # a pause fixes no key, balance or model access
                break
            if attempt == LIMIT_RETRIES:
                print(f"stopped: still rate or usage limited after {LIMIT_RETRIES} pauses, "
                      f"${spent:.4f} spent", file=sys.stderr)
                return 2
            print(f"{task['id']} {config['id']}: rate or usage limited, pausing", file=sys.stderr)
            time.sleep(LIMIT_PAUSE * 2 ** attempt)
        if row.get("limited_reason") == "api_error":
            errors += 1
            print(f"{task['id']} {config['id']}: API error before any spend, set aside: {row['answer'][:200]}",
                  file=sys.stderr)
            if errors == API_ERROR_STOP:
                print(f"stopped: {errors} API errors in a row before any spend; check the key, the credit balance "
                      "and the account's model access", file=sys.stderr)
                return 2
            continue
        errors = 0
        with args.out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        print(f"{task['id']} {config['id']} run {run_index}: passed={row['passed']} "
              f"${row['total_cost_usd']:.4f} {time.time() - begin:.0f}s, total ${spent:.4f}")
        if row["grade"]["verdict"] == "broken":
            print(f"{task['id']}: its pass check broke ({row['grade']['reason']}); analyze sets this run aside",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
