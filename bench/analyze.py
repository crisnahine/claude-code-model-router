"""Turn bench results into docs/BENCHMARKS.md: pass rate and cost per solved task for each task class and
configuration, with 95% bootstrap intervals, and the router's standing against the best baseline.

  python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH))
import run  # noqa: E402

MIN_RUNS = 10  # fewer runs per configuration than this and no row is called a win or a loss


def _interval(draws: list[float]) -> tuple[float, float]:
    ordered = sorted(draws)
    return ordered[int(0.025 * (len(ordered) - 1))], ordered[int(0.975 * (len(ordered) - 1))]


def _finite(value: float) -> float | None:
    return None if math.isinf(value) else value


def _cell(runs: list[dict], rng: random.Random, draws: int) -> dict:
    costs = [float(r["total_cost_usd"]) for r in runs]
    passed = [bool(r["passed"]) for r in runs]
    n, solved, total = len(runs), sum(passed), sum(costs)
    cps, rate = [], []
    for _ in range(draws):
        picks = [rng.randrange(n) for _ in range(n)]
        hits = sum(passed[i] for i in picks)
        cps.append(sum(costs[i] for i in picks) / hits if hits else math.inf)
        rate.append(hits / n)
    return {"runs": n, "solved": solved, "cost_usd": total, "pass_rate": solved / n,
            "cost_per_solved": total / solved if solved else None,
            "pass_ci": _interval(rate), "cps_ci": tuple(_finite(v) for v in _interval(cps)),
            "_cps": cps, "_rate": rate}


def _key(cell: dict) -> float:
    return math.inf if cell["cost_per_solved"] is None else cell["cost_per_solved"]


def _difference(ours: list[float], theirs: list[float]) -> tuple[float, float]:
    return _interval([0.0 if math.isinf(a) and math.isinf(b) else a - b for a, b in zip(ours, theirs)])


def _verdict(cells: dict, router: str) -> dict:
    base = {name: cell for name, cell in cells.items() if name != router}
    if router not in cells or not base:
        return {"status": "PARTIAL", "rank": None, "best_baseline": None, "reason": "no router or baseline runs"}
    ours = cells[router]
    best = min(base, key=lambda name: (_key(base[name]), name))
    theirs = base[best]
    verdict = {"rank": 1 + sum(_key(cell) < _key(ours) for cell in base.values()), "best_baseline": best}
    if ours["runs"] < MIN_RUNS or theirs["runs"] < MIN_RUNS:
        return {**verdict, "status": "PARTIAL",
                "reason": f"too few runs ({router} {ours['runs']}, {best} {theirs['runs']}; {MIN_RUNS} needed)"}
    if _difference(ours["_rate"], theirs["_rate"])[1] < 0:
        gap = theirs["pass_rate"] - ours["pass_rate"]
        return {**verdict, "status": "FAIL", "reason": f"pass rate behind {best} by {gap:.0%}"}
    low, high = _difference(ours["_cps"], theirs["_cps"])
    if high < 0:
        return {**verdict, "status": "PASS", "reason": f"cost per solved task below {best}'s; "
                                                       f"95% interval of the difference ${low:.4f} to ${high:.4f}"}
    if low > 0:
        return {**verdict, "status": "FAIL", "reason": f"cost per solved task above {best}'s; "
                                                       f"95% interval of the difference ${low:.4f} to ${high:.4f}"}
    return {**verdict, "status": "PARTIAL", "reason": f"not significant against {best}"}


def summarize(rows: list[dict], router: str = "plugin", draws: int = 2000, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    order = {name: i for i, name in enumerate(run.CLASSES)}
    out = []
    for cls in sorted({r["class"] for r in rows}, key=lambda c: (order.get(c, len(order)), c)):
        configs = sorted({r["config"] for r in rows if r["class"] == cls}, key=lambda c: (c != router, c))
        cells = {c: _cell([r for r in rows if r["class"] == cls and r["config"] == c], rng, draws) for c in configs}
        verdict = _verdict(cells, router)
        for cell in cells.values():
            del cell["_cps"], cell["_rate"]
        out.append({"class": cls, "configs": cells, **verdict})
    return out


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def _cell_text(cell: dict) -> str:
    low, high = cell["pass_ci"]
    cps_low, cps_high = cell["cps_ci"]
    return (f"{cell['solved']} of {cell['runs']} solved ({cell['pass_rate']:.0%}; {low:.0%} to {high:.0%}); "
            f"{_money(cell['cost_per_solved'])} per solved ({_money(cps_low)} to {_money(cps_high)})")


def render(summary: list[dict], meta: dict) -> str:
    configs = []
    for row in summary:
        configs += [c for c in row["configs"] if c not in configs]
    lines = ["| Task class | " + " | ".join(configs) + " | Rank | Status |",
             "| :- | " + " | ".join(":-" for _ in configs) + " | -: | :- |"]
    for row in summary:
        cells = [_cell_text(row["configs"][c]) if c in row["configs"] else "not run" for c in configs]
        lines.append(f"| {row['class']} | " + " | ".join(cells) + f" | {row['rank'] or 'n/a'} | {row['status']} |")
    reasons = [f"- {row['class']}: {row['status']}, {row['reason']}." for row in summary]
    head = meta.get("header", "# Benchmarks")
    return "\n".join([head, "", "## Results", "", *lines, "", *reasons, "", meta.get("body", "")]).rstrip() + "\n"


def _newest_price(prices: dict, alias: str) -> dict:
    versions = [(tuple(int(n) for n in key.split("-")[2:] if n.isdigit()), value) for key, value in prices.items()
                if key.startswith(f"claude-{alias}-") and not key.endswith("*")]
    return max(versions, key=lambda v: v[0])[1] if versions else {}


def estimate(rows: list[dict], tasks: list[dict], configs: list[dict], prices: dict) -> dict:
    """What MIN_RUNS of every task under every configuration would cost. A class's runs cost what its runs
    here cost on average (all runs' mean for a class with none), and a configuration whose subagents run a
    dearer model than sonnet costs that much more on the whole run, an upper bound since the session stays
    on sonnet. Cheaper models are never priced down."""
    overall = sum(r["total_cost_usd"] for r in rows) / len(rows) if rows else 0.0
    by_class: dict[str, list[float]] = {}
    for r in rows:
        by_class.setdefault(r["class"], []).append(r["total_cost_usd"])
    means = {cls: sum(costs) / len(costs) for cls, costs in by_class.items()}
    base = _newest_price(prices, "sonnet").get("output") or 1
    factors = {c["id"]: max(1.0, (_newest_price(prices, c.get("price_model", "sonnet")).get("output") or base) / base)
               for c in configs}
    usd = MIN_RUNS * sum(means.get(t["class"], overall) * factors[c["id"]] for t in tasks for c in configs)
    return {"runs": len(tasks) * len(configs) * MIN_RUNS, "usd": usd, "means": means, "overall": overall,
            "factors": factors}


def _page(rows: list[dict], files: list[Path], summary: list[dict]) -> dict:
    total = sum(r["total_cost_usd"] for r in rows)
    versions = sorted({r["claude_version"] for r in rows})
    main = sorted({r["main_model"] for r in rows})
    dates = sorted(r["started_at"][:10] for r in rows)
    configs = {c["id"]: c["description"] for c in run.load_configs()}
    tasks = run.load_tasks()
    used = sorted({r["config"] for r in rows})
    models: dict[str, list[float]] = {}
    agents: dict[str, dict[str, int]] = {}
    for r in rows:
        for name, usage in r["models"].items():
            models.setdefault(name, []).append(usage.get("costUSD") or 0.0)
        for name, count in ((r.get("subagents") or {}).get("by_type") or {}).items():
            agents.setdefault(r["config"], {}).setdefault(name, 0)
            agents[r["config"]][name] += count
    stale = sum(1 for r in rows if "plugin_state" not in r)
    unsaved = sum(1 for r in rows if "sessions_saved" not in r)
    borrowed = sum(1 for r in rows if r.get("plugin_state") is not None and "plugin_data_own" not in r)
    prices = json.loads((run.PLUGIN / "config" / "models.json").read_text(encoding="utf-8"))["prices"]
    plan = estimate(rows, tasks, run.load_configs(), prices)
    header = "\n".join([
        "# Benchmarks", "",
        f"Cost and quality of Model Router against Claude Code without it. {len(rows)} runs, "
        f"${total:.2f} at list price, on {dates[0] if dates else 'n/a'}"
        + (f" to {dates[-1]}" if dates and dates[-1] != dates[0] else "") + ".", "",
        "This is a pilot. Every row is PARTIAL because each configuration ran each task too few times to tell "
        "a difference from noise. Claim nothing from it beyond what the raw data shows."
        if all(row["status"] == "PARTIAL" for row in summary) else
        "Claim \"cheapest\" or \"best\" only for a PASS row, and link its raw data.",
    ])
    body = "\n".join([
        "## How to read it", "",
        "- A task counts as solved only when its pass check passes. A wrong answer is a failure, the router's too.",
        "- Cost per solved task is a configuration's total spend on a task class divided by the tasks it solved, "
        "so failed runs count against it.",
        "- Intervals are 95% percentile bootstrap intervals over runs (2,000 resamples, seed 0).",
        f"- PASS means the router's cost per solved task is below the best baseline's with the whole 95% interval "
        f"of the difference below zero, and its pass rate is not behind beyond noise. FAIL means it is behind on "
        f"either. PARTIAL means a tie, a difference inside the noise, or fewer than {MIN_RUNS} runs per "
        "configuration. The best baseline is the one with the lowest cost per solved task.",
        "- Rank is the router's place among the configurations by cost per solved task, 1 being cheapest.", "",
        "## Setup", "",
        f"- Claude Code: {', '.join(versions) or 'n/a'}. Session model: {', '.join(main) or 'n/a'}, the same for "
        "every configuration.",
        "- Configurations run: " + "; ".join(f"`{c}` ({configs.get(c, '?')})" for c in used) + ".",
        "- Models that ran, with Claude Code's list-price cost across all runs: "
        + "; ".join(f"`{name}` ${sum(costs):.4f} over {len(costs)} runs" for name, costs in sorted(models.items()))
        + ". Each run's raw line also carries the registry's prices for those models.",
        "- Agents started, by configuration: "
        + "; ".join(f"`{c}` " + ", ".join(f"{name} x{n}" for name, n in sorted(agents.get(c, {}).items()))
                    for c in used) + ".",
        "- Shape: a single agent. The session hands each task to one subagent with the Agent tool and relays its "
        "answer, so the subagent is what each configuration changes.",
        "- Each run starts in a fresh copy of the task's files, outside any repository, with "
        "`--setting-sources project`, `--strict-mcp-config`, "
        "`--permission-mode dontAsk`, `--allowedTools` " + ", ".join(f"`{t}`" for t in run.TOOLS) + " (every "
        "part of a compound command must match, so `tail`, `head` and `echo` let a subagent pipe its test output), "
        "`CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. Every inherited "
        "`CLAUDE_CODE_*`, `CLAUDECODE` and `CLAUDE_PLUGIN_*` variable is dropped except the provider switches "
        "(`CLAUDE_CODE_USE_*`) and `CLAUDE_CODE_OAUTH_TOKEN`, so `CLAUDE_CODE_SUBAGENT_MODEL`, "
        "`CLAUDE_CODE_SUBAGENT_MODEL_FORCE` and the effort variables are set only by a configuration. Only the "
        "router configuration passes `--plugin-dir`.",
        "- The router keeps learned state across sessions (model health, level raises and its ledger) in its "
        "plugin data folder, by default `~/.claude/plugins/data/model-router-inline/`, which every `--plugin-dir` "
        "session on the machine shares. The runner gives each run a plugins root of its own inside its scratch "
        "copy with `CLAUDE_CODE_PLUGIN_CACHE_DIR` (https://code.claude.com/docs/en/env-vars.md), so every router "
        "run starts from an empty data folder, never reads or writes the shared one, and its data goes when the "
        "scratch copy does. Each raw line records `plugin_data_own` and, for router runs, hashes of the "
        "`state.json` and `learned.json` the run started from in `plugin_state`."
        + (f" {borrowed} router run(s) here borrowed the shared folder instead: the runner copied it before the "
           "run and put it back after, which also wiped what other sessions wrote there meanwhile." if borrowed else "")
        + (f" {stale} run(s) here predate this: their lines have no `plugin_state`, and their router runs "
           "started from whatever earlier runs and other sessions had left there. They also predate the `tail`, "
           "`head` and `echo` rules and the stricter `lookup-retry-limit` check, and their `bench_commit` is the "
           "commit the harness ran on top of before it was committed. Newer lines carry `bench_dirty`, true when "
           "`bench/` or the plugin had uncommitted changes." if stale else ""),
        "- Sessions are saved, as Claude Code saves them by default, so the router's Stop check can read the "
        "transcripts and see a test run, the subagents' included. After each run the runner deletes that run's "
        "folder under `~/.claude/projects/` (the one named after its scratch copy) and nothing else, and each "
        "raw line records `sessions_saved` and `session_dir_removed`."
        + (f" {unsaved} run(s) here ran with `--no-session-persistence`, which saves no transcript, so the Stop "
           "check could never see a test run and asked for a verifier after every edit; their lines have no "
           "`sessions_saved`." if unsaved else ""),
        "- Cost is Claude Code's `total_cost_usd`, which includes subagents and is a client-side estimate at list "
        "price, not a bill (https://code.claude.com/docs/en/agent-sdk/cost-tracking.md). Every run is capped with "
        "`--max-budget-usd`, which a run can pass by up to the cost of one response plus anything that subagents "
        "still running at that moment spend before they stop "
        "(https://code.claude.com/docs/en/agent-sdk/agent-loop.md#budget-headroom). The runner starts a run only "
        "with room for twice its cap under the total, which is a margin, not a bound.",
        "- The prompt cache is shared across runs, so later runs of a configuration may read what earlier ones "
        "wrote. The runner rotates the configuration order on each run, and every raw line records its cache "
        "tokens.", "",
        "## Task set", "",
        *[f"- `{t['id']}` ({t['class']}): {t['brief']}" for t in tasks], "",
        "Each task's files and pass check are under `bench/tasks/<id>/`. Each check fails on the files as shipped "
        "and passes on a reference answer (`tests/test_bench.py`).", "",
        "## Raw data and commands", "",
        *[f"- [`bench/results/{f.name}`](../bench/results/{f.name})" for f in files], "",
        "```bash",
        "python3 bench/run.py --tasks <ids> --configs <ids> --runs <n> --budget 0.5 --max-total <usd> "
        "--out bench/results/<name>.jsonl",
        "python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md",
        "```", "",
        "## The full suite", "",
        f"A run of the whole current task set under every configuration in `bench/configs.json`, {MIN_RUNS} times "
        f"each, is {len(tasks)} tasks x {len(configs)} configurations x {MIN_RUNS} runs = {plan['runs']} runs, "
        f"about ${plan['usd']:.2f} at list price. Each class is priced at its own mean per run here ("
        + ", ".join(f"{cls} ${mean:.4f}" for cls, mean in sorted(plan["means"].items()))
        + f"), and a class with no runs yet at the mean of all runs (${plan['overall']:.4f}). A configuration "
        "whose subagents run a dearer model than Sonnet is priced up by that model's output price over Sonnet's, "
        "on the whole run: "
        + ", ".join(f"`{cid}` x{factor:g}" for cid, factor in plan["factors"].items() if factor > 1)
        + ". That is an upper bound for those, since the session stays on Sonnet. The new classes are harder than "
        "the pilot's, so their runs may cost more than the mean. The full suite needs the maintainer's go-ahead "
        "before it runs.", "",
        "Not covered yet: the fan-out, workflow, team and redo shapes; `haiku`, `opus` and `fable` at `high` "
        "effort; a cold and a warm prompt cache measured apart; router overhead per spawn and per turn; and each "
        "`priority` setting. `opusplan` in a headless `dontAsk` run never enters plan mode, so it runs Sonnet "
        "throughout (https://code.claude.com/docs/en/model-config.md#opusplan-model-setting, "
        "https://code.claude.com/docs/en/permission-modes.md). The advisor needs the Anthropic API, Claude "
        "Platform on AWS or Claude Models on Google Cloud (https://code.claude.com/docs/en/advisor.md), and the "
        "`fable` configuration needs Fable access.",
    ])
    return {"header": header, "body": body}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--router", default="plugin")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    rows = [json.loads(line) for path in args.results for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    summary = summarize(rows, router=args.router)
    args.out.write_text(render(summary, _page(rows, sorted(args.results), summary)), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
