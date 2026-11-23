"""Turn bench results into docs/BENCHMARKS.md: pass rate and cost per solved task for each task class and
configuration, with 95% bootstrap intervals, and the router's standing against the best baseline.

  python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md
  python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md --check
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH))
import run  # noqa: E402
sys.path.insert(0, str(run.PLUGIN / "scripts"))
from modelrouter import config as mrconfig, profile  # noqa: E402
from overhead import percentile  # noqa: E402

RESULTS = BENCH / "results"
HEAD_END = "<!-- End of page head. -->\n\n"  # the same text as docs_assets.HEAD_END
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
        picks = [int(rng.random() * n) for _ in range(n)]
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


def _verdict(cells: dict, router: str, routers: frozenset[str]) -> dict:
    # A router variant (any configuration that loads the plugin) is a rival build, never a baseline.
    base = {name: cell for name, cell in cells.items() if name != router and name not in routers}
    if router not in cells or not base:
        return {"status": "PARTIAL", "rank": None, "best_baseline": None, "differences": {},
                "reason": "no router or baseline runs"}
    ours = cells[router]
    best = min(base, key=lambda name: (_key(base[name]), name))
    theirs = base[best]
    verdict = {"rank": 1 + sum(_key(cell) < _key(ours) for cell in base.values()), "best_baseline": best,
               "differences": {name: _difference(ours["_cps"], cell["_cps"]) for name, cell in base.items()}}
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


def summarize(rows: list[dict], *, routers: frozenset[str], router: str = "plugin", draws: int = 2000,
              seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    order = {name: i for i, name in enumerate(run.CLASSES)}
    out = []
    for cls in sorted({r["class"] for r in rows}, key=lambda c: (order.get(c, len(order)), c)):
        configs = sorted({r["config"] for r in rows if r["class"] == cls}, key=lambda c: (c != router, c))
        cells = {c: _cell([r for r in rows if r["class"] == cls and r["config"] == c], rng, draws) for c in configs}
        verdict = _verdict(cells, router, routers)
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


def _table(summary: list[dict]) -> list[str]:
    configs = []
    for row in summary:
        configs += [c for c in row["configs"] if c not in configs]
    lines = ["| Task class | " + " | ".join(configs) + " | Rank | Status |",
             "| :- | " + " | ".join(":-" for _ in configs) + " | -: | :- |"]
    for row in summary:
        cells = [_cell_text(row["configs"][c]) if c in row["configs"] else "not run" for c in configs]
        lines.append(f"| {row['class']} | " + " | ".join(cells) + f" | {row['rank'] or 'n/a'} | {row['status']} |")
    return [*lines, "", *[f"- {row['class']}: {row['status']}, {row['reason']}." for row in summary]]


def render(summary: list[dict], meta: dict) -> str:
    head = meta.get("header", "# Benchmarks")
    return "\n".join([head, "", "## Results", "", *_table(summary), "", meta.get("body", "")]).rstrip() + "\n"


def sessions(results: Results, router: str | None = None) -> list[tuple[str, str, list[dict], list[dict]]]:
    """One summary per session model, since a configuration costs differently on each. Its router is `router`,
    or else the first configuration in configs.json that loads the plugin and ran on that session model. A
    configuration that sets its own session model (opusplan) runs the same in any batch, so it joins every table."""
    groups: dict[str, list[dict]] = {}
    for r in results.rows:
        if r["config"] not in results.own_model:
            groups.setdefault(r.get("main_model") or "unknown", []).append(r)
    for r in results.rows:
        if r["config"] in results.own_model:
            for group in groups.values() or [groups.setdefault(r.get("main_model") or "unknown", [])]:
                group.append(r)
    out = []
    for model, group in sorted(groups.items()):
        ran = {r["config"] for r in group}
        chosen = router or next((c["id"] for c in results.configs if c.get("plugin") and c["id"] in ran), "plugin")
        out.append((model, chosen, summarize(group, routers=results.routers, router=chosen), group))
    return out


def _cache_share(rows: list[dict]) -> float | None:
    """Cache reads over all input tokens, which are input plus cache reads plus cache writes."""
    read = total = 0
    for r in rows:
        for u in (r.get("models") or {}).values():
            read += u.get("cacheReadInputTokens") or 0
            total += ((u.get("inputTokens") or 0) + (u.get("cacheReadInputTokens") or 0)
                      + (u.get("cacheCreationInputTokens") or 0))
    return read / total if total else None


def _metric_text(cell: list[dict], routed: bool) -> str:
    secs = [r["wall_ms"] / 1000 for r in cell if isinstance(r.get("wall_ms"), (int, float))]
    parts = [f"p50 {percentile(secs, 0.5):.0f} s, p90 {percentile(secs, 0.9):.0f} s"
             + (f" ({len(secs)} of {len(cell)} runs)" if len(secs) < len(cell) else "") if secs else "latency n/a"]
    share = _cache_share(cell)
    parts.append("cache n/a" if share is None else f"cache {share:.0%}")
    if not routed:
        return ", ".join(parts + ["not routed"])
    known = [r for r in cell if isinstance(r.get("routed"), list)]
    # A run's level is the one its first subagent got: the bench hands each task to one subagent.
    first = [(r["routed"][0]["level"], bool(r.get("passed"))) for r in known if r["routed"] and r["routed"][0].get("level")]
    levels: dict[str, list[int]] = {}
    for level, ok in first:
        levels.setdefault(level, [0, 0])
        levels[level][0] += ok
        levels[level][1] += 1
    if levels:
        order = sorted(levels, key=lambda lv: mrconfig.LEVELS.index(lv) if lv in mrconfig.LEVELS else len(mrconfig.LEVELS))
        text = ", ".join(f"{lv} {levels[lv][0]}/{levels[lv][1]}" for lv in order)
        parts.append(f"accuracy by level: {text}" + (f" ({len(first)} of {len(cell)} runs)" if len(first) < len(cell) else ""))
    else:
        parts.append("accuracy by level n/a")
    spawns = [s for r in known for s in r["routed"] if "escalated_from" in s]
    parts.append(f"escalated {sum(bool(s['escalated_from']) for s in spawns)} of {len(spawns)} spawns" if spawns
                 else "escalation n/a")
    return ", ".join(parts)


def _metrics(summary: list[dict], rows: list[dict], routers: frozenset[str]) -> list[str]:
    configs = []
    for row in summary:
        configs += [c for c in row["configs"] if c not in configs]
    lines = ["### Run metrics", "", "| Task class | " + " | ".join(configs) + " |",
             "| :- | " + " | ".join(":-" for _ in configs) + " |"]
    for row in summary:
        cells = []
        for c in configs:
            cell = [r for r in rows if r["class"] == row["class"] and r["config"] == c]
            cells.append(_metric_text(cell, c in routers) if cell else "not run")
        lines.append(f"| {row['class']} | " + " | ".join(cells) + " |")
    return lines


def render_sessions(parts: list[tuple[str, str, list[dict], list[dict]]], meta: dict, routers: frozenset[str]) -> str:
    body = []
    for model, router, summary, rows in parts:
        own = sorted({r["config"] for r in rows if r.get("main_model") != model})
        but = f", except {', '.join(f'`{c}`' for c in own)}, which sets its own" if own else ""
        body += [f"## Results: session model `{model}`", "", f"Every run here had `{model}` as its session model{but}; "
                 f"router: `{router}`.", "", *_table(summary), "", *_metrics(summary, rows, routers), ""]
    return "\n".join([meta.get("header", "# Benchmarks"), "", *body, meta.get("body", "")]).rstrip() + "\n"


def _newest_price(prices: dict, alias: str) -> dict:
    versions = [(tuple(int(n) for n in key.split("-")[2:] if n.isdigit()), value) for key, value in prices.items()
                if key.startswith(f"claude-{alias}-") and not key.endswith("*")]
    return max(versions, key=lambda v: v[0])[1] if versions else {}


def estimate(rows: list[dict], tasks: list[dict], configs: list[dict], prices: dict) -> dict:
    """What MIN_RUNS of every task under every configuration would cost. A class's runs cost what its runs
    here cost on average (all runs' mean for a class with none), and a configuration whose subagents run a
    dearer model than sonnet costs that much more per token on the whole run. Cheaper models are never priced
    down. A guide, not a bound: token counts differ by model and the means pool every configuration."""
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


def _batch(path: Path) -> str:
    """A raw file's batch: its name without the date and the task class, so m6-05-bugfix-2026-11-22 is m6-05."""
    name = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", path.stem)
    return next((name[:-len(cls) - 1] for cls in run.CLASSES if name.endswith(f"-{cls}")), name)


def _jsonl(paths) -> list[dict]:
    return [json.loads(line) for p in sorted(paths) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


@dataclass(frozen=True)
class Results:
    files: list[Path]
    rows: list[dict]
    aside: list[tuple[str, dict]]
    archive: list[tuple[str, list[dict], int]]
    configs: list[dict]
    tasks: list[dict]
    prices: dict
    routers: frozenset[str]
    own_model: frozenset[str]


def default_paths() -> list[Path]:
    return sorted(RESULTS.glob("*.jsonl"))


def router_ids(configs: list[dict]) -> frozenset[str]:
    return frozenset(c["id"] for c in configs if c.get("plugin"))


def load(paths: list[Path]) -> Results:
    """Everything the page is built from, read once. The order and repeats of `paths` don't matter."""
    files = sorted({p.resolve() for p in paths})
    rows = [dict(row, _batch=_batch(path)) for path in files for row in _jsonl([path])]
    folders = sorted({f.parent for f in files})
    aside = [(sub, row) for folder in folders for sub in ("infra", "limited") for row in _jsonl((folder / sub).glob("*.jsonl"))]
    archive = []
    for results in folders:
        top = results / "archive"
        for folder in sorted({top, *(p for p in top.glob("*") if p.is_dir())}) if top.is_dir() else []:
            kept = _jsonl(folder.glob("*.jsonl"))
            if kept:
                lines = len(_jsonl([*folder.glob("infra/*.jsonl"), *folder.glob("limited/*.jsonl")]))
                archive.append((folder.relative_to(results).as_posix(), kept, lines))
    configs = run.load_configs()
    prices = json.loads((run.PLUGIN / "config" / "models.json").read_text(encoding="utf-8"))["prices"]
    return Results(files, rows, aside, archive, configs, run.load_tasks(), prices, router_ids(configs),
                   frozenset(c["id"] for c in configs if c.get("model")))


def _notes(results: Results) -> list[str]:
    """Page lines on runs set aside, router spend by agent type, and the batches each configuration's rows pool."""
    rows, found = results.rows, results.aside
    aside = [row for _, row in found]
    where = " and ".join(f"`bench/results/{sub}/`" for sub in sorted({sub for sub, _ in found}))
    lines = []
    if aside:
        reasons = [r.get("limited_reason") or ("network" if "infra_reason" in r else "limit") for r in aside]
        again = sum(any(o["task"] == r["task"] and o["config"] == r["config"] and o["started_at"] > r["started_at"]
                        for o in rows) for r in aside)
        parts = [f"{reasons.count('network')} lost the network"] * ("network" in reasons) + \
                [f"{reasons.count('limit')} hit a rate or usage limit"] * ("limit" in reasons)
        lines.append(f"- {len(aside)} run(s) are set aside, outside the results, because the API never got to the "
                     f"task: {', '.join(parts)}. {again} of them were run again later on the same task and "
                     "configuration, and none counts for or against a configuration. Their raw lines, with the reason, "
                     f"are under {where}.")
    spend: dict[tuple, dict[str, float]] = {}
    counted: dict[tuple, int] = {}
    for r in rows:
        if isinstance(r.get("agent_costs"), dict):
            key = (r["config"], r.get("main_model") or "unknown")
            counted[key] = counted.get(key, 0) + 1
            for who, usd in r["agent_costs"].items():
                spend.setdefault(key, {}).setdefault(who, 0.0)
                spend[key][who] += usd
    if spend:
        def share(key: tuple) -> str:
            total = sum(spend[key].values()) or 1
            order = sorted(spend[key].items(), key=lambda kv: (kv[0] != "main", -kv[1]))
            runs = counted[key]
            return f"`{key[0]}` on `{key[1]}` ({runs} run{'s' * (runs != 1)}): " + ", ".join(
                f"{'main loop' if who == 'main' else f'`{who}`'} ${usd:.4f} ({usd / total:.0%})" for who, usd in order)
        lines.append("- Router spend by agent type, from the router's ledger (`agent_costs`), over the runs that "
                     "record it: " + "; ".join(share(c) for c in sorted(spend)) + ".")
    for task in results.tasks:
        old = sum(r["task"] == task["id"] and r.get("task_revision", 1) < task.get("revision", 1) for r in rows)
        if old:
            changes = "; ".join(f"revision {n}: {why}" for n, why in sorted(task.get("revisions", {}).items()))
            lines.append(f"- `{task['id']}` is at revision {task['revision']}; {old} of its runs here ran on an "
                         f"earlier revision ({changes}), so they are not comparable with later runs of it.")
    named = [t["id"] for t in results.tasks if profile.signals(t["id"].lower())]
    early = sum(r["config"] in results.routers and r["task"] in named and "task_revision" not in r for r in rows)
    if named and early:
        lines.append(f"- Bias: {early} router-arm runs of the tasks named here were recorded while each run's "
                     "folder name held its task id, so a brief that quoted its working directory could give the "
                     "router signal words from the folder name for " + ", ".join(f"`{t}`" for t in named) +
                     ". That could only have raised the router's level and cost on those tasks, so on cost those rows understate the router; a "
                     "pass the higher level bought may not repeat.")
    unclocked = sum(1 for r in rows if not isinstance(r.get("wall_ms"), (int, float)))
    if unclocked:
        lines.append(f"- {unclocked} run(s) here carry no `wall_ms`, the runner's own clock around the `claude` "
                     "process, so their latency is n/a. Their `duration_ms` is Claude Code's figure, and it isn't "
                     "taken as wall time: the docs list the wait for background subagents in `duration_api_ms`, not "
                     "in `duration_ms` (https://code.claude.com/docs/en/agent-sdk/cost-tracking.md), and on many "
                     "rows here it falls well short of the gap to the next run's start (measured in "
                     "`docs/TEST_RESULTS.md`).")
    unrouted = sum(1 for r in rows if r["config"] in results.routers and not isinstance(r.get("routed"), list))
    if unrouted:
        lines.append(f"- {unrouted} router run(s) here carry no `routed`, the router's spawn decisions: they were "
                     "recorded before the runner kept them. Their accuracy by level and escalation count as n/a, "
                     "not as zero.")
    batches: dict[str, dict[str, int]] = {}
    for r in rows:
        batches.setdefault(r["config"], {}).setdefault(r.get("_batch", "?"), 0)
        batches[r["config"]][r.get("_batch", "?")] += 1
    lines.append("- Each configuration's runs pool every batch it ran in, on the same machine, so a configuration "
                 "run in a later batch is compared with the baseline runs of earlier ones. Batches, by configuration: "
                 + "; ".join(f"`{c}` " + ", ".join(f"{b} ({n})" for b, n in sorted(batches[c].items()))
                             for c in sorted(batches)) + ".")
    return lines


def _archived(results: Results) -> list[str]:
    """Each folder of rows kept under results/archive/, which no table reads, and why its rows are left out."""
    commits = {r.get("bench_commit") for r in results.rows}
    revisions = {t["id"]: t.get("revision", 1) for t in results.tasks}
    lines = []
    for folder, kept, aside in results.archive:
        other = [r for r in kept if r.get("bench_commit") not in commits]
        stale = sorted({r["task"] for r in kept if r.get("task_revision", 1) < revisions.get(r.get("task"), 1)})
        reasons = []
        if other:
            reasons.append(f"{len(other)} ran on {len({r.get('bench_commit') for r in other})} other commits "
                           "(older builds of the router and the bench)")
        if stale:
            n = sum(r.get("task_revision", 1) < revisions.get(r.get("task"), 1) for r in kept)
            reasons.append(f"{n} on an earlier revision of " + ", ".join(f"`{t}`" for t in stale))
        unnamed = sum("task_revision" not in r for r in kept)
        if unnamed:
            reasons.append(f"{unnamed} were recorded while the run's folder name held its task id")
        listed = (", ".join(reasons[:-1]) + ", and " + reasons[-1]) if len(reasons) > 1 else "".join(reasons)
        lines.append(f"- `bench/results/{folder}/` holds {len(kept)} rows left out "
                     "of every table" + (f", plus {aside} set-aside line{'s' * (aside != 1)} under its `infra/` and "
                                         "`limited/`" if aside else "") + (f": {listed}." if listed else "."))
    return lines + [""] if lines else []


def _page(results: Results, summary: list[dict]) -> dict:
    rows, files, tasks = results.rows, results.files, results.tasks
    total = sum(r["total_cost_usd"] for r in rows)
    versions = sorted({r["claude_version"] for r in rows})
    main = sorted({r["main_model"] for r in rows})
    dates = sorted(r["started_at"][:10] for r in rows)
    configs = {c["id"]: c["description"] for c in results.configs}
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
    plan = estimate(rows, tasks, results.configs, results.prices)
    budget = max((r.get("budget_usd") or 0.5 for r in rows), default=0.5)
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
        "- Rank is the router's place among the baselines by cost per solved task, 1 being cheapest. A router "
        "variant (another configuration that loads the plugin) has its own column but is never a baseline.",
        "- Run metrics, per class and configuration: p50 and p90 are nearest-rank percentiles of each run's wall "
        "time (`wall_ms`, the runner's clock around the `claude` process). Cache is cache-read tokens over all input tokens, which are input plus cache reads "
        "plus cache writes (https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md), summed over "
        "every model that ran. For a router configuration, accuracy by level is the pass rate of its runs grouped by "
        "the level the router gave the run's first subagent. Escalated counts the spawns the router moved one level "
        "up because the same call came back for a redo, over the spawns whose ledger row says either way. Both "
        "come from the run's `routed` line, and n/a means no run in the cell has it.", "",
        "## Setup", "",
        f"- Claude Code: {', '.join(versions) or 'n/a'}. Session models: {', '.join(main) or 'n/a'} (`--model`), "
        "one results table each. Inside a table every configuration ran on that session model, except one that "
        "sets its own (`opusplan`).",
        "- Configurations run: " + "; ".join(f"`{c}` ({configs.get(c, '?')})" for c in used) + ".",
        "- Models that ran, with Claude Code's list-price cost across all runs: "
        + "; ".join(f"`{name}` ${sum(costs):.4f} over {len(costs)} runs" for name, costs in sorted(models.items()))
        + ". Each run's raw line also carries the registry's prices for those models.",
        "- Agents started, by configuration: "
        + "; ".join(f"`{c}` " + ", ".join(f"{name} x{n}" for name, n in sorted(agents.get(c, {}).items()))
                    for c in used) + ".",
        *_notes(results),
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
        *_archived(results),
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
        + ". That scales the price per token only: a dearer model may use more or fewer tokens, the class means "
        "pool every configuration's runs, and the new classes are harder than the pilot's two, so treat the figure "
        f"as a guide, not a bound. At the per-run cap the pilot used, {plan['runs']} runs x ${budget:g} come to "
        f"${plan['runs'] * budget:,.2f} before overshoot, which has no fixed limit; only `--max-total` stops "
        "spend. The full suite needs the maintainer's go-ahead before it runs.", "",
        "Defined in `bench/configs.json` but not run here: "
        + (", ".join(f"`{c['id']}`" for c in results.configs if c["id"] not in used) or "none") + ".", "",
        "Not covered yet: the fan-out, workflow, team and redo shapes; a cold and a warm prompt cache measured "
        "apart; router overhead per spawn and per turn; and each `priority` setting. `opusplan` in a headless `dontAsk` run never enters plan mode, so it runs Sonnet "
        "throughout (https://code.claude.com/docs/en/model-config.md#opusplan-model-setting, "
        "https://code.claude.com/docs/en/permission-modes.md). The advisor needs the Anthropic API, Claude "
        "Platform on AWS or Claude Models on Google Cloud (https://code.claude.com/docs/en/advisor.md), and the "
        "`fable` and `fable-high` configurations need Fable access.",
    ])
    return {"header": header, "body": body}


def build_page(results: Results, router: str | None = None, current: str | None = None) -> str:
    parts = sessions(results, router)
    summary = [row for _, _, part, _ in parts for row in part]
    kept = bool(current and current.startswith("<!-- Page head") and HEAD_END in current)
    head = current.split(HEAD_END, 1)[0] + HEAD_END if kept else ""
    return head + render_sessions(parts, _page(results, summary), results.routers)


def summary_data(results: Results, router: str | None = None) -> dict:
    """The page's figures as data, for the docs chart."""
    rows = results.rows
    dates = sorted(r["started_at"][:10] for r in rows)
    out = []
    for model, chosen, classes, group in sessions(results, router):
        pooled: dict[str, dict] = {}
        for r in group:
            cell = pooled.setdefault(r["config"], {"runs": 0, "solved": 0, "cost_usd": 0.0})
            cell["runs"] += 1
            cell["solved"] += bool(r["passed"])
            cell["cost_usd"] += float(r["total_cost_usd"])
        ref = next((c.get("plugin_ref") for c in results.configs if c["id"] == chosen), None)
        out.append({"model": model, "router": chosen, "plugin_ref": ref, "classes": classes, "pooled": pooled})
    return {"runs": len(rows), "usd": sum(float(r["total_cost_usd"]) for r in rows),
            "dates": [dates[0], dates[-1]] if dates else [], "claude_versions": sorted({r["claude_version"] for r in rows}),
            "sessions": out}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--router", help="the router configuration (default: per session model, the first "
                                             "configuration in configs.json that loads the plugin)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="fail when --out differs from the page the results give")
    args = parser.parse_args(argv)
    try:
        # As text, so a checkout that turned the page's line endings to CRLF still matches.
        current = args.out.read_text(encoding="utf-8") if args.out.is_file() else None
    except (OSError, UnicodeDecodeError):
        current = None
    page = build_page(load(args.results), args.router, current)
    if not args.check:
        args.out.write_text(page, encoding="utf-8")
        return 0
    if current == page:
        return 0
    print(f"{args.out}: out of date, run python3 bench/analyze.py bench/results/*.jsonl --out {args.out}",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
