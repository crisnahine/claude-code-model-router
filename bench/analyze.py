"""Turn bench results into docs/BENCHMARKS.md: pass rate (exact 95% binomial interval) and cost per solved task
(95% bootstrap interval) for each task class and configuration, and one verdict per baseline from paired per-task
differences, withheld when the runs can't support one. The rows under results/pilot/ get a plain table each.

  python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md
  python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md --check
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH))
import run  # noqa: E402
sys.path.insert(0, str(run.PLUGIN / "scripts"))
sys.path.insert(0, str(BENCH.parent / "tools"))
from docs_pages import HEAD_END, HEAD_START  # noqa: E402
from modelrouter import config as mrconfig  # noqa: E402
from overhead import percentile  # noqa: E402

RESULTS = BENCH / "results"
PILOT = RESULTS / "pilot"  # exploratory rounds: shown on the page, never judged
MIN_RUNS = 10  # fewer runs of either of the split pair than this and a class isn't read as saturated
ALPHA = 0.05  # the 95% intervals, and the one-sided level of the exact comparisons
MARGIN = 0.10  # the non-inferiority margin on pass rate, fixed before any run
MARGIN_TEXT = f"{MARGIN * 100:.0f} points"  # a difference of pass rates, never a share of one
DENIAL_SHARE = 0.25  # a cell with more of its runs denied a Bash call than this gets no verdict
LABELS = {"WIN": "WIN", "LOSE": "LOSE", "NONE": "no difference shown", "REFUSED": "no verdict"}
SPLIT_STRONG, SPLIT_WEAK = "defaults", "haiku-high"  # the pair a task must split to count as telling models apart
ALL = "all tasks"  # the row whose verdicts pair every task in a table, whatever its class


def _interval(draws: list[float]) -> tuple[float, float]:
    ordered = sorted(draws)
    return ordered[int(0.025 * (len(ordered) - 1))], ordered[int(0.975 * (len(ordered) - 1))]


def _finite(value: float) -> float | None:
    return None if math.isinf(value) else value


def _head(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def exact_interval(solved: int, n: int) -> tuple[float, float]:
    """The exact (Clopper-Pearson) 95% interval for a pass rate, by bisection on the binomial tail."""
    def bound(above: bool, k: int) -> float:
        low, high = 0.0, 1.0
        for _ in range(60):
            mid = (low + high) / 2
            tail = 1 - _head(k - 1, n, mid) if above else _head(k, n, mid)
            if (tail < ALPHA / 2) == above:
                low = mid
            else:
                high = mid
        return (low + high) / 2
    return (bound(True, solved) if solved else 0.0, bound(False, solved) if solved < n else 1.0)


def _lower_p(k1: int, n1: int, k2: int, n2: int) -> float:
    """Exact one-sided p that the first rate is below the second if both come from one rate (hypergeometric)."""
    total = k1 + k2
    return sum(math.comb(n1, i) * math.comb(n2, total - i) for i in range(max(0, total - n2), k1 + 1)) \
        / math.comb(n1 + n2, total)


def _cell(runs: list[dict], rng: random.Random, draws: int) -> dict:
    """A cell's own figures for display: pass rate with its exact interval, and cost per solved task with a run-level
    bootstrap interval. Verdicts come from `_paired`, never from these."""
    costs = [float(r["total_cost_usd"]) for r in runs]
    passed = [bool(r["passed"]) for r in runs]
    n, solved, total = len(runs), sum(passed), sum(costs)
    cps = []
    for _ in range(draws):
        picks = [int(rng.random() * n) for _ in range(n)]
        hits = sum(passed[i] for i in picks)
        cps.append(sum(costs[i] for i in picks) / hits if hits else math.inf)
    return {"runs": n, "solved": solved, "cost_usd": total, "pass_rate": solved / n,
            "cost_per_solved": total / solved if solved else None,
            "pass_ci": exact_interval(solved, n), "cps_ci": tuple(_finite(v) for v in _interval(cps))}


def _key(cell: dict) -> float:
    return math.inf if cell["cost_per_solved"] is None else cell["cost_per_solved"]


STOPS = {"budget": "by the budget cap", "timeout": "by the timeout"}


def stopped_by(r: dict) -> str | None:
    """What the harness stopped a run with: `timeout` or `budget`. A row from before `stopped_by` was written shows a
    timeout as a null exit code with no result, and the budget cap as its result subtype."""
    if "stopped_by" in r:
        return r["stopped_by"]
    if r.get("subtype") == run.BUDGET_STOP:
        return "budget"
    return "timeout" if "exit_code" in r and r["exit_code"] is None and r.get("subtype") is None else None


def _settle(r: dict) -> dict:
    """The row as it counts: a run the harness stopped is failed, whatever its files held."""
    how = stopped_by(r)
    return dict(r, passed=False, stopped_by=how) if how else r


def _stops(runs: list[dict]) -> str:
    """How many of `runs` the harness stopped, by what."""
    counts = Counter(r["stopped_by"] for r in runs if r.get("stopped_by"))
    return ", ".join(f"{n} {STOPS.get(how, f'by {how}')}" for how, n in sorted(counts.items())) or "none"


def bash_denials(runs: list[dict]) -> tuple[int, int]:
    """How many runs had a Bash call denied, of how many."""
    denied = sum(any(d.get("tool") == "Bash" for d in r.get("permission_denials") or []) for r in runs)
    return denied, len(runs)


def _paired(ours: list[dict], theirs: list[dict], rng: random.Random, draws: int) -> tuple[tuple, tuple, bool]:
    """95% intervals of the router's pass rate minus a baseline's and of its cost per solved task minus theirs, paired
    by task: each draw resamples the tasks, then each task's runs. A task's pool gets one solved and one failed run
    that carry no cost, so a cell where every run passed still has spread (Agresti-Caffo). Also whether the router
    solved a smaller share of runs, its per-task pass rates averaged, than the baseline did."""
    tasks = sorted({r["task"] for r in ours})
    cells = [[([float(r["total_cost_usd"]) for r in side if r["task"] == t], [bool(r["passed"]) for r in side if r["task"] == t])
              for side in (ours, theirs)] for t in tasks]
    fewer = sum(Fraction(sum(a[1]), len(a[1])) - Fraction(sum(b[1]), len(b[1])) for a, b in cells) < 0
    rand, k = rng.random, len(tasks)
    rates, costs = [], []
    for _ in range(draws):
        sums, gap = [0.0, 0, 0.0, 0], 0.0
        for _ in range(k):
            for side, (cost, passed) in enumerate(cells[int(rand() * k)]):
                n = len(cost)
                for _ in range(n):
                    i = int(rand() * n)
                    sums[2 * side] += cost[i]
                    sums[2 * side + 1] += passed[i]
                # The pass rate draws n + 2 from the runs plus the added solved one (index n) and failed one.
                hits = 0
                for _ in range(n + 2):
                    i = int(rand() * (n + 2))
                    hits += passed[i] if i < n else i == n
                gap += hits / (n + 2) if side == 0 else -hits / (n + 2)
        rates.append(round(gap / k, 12))  # so a bound on the margin itself can't land either side of it by float error
        a = sums[0] / sums[1] if sums[1] else math.inf
        b = sums[2] / sums[3] if sums[3] else math.inf
        costs.append(0.0 if math.isinf(a) and math.isinf(b) else a - b)
    return _interval(rates), _interval(costs), fewer


def _refusal(name: str, ours: list[dict], base: str, theirs: list[dict]) -> str | None:
    """Why the router's runs and a baseline's can't support a verdict, if they can't."""
    why = []
    # Each side's cost is pooled over its runs, so a task one side ran more often weighs more on that side.
    mine, yours = (Counter((r["task"], r.get("batch")) for r in side) for side in (ours, theirs))
    uneven = sorted((k for k in mine.keys() | yours.keys() if mine[k] != yours[k]), key=str)
    if uneven:
        (task, batch), more = uneven[0], len(uneven) - 1
        why.append(f"`{task}`" + (f" in batch `{batch}`" if batch else "") + f" ran {mine[uneven[0]]} times under "
                   f"`{name}` and {yours[uneven[0]]} under `{base}`" + (f", and {more} more task and batch pair(s) "
                                                                        "differ" if more else ""))
    builds = {r.get("plugin_hash") for r in ours}
    if len(builds) > 1:
        why.append(f"`{name}`'s runs loaded {len(builds)} plugin builds (`plugin_hash`)")
    # A task may have its own budget, but both sides must have run it at that one and on one Claude Code.
    for key, what in (("budget_usd", "budget"), ("claude_version", "Claude Code")):
        for task in sorted({r["task"] for r in ours} & {r["task"] for r in theirs}):
            a, b = (", ".join(sorted({str(r.get(key)) for r in side if r["task"] == task})) for side in (ours, theirs))
            if a != b:
                why.append(f"`{task}` ran at {what} {a} under `{name}` and {b} under `{base}` (`{key}`)")
                break
    dirty = sum(bool(r.get("bench_dirty")) for r in ours + theirs)
    if dirty:
        why.append(f"{dirty} of the runs ran from code with uncommitted changes (`bench_dirty`)")
    for who, runs in ((name, ours), (base, theirs)):
        denied, n = bash_denials(runs)
        if denied > DENIAL_SHARE * n:
            why.append(f"Bash denied in {denied} of {n} `{who}` runs, more than a quarter")
    return "; ".join(why) or None


def _judge(pass_ci: tuple, cost_ci: tuple, fewer: bool, solved: tuple) -> tuple[str, str]:
    """The verdict against one baseline; `solved` is the router's solved runs and runs, then the baseline's."""
    (p_lo, p_hi), (c_lo, c_hi) = pass_ci, cost_ci
    seen = (f"cost per solved task {_fmt(c_lo)} to {_fmt(c_hi)}, pass rate {p_lo * 100:+.0f} to {p_hi * 100:+.0f} "
            "points (95% intervals of the router's minus the baseline's)")
    # The runs added per task shrink a pass-rate gap, so the exact test can see a loss the interval hides.
    behind = _lower_p(*solved)
    if p_hi < 0 or behind < ALPHA:
        return "LOSE", (f"pass rate behind beyond noise: solved {solved[0]} of {solved[1]} runs against {solved[2]} of "
                        f"{solved[3]} (exact one-sided p = {behind:.3f}); {seen}")
    # A bound with no value comes from draws where a side solved nothing, which says nothing about cost.
    if math.isfinite(c_hi) and c_hi < 0 and p_lo > -MARGIN:
        if fewer:  # a saving counts only at an equal or higher pass rate
            return "NONE", f"cheaper per solved task beyond noise, but it solved a smaller share of the runs; {seen}"
        return "WIN", (f"cheaper per solved task beyond noise, at an equal or higher share of runs solved, its pass "
                       f"rate shown no more than {MARGIN_TEXT} behind; {seen}")
    if math.isfinite(c_lo) and c_lo > 0 and p_hi < MARGIN:
        return "LOSE", f"dearer per solved task beyond noise, pass rate shown under {MARGIN_TEXT} ahead; {seen}"
    return "NONE", seen


def _fmt(value: float) -> str:
    return "n/a" if math.isinf(value) else f"{'-' if value < 0 else '+'}${abs(value):.4f}"


def _status(verdicts: dict) -> str:
    """A class's status: LOSE when any baseline beats the router, no verdict when a comparison has none, WIN only
    when it wins against every baseline, else no difference shown."""
    codes = [v["verdict"] for v in verdicts.values()]
    for code in ("LOSE", "REFUSED"):
        if code in codes:
            return code
    return "WIN" if codes and all(c == "WIN" for c in codes) else "NONE" if codes else "REFUSED"


def splitting_tasks(runs: list[dict]) -> list[str] | None:
    """The tasks that tell two models apart: `SPLIT_STRONG` solved at least 2 of every 3 runs and `SPLIT_WEAK` at most
    1 of every 3. A reading rule for a few runs, not a significance test. None when either configuration has no runs."""
    def solved(task: str, config: str) -> tuple[int, int]:
        ok = [bool(r["passed"]) for r in runs if r["task"] == task and r["config"] == config]
        return sum(ok), len(ok)
    if not any(r["config"] == SPLIT_STRONG for r in runs) or not any(r["config"] == SPLIT_WEAK for r in runs):
        return None
    out = []
    for task in sorted({r["task"] for r in runs}):
        (s, sn), (w, wn) = solved(task, SPLIT_STRONG), solved(task, SPLIT_WEAK)
        if sn and wn and 3 * s >= 2 * sn and 3 * w <= wn:
            out.append(task)
    return out


def _quality(cells: dict, runs: list[dict]) -> dict:
    """Whether a class's tasks tell models apart: when `haiku-high` can't be told from `defaults` by an exact one-sided
    test, the class is `saturated` and an equal pass rate there says nothing."""
    weak, strong = SPLIT_WEAK, SPLIT_STRONG
    out = {"saturated": None, "splits": splitting_tasks(runs)}
    if weak not in cells or strong not in cells:
        return {**out, "reason": f"`{weak}` or `{strong}` didn't run, so the class can't show whether the tasks tell "
                                 "models apart"}
    w, s = cells[weak], cells[strong]
    if min(w["runs"], s["runs"]) < MIN_RUNS:
        return {**out, "reason": f"too few runs ({MIN_RUNS} of each needed) to say whether the tasks tell models apart"}
    p = _lower_p(w["solved"], w["runs"], s["solved"], s["runs"])
    seen = (f"`{weak}` solved {w['solved']} of {w['runs']} and `{strong}` {s['solved']} of {s['runs']} (exact "
            f"one-sided p = {p:.3f})")
    if p >= ALPHA:
        return {**out, "saturated": True, "reason": f"{seen}, so `{weak}` wasn't shown to solve fewer and these tasks "
                                                    "can't tell models apart"}
    return {**out, "saturated": False, "reason": f"{seen}, so `{weak}` solved fewer and these tasks tell models apart"}


def _summary(cls: str, runs: list[dict], *, routers: frozenset[str], router: str, draws: int, seed: int,
             session: str) -> dict:
    """One row of a table: each configuration's cell and the router's verdict against each baseline."""
    configs = sorted({r["config"] for r in runs}, key=lambda c: (c != router, c))
    by = {c: [r for r in runs if r["config"] == c] for c in configs}
    # One generator per cell and per pair, so draws never depend on which other cells are in the page.
    cells = {c: _cell(by[c], random.Random(f"{seed}/{session}/{cls}/{c}"), draws) for c in configs}
    # A router variant (any configuration that loads the plugin) is a rival build, never a baseline.
    base = [c for c in configs if c != router and c not in routers]
    verdicts = {}

    def judge(b: str, theirs: list[dict]) -> tuple:
        pass_ci, cost_ci, fewer = _paired(by[router], theirs, random.Random(f"{seed}/{session}/{cls}/{router}/{b}"), draws)
        solved = [x for side in (by[router], theirs) for x in (sum(bool(r["passed"]) for r in side), len(side))]
        return (*_judge(pass_ci, cost_ci, fewer, tuple(solved)), pass_ci, cost_ci)

    for b in base if router in cells else []:
        why = _refusal(router, by[router], b, by[b])
        if why:
            verdicts[b] = {"verdict": "REFUSED", "reason": why}
            continue
        code, reason, pass_ci, cost_ci = judge(b, by[b])
        stopped = [r for r in by[b] if r.get("stopped_by")]
        # A cap or timeout that stopped the baseline must not hand the router its verdict; a LOSE can't get worse.
        if stopped and code != "LOSE":
            again = judge(b, [dict(r, passed=True) if r.get("stopped_by") else r for r in by[b]])[0]
            if again != code:
                verdicts[b] = {"verdict": "REFUSED", "reason": f"{len(stopped)} of `{b}`'s {len(by[b])} runs were "
                               f"stopped by the harness ({_stops(stopped)}); counted as solved, the verdict would be "
                               f"{LABELS[again]}, so the stopped runs decide it"}
                continue
        verdicts[b] = {"verdict": code, "reason": reason, "pass_ci": pass_ci, "cost_ci": tuple(map(_finite, cost_ci))}
    status = _status(verdicts)
    # A rank is a cost claim, which a row with no verdict can't support.
    rank = 1 + sum(_key(cells[b]) < _key(cells[router]) for b in base) \
        if router in cells and base and status != "REFUSED" else None
    return {"class": cls, "configs": cells, "verdicts": verdicts, "status": status, "rank": rank,
            "reason": "no router or baseline runs" if not verdicts else _verdict_text(verdicts),
            "quality": _quality(cells, runs)}


def summarize(rows: list[dict], *, routers: frozenset[str], router: str = "plugin", draws: int = 2000,
              seed: int = 0, session: str = "") -> list[dict]:
    """One row per task class."""
    order = {name: i for i, name in enumerate(run.CLASSES)}
    return [_summary(cls, [r for r in rows if r["class"] == cls], routers=routers, router=router, draws=draws,
                     seed=seed, session=session)
            for cls in sorted({r["class"] for r in rows}, key=lambda c: (order.get(c, len(order)), c))]


def overall(rows: list[dict], *, routers: frozenset[str], router: str = "plugin", draws: int = 2000,
            seed: int = 0, session: str = "") -> dict | None:
    """One row over every task in a table, the tasks of all its classes resampled together, so a plan with one task
    per class can still show the margin."""
    return _summary(ALL, rows, routers=routers, router=router, draws=draws, seed=seed,
                    session=session) if rows else None


def _verdict_text(verdicts: dict) -> str:
    """The verdicts against each baseline, those that share a verdict and a reason named together."""
    groups: dict[tuple, list[str]] = {}
    for b, v in sorted(verdicts.items()):
        groups.setdefault((v["verdict"], v["reason"]), []).append(b)
    return "; ".join(f"{LABELS[code]} against {', '.join(f'`{b}`' for b in names)} ({reason})"
                     for (code, reason), names in groups.items())


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def _cell_text(cell: dict) -> str:
    low, high = cell["pass_ci"]
    cps_low, cps_high = cell["cps_ci"]
    return (f"{cell['solved']} of {cell['runs']} solved ({cell['pass_rate']:.0%}; {low:.0%} to {high:.0%}); "
            f"{_money(cell['cost_per_solved'])} per solved ({_money(cps_low)} to {_money(cps_high)})")


def _table(summary: list[dict]) -> list[str]:
    configs = list(dict.fromkeys(c for row in summary for c in row["configs"]))
    lines = ["| Task class | " + " | ".join(configs) + " | Rank | Status |",
             "| :- | " + " | ".join(":-" for _ in configs) + " | -: | :- |"]
    for row in summary:
        cells = [_cell_text(row["configs"][c]) if c in row["configs"] else "not run" for c in configs]
        lines.append(f"| {row['class']} | " + " | ".join(cells) + f" | {row['rank'] or 'n/a'} | {LABELS[row['status']]} |")
    return [*lines, "", *[f"- {row['class']}: {row['reason']}." for row in summary]]


def sessions(results: Results, router: str | None = None) -> list[tuple[str, str, list[dict], dict | None, list[dict]]]:
    """One summary per session model, since a configuration costs differently on each, with its class rows and its
    all-tasks row. Its router is `router`, or else the configuration configs.json marks `judged`."""
    groups: dict[str, list[dict]] = {}
    for r in results.rows:
        groups.setdefault(r.get("main_model") or "unknown", []).append(r)
    chosen = router or next((c["id"] for c in results.configs if c.get("judged")), "plugin")
    out = []
    for model, group in sorted(groups.items()):
        kwargs = {"routers": results.routers, "router": chosen, "session": model}
        out.append((model, chosen, summarize(group, **kwargs), overall(group, **kwargs), group))
    return out


def _quality_lines(summary: list[dict]) -> list[str]:
    lines = ["### Do the tasks tell models apart", ""]
    for row in summary:
        q = row["quality"]
        tag = "saturated" if q["saturated"] else "not saturated" if q["saturated"] is False else "not known"
        split = ("" if q["splits"] is None else
                 f" Tasks that split `{SPLIT_STRONG}` from `{SPLIT_WEAK}`: " + (", ".join(f"`{t}`" for t in q["splits"]) or "none") + ".")
        lines.append(f"- {row['class']}: {tag}, {q['reason']}.{split}")
    return lines


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
    parts.append("Bash denied in {} of {}".format(*bash_denials(cell)))
    told = [r for r in cell if isinstance(r.get("agent_calls"), list)]
    calls = [c for r in told for c in r["agent_calls"] if isinstance(c, dict)]
    parts.append(f"model named on {sum(bool(c.get('named_model')) for c in calls)} of {len(calls)} Agent calls and "
                 f"effort on {sum(bool(c.get('named_effort')) for c in calls)}"
                 + (f" ({len(told)} of {len(cell)} runs)" if len(told) < len(cell) else "") if told else "named n/a")
    # A redo starts the same agent type again; a worker plus a verifier is two types, not a redo.
    redo = [max(r["agents_started"].values(), default=0) for r in cell
            if r.get("shape") == "redo" and isinstance(r.get("agents_started"), dict)]
    if redo:
        parts.append(f"redo {sum(n > 1 for n in redo)} of {len(redo)} runs")
    if not routed:
        return ", ".join(parts + ["not routed"])
    known = [r for r in cell if isinstance(r.get("routed"), list)]
    # A run's level is the one its first subagent got, which holds only where one subagent does the whole task.
    first = [(r["routed"][0]["level"], bool(r.get("passed"))) for r in known
             if r.get("shape", "single") == "single" and r["routed"] and r["routed"][0].get("level")]
    levels: dict[str, list[int]] = {}
    for level, ok in first:
        levels.setdefault(level, [0, 0])
        levels[level][0] += ok
        levels[level][1] += 1
    if levels:
        order = sorted(levels, key=lambda lv: (mrconfig.Level.parse(lv) is None, mrconfig.Level.parse(lv) or mrconfig.Level.lookup))
        text = ", ".join(f"{lv} {levels[lv][0]}/{levels[lv][1]}" for lv in order)
        parts.append(f"accuracy by level: {text}" + (f" ({len(first)} of {len(cell)} runs)" if len(first) < len(cell) else ""))
    else:
        parts.append("accuracy by level n/a")
    spawns = [s for r in known for s in r["routed"] if "escalated_from" in s]
    # A redo with no run on record climbs from the level it would get unmarked, which never ran.
    redone = sum(bool(s["escalated_from"]) and s.get("on_record") is not False for s in spawns)
    parts.append(f"escalated {redone} of {len(spawns)} spawns" if spawns else "escalation n/a")
    return ", ".join(parts)


def _metrics(summary: list[dict], rows: list[dict], routers: frozenset[str]) -> list[str]:
    configs = list(dict.fromkeys(c for row in summary for c in row["configs"]))
    lines = ["### Run metrics", "", "| Task class | " + " | ".join(configs) + " |",
             "| :- | " + " | ".join(":-" for _ in configs) + " |"]
    for row in summary:
        cells = []
        for c in configs:
            cell = [r for r in rows if r["class"] == row["class"] and r["config"] == c]
            cells.append(_metric_text(cell, c in routers) if cell else "not run")
        lines.append(f"| {row['class']} | " + " | ".join(cells) + " |")
    return lines


def render_sessions(parts: list[tuple], meta: dict, routers: frozenset[str]) -> str:
    body = []
    for model, router, summary, every, rows in parts:
        rows_here = summary + ([every] if every else [])
        body += [f"## Results: session model `{model}`", "",
                 f"Every run here had `{model}` as its session model; router: `{router}`.", "",
                 *_table(rows_here), "", *_quality_lines(rows_here), "",
                 *_metrics(summary, rows, routers), ""]
    return "\n".join([meta.get("header", "# Benchmarks"), "", *body, meta.get("body", "")]).rstrip() + "\n"


def _jsonl(paths) -> list[dict]:
    """The rows of each file, refusing a row an older harness wrote: its fields can't be read the same way."""
    rows = []
    for p in sorted(paths):
        for number, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise SystemExit(f"{p}:{number}: not a JSON line ({exc})") from None
            schema = row.get("schema") if isinstance(row, dict) else None
            if not isinstance(schema, int) or schema < run.ROW_SCHEMA:
                raise SystemExit(f"{p}:{number}: row schema {schema if schema is not None else 'missing'}, below "
                                 f"{run.ROW_SCHEMA}: written by an older harness, which this one can't read")
            rows.append(row)
    return rows


@dataclass(frozen=True)
class Results:
    files: list[Path]
    rows: list[dict]
    stale: list[dict]
    broken: list[dict]
    aside: list[dict]
    configs: list[dict]
    tasks: list[dict]
    routers: frozenset[str]
    pilot: list = field(default_factory=list)
    pilot_notes: dict = field(default_factory=dict)


def router_ids(configs: list[dict]) -> frozenset[str]:
    return frozenset(c["id"] for c in configs if c.get("plugin"))


def _split(every: list[dict], tasks: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """The rows that count, each settled, then the rows on an earlier revision of their task, which aren't comparable
    with later runs of it, then the rows whose pass check broke or that the bench stopped before their check finished,
    which were neither solved nor failed."""
    revisions = {t["id"]: t.get("revision", 1) for t in tasks}
    old = [r.get("task_revision", 1) < revisions.get(r["task"], 1) for r in every]
    bad = [(r.get("grade") or {}).get("verdict") in ("broken", "stopped") for r in every]
    return ([_settle(r) for r, gone, b in zip(every, old, bad) if not (gone or b)],
            [r for r, gone in zip(every, old) if gone], [r for r, gone, b in zip(every, old, bad) if b and not gone])


def load(paths: list[Path]) -> Results:
    """Everything the page is built from, read once. The order and repeats of `paths` don't matter. Rows are split
    as `_split` says. A row on the current revision that another check graded stops it: the check changed without
    a raise of the task's revision."""
    files = sorted({p.resolve() for p in paths})
    tasks = run.load_tasks()
    configs = run.load_configs()
    rows, stale, broken = _split(_jsonl(files), tasks)
    today = {t["id"]: run.check_hash(t["dir"] / "check.py") for t in tasks}
    moved = sorted({(r["task"], str((r.get("grade") or {}).get("check_hash"))) for r in rows + broken
                    if r["task"] in today and (r.get("grade") or {}).get("check_hash") != today[r["task"]]})
    if moved:
        raise SystemExit("rows on a task's current revision were graded by another check; raise its revision in "
                         "task.json if the change can change a run's outcome: " + "; ".join(
                             f"`{task}`: a row graded by check {got}, today's is {today[task]}" for task, got in moved))
    aside = [row for folder in sorted({f.parent for f in files}) for row in _jsonl((folder / "limited").glob("*.jsonl"))]
    return Results(files, rows, stale, broken, aside, configs, tasks, router_ids(configs))


CACHE_FIELDS = ("cache_read_tokens", "cache_write_tokens", "fresh_input_tokens")
# The apiKeySource values a run can report (https://code.claude.com/docs/en/agent-sdk/typescript#apikeysource).
PAID = {"ANTHROPIC_API_KEY": "an API key (`ANTHROPIC_API_KEY`)",
        "none": "no API key, so a login token, a bearer token or a cloud provider (`none`)"}


def _cache_line(by_config: dict[str, list[dict]]) -> str:
    """Each configuration's share of input tokens read from the prompt cache, over the runs whose row records the
    three counts; a row without them (an older harness's) is left out, never read as zero."""
    parts = []
    for config, group in sorted(by_config.items()):
        told = [r for r in group if all(isinstance(r.get(k), (int, float)) for k in CACHE_FIELDS)]
        total = sum(r[k] for r in told for k in CACHE_FIELDS)
        if not total:
            parts.append(f"`{config}` " + ("no input tokens" if told else "not recorded"))
            continue
        runs = f"{len(told)} of {len(group)} runs" if len(told) < len(group) else f"{len(group)} run{'s' * (len(group) != 1)}"
        parts.append(f"`{config}` {sum(r['cache_read_tokens'] for r in told) / total:.0%} ({runs})")
    return ("- Input tokens read from the prompt cache, by configuration (`cache_read_tokens` over it plus "
            "`cache_write_tokens` and `fresh_input_tokens`, the whole run's, subagents and workflow agents included): "
            + "; ".join(parts) + ". It shows how much a run reuses within itself, since every run starts cold: each has "
            "its own work dir and config folder, and Claude Code's prompt cache is scoped to one machine and directory "
            "(https://code.claude.com/docs/en/prompt-caching.md#cache-scope).")


def _notes(results: Results) -> list[str]:
    """Page lines on runs set aside, router spend by agent type, and the batches each configuration's rows pool."""
    rows, found = results.rows, results.aside
    own = {"plugin_not_loaded": "router run(s) are set aside, outside the results, because the plugin did not load: "
                                "the run's `system/init` event didn't list it or reported a plugin error "
                                "(https://code.claude.com/docs/en/headless)",
           "no_start": "run(s) are set aside, outside the results, because `claude` stopped before its stream began "
                       "(no `system/init` event and no result), as when the sandbox can't start",
           "router_silent": "router run(s) are set aside, outside the results, because a subagent ran but the "
                            "router's ledger holds no spawn row, so its hooks never acted"}
    aside = [row for row in found if row.get("limited_reason") not in own]
    lines = []
    for reason, text in own.items():
        n = sum(row.get("limited_reason") == reason for row in found)
        if n:
            lines.append(f"- {n} {text}. Their raw lines are under `bench/results/limited/`.")
    if aside:
        reasons = [r.get("limited_reason") for r in aside]
        again = sum(any(o["task"] == r["task"] and o["config"] == r["config"] and o["started_at"] > r["started_at"]
                        for o in rows) for r in aside)
        parts = [f"{reasons.count('network')} lost the network"] * ("network" in reasons) + \
                [f"{reasons.count('limit')} hit a rate or usage limit"] * ("limit" in reasons) + \
                [f"{reasons.count('api_error')} got an account error (a bad or revoked key, no credit, a spend limit "
                 "or a model the account can't use), whatever was spent"] * ("api_error" in reasons)
        lines.append(f"- {len(aside)} run(s) are set aside, outside the results, because the API stopped serving "
                     f"them, before or during the task: {', '.join(parts)}. {again} of them were run again later on the same task and "
                     "configuration, and none counts for or against a configuration. Their raw lines, with the reason, "
                     "are under `bench/results/limited/`.")
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
    broke = [r for r in results.broken if r["grade"]["verdict"] == "broken"]
    halted = [r for r in results.broken if r["grade"]["verdict"] == "stopped"]
    if broke:
        names = sorted({r["task"] for r in broke})
        lines.append(f"- {len(broke)} run(s) (${sum(r['total_cost_usd'] for r in broke):.2f}) are "
                     "set aside, though they stay in the results files: the pass check of "
                     + ", ".join(f"`{n}`" for n in names) + " broke on them (exit code 2, a bug in the check), so "
                     "none counts as solved or failed. Each raw line's `grade` has the check's error.")
    if halted:
        lines.append(f"- {len(halted)} run(s) (${sum(r['total_cost_usd'] for r in halted):.2f}) are set aside, though "
                     "they stay in the results files: the bench itself stopped them before their check finished (an "
                     "interrupt, SIGTERM or an error in the harness), so none counts as solved or failed, and the same "
                     "command run again runs them again. Each raw line's `grade.reason` says what stopped it.")
    for task in results.tasks:
        old = [r for r in results.stale if r["task"] == task["id"]]
        if old:
            changes = "; ".join(f"revision {n}: {why}" for n, why in sorted(task.get("revisions", {}).items()))
            lines.append(f"- `{task['id']}` is at revision {task['revision']}; {len(old)} of its runs "
                         f"(${sum(r['total_cost_usd'] for r in old):.2f}) ran on an earlier revision ({changes}), so "
                         "they are left out of every table.")
    by_config: dict[str, list[dict]] = {}
    for r in rows:
        by_config.setdefault(r["config"], []).append(r)
    caps = Counter(r["budget_usd"] for r in rows if r.get("budget_usd") is not None)
    if caps:
        lines.append("- Budget cap of a run (`--budget`, recorded as `budget_usd`): " + "; ".join(
            f"${cap:.2f} in {n} run{'s' * (n != 1)}" for cap, n in sorted(caps.items())) + ".")
    lines.append("- Runs the harness stopped, by configuration: " + "; ".join(
        f"`{c}` {sum(bool(r.get('stopped_by')) for r in group)} of {len(group)}"
        + (f" ({_stops(group)})" if any(r.get("stopped_by") for r in group) else "")
        for c, group in sorted(by_config.items())) + ". They count as failed whatever their files held, so a "
        "budget or timeout set too low shows here (`stopped_by`).")
    lines.append("- Runs with a Bash denial, by configuration: " + "; ".join(
        "`{}` {} of {}".format(c, *bash_denials(group)) for c, group in sorted(by_config.items())) + ".")
    lines.append(_cache_line(by_config))
    managed = sum(bool(r.get("managed_settings")) for r in rows)
    if managed:
        lines.append(f"- {managed} run(s) here ran on a machine with managed settings files or a macOS plist, which "
                     "a run loads whatever its own settings say; each raw line's `managed_settings` names them.")
    batches: dict[str, dict[str, int]] = {}
    for r in rows:
        batches.setdefault(r["config"], {}).setdefault(r.get("batch") or "?", 0)
        batches[r["config"]][r.get("batch") or "?"] += 1
    lines.append("- A verdict compares the router only with a baseline that ran each task the same number of times "
                 "in each batch, since pass rates and the prompt cache drift between batches. Batches, by "
                 "configuration: "
                 + "; ".join(f"`{c}` " + ", ".join(f"{b} ({n})" for b, n in sorted(batches[c].items()))
                             for c in sorted(batches)) + ".")
    return lines


def _page(results: Results, summary: list[dict]) -> dict:
    rows, files, tasks = results.rows, results.files, results.tasks
    read = [
        "## How to read it", "",
        "- A task counts as solved only when its pass check passes. A wrong answer is a failure, the router's too.",
        "- A run the harness stopped, by the timeout or by the budget cap (`--max-budget-usd`), counts as failed "
        "whatever its files held, the router's too; its raw line's `stopped_by` says which. There is no verdict "
        "against a baseline when its stopped runs decide it: counted as solved, they would give the router another "
        "verdict. The router's own stopped runs count against it and never withhold a verdict.",
        "- Cost per solved task is a configuration's total spend on a task class divided by the runs it solved, "
        "so failed runs count against it.",
        "- The cells show each configuration's own figures: exact 95% binomial (Clopper-Pearson) pass-rate "
        "intervals, and 95% percentile bootstrap intervals of cost per solved task over runs (2,000 resamples, each "
        "cell from a generator seeded by its session model, class and configuration). No verdict rests on them.",
        f"- Verdicts, one per baseline, by a rule fixed before any run. The router's runs and the baseline's are "
        "paired by task. Each of 2,000 resamples draws the row's tasks with replacement, then each drawn task's "
        "runs with replacement, so the interval reflects how few tasks there are as well as how few runs. For the "
        "pass rate, each task's runs get one solved and one failed run added, so a cell where every run passed "
        f"still has spread. The non-inferiority margin is {MARGIN_TEXT} of pass rate. WIN: the 95% interval of the "
        "router's cost per solved task minus the baseline's is below zero, the lower bound of its pass rate "
        f"minus the baseline's is above minus {MARGIN_TEXT}, and the router solved at least the baseline's share of "
        "runs (per-task pass rates averaged), so a saving counts only at an equal or higher pass rate. LOSE: the "
        "router's pass rate is behind beyond noise (the upper bound of its pass rate minus the baseline's is below "
        "zero, or an exact one-sided test at 5% of its solved runs against the baseline's, every run of the row "
        "pooled, puts it behind, which catches a gap the added runs shrink), "
        "or its cost per solved task is above beyond noise while the upper bound of its pass rate minus the "
        f"baseline's is below plus {MARGIN_TEXT}, the mirror of the WIN rule. A cost bound that is n/a decides "
        "nothing: it comes from resamples in which one side solved none of the runs drawn, so it has no cost per "
        "solved task, and a row where a side solved nothing, or nearly nothing, gets its verdict from the pass rate "
        "alone. Anything else is \"no difference shown\", which is not parity. There is no verdict when the two "
        "didn't run each task the same number of times in each batch, since each side's cost is pooled over its runs and pass "
        f"rates drift between batches, when more than {DENIAL_SHARE:.0%} of either's runs had a Bash call denied, "
        "when the router's runs loaded more than one plugin build (`plugin_hash`), when the two ran a task at "
        "different budgets (`budget_usd`) or on different Claude Code versions (`claude_version`), or when any of "
        "their runs ran from code with uncommitted changes (`bench_dirty`). A row "
        "is LOSE when any baseline beats the router, has no verdict when any comparison has none, is WIN only when "
        "it wins against every baseline, and is otherwise \"no difference shown\".",
        f"- Each table ends with an `{ALL}` row, judged the same way over every task in the table: the tasks of all "
        "its classes are paired and resampled together. A class with one task can't show the margin, so a plan "
        f"with a few tasks per class is judged on its `{ALL}` row.",
        f"- A row, the `{ALL}` row too, is saturated when an exact one-sided test (5%, at least {MIN_RUNS} runs of each) "
        f"doesn't show `{SPLIT_WEAK}` (every subagent on Haiku) solving fewer runs than `{SPLIT_STRONG}` (subagents on "
        "the session model). Then an equal pass rate says nothing about quality: the tasks can't tell a small model "
        "from a strong one. Runs on an earlier revision of a task are left out of every table. A run on a "
        "task's current revision that a check other than today's graded stops the page from being built, since the "
        "check changed without a raise of the revision.",
        "- Rank is the router's place among the baselines by cost per solved task, 1 being cheapest, and n/a on a "
        "row with no verdict; a configuration that solved none of the row's runs counts as the dearest. A router "
        "variant (another configuration that loads the plugin) has its own column but is never a baseline.",
        "- Run metrics, per class and configuration: p50 and p90 are nearest-rank percentiles of each run's wall "
        "time (`wall_ms`, the runner's clock around the `claude` process). Cache is cache-read tokens over all input tokens, which are input plus cache reads "
        "plus cache writes (https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md), summed over "
        "every model that ran. Bash denied counts the runs whose result lists a denied Bash call. Model named counts "
        "the Agent calls in the runs' streams that set their own `model`, and effort the ones that set `effort` "
        "(`agent_calls`, which also holds the model each call's first message ran on): Model Router keeps what a "
        "call names, and a call that names no model stays on its parent's. Redo, for a task with the redo shape, "
        "counts the runs that started one agent type twice or more (`agents_started`). It is an estimate: a second "
        "agent of that type that only ran the check counts too, and work sent back as a message to the same "
        "subagent doesn't. For a router "
        "configuration, accuracy by level is the pass rate of its single-agent runs grouped by "
        "the level the router gave the run's first subagent. Escalated counts the spawns whose ledger row names the "
        "level of a run they redo (`escalated_from`, unless `on_record` is false, since then no run at that level is "
        "on record), over the spawns whose row says either way. Both "
        "come from the run's `routed` line, and n/a means no run in the cell has it.", ""]
    commands = [
        "```bash",
        "python3 bench/run.py --tasks <ids> --configs <ids> --runs <n> --budget <usd> --max-total <usd> "
        "--out bench/results/<name>.jsonl",
        "python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md",
        "```", ""]
    if not rows:
        none = ("No judged round has been recorded yet. The exploratory rounds are at the end of this page."
                if results.pilot else "No round has been recorded yet.")
        return {"header": f"# Benchmarks\n\nCost and quality of Model Router against Claude Code without it. {none}",
                "body": "\n".join([*read, "## Configurations", "",
                                   *[f"- `{c['id']}`: {c['description']}" for c in results.configs], "",
                                   "## Commands", "", *commands])}
    total = sum(r["total_cost_usd"] for r in rows)
    versions = sorted({r["claude_version"] for r in rows})
    main = sorted({r["main_model"] for r in rows})
    dates = sorted(r["started_at"][:10] for r in rows)
    used = sorted({r["config"] for r in rows})
    described = {c["id"]: c["description"] for c in results.configs}
    models: dict[str, list[float]] = {}
    agents: dict[str, dict[str, int]] = {}
    for r in rows:
        for name, usage in r["models"].items():
            models.setdefault(name, []).append(usage.get("costUSD") or 0.0)
        for name, count in (r.get("agents_started") or {}).items():
            agents.setdefault(r["config"], {}).setdefault(name, 0)
            agents[r["config"]][name] += count
    paid: dict[str, Counter] = {}
    for r in rows:
        paid.setdefault(r.get("main_model") or "unknown", Counter())[r.get("auth")] += 1
    shapes = {r.get("shape", "single") for r in rows}
    uncovered = [name for shape, name in (("fanout", "fan-out"), ("plan-build", "plan-then-build"), ("redo", "redo"),
                                          ("workflow", "workflow")) if shape not in shapes]
    saturated = sum(bool(row["quality"]["saturated"]) for row in summary)
    every = [row for row in summary if row["class"] == ALL]
    counted = sorted({r["task"] for r in rows})
    brief = {t["id"]: t for t in tasks}
    header = "\n".join([
        "# Benchmarks", "",
        f"Cost and quality of Model Router against Claude Code without it. {len(rows)} runs, "
        f"${total:.2f} at list price, on {dates[0]}" + (f" to {dates[-1]}" if dates[-1] != dates[0] else "") + ".", "",
        "No row here has a verdict against any baseline: the runs can't support one (each row's line says why). "
        "Claim nothing from them beyond what the raw data shows."
        if summary and all(row["status"] == "REFUSED" for row in summary) else
        "Claim \"cheaper\" only against a baseline the router has a WIN against, and link its raw data.",
        *(["", f"{saturated} of {len(summary)} rows below are saturated ({sum(bool(r['quality']['saturated']) for r in every)} "
              f"of {len(every)} `{ALL}` rows): on their tasks the cheapest forced baseline, `{SPLIT_WEAK}` (every subagent "
              f"on Haiku), wasn't shown to solve fewer runs than Claude Code's defaults (`{SPLIT_STRONG}`), so an equal "
              "pass rate there says nothing about quality."] if saturated else []),
    ])
    body = "\n".join([
        *read,
        "## Setup", "",
        f"- Claude Code: {', '.join(versions)}. Session models: {', '.join(main) or 'n/a'} (`--model`), "
        "one results table each. Inside a table every configuration ran on that session model.",
        "- How the runs were paid for, by session model (`auth`, the `apiKeySource` of the run's `system/init` event): "
        + "; ".join(f"`{model}`: " + "; ".join(
            f"{PAID.get(how, f'`{how}`') if how else 'not recorded'} in {n} run{'s' * (n != 1)}"
            for how, n in sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0]))))
            for model, counts in sorted(paid.items())) + ".",
        "- Configurations run: " + "; ".join(f"`{c}` ({described.get(c, '?')})" for c in used) + ".",
        "- Models that ran, with Claude Code's list-price cost across all runs: "
        + "; ".join(f"`{name}` ${sum(costs):.4f} over {len(costs)} runs" for name, costs in sorted(models.items()))
        + ". Each run's raw line also carries the list prices of those models in `bench/prices.json`.",
        "- Agents started, by configuration, counted from the Agent calls in the run's stream (`agents_started`): "
        + "; ".join(f"`{c}` " + ", ".join(f"{name} x{n}" for name, n in sorted(agents.get(c, {}).items()))
                    for c in used) + ".",
        *_notes(results),
        "- Shape: a single agent. The prompt hands each task to one subagent with the Agent tool and relays its "
        "answer, so the subagent is what each configuration changes. A task with a `shape` in its task.json gets "
        "another prompt (`run.SHAPES`): `redo` runs the check the brief "
        "names and sends the work back, `fanout` gives each question its own subagent, all started at once, and "
        "`plan-build` hands out the steps in order, and `workflow` has the session write a script of stages and run it "
        "with the Workflow tool, with `Workflow` and `Read(./**)` added to `--allowedTools` for it. Each row "
        "records its `shape`. An agent-team shape can't run: a headless or Agent SDK session never starts teammates "
        "(https://code.claude.com/docs/en/agent-teams.md).",
        "- Each run starts in a fresh copy of the task's files, outside any repository, with a Claude Code config "
        "folder of its own inside that copy (`CLAUDE_CONFIG_DIR`, with `CLAUDE_CODE_PROJECT_DIR_NAME=run`; "
        "https://code.claude.com/docs/en/env-vars), so `~/.claude.json`, user settings, plugins and transcripts "
        "never come from the machine's own. It runs with `--output-format stream-json --verbose`, "
        "`--setting-sources project`, `--strict-mcp-config`, `--permission-mode dontAsk`, `--allowedTools` "
        + ", ".join(f"`{t}`" for t in run.TOOLS) + " (so the file tools read and edit only the work dir), and one "
        "`--settings` JSON that runs Bash in the OS sandbox "
        "with `sandbox.enabled`, `sandbox.autoAllowBashIfSandboxed`, `sandbox.allowUnsandboxedCommands: false` "
        "and `sandbox.failIfUnavailable`, plus `sandbox.credentials` deny entries for credential folders and for "
        "every secret variable in the environment, and `sandbox.filesystem.denyRead` entries for every place known "
        "to hold the reference answers or graded runs: this checkout, the main checkout and its git data, every "
        "worktree, Claude Code's shared temp folder, the results folder, `--keep` and the folder that holds every "
        "run's scratch copy, with `allowRead` for the run's own (https://code.claude.com/docs/en/sandboxing). "
        "`CLAUDE_CODE_TMPDIR` is the run's scratch copy, so its sandboxed commands get a temp folder of their own. "
        "`permissions.deny` rules `Read(//<path>/**)` and `Edit(//<path>/**)` keep the file tools out of the same "
        "places but the folder of scratch copies, which holds the run's own, since no rule re-opens a denied path "
        "(https://code.claude.com/docs/en/permissions). "
        "`CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` are set. Every inherited "
        "`CLAUDE_CODE_*`, `CLAUDECODE` and `CLAUDE_PLUGIN_*` variable is dropped except the provider switches "
        "(`CLAUDE_CODE_USE_*`) and `CLAUDE_CODE_OAUTH_TOKEN`, so `CLAUDE_CODE_SUBAGENT_MODEL`, "
        "`CLAUDE_CODE_SUBAGENT_MODEL_FORCE` and the effort variables are set only by a configuration; so are "
        "`PWD` and `OLDPWD`. Managed settings still load from the machine. Each raw line lists the managed "
        "settings files and the macOS plist it found in `managed_settings`, but server-managed settings, which "
        "claude.ai sends to an eligible login, and the Windows registry policy can't be seen that way, so an "
        "empty list doesn't prove no policy applied (https://code.claude.com/docs/en/managed-settings.md). Only a "
        "router configuration passes `--plugin-dir`, from a copy of `plugins/model-router` made once when the "
        "batch starts. Each raw line records the hash of the plugin "
        "it loaded in `plugin_hash`, null with no plugin.",
        "- The router keeps state (model health and its ledger) in its plugin data folder, inside the run's own "
        "config folder, so every router run starts from an empty one and its data goes when the scratch copy does. "
        "For a router run the raw line records the count of ledger rows with event `gate` in `gate_events`.",
        "- Sessions are saved in the run's own config folder, as Claude Code saves them by default, so the "
        "router's Stop check can read the transcripts and see a test run, the subagents' included. They go with "
        "the scratch copy. A run that failed or had a denied tool call keeps them in `transcripts/` beside its "
        "results file, its raw line naming the folder in `transcripts`.",
        "- Cost is Claude Code's `total_cost_usd`, which includes subagents and is a client-side estimate at list "
        "price, not a bill (https://code.claude.com/docs/en/agent-sdk/cost-tracking.md). A run with no result (a "
        "timeout, an interrupt, SIGTERM or a crash) is priced from its saved transcripts at list price, each message once, or charged the "
        "whole budget when they hold no usage or a model with no price; `cost_source` says which. Every run is "
        "capped with "
        "`--max-budget-usd`, which a run can pass by up to the cost of one response plus anything that subagents "
        "still running at that moment spend before they stop "
        "(https://code.claude.com/docs/en/agent-sdk/agent-loop.md#budget-headroom). The runner starts a run only "
        "with room for twice its cap under the total, which is a margin, not a bound.",
        "- The runner shuffles the run order with a seed each raw line records (`order_seed`), so no configuration "
        "always runs first or at one time of day.", "",
        "## Task set", "",
        f"The tables count runs of these {len(counted)} tasks:", "",
        *[f"- `{t}` ({brief[t]['class']}): {brief[t]['brief']}" if t in brief else
          f"- `{t}` ({next(r['class'] for r in rows if r['task'] == t)})" for t in counted], "",
        "Defined under `bench/tasks/` with no counted run: "
        + (", ".join(f"`{t['id']}`" for t in tasks if t["id"] not in counted) or "none") + ".", "",
        "Each task's files and pass check are under `bench/tasks/<id>/`. Each check fails on the files as shipped "
        "and passes on a reference answer (`tests/test_bench.py`). A check runs in the OS sandbox a run's own "
        "commands get, with no network, the credential files unreadable and writes only to its scratch folder, "
        "where one can start; each raw line's `grade.sandbox` names it, and is null where none could.", "",
        "## Raw data and commands", "",
        *[f"- [`bench/results/{f.name}`](../bench/results/{f.name})" for f in files], "",
        *commands,
        "Defined in `bench/configs.json` but not run here: "
        + (", ".join(f"`{c['id']}`" for c in results.configs if c["id"] not in used) or "none") + ".", "",
        "Not covered yet: "
        + (f"the {' and '.join([', '.join(uncovered[:-1]), uncovered[-1]] if len(uncovered) > 1 else uncovered)} "
           f"shape{'s' * (len(uncovered) > 1)}; " if uncovered else "")
        + "a warm prompt cache, since every run starts cold; and router overhead per spawn and per turn.",
    ])
    return {"header": header, "body": body}


def _exploratory(rows: list[dict], notes: dict, tasks: list[dict]) -> list[str]:
    """A table per exploratory batch, oldest first: what each configuration solved and cost, and nothing judged. It
    leaves out what the judged tables leave out."""
    batches: dict[str, list[dict]] = {}
    for r in sorted(rows, key=lambda r: r.get("started_at") or ""):
        batches.setdefault(r.get("batch") or "unnamed", []).append(r)
    out = ["## Exploratory rounds", "",
           "Small rounds run to choose tasks and settings. They get no verdict: each has too few runs of a "
           "configuration for one, and a router configuration here may run the plugin from another commit. A run "
           "the harness stopped, by the timeout or the budget cap, counts as failed whatever its files held. As in "
           "the judged tables, runs on an earlier revision of a task, runs whose pass check broke and runs the bench "
           "stopped before their check finished are left out.", ""]
    for name, every in batches.items():
        runs, stale, broken = _split(every, tasks)
        models = ", ".join(f"`{m}`" for m in sorted({r.get("main_model") or "unknown" for r in every}))
        out += [f"### `{name}`: {len(runs)} runs on {every[0].get('started_at', '')[:10]}, "
                f"${sum(r['total_cost_usd'] for r in runs):.2f}, session on {models}", ""]
        if isinstance(notes.get(name), str):
            out += [notes[name], ""]
        left = [f"{len(stale)} run(s) (${sum(r['total_cost_usd'] for r in stale):.2f}) on an earlier revision of "
                + ", ".join(f"`{t}`" for t in sorted({r["task"] for r in stale}))] if stale else []
        for verdict, what in (("broken", "whose pass check broke"),
                              ("stopped", "the bench stopped before their check finished")):
            some = [r for r in broken if r["grade"]["verdict"] == verdict]
            left += [f"{len(some)} run(s) (${sum(r['total_cost_usd'] for r in some):.2f}) {what}"] if some else []
        if left:
            out += ["Left out of the table: " + (", ".join(left[:-1]) + " and " if len(left) > 1 else "") + left[-1]
                    + ".", ""]
        out += ["| Configuration | Runs | Solved | Stopped | Cost per run | Plugin commit |",
                "| :- | -: | -: | :- | -: | :- |"] if runs else []
        cells: dict[str, list[dict]] = {}
        for r in runs:
            cells.setdefault(r["config"], []).append(r)
        for config, cell in sorted(cells.items(), key=lambda kv: -sum(r["total_cost_usd"] for r in kv[1]) / len(kv[1])):
            # A row says whether the plugin loaded only where its configuration loads one.
            ran = {str(r.get("bench_commit") or "")[:7]
                   for r in cell if r.get("plugin_loaded") is not None}
            commit = ", ".join(f"`{c}`" for c in sorted(ran) if c)
            out.append(f"| `{config}` | {len(cell)} | {sum(bool(r['passed']) for r in cell)} | {_stops(cell)} | "
                       f"${sum(r['total_cost_usd'] for r in cell) / len(cell):.3f} | {commit} |")
        out.append("")
    return out


def pilot_rows() -> tuple[list[dict], dict]:
    """The rows under PILOT and the note each batch carries in its notes.json."""
    try:
        notes = json.loads((PILOT / "notes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        notes = {}
    return _jsonl(sorted(PILOT.glob("*.jsonl"))), notes if isinstance(notes, dict) else {}


def build_page(results: Results, router: str | None = None, current: str | None = None) -> str:
    parts = sessions(results, router)
    summary = [row for part in parts for row in part[2] + ([part[3]] if part[3] else [])]
    kept = bool(current and current.startswith(HEAD_START) and HEAD_END in current)
    head = current.split(HEAD_END, 1)[0] + HEAD_END if kept else ""
    page = head + render_sessions(parts, _page(results, summary), results.routers)
    if not results.pilot:
        return page
    return page.rstrip("\n") + "\n\n" + "\n".join(_exploratory(results.pilot, results.pilot_notes, results.tasks)).rstrip("\n") + "\n"


def check(results: Results, out: Path, current: str | None, router: str | None = None) -> list[str]:
    """Why `out`, whose text is `current`, doesn't match the results."""
    return [f"{out}: out of date, run python3 bench/analyze.py bench/results/*.jsonl --out {out}"] \
        if current != build_page(results, router, current) else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", nargs="*", help="results files; a pattern that matched none is no file")
    parser.add_argument("--router", help="the router configuration the tables judge (default: the one configs.json "
                                             "marks judged)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="fail when --out differs from the page the results give")
    args = parser.parse_args(argv)
    try:
        # As text, so a checkout that turned the page's line endings to CRLF still matches.
        current = args.out.read_text(encoding="utf-8") if args.out.is_file() else None
    except (OSError, UnicodeDecodeError):
        current = None
    # A shell passes a pattern that matched no file as it is.
    results = load([Path(p) for arg in args.results for p in (glob.glob(arg) or ([] if re.search(r"[*?[]", arg) else [arg]))])
    pilot, notes = pilot_rows()
    results = replace(results, pilot=pilot, pilot_notes=notes)
    if not args.check:
        args.out.write_text(build_page(results, args.router, current), encoding="utf-8")
        return 0
    problems = check(results, args.out, current, args.router)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
