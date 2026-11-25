"""Turn bench results into docs/BENCHMARKS.md: pass rate (exact 95% binomial interval) and cost per solved task
(95% bootstrap interval) for each task class and configuration, and one verdict per baseline from paired per-task
differences, withheld when the runs can't support one. `figures` gives the docs the same numbers by role (router,
defaults, best, strongest), with the yes/no facts their prose relies on.

  python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md
  python3 bench/analyze.py bench/results/*.jsonl --out docs/BENCHMARKS.md --check
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import random
import re
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH))
import run  # noqa: E402
sys.path.insert(0, str(run.PLUGIN / "scripts"))
sys.path.insert(0, str(BENCH.parent / "tools"))
from docs_pages import HEAD_END, HEAD_START  # noqa: E402
from modelrouter import config as mrconfig  # noqa: E402
from modelrouter.registry import Registry  # noqa: E402
from overhead import percentile  # noqa: E402

RESULTS = BENCH / "results"
MIN_RUNS = 10  # fewer runs per forced baseline than this and a class isn't read as saturated
ALPHA = 0.05  # the 95% intervals, and the one-sided level of the exact comparisons
MARGIN = 0.10  # the non-inferiority margin on pass rate, fixed before any run
DENIAL_SHARE = 0.25  # a cell with more of its runs denied a Bash call than this gets no verdict
LABELS = {"WIN": "WIN", "LOSE": "LOSE", "NONE": "no difference shown", "REFUSED": "no verdict"}
SPLIT_STRONG, SPLIT_WEAK = "opus", "sonnet"  # the pair a task must split to count as telling models apart
ALL = "all tasks"  # the row whose verdicts pair every task in a table, whatever its class
# Router variants configs.json no longer defines; their archived rows keep the role.
ARCHIVED_ROUTERS = ("plugin-cheap", "plugin-cheap-nomain", "plugin-cheap-nomain-nogate", "plugin-m6-08-b",
                    "plugin-m6-08-b-nogate", "plugin-m6-10")
LEGACY_ROUTER = "plugin-next"  # the build the rows below schema 3, and the docs that quote them, report on


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
    cps, rate = [], []
    for _ in range(draws):
        picks = [int(rng.random() * n) for _ in range(n)]
        hits = sum(passed[i] for i in picks)
        cps.append(sum(costs[i] for i in picks) / hits if hits else math.inf)
        rate.append(hits / n)
    return {"runs": n, "solved": solved, "cost_usd": total, "pass_rate": solved / n,
            "cost_per_solved": total / solved if solved else None,
            "pass_ci": exact_interval(solved, n), "cps_ci": tuple(_finite(v) for v in _interval(cps)),
            "_cps": cps, "_rate": rate}


def _key(cell: dict) -> float:
    return math.inf if cell["cost_per_solved"] is None else cell["cost_per_solved"]


def bash_denials(runs: list[dict]) -> tuple[int, int]:
    """How many runs had a Bash call denied, of how many. An older row lists the denied tools by name alone."""
    denied = sum(any((d.get("tool") if isinstance(d, dict) else d) == "Bash" for d in r.get("permission_denials") or [])
                 for r in runs)
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
    if {r["task"] for r in ours} != {r["task"] for r in theirs}:
        why.append(f"`{name}` and `{base}` ran different tasks")
    if any(r.get("schema", 1) < 3 for r in ours + theirs):
        why.append("rows recorded before Bash ran in the sandbox, at row schema below 3")
    if {r.get("_batch", r.get("batch")) for r in ours} != {r.get("_batch", r.get("batch")) for r in theirs}:
        why.append("the two ran in different batches")
    for who, runs in ((name, ours), (base, theirs)):
        denied, n = bash_denials(runs)
        if denied > DENIAL_SHARE * n:
            why.append(f"Bash denied in {denied} of {n} `{who}` runs, more than a quarter")
    return "; ".join(why) or None


def _judge(base: str, pass_ci: tuple, cost_ci: tuple, fewer: bool) -> tuple[str, str]:
    (p_lo, p_hi), (c_lo, c_hi) = pass_ci, cost_ci
    seen = (f"cost per solved task {_fmt(c_lo)} to {_fmt(c_hi)}, pass rate {p_lo:+.0%} to {p_hi:+.0%} "
            "(95% intervals of the router's minus the baseline's)")
    if p_hi < 0:
        return "LOSE", f"pass rate behind beyond noise; {seen}"
    if c_hi < 0 and p_lo > -MARGIN:
        if fewer:  # a saving counts only at an equal or higher pass rate
            return "NONE", f"cheaper per solved task beyond noise, but it solved a smaller share of the runs; {seen}"
        return "WIN", (f"cheaper per solved task beyond noise, at an equal or higher share of runs solved, pass rate "
                       f"within {MARGIN:.0%} of it; {seen}")
    if c_lo > 0 and p_hi < MARGIN:
        return "LOSE", f"dearer per solved task beyond noise, pass rate shown under {MARGIN:.0%} ahead; {seen}"
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


def splitting_tasks(runs: list[dict], strong: str = SPLIT_STRONG, weak: str = SPLIT_WEAK) -> list[str] | None:
    """The tasks that tell two models apart: `strong` solved at least 2 of every 3 runs and `weak` at most 1 of
    every 3. A reading rule for a few runs, not a significance test. None when either configuration has no runs."""
    def solved(task: str, config: str) -> tuple[int, int]:
        ok = [bool(r["passed"]) for r in runs if r["task"] == task and r["config"] == config]
        return sum(ok), len(ok)
    if not any(r["config"] == strong for r in runs) or not any(r["config"] == weak for r in runs):
        return None
    out = []
    for task in sorted({r["task"] for r in runs}):
        (s, sn), (w, wn) = solved(task, strong), solved(task, weak)
        if sn and wn and 3 * s >= 2 * sn and 3 * w <= wn:
            out.append(task)
    return out


def _quality(cells: dict, runs: list[dict], forced: dict) -> dict:
    """Whether a class's tasks tell models apart: when the weakest forced baseline can't be told from the strongest
    by an exact one-sided test, the class is `saturated` and an equal pass rate there says nothing."""
    ladder = sorted((n for n in cells if n in forced), key=lambda n: (forced[n], n))
    weak, strong = (ladder[0], ladder[-1]) if ladder else (None, None)
    out = {"saturated": None, "weakest": weak, "strongest": strong, "splits": splitting_tasks(runs)}
    if not ladder or forced[weak][0] == forced[strong][0]:
        return {**out, "reason": "forced baselines on fewer than 2 models, so the class can't show whether the "
                                 "tasks tell models apart"}
    w, s = cells[weak], cells[strong]
    if min(w["runs"], s["runs"]) < MIN_RUNS:
        return {**out, "reason": f"too few runs ({MIN_RUNS} needed) to say whether the tasks tell models apart"}
    p = _lower_p(w["solved"], w["runs"], s["solved"], s["runs"])
    seen = f"{weak} solved {w['solved']} of {w['runs']}, {strong} {s['solved']} of {s['runs']} (exact p = {p:.3f})"
    if p >= ALPHA:
        return {**out, "saturated": True, "reason": f"{seen}, a gap that could be chance, so these tasks can't tell "
                                                    "models apart"}
    return {**out, "saturated": False, "reason": f"{seen}, so these tasks tell models apart"}


def _summary(cls: str, runs: list[dict], *, routers: frozenset[str], router: str, draws: int, seed: int,
             session: str, forced: dict | None) -> dict:
    """One row of a table: each configuration's cell and the router's verdict against each baseline."""
    configs = sorted({r["config"] for r in runs}, key=lambda c: (c != router, c))
    by = {c: [r for r in runs if r["config"] == c] for c in configs}
    # One generator per cell and per pair, so draws never depend on which other cells are in the page.
    cells = {c: _cell(by[c], random.Random(f"{seed}/{session}/{cls}/{c}"), draws) for c in configs}
    # A router variant (any configuration that loads the plugin) is a rival build, never a baseline.
    base = [c for c in configs if c != router and c not in routers]
    verdicts, differences, pass_differences = {}, {}, {}
    for b in base if router in cells else []:
        why = _refusal(router, by[router], b, by[b])
        if why:
            verdicts[b], differences[b] = {"verdict": "REFUSED", "reason": why}, None
            continue
        pass_ci, cost_ci, fewer = _paired(by[router], by[b], random.Random(f"{seed}/{session}/{cls}/{router}/{b}"), draws)
        code, reason = _judge(b, pass_ci, cost_ci, fewer)
        verdicts[b] = {"verdict": code, "reason": reason, "pass_ci": pass_ci, "cost_ci": tuple(map(_finite, cost_ci))}
        differences[b], pass_differences[b] = verdicts[b]["cost_ci"], pass_ci
    best = min(base, key=lambda name: (_key(cells[name]), name)) if base else None
    status = _status(verdicts)
    # A rank is a cost claim, which a row with no verdict can't support.
    rank = 1 + sum(_key(cells[b]) < _key(cells[router]) for b in base) \
        if router in cells and base and status != "REFUSED" else None
    for cell in cells.values():
        del cell["_cps"], cell["_rate"]
    return {"class": cls, "configs": cells, "verdicts": verdicts, "differences": differences,
            "pass_differences": pass_differences, "status": status, "rank": rank, "best_baseline": best,
            "reason": "no router or baseline runs" if not verdicts else _verdict_text(verdicts),
            "quality": _quality(cells, runs, forced or {})}


def summarize(rows: list[dict], *, routers: frozenset[str], router: str = "plugin", draws: int = 2000,
              seed: int = 0, session: str = "", forced: dict | None = None) -> list[dict]:
    """One row per task class."""
    order = {name: i for i, name in enumerate(run.CLASSES)}
    return [_summary(cls, [r for r in rows if r["class"] == cls], routers=routers, router=router, draws=draws,
                     seed=seed, session=session, forced=forced)
            for cls in sorted({r["class"] for r in rows}, key=lambda c: (order.get(c, len(order)), c))]


def overall(rows: list[dict], *, routers: frozenset[str], router: str = "plugin", draws: int = 2000,
            seed: int = 0, session: str = "", forced: dict | None = None) -> dict | None:
    """One row over every task in a table, the tasks of all its classes resampled together, so a plan with one task
    per class can still show the margin."""
    return _summary(ALL, rows, routers=routers, router=router, draws=draws, seed=seed, session=session,
                    forced=forced) if rows else None


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
    configs = []
    for row in summary:
        configs += [c for c in row["configs"] if c not in configs]
    lines = ["| Task class | " + " | ".join(configs) + " | Rank | Status |",
             "| :- | " + " | ".join(":-" for _ in configs) + " | -: | :- |"]
    for row in summary:
        cells = [_cell_text(row["configs"][c]) if c in row["configs"] else "not run" for c in configs]
        lines.append(f"| {row['class']} | " + " | ".join(cells) + f" | {row['rank'] or 'n/a'} | {LABELS[row['status']]} |")
    return [*lines, "", *[f"- {row['class']}: {row['reason']}." for row in summary]]


def _group(r: dict) -> str:
    """A row's table: its session model, and its arm when the prompt wasn't the one-subagent hand-off."""
    model, arm = r.get("main_model") or "unknown", r.get("arm") or "delegate"
    return model if arm == "delegate" else f"{model}, {arm} arm"


def sessions(results: Results, router: str | None = None,
             legacy: bool = False) -> list[tuple[str, str, list[dict], dict | None, list[dict]]]:
    """One summary per session model and prompt arm, since a configuration costs differently on each, with its
    class rows and its all-tasks row. Its router is `router`, or else the configuration configs.json marks
    `judged`; the rows below schema 3 (`legacy`) are always judged on LEGACY_ROUTER. A configuration that sets its
    own session model runs the same in any batch, so it joins every table of its arm."""
    rows = results.legacy if legacy else results.rows
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r["config"] not in results.own_model:
            groups.setdefault(_group(r), []).append(r)
    for r in rows:
        if r["config"] in results.own_model:
            arm = r.get("arm") or "delegate"
            mine = [g for name, g in groups.items() if (name.endswith(f", {arm} arm") or arm == "delegate"
                                                         and not name.endswith(" arm"))]
            for group in mine or [groups.setdefault(_group(r), [])]:
                group.append(r)
    chosen = LEGACY_ROUTER if legacy else router or next((c["id"] for c in results.configs if c.get("judged")), "plugin")
    out = []
    for model, group in sorted(groups.items()):
        kwargs = {"routers": results.routers, "router": chosen, "session": model, "forced": results.forced}
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


def render_sessions(parts: list[tuple], meta: dict, routers: frozenset[str], own_model: frozenset[str] = frozenset(),
                    legacy: list[tuple] = ()) -> str:
    body = []
    for title, group in (("Results", parts), ("Before the sandbox", legacy)):
        for name, router, summary, every, rows in group:
            model, _, arm = name.partition(", ")
            own = sorted({r["config"] for r in rows if r["config"] in own_model})
            but = f", except {', '.join(f'`{c}`' for c in own)}, which sets its own" if own else ""
            prompt = f" The prompt is the {arm}: the brief alone, with no order to delegate." if arm else ""
            old = (" These runs predate the sandboxed harness (row schema below 3), so no row here has a verdict or a "
                   "rank. Their router runs loaded an older design of Model Router, which read each subagent's brief "
                   "and chose the model itself; the router now keeps the model and effort each call names.") \
                if group is legacy else ""
            body += [f"## {title}: session model `{model}`" + (f", {arm}" if arm else ""), "",
                     f"Every run here had `{model}` as its session model{but}; router: `{router}`.{prompt}{old}", "",
                     *_table(summary + ([every] if every else [])), "", *_quality_lines(summary), "",
                     *_metrics(summary, rows, routers), ""]
    return "\n".join([meta.get("header", "# Benchmarks"), "", *body, meta.get("body", "")]).rstrip() + "\n"


def _batch(path: Path) -> str:
    """A raw file's batch: its name without the date and the task class, so m6-05-bugfix-2026-11-22 is m6-05."""
    name = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", path.stem)
    return next((name[:-len(cls) - 1] for cls in run.CLASSES if name.endswith(f"-{cls}")), name)


def _jsonl(paths) -> list[dict]:
    rows = []
    for p in sorted(paths):
        for number, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            try:
                rows += [json.loads(line)] if line.strip() else []
            except ValueError as exc:
                raise SystemExit(f"{p}:{number}: not a JSON line ({exc})") from None
    return rows


@dataclass(frozen=True)
class Results:
    files: list[Path]
    rows: list[dict]
    stale: list[dict]
    broken: list[dict]
    aside: list[tuple[str, dict]]
    archive: list[tuple[str, list[dict], int]]
    configs: list[dict]
    tasks: list[dict]
    registry: Registry
    routers: frozenset[str]
    own_model: frozenset[str]
    forced: dict
    legacy: list[dict] = dataclasses.field(default_factory=list)  # rows below schema 3, in their own tables


def default_paths() -> list[Path]:
    return sorted(RESULTS.glob("*.jsonl"))


def router_ids(configs: list[dict]) -> frozenset[str]:
    return frozenset(c["id"] for c in configs if c.get("plugin")) | frozenset(ARCHIVED_ROUTERS)


def forced_ranks(configs: list[dict], registry_config: Registry) -> dict[str, tuple[float, int]]:
    """Each configuration that forces every subagent onto one model, ranked by that model's strength in the
    registry, then by the effort it sets (none ranks below low)."""
    out = {}
    for c in configs:
        env = c.get("env") or {}
        if env.get("CLAUDE_CODE_SUBAGENT_MODEL_FORCE") and env.get("CLAUDE_CODE_SUBAGENT_MODEL"):
            model = registry_config.family(env["CLAUDE_CODE_SUBAGENT_MODEL"])
            effort = env.get("CLAUDE_CODE_SUBAGENT_EFFORT")
            out[c["id"]] = (registry_config.strength(model),
                            mrconfig.EFFORTS.index(effort) if effort in mrconfig.EFFORTS else -1)
    return out


def load(paths: list[Path]) -> Results:
    """Everything the page is built from, read once. The order and repeats of `paths` don't matter. A row on an
    earlier revision of its task isn't comparable with later runs of it, so it sits in `stale`, not `rows`, a
    row whose pass check broke sits in `broken`, and a row below schema 3, which ran with Bash on an exact
    allowlist, sits in `legacy`."""
    files = sorted({p.resolve() for p in paths})
    tasks = run.load_tasks()
    revisions = {t["id"]: t.get("revision", 1) for t in tasks}
    configs = run.load_configs()
    # A configuration renamed since its rows ran keeps its old id in `was`.
    renamed = {c["was"]: c["id"] for c in configs if c.get("was")}
    every = [dict(row, _batch=row.get("batch") or _batch(path), config=renamed.get(row["config"], row["config"]))
             for path in files for row in _jsonl([path])]
    old = [r.get("task_revision", 1) < revisions.get(r["task"], 1) for r in every]
    stale = [r for r, gone in zip(every, old) if gone]
    # A run whose pass check broke has no verdict: it was neither solved nor failed.
    bad_check = [(r.get("grade") or {}).get("verdict") == "broken" for r in every]
    broken = [r for r, gone, bad in zip(every, old, bad_check) if bad and not gone]
    counted = [r for r, gone, bad in zip(every, old, bad_check) if not (gone or bad)]
    rows = [r for r in counted if r.get("schema", 1) >= run.ROW_SCHEMA]
    legacy = [r for r in counted if r.get("schema", 1) < run.ROW_SCHEMA]
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
    registry_config = mrconfig.shipped()
    return Results(files, rows, stale, broken, aside, archive, configs, tasks, registry_config, router_ids(configs),
                   frozenset(c["id"] for c in configs if c.get("model")), forced_ranks(configs, registry_config),
                   legacy)


def _lacks(row: dict, key: str) -> bool:
    """A missing key means an older harness only on a row written before ROW_SCHEMA, whose schema says so."""
    return row.get("schema", 1) < run.ROW_SCHEMA and key not in row


def _notes(results: Results) -> list[str]:
    """Page lines on runs set aside, router spend by agent type, and the batches each configuration's rows pool."""
    rows, found = results.rows, results.aside
    own = {"plugin_not_loaded": "router run(s) are set aside, outside the results, because the plugin did not load: "
                                "the run's `system/init` event didn't list it or reported a plugin error "
                                "(https://code.claude.com/docs/en/headless)",
           "no_start": "run(s) are set aside, outside the results, because `claude` stopped before its stream began "
                       "(no `system/init` event and no result), as when the sandbox can't start",
           "off_pin": "run(s) of a configuration with pinned agents are set aside, outside the results, because they "
                      "used none of those agents, another one, or a model or effort set on the Agent call",
           "router_silent": "router run(s) are set aside, outside the results, because a subagent ran but the "
                            "router's ledger holds no spawn row, so its hooks never acted",
           "options_not_applied": "router run(s) are set aside, outside the results, because their ledger holds a row "
                                  "their configuration's plugin options rule out"}
    aside = [row for _, row in found if row.get("limited_reason") not in own]
    where = " and ".join(f"`bench/results/{sub}/`" for sub in sorted({sub for sub, _ in found}))
    lines = []
    for reason, text in own.items():
        n = sum(row.get("limited_reason") == reason for _, row in found)
        if n:
            lines.append(f"- {n} {text}. Their raw lines are under `bench/results/limited/`.")
    if aside:
        reasons = [r.get("limited_reason") or ("network" if "infra_reason" in r else "limit") for r in aside]
        again = sum(any(o["task"] == r["task"] and o["config"] == r["config"] and o["started_at"] > r["started_at"]
                        for o in rows) for r in aside)
        parts = [f"{reasons.count('network')} lost the network"] * ("network" in reasons) + \
                [f"{reasons.count('limit')} hit a rate or usage limit"] * ("limit" in reasons) + \
                [f"{reasons.count('api_error')} got an API error before any spend (a bad key, no credit or a model "
                 "the account can't use)"] * ("api_error" in reasons)
        lines.append(f"- {len(aside)} run(s) are set aside, outside the results, because the API never got to the "
                     f"task: {', '.join(parts)}. {again} of them were run again later on the same task and "
                     "configuration, and none counts for or against a configuration. Their raw lines, with the reason, "
                     f"are under {where}.")
    spend: dict[tuple, dict[str, float]] = {}
    counted: dict[tuple, int] = {}
    for r in rows:
        if isinstance(r.get("agent_costs"), dict):
            key = (r["config"], r.get("main_model") or "unknown", r.get("schema", 1) < run.ROW_SCHEMA)
            counted[key] = counted.get(key, 0) + 1
            for who, usd in r["agent_costs"].items():
                spend.setdefault(key, {}).setdefault(who, 0.0)
                spend[key][who] += usd
    if spend:
        def share(key: tuple) -> str:
            total = sum(spend[key].values()) or 1
            order = sorted(spend[key].items(), key=lambda kv: (kv[0] != "main", -kv[1]))
            runs = counted[key]
            era = " below schema 3" if key[2] else ""
            return f"`{key[0]}`{era} on `{key[1]}` ({runs} run{'s' * (runs != 1)}): " + ", ".join(
                f"{'main loop' if who == 'main' else f'`{who}`'} ${usd:.4f} ({usd / total:.0%})" for who, usd in order)
        lines.append("- Router spend by agent type, from the router's ledger (`agent_costs`), over the runs that "
                     "record it: " + "; ".join(share(c) for c in sorted(spend)) + ".")
    if results.broken:
        names = sorted({r["task"] for r in results.broken})
        lines.append(f"- {len(results.broken)} run(s) (${sum(r['total_cost_usd'] for r in results.broken):.2f}) are "
                     "set aside, though they stay in the results files: the pass check of "
                     + ", ".join(f"`{n}`" for n in names) + " broke on them (exit code 2, a bug in the check), so "
                     "none counts as solved or failed. Each raw line's `grade` has the check's error.")
    for task in results.tasks:
        old = [r for r in results.stale if r["task"] == task["id"]]
        if old:
            changes = "; ".join(f"revision {n}: {why}" for n, why in sorted(task.get("revisions", {}).items()))
            lines.append(f"- `{task['id']}` is at revision {task['revision']}; {len(old)} of its runs "
                         f"(${sum(r['total_cost_usd'] for r in old):.2f}) ran on an earlier revision ({changes}), so "
                         "they are left out of every table.")
    unclocked = sum(1 for r in rows if _lacks(r, "wall_ms"))
    if unclocked:
        lines.append(f"- {unclocked} run(s) here carry no `wall_ms`, the runner's own clock around the `claude` "
                     "process, so their latency is n/a. Their `duration_ms` is Claude Code's figure, and it isn't "
                     "taken as wall time: the docs list the wait for background subagents in `duration_api_ms`, not "
                     "in `duration_ms` (https://code.claude.com/docs/en/agent-sdk/cost-tracking.md), and on many "
                     "rows here it falls well short of the gap to the next run's start (measured in "
                     "`docs/TEST_RESULTS.md`).")
    unrouted = sum(1 for r in rows if r["config"] in results.routers and _lacks(r, "routed"))
    if unrouted:
        lines.append(f"- {unrouted} router run(s) here carry no `routed`, the router's spawn decisions: they were "
                     "recorded before the runner kept them. Their accuracy by level and escalation count as n/a, "
                     "not as zero.")
    eras: dict[tuple, list[dict]] = {}
    for r in rows:
        eras.setdefault((r["config"], r.get("schema", 1) < run.ROW_SCHEMA), []).append(r)
    if eras:
        counts = {key: bash_denials(group) for key, group in sorted(eras.items())}
        lines.append("- Runs with a Bash denial, by configuration: " + "; ".join(
            f"`{c}`{' below schema 3' if old else ''} {k} of {n}" for (c, old), (k, n) in counts.items()) + ".")
    managed = sum(bool(r.get("managed_settings")) for r in rows)
    if managed:
        lines.append(f"- {managed} run(s) here ran on a machine with managed settings files or a macOS plist, which "
                     "a run loads whatever its own settings say; each raw line's `managed_settings` names them.")
    batches: dict[str, dict[str, int]] = {}
    for r in rows:
        batches.setdefault(r["config"], {}).setdefault(r.get("_batch", "?"), 0)
        batches[r["config"]][r.get("_batch", "?")] += 1
    lines.append("- A verdict compares the router only with a baseline that ran in the same batches, since pass "
                 "rates and the prompt cache drift between batches. A row without a `batch` field takes its batch "
                 "from its results file's name, less the date and the task class. Batches, by configuration: "
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
        unnamed = sum(_lacks(r, "task_revision") for r in kept)
        if unnamed:
            reasons.append(f"{unnamed} were recorded while the run's folder name held its task id")
        listed = (", ".join(reasons[:-1]) + ", and " + reasons[-1]) if len(reasons) > 1 else "".join(reasons)
        lines.append(f"- `bench/results/{folder}/` holds {len(kept)} rows left out "
                     "of every table" + (f", plus {aside} set-aside line{'s' * (aside != 1)} under its `infra/` and "
                                         "`limited/`" if aside else "") + (f": {listed}." if listed else "."))
    return lines + [""] if lines else []


def _described(config: str, results: Results) -> str:
    """A configuration as its rows ran it: configs.json says what a current row ran, and a router row below schema
    3 ran the build its commit names."""
    router = config in results.routers
    parts = []
    if not router or any(r["config"] == config for r in results.rows):
        parts += [c["description"] for c in results.configs if c["id"] == config][:1]
    old = [r for r in results.legacy if r["config"] == config] if router else []
    if old:
        builds = sorted({f"at `{r['plugin_ref']}`" if r.get("plugin_ref") else
                         f"from the checkout at `{(r.get('bench_commit') or '?')[:7]}`" for r in old})
        parts.append(f"Model Router loaded {' and '.join(builds)}, in its {len(old)} rows below schema 3")
    return "; ".join(parts) or "?"


def _page(results: Results, summary: list[dict]) -> dict:
    current = results
    results = dataclasses.replace(results, rows=results.rows + results.legacy)
    rows, files, tasks = results.rows, results.files, results.tasks
    total = sum(r["total_cost_usd"] for r in rows)
    versions = sorted({r["claude_version"] for r in rows})
    main = sorted({r["main_model"] for r in rows if r["config"] not in results.own_model})
    dates = sorted(r["started_at"][:10] for r in rows)
    used = sorted({r["config"] for r in rows})
    models: dict[str, list[float]] = {}
    agents: dict[tuple, dict[str, int]] = {}  # by configuration and whether the row is below schema 3
    for r in rows:
        for name, usage in r["models"].items():
            models.setdefault(name, []).append(usage.get("costUSD") or 0.0)
        started = r.get("agents_started") if "agents_started" in r else (r.get("subagents") or {}).get("by_type")
        era = (r["config"], r.get("schema", 1) < run.ROW_SCHEMA)
        for name, count in (started or {}).items():
            agents.setdefault(era, {}).setdefault(name, 0)
            agents[era][name] += count
    shapes = {r.get("shape", "single") for r in rows}
    uncovered = [name for shape, name in (("fanout", "fan-out"), ("plan-build", "plan-then-build"), ("redo", "redo"))
                 if shape not in shapes] + ["workflow", "team"]
    predate = sum(1 for r in rows if _lacks(r, "plugin_state"))
    unsaved = sum(1 for r in rows if _lacks(r, "sessions_saved"))
    borrowed = sum(1 for r in rows if r.get("plugin_state") is not None and _lacks(r, "plugin_data_own"))
    allowlisted = sum(1 for r in rows if r.get("schema", 1) < 3)
    since = f"A raw line at row schema 3 or later ({len(rows) - allowlisted} of the {len(rows)} here)"
    saturated = sum(bool(row["quality"]["saturated"]) for row in summary)
    counted = sorted({r["task"] for r in rows})
    brief = {t["id"]: t for t in tasks}
    header = "\n".join([
        "# Benchmarks", "",
        f"Cost and quality of Model Router against Claude Code without it. {len(rows)} runs, "
        f"${total:.2f} at list price, on {dates[0] if dates else 'n/a'}"
        + (f" to {dates[-1]}" if dates and dates[-1] != dates[0] else "") + "."
        + (f" {'All' if not current.rows else len(current.legacy)} of them ran before Bash ran in the sandbox (row "
           "schema below 3). They sit in their own tables, under \"Before the sandbox\", with no verdict or rank."
           if current.legacy else ""), "",
        "No class here has a verdict against any baseline: the runs can't support one (each class line says why). "
        "Claim nothing from them beyond what the raw data shows."
        if summary and all(row["status"] == "REFUSED" for row in summary) else
        "Claim \"cheaper\" only against a baseline the router has a WIN against, and link its raw data.",
        *(["", f"{saturated} of {len(summary)} class rows below are saturated: the weakest forced baseline solved "
              "as many runs as the strongest, within noise, so these tasks can't tell models apart, and an equal "
              "pass rate there says nothing about quality."] if saturated else []),
    ])
    body = "\n".join([
        "## How to read it", "",
        "- A task counts as solved only when its pass check passes. A wrong answer is a failure, the router's too.",
        "- Cost per solved task is a configuration's total spend on a task class divided by the runs it solved, "
        "so failed runs count against it.",
        "- The cells show each configuration's own figures: exact 95% binomial (Clopper-Pearson) pass-rate "
        "intervals, and 95% percentile bootstrap intervals of cost per solved task over runs (2,000 resamples, each "
        "cell from a generator seeded by its session model, class and configuration). No verdict rests on them.",
        f"- Verdicts, one per baseline, by a rule fixed before any run. The router's runs and the baseline's are "
        "paired by task. Each of 2,000 resamples draws the row's tasks with replacement, then each drawn task's "
        "runs with replacement, so the interval reflects how few tasks there are as well as how few runs. For the "
        "pass rate, each task's runs get one solved and one failed run added, so a cell where every run passed "
        f"still has spread. The non-inferiority margin is {MARGIN:.0%} of pass rate. WIN: the 95% interval of the "
        "router's cost per solved task minus the baseline's is below zero, the lower bound of its pass rate "
        f"minus the baseline's is above minus {MARGIN:.0%}, and the router solved at least the baseline's share of "
        "runs (per-task pass rates averaged), so a saving counts only at an equal or higher pass rate. LOSE: the "
        "router's pass rate is behind beyond noise, "
        "or its cost per solved task is above beyond noise while the upper bound of its pass rate minus the "
        f"baseline's is below plus {MARGIN:.0%}, the mirror of the WIN rule. "
        "Anything else is \"no difference shown\", which is not parity. There is no verdict when the two ran "
        "different tasks or in different batches, when either has a row from before Bash ran in the sandbox (row "
        f"schema below 3), or when more than {DENIAL_SHARE:.0%} of either's runs had a Bash call denied. A row "
        "is LOSE when any baseline beats the router, has no verdict when any comparison has none, is WIN only when "
        "it wins against every baseline, and is otherwise \"no difference shown\".",
        f"- Each table ends with an `{ALL}` row, judged the same way over every task in the table: the tasks of all "
        "its classes are paired and resampled together. A class with one task can't show the margin, so a plan "
        f"with a few tasks per class is judged on its `{ALL}` row.",
        "- A class is saturated when the weakest forced baseline (every subagent on the weakest model) can't be told "
        f"from the strongest by an exact one-sided test (5%, {MIN_RUNS} runs each at least). Then an equal pass rate "
        "says nothing about quality. Runs on an earlier revision of a task are left out of every table.",
        "- Rank is the router's place among the baselines by cost per solved task, 1 being cheapest, and n/a on a "
        "row with no verdict. A router variant (another configuration that loads the plugin, or one archived) has "
        "its own column but is never a baseline.",
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
        "come from the run's `routed` line, and n/a means no run in the cell has it.", "",
        "## Setup", "",
        f"- Claude Code: {', '.join(versions) or 'n/a'}. Session models: {', '.join(main) or 'n/a'} (`--model`), "
        "one results table each, and one per prompt arm. Inside a table every configuration that sets no model of "
        "its own ran on that session model. A configuration that sets its own model is shown in every table of "
        "its arm.",
        "- Configurations run: " + "; ".join(f"`{c}` ({_described(c, current)})" for c in used) + ".",
        "- Models that ran, with Claude Code's list-price cost across all runs: "
        + "; ".join(f"`{name}` ${sum(costs):.4f} over {len(costs)} runs" for name, costs in sorted(models.items()))
        + ". Each run's raw line also carries the registry's prices for those models.",
        "- Agents started, by configuration, counted from the Agent calls in the run's stream (`agents_started`), "
        "or for a row below schema 3 from Claude Code's `subagent_stats`, which no documented result type has: "
        + "; ".join(f"`{c}`{' below schema 3' if old else ''} " + ", ".join(
            f"{name} x{n}" for name, n in sorted(agents.get((c, old), {}).items()))
                    for c, old in sorted({(r["config"], r.get("schema", 1) < run.ROW_SCHEMA) for r in rows})) + ".",
        *_notes(results),
        "- Shape: a single agent. In the `delegate` arm the prompt hands each task to one subagent with the Agent "
        "tool and relays its answer, so the subagent is what each configuration changes. For a configuration that "
        "pins its agents with `--agents` the prompt names them, and a run that used none of them, another agent, "
        "or set a model or effort on the Agent call, which outranks the agent's own "
        "(https://code.claude.com/docs/en/sub-agents), is set aside as `off_pin`. In the `direct` arm the "
        "prompt is the brief alone, as a person would type it; its rows get their own table. A task with a "
        "`shape` in its task.json gets another `delegate` prompt (`run.SHAPES`): `redo` runs the check the brief "
        "names and sends the work back, `fanout` gives each question its own subagent, all started at once, and "
        "`plan-build` hands out the steps in order. Each row records its `shape`; a row without one ran a single "
        "agent.",
        "- Each run starts in a fresh copy of the task's files, outside any repository. From row schema 3 it has a "
        "Claude Code config folder of its own inside that copy (`CLAUDE_CONFIG_DIR`, with "
        "`CLAUDE_CODE_PROJECT_DIR_NAME=run`; https://code.claude.com/docs/en/env-vars), so `~/.claude.json`, user "
        "settings, plugins and transcripts never come from the machine's own. A row below schema 3 used the "
        "machine's config folder. It runs with `--output-format stream-json --verbose`, "
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
        f"`PWD` and `OLDPWD`. Managed settings still load from the machine. {since} lists the managed "
        "settings files and the macOS plist it found in `managed_settings`, but server-managed settings, which "
        "claude.ai sends to an eligible login, and the Windows registry policy can't be seen that way, so an "
        "empty list doesn't prove no policy applied (https://code.claude.com/docs/en/managed-settings.md). Only a "
        "router configuration passes `--plugin-dir`, from a copy of `plugins/model-router` made once when the "
        f"batch starts, or for a `plugin_ref` an export of that commit. {since} records the hash of the plugin it "
        "loaded in `plugin_hash`, null with no plugin. The plugin keeps no copy of its options, so a run of a router "
        "variant is set aside only when its ledger holds a row an option rules out (a `gate` row with `verify_gate` "
        "off, a rewritten spawn with `routing_mode` off); `options_applied` true means no such row, not proof."
        + (f" {allowlisted} run(s) here are below row schema 3: they ran with an exact list of `python3`, "
           "`python`, `tail`, `head` and `echo` command rules instead of the sandbox, which denied many test runs."
           if allowlisted else ""),
        "- The router keeps state (model health and its ledger) in its plugin data folder. "
        "From row schema 3 that folder is inside the run's own config folder; a row below schema 3 had a plugins "
        "root of its own inside its scratch copy (`CLAUDE_CODE_PLUGIN_CACHE_DIR`). Either way every router run "
        "starts from an empty one and its data goes when the scratch copy does. Each raw line records `plugin_data_own` and, for router "
        "runs, hashes of the state files the run started from in `plugin_state`. "
        f"{since} also records the count of ledger rows with event `gate` in `gate_events`."
        + (f" {borrowed} router run(s) here borrowed the shared folder instead: the runner copied it before the "
           "run and put it back after, which also wiped what other sessions wrote there meanwhile." if borrowed else "")
        + (f" {predate} run(s) here predate this: their lines have no `plugin_state`, and their router runs "
           "started from whatever earlier runs and other sessions had left there. They also predate the stricter "
           "`lookup-retry-limit` check, and their `bench_commit` is the "
           "commit the harness ran on top of before it was committed. Newer lines carry `bench_dirty`, true when "
           "`bench/` or the plugin had uncommitted changes." if predate else ""),
        "- From row schema 3, sessions are saved in the run's own config folder, as Claude Code saves them by "
        "default, so the router's Stop check can read the transcripts and see a test run, the subagents' included. "
        "They go with the scratch copy. A row below schema 3 saved them under the machine's `~/.claude/projects/`, "
        "and the runner deleted that run's folder there after it. From row schema 3, a run that failed or had a denied tool call keeps them in `transcripts/` beside "
        "its results file, its raw line naming the folder in `transcripts`."
        + (f" {unsaved} run(s) here ran with `--no-session-persistence`, which saves no transcript, so the Stop "
           "check could never see a test run and asked for a verifier after every edit; their lines have no "
           "`sessions_saved`." if unsaved else ""),
        "- Cost is Claude Code's `total_cost_usd`, which includes subagents and is a client-side estimate at list "
        "price, not a bill (https://code.claude.com/docs/en/agent-sdk/cost-tracking.md). From row schema 3, a run "
        "with no result (a timeout or a crash) is priced from its saved transcripts at list price, each message "
        "once, or charged the whole budget when they hold no usage or a model with no price; `cost_source` says "
        "which. Every run is capped with "
        "`--max-budget-usd`, which a run can pass by up to the cost of one response plus anything that subagents "
        "still running at that moment spend before they stop "
        "(https://code.claude.com/docs/en/agent-sdk/agent-loop.md#budget-headroom). The runner starts a run only "
        "with room for twice its cap under the total, which is a margin, not a bound.",
        "- The prompt cache is shared across runs, so later runs of a configuration may read what earlier ones "
        "wrote. From row schema 3 the runner shuffles the run order with a seed each raw line records "
        "(`order_seed`). Every raw line records its cache tokens.", "",
        "## Task set", "",
        f"The tables count runs of these {len(counted)} tasks:", "",
        *[f"- `{t}` ({brief[t]['class']}): {brief[t]['brief']}" if t in brief else
          f"- `{t}` ({next(r['class'] for r in rows if r['task'] == t)})" for t in counted], "",
        "Defined under `bench/tasks/` with no counted run: "
        + (", ".join(f"`{t['id']}`" for t in tasks if t["id"] not in counted) or "none") + ".", "",
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
        "Defined in `bench/configs.json` but not run here: "
        + (", ".join(f"`{c['id']}`" for c in results.configs if c["id"] not in used) or "none") + ".", "",
        "Not covered yet: the " + ", ".join(uncovered[:-1]) + f" and {uncovered[-1]} shapes; a cold and a warm "
        "prompt cache measured "
        "apart; and router overhead per spawn and per turn. `sonnet-via-opusplan` runs `--model opusplan`, which "
        "in a headless `dontAsk` run never enters plan mode, so it runs Sonnet throughout "
        "(https://code.claude.com/docs/en/model-config.md#opusplan-model-setting, "
        "https://code.claude.com/docs/en/permission-modes.md). The advisor needs the Anthropic API, Claude "
        "Platform on AWS or Claude Models on Google Cloud (https://code.claude.com/docs/en/advisor.md), and the "
        "`fable` and `fable-high` configurations need Fable access.",
    ])
    return {"header": header, "body": body}


def build_page(results: Results, router: str | None = None, current: str | None = None) -> str:
    parts, legacy = sessions(results, router), sessions(results, legacy=True)
    summary = [row for part in (*parts, *legacy) for row in part[2]]
    kept = bool(current and current.startswith(HEAD_START) and HEAD_END in current)
    head = current.split(HEAD_END, 1)[0] + HEAD_END if kept else ""
    return head + render_sessions(parts, _page(results, summary), results.routers, results.own_model, legacy)


OPUS, SONNET = "claude-opus-5-5", "sonnet"  # the session models the docs' prose is about
NEARLY = 0.95  # "solves nearly every run": at least this share of runs
# The configurations the docs' prose names for each session, and what it says about them.
PROSE = {
    OPUS: {"ran": {"defaults", "haiku", "haiku-high", "opus", "opus-high", "fable", "fable-high", "sonnet-via-opusplan",
                   "advisor"},
           "cheaper": ("haiku-high", "sonnet-via-opusplan"), "nearly": ("sonnet-via-opusplan", "haiku-high")},
    SONNET: {"ran": {"defaults", "sonnet-medium", "sonnet-high", "sonnet-via-opusplan"}, "miss": ("design", "sonnet-medium"),
             "nearly": ("defaults", "sonnet-via-opusplan", "sonnet-medium")},
}
FACT_TEXT = {
    "solved_all": "the router and the defaults solved every run",
    "solved_as_many": "the router solved as many runs as the defaults",
    "one_miss": "the router missed one run, in a class where its cheapest baseline solved them all",
    "cheaper_everywhere": "the named cheap baselines cost less than the router in every class",
    "not_cheapest": "the router is not the cheapest configuration over all runs",
    "nearly_all": f"the cheapest baseline over all runs, and each one the docs say solves nearly every run, solved at "
                  f"least {NEARLY:.0%} of them",
    "no_verdict": "no class has a verdict against any baseline",
    "ran": "the configurations that ran are the ones the docs list",
}


def _pooled(rows: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for r in rows:
        cell = out.setdefault(r["config"], {"runs": 0, "solved": 0, "cost_usd": 0.0})
        cell["runs"] += 1
        cell["solved"] += bool(r["passed"])
        cell["cost_usd"] += float(r["total_cost_usd"])
    return out


def _per_solved(cell: dict | None) -> float | None:
    return cell["cost_usd"] / cell["solved"] if cell and cell["solved"] else None


def _below(low: float | None, high: float | None) -> bool:
    return low is not None and high is not None and low < high


def roles(results: Results, router: str, pooled: dict) -> dict:
    """The configurations that play each part in a session: the router, Claude Code's defaults (no plugin, no
    forced model), the baseline with the lowest cost per solved task over all runs, and the strongest forced one."""
    base = [c for c in pooled if c != router and c not in results.routers]
    best = min((c for c in base if pooled[c]["solved"]), key=lambda c: (_per_solved(pooled[c]), c), default=None)
    plain = [c["id"] for c in results.configs if c["id"] in pooled
             and not (c.get("plugin") or c.get("env") or c.get("model") or c.get("args"))]
    return {"router": router if router in pooled else None, "defaults": plain[0] if plain else None, "best": best,
            "strongest": max((c for c in base if c in results.forced), key=lambda c: (results.forced[c], c), default=None)}


def _against(classes: list[dict], router: str, cfg: str) -> dict:
    """How the router's cost stands against one baseline, class by class, where a verdict was given."""
    cps = lambda c, name: c["configs"][name]["cost_per_solved"]  # noqa: E731
    diffs = [(c, c["differences"][cfg]) for c in classes if c["differences"].get(cfg)]
    below = [c for c, (lo, hi) in diffs if hi is not None and hi < 0]
    less = [1 - cps(c, router) / cps(c, cfg) for c in below if cps(c, cfg) and cps(c, router) is not None]
    return {"below": [c["class"] for c in below],
            "level": [c["class"] for c, (lo, hi) in diffs if lo is not None and hi is not None and lo <= 0 <= hi],
            "less": (min(less), max(less)) if less else None,
            "best_in": [c["class"] for c in classes if c["best_baseline"] == cfg and c["status"] == "LOSE"]}


def _behind(classes: list[dict], router: str) -> dict | None:
    """The router's cost per solved task over the cheapest baseline's, as a fraction, in the LOSE classes."""
    cps = lambda c, name: c["configs"][name]["cost_per_solved"]  # noqa: E731
    over = [(cps(c, router) / cps(c, c["best_baseline"]) - 1, c["class"]) for c in classes
            if c["status"] == "LOSE" and c["best_baseline"] and cps(c, router) is not None and cps(c, c["best_baseline"])]
    return {"min": min(over, key=lambda o: o[0]), "max": max(over, key=lambda o: o[0])} if over else None


def facts(session: dict, routers: frozenset[str], forced: dict) -> dict[str, bool]:
    """The yes/no claims the docs' prose makes about a session, each true or false by its rows. Empty for a
    session the prose says nothing about."""
    named = PROSE.get(session["model"])
    if named is None:
        return {}
    classes, pooled, role = session["classes"], session["pooled"], session["roles"]
    router, defaults, best = role["router"], role["defaults"], role["best"]
    row = lambda name: next((c for c in classes if c["class"] == name), None)  # noqa: E731
    cps = lambda c, name: c["configs"][name]["cost_per_solved"] if name in c["configs"] else None  # noqa: E731
    solved = lambda name: name in pooled and pooled[name]["solved"] == pooled[name]["runs"]  # noqa: E731
    out = {
        "nearly_all": best is not None and all(name in pooled and pooled[name]["solved"] >= NEARLY * pooled[name]["runs"]
                                               for name in {best, *named.get("nearly", ())}),
        "no_verdict": bool(classes) and all(c["status"] == "REFUSED" for c in classes),
        "ran": {c for c in pooled if c != router and c not in routers} == named["ran"],
    }
    if "cheaper" in named:
        out["solved_all"] = solved(router) and solved(defaults)
        out["cheaper_everywhere"] = bool(classes) and all(
            n in c["configs"] and router in c["configs"] and _below(cps(c, n), cps(c, router))
            for c in classes for n in named["cheaper"])
        out["not_cheapest"] = _below(_per_solved(pooled.get(best)), _per_solved(pooled.get(router)))
    if "miss" in named:
        name, other = named["miss"]
        ex = row(name)
        mine, theirs = pooled.get(router), pooled.get(defaults)
        out["solved_as_many"] = bool(mine and theirs) and mine["solved"] >= theirs["solved"]
        cell = lambda name: (ex or {}).get("configs", {}).get(name)  # noqa: E731
        out["one_miss"] = bool(mine and cell(router) and cell(other)) and mine["runs"] - mine["solved"] == 1 \
            and cell(router)["solved"] < cell(router)["runs"] and ex["best_baseline"] == other \
            and cell(other)["solved"] == cell(other)["runs"]
    return out


def broken_facts(data: dict) -> list[str]:
    """One line for each fact in `data` (from `figures`) that is false."""
    return [f"{s['model']}: {FACT_TEXT[name]} is false now" for s in data["sessions"]
            for name, ok in s["facts"].items() if not ok]


def figures(results: Results, router: str | None = None, legacy: bool = False) -> dict:
    """The page's figures as data, for the docs: per session model, each table's classes, the figures over all
    runs, the roles (router, defaults, best, strongest), how the router stands against each baseline, and the
    facts the docs' prose relies on. `legacy` reads the rows below schema 3, the run the docs quote today."""
    rows = results.legacy if legacy else results.rows
    dates = sorted(r["started_at"][:10] for r in rows)
    out = []
    for model, chosen, classes, _, group in sessions(results, router, legacy):
        pooled = _pooled(group)
        role = roles(results, chosen, pooled)
        ref = next((c.get("plugin_ref") for c in results.configs if c["id"] == chosen), None)
        baselines = sorted({cfg for c in classes for cfg in c["verdicts"]})
        pool_router = _per_solved(pooled[chosen]) if chosen in pooled else None
        session = {"model": model, "router": chosen, "plugin_ref": ref, "classes": classes, "pooled": pooled,
                   "roles": role, "against": {cfg: _against(classes, chosen, cfg) for cfg in baselines},
                   "behind": _behind(classes, chosen),
                   "pooled_less": {cfg: 1 - pool_router / _per_solved(pooled[cfg]) for cfg in baselines
                                   if pool_router is not None and cfg in pooled and pooled[cfg]["solved"]},
                   "saturated": sum(bool(c["quality"]["saturated"]) for c in classes)}
        out.append({**session, "facts": facts(session, results.routers, results.forced)})
    return {"runs": len(rows), "usd": sum(float(r["total_cost_usd"]) for r in rows),
            "dates": [dates[0], dates[-1]] if dates else [], "claude_versions": sorted({r["claude_version"] for r in rows}),
            "per_cell": sorted({cell["runs"] for s in out for c in s["classes"] for cell in c["configs"].values()}),
            "tasks": len({r["task"] for r in rows}), "tasks_total": len(results.tasks),
            "sessions": out}


def check(results: Results, out: Path, current: str | None, router: str | None = None) -> list[str]:
    """Why `out`, whose text is `current`, doesn't match the results: a stale page, or a fact the docs state
    that the rows no longer bear out."""
    stale = [f"{out}: out of date, run python3 bench/analyze.py bench/results/*.jsonl --out {out}"] \
        if current != build_page(results, router, current) else []
    return stale + broken_facts(figures(results, legacy=True))  # the docs' prose quotes the stored run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--router", help="the router configuration the tables judge (default: the one configs.json "
                                             "marks judged); the rows below schema 3 are always judged on "
                                             f"{LEGACY_ROUTER}")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="fail when --out differs from the page the results give, or a fact the docs state about the rows is false")
    args = parser.parse_args(argv)
    try:
        # As text, so a checkout that turned the page's line endings to CRLF still matches.
        current = args.out.read_text(encoding="utf-8") if args.out.is_file() else None
    except (OSError, UnicodeDecodeError):
        current = None
    results = load(args.results)
    if not args.check:
        args.out.write_text(build_page(results, args.router, current), encoding="utf-8")
        return 0
    problems = check(results, args.out, current, args.router)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
