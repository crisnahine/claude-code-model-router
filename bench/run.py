"""Run bench tasks under each configuration through headless Claude Code and append one JSON line per run.

Example (the pilot):
  python3 bench/run.py --tasks lookup-retry-limit bugfix-pagination --configs plugin defaults haiku \\
      --runs 1 --budget 0.5 --max-total 3 --out bench/results/pilot-2026-11-22.jsonl
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterator

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


def command(prompt: str, config: dict, options: dict, base_env: dict) -> tuple[list[str], dict]:
    env = {k: v for k, v in base_env.items() if k in KEPT_EXACT or k.startswith(KEPT) or not k.startswith(CLEARED)}
    env.update(ISOLATION)
    env.update(config.get("env", {}))
    cmd = [options["claude"], "-p", prompt, "--output-format", "json", "--model", options["model"],
           "--max-budget-usd", str(options["budget"]), "--setting-sources", "project", "--strict-mcp-config",
           "--no-session-persistence", "--permission-mode", "dontAsk"]
    if config.get("plugin"):
        cmd += ["--plugin-dir", options["plugin_dir"]]
    return cmd + ["--allowedTools", *TOOLS], env


def can_start(spent: float, budget: float, max_total: float) -> bool:
    # A run can pass --max-budget-usd by one response plus what running subagents spend before they stop, so
    # this leaves room for twice its cap. That is a margin, not a bound.
    return spent + 2 * budget <= max_total


def plugin_data_dir(env: dict) -> Path:
    """Where Claude Code keeps the --plugin-dir router's data (health, learned raises, the ledger):
    plugins/data/<id>, the id with anything but letters, digits, _ and - turned into -."""
    name = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    home = Path(env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    return home / "plugins" / "data" / re.sub(r"[^A-Za-z0-9_-]", "-", f"{name}@inline")


def state_hashes(folder: Path) -> dict:
    return {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()[:16] if (folder / name).is_file() else None
            for name in STATE_FILES}


def _clear(folder: Path) -> None:
    for child in folder.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


@contextlib.contextmanager
def state_kept(folder: Path) -> Iterator[dict]:
    """Yield the hashes of the router's state, and put the folder back as it was when the block ends. The copy
    sits next to the folder; one left there by a crashed run holds the real state, so the bench won't start."""
    backup = folder.with_name(folder.name + ".bench-saved")
    if backup.exists():
        raise RuntimeError(f"{backup} holds the router state from an interrupted bench: copy it back to {folder}")
    existed = folder.is_dir()
    if existed:
        shutil.copytree(folder, backup, symlinks=True)
    try:
        yield state_hashes(folder)
    finally:
        try:
            if existed:
                _clear(folder)
                shutil.copytree(backup, folder, symlinks=True, dirs_exist_ok=True)
                shutil.rmtree(backup)
            elif folder.exists():
                shutil.rmtree(folder)
        except OSError:
            print(f"could not restore {folder}; the router state before this run is in {backup}", file=sys.stderr)
            raise


def _text(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")


def _git_head() -> str | None:
    res = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
    return res.stdout.strip() or None


def _git_dirty() -> bool:
    """Whether bench/ or the plugin has changes HEAD doesn't hold, so bench_commit isn't the code that ran."""
    res = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "bench", "plugins"],
                         capture_output=True, text=True)
    return bool(res.stdout.strip())


def run_one(task: dict, config: dict, options: dict, prices: dict) -> dict:
    scratch = Path(tempfile.mkdtemp(prefix=f"mrbench-{task['id']}-{config['id']}-"))
    work = scratch / "work"
    shutil.copytree(task["dir"] / "repo", work)
    cmd, env = command(PROMPT.format(brief=task["brief"]), config, options, dict(os.environ))
    started = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with state_kept(plugin_data_dir(env)) if config.get("plugin") else contextlib.nullcontext() as plugin_state:
        try:
            proc = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, timeout=options["timeout"])
            stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, code = _text(exc.stdout), "timed out", None
    try:
        result = json.loads(stdout)
    except ValueError:
        result = {}
    answer = result.get("result") or ""
    (scratch / "answer.txt").write_text(answer, encoding="utf-8")
    check = subprocess.run([sys.executable, str(task["dir"] / "check.py"), str(work), str(scratch / "answer.txt")],
                           capture_output=True, text=True, timeout=120)
    shutil.rmtree(scratch, ignore_errors=True)
    models = {name: {k: usage.get(k) for k in ("inputTokens", "outputTokens", "cacheReadInputTokens",
                                                "cacheCreationInputTokens", "costUSD", "costBasis")}
              for name, usage in (result.get("modelUsage") or {}).items()}
    agents = result.get("subagent_stats") or {}
    cost = result.get("total_cost_usd")
    return {
        "schema": 1, "task": task["id"], "class": task["class"], "config": config["id"],
        "started_at": started, "claude_version": options["claude_version"], "main_model": options["model"],
        "budget_usd": options["budget"], "bench_commit": options["commit"], "bench_dirty": options.get("dirty"),
        "plugin_state": plugin_state,
        "exit_code": code, "subtype": result.get("subtype"), "is_error": result.get("is_error"),
        # Without a result the cost is unknown; count the whole budget so totals never understate spend.
        "total_cost_usd": cost if isinstance(cost, (int, float)) else options["budget"],
        "cost_known": isinstance(cost, (int, float)),
        "duration_ms": result.get("duration_ms"), "num_turns": result.get("num_turns"),
        "models": models, "prices": {name: prices.get(name) for name in models},
        "subagents": {k: agents.get(k) for k in ("spawned", "completed", "failed", "by_type")},
        "permission_denials": [d.get("tool_name") for d in result.get("permission_denials") or []],
        "passed": check.returncode == 0, "answer": answer[:2000],
        "stderr": stderr[-300:] if code else "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tasks", nargs="*", help="task ids (default: all)")
    parser.add_argument("--configs", nargs="*", help="configuration ids (default: all)")
    parser.add_argument("--runs", type=int, default=1, help="runs of each task under each configuration")
    parser.add_argument("--model", default="sonnet", help="the session model, the same for every configuration")
    parser.add_argument("--budget", type=float, default=0.5, help="--max-budget-usd for each run")
    parser.add_argument("--max-total", type=float, required=True, help="stop before total spend could pass this")
    parser.add_argument("--timeout", type=int, default=900, help="seconds before a run is killed")
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    tasks = [t for t in load_tasks() if not args.tasks or t["id"] in args.tasks]
    configs = [c for c in load_configs() if not args.configs or c["id"] in args.configs]
    version = subprocess.run([args.claude, "--version"], capture_output=True, text=True).stdout.strip()
    prices = json.loads((PLUGIN / "config" / "models.json").read_text(encoding="utf-8"))["prices"]
    options = {"claude": args.claude, "model": args.model, "budget": args.budget, "timeout": args.timeout,
               "plugin_dir": str(PLUGIN), "claude_version": version, "commit": _git_head(), "dirty": _git_dirty()}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    spent = 0.0
    for run_index in range(args.runs):
        # Rotate the order so no configuration always runs first on a cold prompt cache.
        shift = run_index % len(configs)
        for task in tasks:
            for config in configs[shift:] + configs[:shift]:
                if not can_start(spent, args.budget, args.max_total):
                    print(f"stopped: ${spent:.4f} spent, cap ${args.max_total}", file=sys.stderr)
                    return 1
                begin = time.time()
                row = run_one(task, config, options, prices)
                row["run"] = run_index
                spent += row["total_cost_usd"]
                with args.out.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, sort_keys=True) + "\n")
                print(f"{task['id']} {config['id']} run {run_index}: passed={row['passed']} "
                      f"${row['total_cost_usd']:.4f} {time.time() - begin:.0f}s, total ${spent:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
