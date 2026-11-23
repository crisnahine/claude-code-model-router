"""Run bench tasks under each configuration through headless Claude Code and append one JSON line per run.

Example (the pilot):
  python3 bench/run.py --tasks lookup-retry-limit bugfix-pagination --configs plugin defaults haiku \\
      --runs 1 --budget 0.5 --max-total 3 --out bench/results/pilot-2026-11-22.jsonl
"""
from __future__ import annotations

import argparse
import datetime
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

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent
PLUGIN = ROOT / "plugins" / "model-router"
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
STATE_FILES = ("state.json", "learned.json")
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
# The messages Claude Code prints when the API refuses for a limit (https://code.claude.com/docs/en/errors.md).
_LIMIT_MESSAGE = (r"API Error: (?:Repeated )?(?:429|529)\b|API Error: Repeated 529 Overloaded|Request rejected \(429\)|"
                  r"Server is temporarily limiting requests|You've hit your (?:session|weekly|Opus|Sonnet) limit")


# What Claude Code printed when the network went away (2026-11-22): an API Error naming a network code, or a
# dropped connection.
_NETWORK_MESSAGE = (r"API Error: [^\n]*\((?:ENOTFOUND|ENETUNREACH|ECONNRESET|ECONNREFUSED|ETIMEDOUT|EAI_AGAIN)\)|"
                    r"API Error: (?:Can't reach the API server|No internet route)|Connection lost")


def refusal(row: dict) -> str | None:
    """Why the API never got to the task: "limit" or "network", else None. Only an errored run counts, and only
    on the API's status or Claude Code's own message, at the start of the answer (which is then that message) or
    in stderr. Task text that talks about limits or network codes is never read as one."""
    if not (row.get("is_error") or row.get("exit_code")):
        return None
    if row.get("api_error_status") in (429, 529):
        return "limit"
    answer, stderr = str(row.get("answer") or ""), str(row.get("stderr") or "")
    for reason, message in (("limit", _LIMIT_MESSAGE), ("network", _NETWORK_MESSAGE)):
        if re.match(rf"\s*(?:{message})", answer) or re.search(message, stderr):
            return reason
    return None


def rate_limited(row: dict) -> bool:
    """A run refused for a rate or usage limit or a lost network: no measurement, not a failed task."""
    return refusal(row) is not None


SECRET_NAMES = ("TOKEN", "KEY", "SECRET", "PASSWORD")


def grading_env(env: dict) -> dict:
    """The environment a check runs in: the harness's own, minus credentials, since the check's process also runs
    the code a run left in the work dir."""
    return {k: v for k, v in env.items()
            if not (any(w in k.upper() for w in SECRET_NAMES) or k.upper().startswith(("ANTHROPIC_", "CLAUDE_CODE_OAUTH")))}


def grade(check: Path, work: Path, answer: Path, timeout: int = 120) -> bool:
    """Whether the task's check passes. -I keeps the check's folder and the work dir off sys.path and ignores
    PYTHON* variables, so code a run left behind can't shadow what the check imports."""
    try:
        return subprocess.run([sys.executable, "-I", str(check), str(work), str(answer)], capture_output=True,
                              env=grading_env(dict(os.environ)), timeout=timeout).returncode == 0
    except subprocess.TimeoutExpired:  # code the run left that hangs the check fails the task, not the bench
        return False


def can_start(spent: float, budget: float, max_total: float) -> bool:
    # A run can pass --max-budget-usd by one response plus what running subagents spend before they stop, so
    # this leaves room for twice its cap. That is a margin, not a bound.
    return spent + 2 * budget <= max_total


def plugin_data_dir(env: dict) -> Path:
    """Where Claude Code keeps the --plugin-dir router's data (health, learned raises, the ledger):
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
    """The router's ledger rows for a run, in order; None when it kept no ledger. Lines that aren't objects are skipped."""
    ledger = plugin_data_dir(env) / "ledger.jsonl"
    if not ledger.is_file():
        return None
    rows = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


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


def run_one(task: dict, config: dict, options: dict, prices: dict) -> dict:
    # No task or configuration name: a brief that quotes its working directory would hand the router signal words.
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
    passed = grade(task["dir"] / "check.py", work, scratch / "answer.txt")
    ledger = ledger_rows(env) if config.get("plugin") else None
    types = agent_types(work, env) if config.get("plugin") else {}
    kept = None
    if options.get("keep"):
        # The work the run left, its answer, the router's own data and ledger, and the session's transcripts.
        kept = Path(options["keep"]) / scratch.name
        shutil.copytree(scratch, kept, symlinks=True)
        if session_dir(work, env).is_dir():
            shutil.copytree(session_dir(work, env), kept / "session", symlinks=True)
    session_removed = _forget_session(session_dir(work, env))
    shutil.rmtree(scratch, ignore_errors=True)
    return record(task, config, options, prices, result,
                  {"started_at": started, "wall_ms": wall_ms, "exit_code": code, "stderr": stderr, "passed": passed,
                   "plugin_state": plugin_state, "ledger": ledger, "agent_types": types,
                   "session_dir_removed": session_removed, "kept": kept and str(kept)})


def record(task: dict, config: dict, options: dict, prices: dict, result: dict, facts: dict) -> dict:
    """The row for one run, from Claude Code's parsed result and what the runner saw around the process."""
    models = {name: {k: usage.get(k) for k in ("inputTokens", "outputTokens", "cacheReadInputTokens",
                                                "cacheCreationInputTokens", "costUSD", "costBasis")}
              for name, usage in (result.get("modelUsage") or {}).items()}
    agents = result.get("subagent_stats") or {}
    cost = result.get("total_cost_usd")
    ledger, code = facts["ledger"], facts["exit_code"]
    stderr = facts["stderr"][-300:] if code else ""
    refused = refusal({"is_error": result.get("is_error"), "exit_code": code, "stderr": stderr,
                       "api_error_status": result.get("api_error_status"), "answer": result.get("result")})
    # A cost of 0 on error_during_execution "may be zeroed" (https://code.claude.com/docs/en/agent-sdk/cost-tracking.md),
    # except when the API refused the run: it never started, so nothing was spent.
    known = isinstance(cost, (int, float)) and not (
        result.get("subtype") == "error_during_execution" and cost == 0 and not refused)
    return {
        "schema": 1, "task": task["id"], "task_revision": task.get("revision", 1), "class": task["class"],
        "config": config["id"],
        "started_at": facts["started_at"], "claude_version": options["claude_version"],
        "main_model": config.get("model", options["model"]),
        "budget_usd": options["budget"], "bench_commit": options["commit"], "bench_dirty": options.get("dirty"),
        "plugin_state": facts["plugin_state"],
        "agent_costs": None if ledger is None else spend_by_agent(ledger, facts["agent_types"]),
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
        "models": models, "prices": {name: prices.get(name) for name in models},
        "subagents": {k: agents.get(k) for k in ("spawned", "completed", "failed", "by_type")},
        "permission_denials": [d.get("tool_name") for d in result.get("permission_denials") or []],
        "passed": facts["passed"], "answer": (result.get("result") or "")[:2000],
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
    version = subprocess.run([*args.claude, "--version"], capture_output=True, text=True).stdout.strip()
    prices = json.loads((PLUGIN / "config" / "models.json").read_text(encoding="utf-8"))["prices"]
    options = {"claude": args.claude, "model": args.model, "budget": args.budget, "timeout": args.timeout,
               "plugin_dir": str(PLUGIN), "claude_version": version, "commit": _git_head(), "dirty": _git_dirty(),
               "keep": str(args.keep) if args.keep else None}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    spent = 0.0
    for run_index, task, config in schedule(tasks, configs, args.runs, args.first_run):
        if not can_start(spent, args.budget, args.max_total):
            print(f"stopped: ${spent:.4f} spent, cap ${args.max_total}", file=sys.stderr)
            return 1
        begin = time.time()
        for attempt in range(LIMIT_RETRIES + 1):
            row = run_one(task, config, options, prices)
            row["run"] = run_index
            spent += row["total_cost_usd"]
            if not rate_limited(row):
                break
            row["limited_reason"] = refusal(row)
            # Kept apart, outside results/*.jsonl, so the spend is on record but never a failed task.
            limited = args.out.parent / "limited" / args.out.name
            limited.parent.mkdir(parents=True, exist_ok=True)
            with limited.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
            if attempt == LIMIT_RETRIES:
                print(f"stopped: still rate or usage limited after {LIMIT_RETRIES} pauses, "
                      f"${spent:.4f} spent", file=sys.stderr)
                return 2
            print(f"{task['id']} {config['id']}: rate or usage limited, pausing", file=sys.stderr)
            time.sleep(LIMIT_PAUSE * 2 ** attempt)
        with args.out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        print(f"{task['id']} {config['id']} run {run_index}: passed={row['passed']} "
              f"${row['total_cost_usd']:.4f} {time.time() - begin:.0f}s, total ${spent:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
