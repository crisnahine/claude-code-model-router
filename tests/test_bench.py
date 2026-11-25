"""Tests for the cost and quality benchmark under bench/. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import configparser
import contextlib
import dataclasses
import fnmatch
import importlib.util
import io
import json
import math
import os
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "bench" / "tasks"))
import analyze  # noqa: E402
import overhead  # noqa: E402
import passcheck  # noqa: E402
import run  # noqa: E402


def gradable_tasks():
    """The tasks whose checks can run on this system: a posix_only task needs POSIX symlinks, which Windows lacks."""
    return [t for t in run.load_tasks() if not (t.get("posix_only") and sys.platform == "win32")]


OPUS = "claude-opus-5-5"


def full_row(**changes):
    """A row as run.record writes it, from a real result line, with `changes` applied. A row of a task in bench/tasks
    carries today's check hash, as a run of it would."""
    result = json.loads(FIXTURE.read_text(encoding="utf-8"))
    row = dict(run.record(Recorder.task, Recorder.config, Recorder.options, result,
                          Recorder.facts), **changes)
    return graded_today(row) if "grade" not in changes else row


def graded_today(row):
    check = run.BENCH / "tasks" / row["task"] / "check.py"
    return dict(row, grade=dict(row["grade"], check_hash=run.check_hash(check))) if check.is_file() else row


def rows(config, cls, costs, passes, task=None, **extra):
    return [{"config": config, "class": cls, "task": task or f"{cls}-1", "total_cost_usd": c, "passed": p,
             "schema": run.ROW_SCHEMA, **extra} for c, p in zip(costs, passes)]


def spread(config, cls, tasks, runs, cost, solved=None, **extra):
    """`runs` rows on each of `tasks` tasks, all solved unless `solved` says how many per task."""
    solved = runs if solved is None else solved
    return [r for t in range(tasks)
            for r in rows(config, cls, [cost] * runs, [True] * solved + [False] * (runs - solved), f"{cls}-{t}", **extra)]


class Analysis(unittest.TestCase):
    def summary(self, data, draws=500):
        return {s["class"]: s for s in analyze.summarize(data, routers=frozenset({"plugin"}), router="plugin",
                                                       draws=draws, seed=1)}

    def verdict(self, data, baseline="defaults", cls="lookup"):
        return self.summary(data)[cls]["verdicts"][baseline]

    def test_pass_rate_and_cost_per_solved_task(self):
        cell = self.summary(rows("plugin", "lookup", [1, 1, 1, 1], [True, True, False, True]))["lookup"]["configs"]["plugin"]
        self.assertEqual((cell["runs"], cell["solved"]), (4, 3))
        self.assertAlmostEqual(cell["pass_rate"], 0.75)
        self.assertAlmostEqual(cell["cost_per_solved"], 4 / 3)

    def test_nothing_solved_has_no_cost_per_solved_task(self):
        cell = self.summary(rows("plugin", "lookup", [0.2, 0.3], [False, False]))["lookup"]["configs"]["plugin"]
        self.assertIsNone(cell["cost_per_solved"])

    def test_every_baseline_gets_its_own_verdict(self):
        data = (spread("plugin", "lookup", 6, 12, 0.10) + spread("defaults", "lookup", 6, 12, 0.50)
                + spread("haiku-high", "lookup", 6, 12, 0.05))
        row = self.summary(data)["lookup"]
        self.assertEqual({b: v["verdict"] for b, v in row["verdicts"].items()}, {"defaults": "WIN", "haiku-high": "LOSE"})
        self.assertEqual((row["status"], row["rank"]), ("LOSE", 2))

    def test_a_win_needs_cost_below_and_the_pass_rate_within_the_margin(self):
        v = self.verdict(spread("plugin", "lookup", 6, 12, 0.10) + spread("defaults", "lookup", 6, 12, 0.50))
        self.assertEqual(v["verdict"], "WIN")
        self.assertLess(v["cost_ci"][1], 0)
        self.assertGreater(v["pass_ci"][0], -analyze.MARGIN)

    def test_a_cell_where_everything_passed_does_not_show_the_margin_on_its_own(self):
        # A percentile bootstrap of 10 of 10 has no spread; one solved and one failed run added per task gives it some.
        v = self.verdict(rows("plugin", "lookup", [0.1] * 10, [True] * 10) + rows("defaults", "lookup", [0.5] * 10, [True] * 10))
        self.assertEqual(v["verdict"], "NONE")
        self.assertLess(v["pass_ci"][0], -analyze.MARGIN)

    def test_solving_fewer_is_never_a_win(self):
        # Inside the margin and far cheaper, but one run short of a baseline that solved them all.
        for runs in (12, 24, 60):
            with self.subTest(runs=runs):
                ours = spread("plugin", "lookup", 6, runs, 0.03)
                ours[0]["passed"] = False
                v = self.verdict(ours + spread("defaults", "lookup", 6, runs, 0.10))
                self.assertNotEqual(v["verdict"], "WIN")
                self.assertIn("smaller share", v["reason"])
        v = self.verdict(spread("plugin", "lookup", 6, 12, 0.03) + spread("defaults", "lookup", 6, 12, 0.10))
        self.assertEqual(v["verdict"], "WIN")

    def test_one_win_and_one_undecided_baseline_leave_the_class_undecided(self):
        self.assertEqual(analyze._status({"a": {"verdict": "WIN"}, "b": {"verdict": "NONE"}}), "NONE")
        self.assertEqual(analyze._status({"a": {"verdict": "WIN"}, "b": {"verdict": "WIN"}}), "WIN")

    def test_a_cost_bound_with_no_value_decides_nothing(self):
        # The router dear and right, the baseline cheap and wrong on every run: every draw's gap is minus infinity.
        data = spread("plugin", "lookup", 2, 2, 1.50) + spread("haiku-high", "lookup", 2, 2, 0.10, solved=0)
        v = self.verdict(data, "haiku-high")
        self.assertEqual((v["verdict"], v["cost_ci"]), ("NONE", (None, None)))
        self.assertIn("cost per solved task n/a to n/a", v["reason"])
        self.assertEqual(analyze._judge("b", (-0.05, 0.05), (math.inf, math.inf), False, (9, 10, 9, 10))[0], "NONE")
        self.assertEqual(analyze._judge("b", (-0.05, 0.05), (-math.inf, -0.1), False, (9, 10, 9, 10))[0], "WIN")
        self.assertEqual(analyze._judge("b", (-0.05, 0.05), (0.1, math.inf), False, (9, 10, 9, 10))[0], "LOSE")
        self.assertIn("A cost bound that is n/a decides nothing", analyze.build_page(analyze.load([])))

    def test_behind_beyond_noise_loses_even_inside_the_margin(self):
        self.assertEqual(analyze._judge("defaults", (-0.08, -0.01), (-0.05, -0.01), False, (9, 10, 9, 10))[0], "LOSE")

    def test_a_pass_rate_the_exact_test_puts_behind_loses_where_the_added_runs_hide_it(self):
        # The solved and failed runs added per task shrink every pass-rate gap by n / (n + 2).
        v = self.verdict(spread("plugin", "lookup", 5, 3, 0.1, solved=2) + spread("defaults", "lookup", 5, 3, 0.5))
        self.assertGreaterEqual(v["pass_ci"][1], 0)
        self.assertEqual(v["verdict"], "LOSE")
        self.assertIn("solved 10 of 15 runs against 15 of 15 (exact one-sided p = 0.021)", v["reason"])
        self.assertIn("an exact one-sided test", analyze.build_page(analyze.load([])))

    def test_a_baseline_with_more_than_a_quarter_of_runs_denied_gets_no_verdict(self):
        theirs = spread("defaults", "lookup", 6, 12, 0.50)
        for r in theirs[:19]:
            r["permission_denials"] = [{"tool": "Bash", "input": "python3 -m unittest"}]
        v = self.verdict(spread("plugin", "lookup", 6, 12, 0.10) + theirs)
        self.assertEqual(v["verdict"], "REFUSED")
        self.assertIn("Bash denied in 19 of 72 `defaults` runs", v["reason"])

    def test_a_class_with_no_verdict_has_no_rank(self):
        apart = spread("plugin", "lookup", 1, 4, 0.10, batch="b1") + spread("defaults", "lookup", 1, 4, 0.50, batch="b2")
        row = self.summary(apart)["lookup"]
        self.assertEqual((row["status"], row["rank"]), ("REFUSED", None))

    OPUS_PLAN = ("rootcause-wallet-race", "cachefix-stale-across-services", "impl-merge-customers",
                 "finance-ledger-reconcile", "legal-services-agreement", "fanout-three-lookups",
                 "design-cache-invalidation", "security-tar-symlink-chain", "redo-failing-check",
                 "workflow-two-stage-report", "mixed-plan-then-build", "research-study-synthesis", "ops-inbox-triage",
                 "migrate-ledger-v3", "bugfix-statement-queries", "design-checkout-retries")
    FABLE_PLAN = ("rootcause-wallet-race", "impl-merge-customers", "fanout-three-lookups", "design-cache-invalidation")

    def plan_rows(self, tasks, runs, costs):
        """Every run of `tasks` solved, at `costs` a run by configuration."""
        classes = {t["id"]: t["class"] for t in run.load_tasks()}
        return [dict(r, task=task, batch="judged") for task in tasks for config, usd in costs.items()
                for r in rows(config, classes[task], [usd] * runs, [True] * runs)]

    def test_the_documented_plan_can_reach_a_verdict_over_all_tasks(self):
        kwargs = {"routers": frozenset({"plugin"}), "router": "plugin", "session": OPUS}
        cheap, dear = ({"plugin": usd, "defaults": 0.26, "haiku-high": 0.10} for usd in (0.05, 0.30))
        for tasks, runs, verdicts in ((self.OPUS_PLAN, 2, ("NONE", "NONE")), (self.OPUS_PLAN, 5, ("WIN", "LOSE")),
                                      (self.FABLE_PLAN, 5, ("NONE", "NONE"))):
            for costs, verdict in zip((cheap, dear), verdicts):
                with self.subTest(tasks=len(tasks), runs=runs, router=costs["plugin"]):
                    every = analyze.overall(self.plan_rows(tasks, runs, costs), **kwargs)
                    self.assertEqual({v["verdict"] for v in every["verdicts"].values()}, {verdict})
        # The knife edge: a lower bound equal to the margin is not inside it.
        edge = analyze.overall(self.plan_rows(self.OPUS_PLAN[:6], 8, {"plugin": 0.05, "pinned": 0.20}), **kwargs)
        self.assertEqual((edge["verdicts"]["pinned"]["pass_ci"][0], edge["verdicts"]["pinned"]["verdict"]),
                         (-analyze.MARGIN, "NONE"))

    def test_the_readme_runs_the_plan_with_commands_that_work_as_written(self):
        text = (ROOT / "bench" / "README.md").read_text(encoding="utf-8").replace("\\\n", " ")
        commands = [line.strip().rstrip("&").replace('"$n"', "0").replace("$n", "0")
                    for block in re.findall(r"```bash\n(.*?)```", text, re.S) for line in block.splitlines()
                    if "bench/run.py" in line]
        flat = " ".join(" ".join(commands).split())
        for tasks, model in ((self.OPUS_PLAN, "claude-opus-5-5"), (self.FABLE_PLAN, "claude-fable-5-2")):
            self.assertIn(f"--tasks {' '.join(tasks)} --configs plugin defaults haiku-high --runs 1 --first-run 0", flat)
            self.assertRegex(flat, rf"--budget 2 --max-total \d+ --model {model} ")
        bare = {k: v for k, v in os.environ.items() if k not in run.AUTH and not k.startswith("CLAUDE_CODE_USE_")}
        for command in commands:
            argv = shlex.split(command.replace("$HOME", str(Path.home())))
            with self.subTest(command=command[:60]), unittest.mock.patch.dict("os.environ", bare, clear=True), \
                    contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
                self.assertEqual(argv[:2], ["python3", "bench/run.py"])
                run.main(argv[2:])
            # Every option and id parses; the one thing missing is the credential, read from the environment.
            self.assertIn("set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY", err.getvalue())

    def test_the_judged_router_comes_from_configs_json_not_file_order(self):
        data = [dict(r, main_model=OPUS) for r in spread("plugin", "lookup", 2, 4, 0.1)
                + spread("plugin-variant", "lookup", 2, 4, 0.1) + spread("defaults", "lookup", 2, 4, 0.1)]
        base = analyze.load([])
        self.assertEqual([c["id"] for c in base.configs if c.get("judged")], ["plugin"])
        variant_first = [{"id": "plugin-variant", "plugin": True}, *base.configs]
        results = dataclasses.replace(base, rows=data, configs=variant_first)
        self.assertEqual([part[1] for part in analyze.sessions(results)], ["plugin"])
        self.assertEqual([part[1] for part in analyze.sessions(results, "plugin-variant")], ["plugin-variant"])

    def test_behind_beyond_noise_loses_however_cheap(self):
        v = self.verdict(rows("plugin", "lookup", [0.01] * 10, [True] * 2 + [False] * 8)
                         + rows("defaults", "lookup", [0.50] * 10, [True] * 10))
        self.assertEqual(v["verdict"], "LOSE")
        self.assertIn("pass rate", v["reason"])

    def test_a_cheaper_baseline_that_holds_its_pass_rate_wins_over_the_router(self):
        dear = spread("plugin", "lookup", 6, 12, 0.50) + spread("opus", "lookup", 6, 12, 0.10)
        self.assertEqual(self.verdict(dear, "opus")["verdict"], "LOSE")
        few = rows("plugin", "lookup", [0.50] * 12, [True] * 12) + rows("opus", "lookup", [0.10] * 12, [True] * 12)
        self.assertEqual(self.verdict(few, "opus")["verdict"], "NONE")  # its pass rate isn't shown held at 12 runs

    def test_the_verdict_never_says_pass(self):
        data = spread("plugin", "lookup", 6, 12, 0.10) + spread("defaults", "lookup", 6, 12, 0.50)
        page = "\n".join(analyze._table(analyze.summarize(data, routers=frozenset({"plugin"}), draws=200)))
        self.assertNotIn("PASS", page)
        self.assertIn("WIN against `defaults`", page)
        # 10 of 10 against 10 of 10 on one task can't show the margin, so it stays undecided.
        even = rows("plugin", "lookup", [0.1] * 10, [True] * 10) + rows("defaults", "lookup", [0.5] * 10, [True] * 10)
        undecided = analyze._table(analyze.summarize(even, routers=frozenset({"plugin"}), draws=200))
        line = next(line for line in undecided if line.startswith("| lookup |"))
        self.assertTrue(line.endswith("| no difference shown |"), line)
        self.assertIn("lookup: no difference shown against `defaults`", "\n".join(undecided))
        self.assertNotIn("PASS", "\n".join(undecided))

    def test_tasks_are_paired_and_resampled_before_runs(self):
        # The router is cheaper on each task, though the tasks' costs differ ten times over: pairing keeps that apart.
        data = (spread("plugin", "lookup", 1, 12, 0.09) + [dict(r, task="lookup-b") for r in spread("plugin", "lookup", 1, 12, 0.9)]
                + spread("defaults", "lookup", 1, 12, 0.10) + [dict(r, task="lookup-b") for r in spread("defaults", "lookup", 1, 12, 1.0)])
        self.assertLess(self.verdict(data)["cost_ci"][1], 0)
        with unittest.mock.patch.object(analyze.random.Random, "randrange", side_effect=AssertionError):
            self.summary(data)  # randrange has no cross-version guarantee; random() does

    def test_the_same_data_and_seed_give_the_same_intervals(self):
        data = rows("plugin", "lookup", [0.1, 0.3, 0.2, 0.4, 0.1], [True, False, True, True, True])
        self.assertEqual(self.summary(data), self.summary(data))

    def test_another_router_configuration_is_never_a_baseline(self):
        data = (rows("plugin", "bugfix", [0.3] * 10, [True] * 10) + rows("plugin-variant", "bugfix", [0.1] * 10, [True] * 10)
                + rows("defaults", "bugfix", [0.2] * 10, [True] * 10))
        row = {s["class"]: s for s in analyze.summarize(data, routers=frozenset({"plugin", "plugin-variant"}),
                                                       draws=200)}["bugfix"]
        self.assertEqual((row["rank"], set(row["verdicts"])), (2, {"defaults"}))
        self.assertIn("plugin-variant", row["configs"])  # still shown on the page

    def test_cells_on_different_tasks_get_no_verdict(self):
        other = [dict(r, task="lookup-2") for r in rows("defaults", "lookup", [0.5] * 10, [True] * 10)]
        v = self.verdict(rows("plugin", "lookup", [0.1] * 10, [True] * 10) + other)
        self.assertEqual(v["verdict"], "REFUSED")
        self.assertIn("`lookup-1` ran 10 times under `plugin` and 0 under `defaults`, and 1 more", v["reason"])

    def test_two_sides_that_ran_a_task_a_different_number_of_times_get_no_verdict(self):
        # The two behave the same on every task; one ran its cheap tasks more often, the other its dear ones.
        def side(config, cheap, dear):
            return [r for t in range(12) for n, usd in ((cheap, 0.10) if t < 6 else (dear, 1.00),)
                    for r in rows(config, "lookup", [usd] * n, [True] * n, f"lookup-{t}")]
        v = self.verdict(side("plugin", 10, 9) + side("defaults", 9, 10))
        self.assertEqual(v["verdict"], "REFUSED")
        self.assertIn("`lookup-0` ran 10 times under `plugin` and 9 under `defaults`, and 11 more", v["reason"])
        self.assertEqual(self.verdict(side("plugin", 10, 10) + side("defaults", 10, 10))["verdict"], "NONE")
        # The same batches on both sides, but no task's runs share one.
        apart = [dict(r, batch="b1" if int(r["task"][-1]) < 5 else "b2") for r in spread("plugin", "lookup", 10, 10, 0.1)]
        apart += [dict(r, batch="b2" if int(r["task"][-1]) < 5 else "b1") for r in spread("defaults", "lookup", 10, 10, 0.5)]
        v = self.verdict(apart)
        self.assertEqual(v["verdict"], "REFUSED")
        self.assertIn("`lookup-0` in batch `b1` ran 10 times under `plugin` and 0 under `defaults`", v["reason"])

    def test_more_than_a_quarter_of_runs_with_a_bash_denial_gets_no_verdict(self):
        denied = [{"tool": "Bash", "input": "python3 -m unittest"}]
        ours = spread("plugin", "lookup", 6, 12, 0.10)
        for r in ours[:18]:  # 18 of 72 is a quarter, not more
            r["permission_denials"] = denied
        theirs = spread("defaults", "lookup", 6, 12, 0.50)
        self.assertEqual(self.verdict(ours + theirs)["verdict"], "WIN")
        ours[18]["permission_denials"] = [{"tool": "Bash", "input": "pytest"}]
        v = self.verdict(ours + theirs)
        self.assertEqual(v["verdict"], "REFUSED")
        self.assertIn("Bash denied in 19 of 72 `plugin` runs", v["reason"])
        self.assertEqual(analyze.bash_denials(ours), (19, 72))

    def test_a_verdict_never_mixes_plugin_builds_budgets_versions_or_uncommitted_code(self):
        ours, theirs = spread("plugin", "lookup", 6, 12, 0.10), spread("defaults", "lookup", 6, 12, 0.50)
        self.assertEqual(self.verdict(ours + theirs)["verdict"], "WIN")
        # A task may run at its own budget, as long as both sides ran it at that one.
        dear = lambda side: [dict(r, budget_usd=2.0 if r["task"] == "lookup-3" else 0.5) for r in side]  # noqa: E731
        self.assertEqual(self.verdict(dear(ours) + dear(theirs))["verdict"], "WIN")
        cases = (([dict(r, plugin_hash=f"h{i % 2}") for i, r in enumerate(ours)], theirs,
                  "`plugin`'s runs loaded 2 plugin builds (`plugin_hash`)"),
                 (dear(ours), [dict(r, budget_usd=0.5) for r in theirs],
                  "`lookup-3` ran at budget 2.0 under `plugin` and 0.5 under `defaults` (`budget_usd`)"),
                 (ours, [dict(r, claude_version="2.1.333" if r["task"] == "lookup-0" else None) for r in theirs],
                  "`lookup-0` ran at Claude Code None under `plugin` and 2.1.333 under `defaults` (`claude_version`)"),
                 (ours, [dict(r, bench_dirty=i < 2) for i, r in enumerate(theirs)],
                  "2 of the runs ran from code with uncommitted changes (`bench_dirty`)"))
        for mine, yours, why in cases:
            with self.subTest(why=why):
                v = self.verdict(mine + yours)
                self.assertEqual(v["verdict"], "REFUSED")
                self.assertIn(why, v["reason"])

    def test_a_run_the_harness_stopped_counts_as_failed_and_the_page_counts_them(self):
        base = full_row(task="bugfix-median", main_model="sonnet", passed=True, **{"class": "bugfix"})
        older = {k: v for k, v in base.items() if k != "stopped_by"}  # a row from before the field
        data = [dict(older, config="defaults", exit_code=None, subtype=None),
                dict(older, config="defaults", exit_code=1, subtype="error_max_budget_usd"),
                dict(older, config="defaults"), dict(base, config="plugin", stopped_by="budget"), dict(base, config="plugin")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in data), encoding="utf-8")
            loaded = analyze.load([path])
            page = analyze.build_page(loaded)
        self.assertEqual([(r["config"], r["passed"], r.get("stopped_by")) for r in loaded.rows],
                         [("defaults", False, "timeout"), ("defaults", False, "budget"), ("defaults", True, None),
                          ("plugin", False, "budget"), ("plugin", True, None)])
        self.assertIn("- Runs the harness stopped, by configuration: `defaults` 2 of 3 (1 by the budget cap, 1 by the "
                      "timeout); `plugin` 1 of 2 (1 by the budget cap).", page)
        self.assertIn("A run the harness stopped, by the timeout or by the budget cap", page)

    def test_a_baseline_whose_stopped_runs_decide_the_verdict_gets_no_verdict(self):
        # Half the baseline's runs hit the cap: as failures they make the dearer router the cheaper per solved task.
        theirs = spread("defaults", "lookup", 6, 12, 0.20)
        for r in theirs[::2]:
            r.update(passed=False, stopped_by="budget")
        v = self.verdict(spread("plugin", "lookup", 6, 12, 0.30) + theirs)
        self.assertEqual(v["verdict"], "REFUSED")
        self.assertIn("36 of `defaults`'s 72 runs were stopped by the harness (36 by the budget cap); counted as "
                      "solved, the verdict would be LOSE", v["reason"])
        # A few stopped runs that don't decide it leave the verdict, and the router's own count against it.
        dear = spread("defaults", "lookup", 6, 12, 0.50)
        dear[0].update(passed=False, stopped_by="timeout")
        self.assertEqual(self.verdict(spread("plugin", "lookup", 6, 12, 0.10) + dear)["verdict"], "WIN")
        ours = spread("plugin", "lookup", 6, 12, 0.10, solved=6)
        for r in ours:
            r.update(stopped_by=None if r["passed"] else "budget")
        self.assertEqual(self.verdict(ours + spread("defaults", "lookup", 6, 12, 0.50))["verdict"], "LOSE")

    def test_a_current_row_graded_by_another_check_stops_the_page(self):
        task = next(t for t in run.load_tasks() if t["id"] == "lookup-todo-count")
        today = {"verdict": "pass", "reason": "solved", "check_hash": run.check_hash(task["dir"] / "check.py")}
        good = full_row(task=task["id"], task_revision=task["revision"], grade=today)
        other = dict(good, grade=dict(today, check_hash="f" * 16))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.jsonl"
            path.write_text(json.dumps(good) + "\n", encoding="utf-8")
            self.assertEqual(len(analyze.load([path]).rows), 1)
            path.write_text(json.dumps(other) + "\n", encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                analyze.load([path])
            self.assertIn(f"`lookup-todo-count`: a row graded by check ffffffffffffffff, today's is {today['check_hash']}",
                          str(raised.exception))
            path.write_text(json.dumps(dict(other, task_revision=0)) + "\n", encoding="utf-8")
            self.assertEqual(len(analyze.load([path]).stale), 1)  # an earlier revision is left out, not checked

    def test_batches_that_did_not_run_together_get_no_verdict(self):
        ours = spread("plugin", "lookup", 6, 12, 0.10, batch="b1")
        self.assertEqual(self.verdict(ours + spread("defaults", "lookup", 6, 12, 0.50, batch="b1"))["verdict"], "WIN")
        v = self.verdict(ours + spread("defaults", "lookup", 6, 12, 0.50, batch="b2"))
        self.assertEqual(v["verdict"], "REFUSED")
        self.assertIn("`lookup-0` in batch `b1` ran 12 times under `plugin` and 0 under `defaults`", v["reason"])

    def test_pass_rate_intervals_are_exact_binomial(self):
        self.assertAlmostEqual(analyze.exact_interval(10, 10)[0], 0.025 ** 0.1, places=6)
        self.assertEqual(analyze.exact_interval(10, 10)[1], 1.0)
        self.assertEqual(analyze.exact_interval(0, 10)[0], 0.0)
        low, high = analyze.exact_interval(3, 10)  # the textbook Clopper-Pearson interval
        self.assertAlmostEqual(low, 0.0667, places=3)
        self.assertAlmostEqual(high, 0.6525, places=3)

    def quality(self, data):
        summary = analyze.summarize(data, routers=frozenset({"plugin"}), router="plugin", draws=200, seed=1)
        return {s["class"]: s for s in summary}["lookup"]["quality"]

    def test_a_class_where_haiku_high_keeps_up_with_defaults_is_saturated(self):
        data = [r for c, usd in (("plugin", 0.1), ("defaults", 0.3), ("haiku-high", 0.1))
                for r in spread(c, "lookup", 6, analyze.MIN_RUNS, usd)]
        self.assertIs(self.quality(data)["saturated"], True)
        recorded = [full_row(task=r["task"], config=r["config"], total_cost_usd=r["total_cost_usd"],
                             **{"class": "lookup"}) for r in data]
        page = analyze.build_page(dataclasses.replace(analyze.load([]), rows=recorded))
        # The all-tasks row is the one a verdict is read from, so its saturation is printed and counted too.
        self.assertIn("2 of 2 rows below are saturated (1 of 1 `all tasks` rows): on their tasks the cheapest forced "
                      "baseline, `haiku-high` (every subagent on Haiku), wasn't shown to solve fewer runs than Claude "
                      "Code's defaults (`defaults`), so an equal pass rate there says nothing about quality.", page)
        quality = page.split("### Do the tasks tell models apart")[1].split("###")[0]
        # The test is one-sided: it asks only whether haiku-high solved fewer, never whether the gap is chance.
        for row in ("lookup", "all tasks"):
            self.assertIn(f"- {row}: saturated, `haiku-high` solved 60 of 60 and `defaults` 60 of 60 (exact one-sided "
                          "p = 1.000), so `haiku-high` wasn't shown to solve fewer and these tasks can't tell models "
                          "apart.", quality)
        self.assertNotIn("could be chance", page)
        # The margin and the intervals are points of pass rate, never a share of it.
        self.assertIn("its pass rate shown no more than 10 points behind", page)
        self.assertIn("is saturated when an exact one-sided test (5%, at least 10 runs of each) doesn't show "
                      "`haiku-high` (every subagent on Haiku) solving fewer runs than `defaults`", page)
        self.assertRegex(page, r"pass rate -\d+ to \+\d+ points \(95% intervals")
        self.assertNotRegex(page, r"[+-]\d+% to")
        n = analyze.MIN_RUNS
        behind = (rows("plugin", "lookup", [0.3] * n, [True] * n) + rows("defaults", "lookup", [0.4] * n, [True] * n)
                  + rows("haiku-high", "lookup", [0.2] * n, [True] * 4 + [False] * 6))
        self.assertIs(self.quality(behind)["saturated"], False)
        self.assertIsNone(self.quality([r for r in behind if r["config"] != "defaults"])["saturated"])

    def test_a_cells_draws_do_not_depend_on_the_other_cells(self):
        n = analyze.MIN_RUNS
        plugin = rows("plugin", "lookup", [0.1, 0.2, 0.3, 0.4, 0.5, 0.9, 0.15, 0.35, 0.6, 0.7], [True] * n)
        alone = self.summary(plugin)["lookup"]["configs"]["plugin"]
        beside = self.summary(plugin + rows("defaults", "lookup", [0.2, 0.9] * (n // 2), [True] * n))["lookup"]["configs"]["plugin"]
        self.assertEqual(alone["cps_ci"], beside["cps_ci"])
        elsewhere = analyze.summarize(plugin, routers=frozenset({"plugin"}), draws=500, seed=1, session="sonnet")
        self.assertNotEqual(elsewhere[0]["configs"]["plugin"]["cps_ci"], alone["cps_ci"])

    def test_a_task_splits_the_models_when_the_strong_one_solves_most_and_the_weak_one_few(self):
        def task_rows(task, strong, weak):
            return [{"task": task, "config": c, "passed": p} for c, ps in (("defaults", strong), ("haiku-high", weak))
                    for p in ps]
        data = (task_rows("a", [True, True, False], [False, True, False])   # 2 of 3 and 1 of 3: splits
                + task_rows("b", [True, True, True], [True, False, False])  # 1 of 3: splits
                + task_rows("c", [True, False, False], [False, False, False])  # strong under 2 of 3
                + task_rows("d", [True, True, True], [True, True, False]))  # weak over 1 of 3
        self.assertEqual(analyze.splitting_tasks(data), ["a", "b"])
        self.assertIsNone(analyze.splitting_tasks([r for r in data if r["config"] == "defaults"]))

    def test_a_class_with_no_router_or_baseline_has_no_verdict(self):
        row = self.summary(rows("defaults", "lookup", [0.3], [True]))["lookup"]
        self.assertEqual((row["verdicts"], row["status"]), ({}, "REFUSED"))

    @staticmethod
    def page_args(tmp, name="page.md"):
        res = Path(tmp) / "results"
        res.mkdir(exist_ok=True)
        data = rows("plugin", "lookup", [0.1, 0.2], [True, False]) + rows("defaults", "lookup", [0.3, 0.4], [True, True])
        records = [dict(r, main_model="fixture-model", started_at="2026-11-22T10:00:00+00:00", claude_version="2.1.330",
                        models={}) for r in data]
        path = res / "a-lookup-2026-11-22.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        return path, Path(tmp) / name

    def check(self, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = analyze.main([*map(str, args), "--check"])
        return code, err.getvalue()

    def test_check_fails_on_a_stale_page_and_passes_on_a_fresh_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, out = self.page_args(tmp)
            self.assertEqual(analyze.main([str(path), "--out", str(out)]), 0)
            self.assertEqual(self.check(path, "--out", out), (0, ""))
            fresh = out.read_text(encoding="utf-8")
            out.write_text(fresh + "x", encoding="utf-8")
            code, err = self.check(path, "--out", out)
            self.assertEqual(code, 1)
            self.assertIn("page.md: out of date, run python3 bench/analyze.py", err)
            self.assertEqual(out.read_text(encoding="utf-8"), fresh + "x")
            out.unlink()
            self.assertEqual(self.check(path, "--out", out)[0], 1)
            self.assertFalse(out.exists())

    def test_the_page_keeps_the_head_docs_assets_wrote(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, out = self.page_args(tmp)
            out.write_text("<!-- Page head, x -->\nhead\n<!-- End of page head. -->\n\n# old\n", encoding="utf-8")
            analyze.main([str(path), "--out", str(out)])
            page = out.read_text(encoding="utf-8")
            self.assertTrue(page.startswith("<!-- Page head, x -->\nhead\n<!-- End of page head. -->\n\n# Benchmarks\n"))
            self.assertEqual(self.check(path, "--out", out), (0, ""))
            out.unlink()
            analyze.main([str(path), "--out", str(out)])
            self.assertTrue(out.read_text(encoding="utf-8").startswith("# Benchmarks\n"))

    def test_an_unreadable_page_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, out = self.page_args(tmp)
            out.write_bytes(b"\xff\xfe\x00 not utf-8")
            code, err = self.check(path, "--out", out)
        self.assertEqual(code, 1)
        self.assertIn("out of date", err)

    def test_one_results_file_in_two_spellings_counts_its_aside_lines_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self.page_args(tmp)
            (path.parent / "limited").mkdir()
            line = json.dumps({"task": "t", "config": "plugin", "started_at": "2026-11-22T10:00:00+00:00",
                               "limited_reason": "network", "schema": run.ROW_SCHEMA})
            (path.parent / "limited" / "a.jsonl").write_text(line + "\n", encoding="utf-8")
            results = analyze.load([path, path.parent / ".." / path.parent.name / path.name])
        self.assertEqual(len(results.files), 1)
        self.assertEqual(len(results.aside), 1)

    def test_the_task_list_comes_from_the_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, out = self.page_args(tmp)
            analyze.main([str(path), "--out", str(out)])
            page = out.read_text(encoding="utf-8")
        tasks = page.split("## Task set")[1].split("## ")[0]
        self.assertIn("The tables count runs of these 1 tasks:\n\n- `lookup-1` (lookup)", tasks)
        never = next(line for line in tasks.splitlines() if line.startswith("Defined under `bench/tasks/`"))
        self.assertIn("`lookup-todo-count`", never)
        self.assertNotIn("`lookup-1`", never)

    def test_check_reads_the_page_as_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, out = self.page_args(tmp)
            analyze.main([str(path), "--out", str(out)])
            out.write_bytes(out.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
            self.assertEqual(self.check(path, "--out", out), (0, ""))

    def test_argv_order_and_repeats_do_not_change_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, out = self.page_args(tmp)
            b = a.with_name("b-lookup-2026-11-22.jsonl")
            b.write_text(a.read_text(encoding="utf-8").replace("0.3", "0.35").replace("0.1", "0.15"), encoding="utf-8")
            pages = []
            for argv in ([b, a], [a, b], [a, b, a]):
                analyze.main([*map(str, argv), "--out", str(out)])
                pages.append(out.read_text(encoding="utf-8"))
            self.assertEqual(pages[0], pages[1])
            self.assertEqual(pages[1], pages[2])

    def test_a_row_below_the_current_schema_is_refused_naming_its_file_and_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, out = self.page_args(tmp)
            good = path.read_text(encoding="utf-8")
            for name, row in (("older.jsonl", {"schema": run.ROW_SCHEMA - 1}), ("unmarked.jsonl", {"schema": None})):
                with self.subTest(name):
                    bad = path.with_name(name)
                    line = json.loads(good.splitlines()[0])
                    bad.write_text(good + json.dumps({k: v for k, v in dict(line, **row).items() if v is not None}) + "\n",
                                   encoding="utf-8")
                    with self.assertRaises(SystemExit) as raised:
                        analyze.main([str(path), str(bad), "--out", str(out)])
                    self.assertIn(f"{bad.resolve()}:5: row schema {row['schema'] or 'missing'}", str(raised.exception))
                    self.assertFalse(out.exists())
                    bad.unlink()
            limited = path.parent / "limited"
            limited.mkdir()
            (limited / path.name).write_text(json.dumps({"task": "t", "limited_reason": "limit"}) + "\n", encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                analyze.load([path])
            self.assertIn(f"{(limited / path.name).resolve()}:1", str(raised.exception))
        self.assertEqual(full_row()["schema"], run.ROW_SCHEMA)  # what the runner writes is never refused

    def test_with_no_rows_the_page_is_short_and_says_no_round_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "page.md"
            pages = []
            # A shell passes a pattern that matched nothing as it is, or with nullglob drops it.
            for argv in ([str(Path(tmp) / "results" / "*.jsonl")], []):
                with self.subTest(argv=argv), unittest.mock.patch.object(analyze, "PILOT", Path(tmp) / "pilot"):
                    self.assertEqual(analyze.main([*argv, "--out", str(out)]), 0)
                    self.assertEqual(self.check(*argv, "--out", out), (0, ""))
                    pages.append(out.read_text(encoding="utf-8"))
            full = analyze.build_page(analyze.load([self.page_args(tmp)[0]]))
        page = pages[0]
        self.assertEqual(pages[0], pages[1])
        self.assertIn("No round has been recorded yet.", page.split("\n\n")[1])
        for text in ("## How to read it", "WIN: the 95% interval", "LOSE: the router's pass rate", "## Configurations",
                     "- `plugin`: Model Router with today's defaults", "- `defaults`: No plugin",
                     "python3 bench/run.py --tasks <ids>"):
            self.assertIn(text, page)
        for gone in ("## Results", "## Setup", "## Task set", "runs, $", "schema", "sandbox"):
            self.assertNotIn(gone, page)
        self.assertLess(len(page), len(full))

    def test_exploratory_rounds_get_a_table_each_and_no_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            pilot = Path(tmp) / "pilot"
            pilot.mkdir()
            batch = dict(batch="ladder-1", main_model="claude-opus-5-5")
            loaded = dict(bench_commit="f" * 40, plugin_loaded=True, passed=True)
            lines = [full_row(config="plugin-cheap", total_cost_usd=0.25, **loaded, **batch),
                     full_row(config="plugin-cheap", total_cost_usd=0.27, **loaded, **batch),
                     full_row(config="defaults", plugin_loaded=None, total_cost_usd=0.4, passed=False, **batch),
                     # An older row a timeout stopped, graded on what it had written by then.
                     {k: v for k, v in full_row(config="defaults", plugin_loaded=None, total_cost_usd=0.2, passed=True,
                                                exit_code=None, subtype=None, **batch).items() if k != "stopped_by"},
                     # Neither a run on an earlier revision nor one whose check broke is counted.
                     full_row(config="defaults", task="ops-inbox-triage", task_revision=1, total_cost_usd=0.5,
                              passed=True, **batch),
                     dict(full_row(config="defaults", total_cost_usd=0.75, passed=False, **batch),
                          grade={"verdict": "broken", "reason": "NameError: x", "check_hash": "b" * 16}),
                     dict(full_row(config="defaults", total_cost_usd=0.5, passed=False, **batch),
                          grade={"verdict": "stopped", "reason": "the bench stopped", "check_hash": "b" * 16}),
                     full_row(config="defaults", plugin_loaded=None, total_cost_usd=0.2, passed=True,
                              batch="calibration-1", main_model="claude-opus-5-5", started_at="2020-01-01T00:00:00+00:00")]
            (pilot / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
            (pilot / "notes.json").write_text(json.dumps({"ladder-1": "Two ladders beside the defaults."}), encoding="utf-8")
            (pilot / "limited").mkdir()
            (pilot / "limited" / "rows.jsonl").write_text(json.dumps(full_row(config="defaults", batch="ladder-1")) + "\n")
            out = Path(tmp) / "page.md"
            with unittest.mock.patch.object(analyze, "PILOT", pilot):
                self.assertEqual(analyze.main(["--out", str(out)]), 0)
                self.assertEqual(self.check("--out", out), (0, ""))
            page = out.read_text(encoding="utf-8")
        self.assertIn("No judged round has been recorded yet", page.split("\n\n")[1])
        section = page.split("## Exploratory rounds", 1)[1]
        self.assertLess(section.index("`calibration-1`"), section.index("`ladder-1`"))  # oldest first
        self.assertIn("### `ladder-1`: 4 runs", section)
        self.assertIn("Two ladders beside the defaults.", section)
        self.assertIn("| `plugin-cheap` | 2 | 2 | none | $0.260 | `fffffff` |", section)
        self.assertIn("| `defaults` | 2 | 0 | 1 by the timeout | $0.300 |  |", section)  # a set-aside run is not counted
        self.assertIn("A run the harness stopped, by the timeout or the budget cap, counts as failed", section)
        self.assertIn("Left out of the table: 1 run(s) ($0.50) on an earlier revision of `ops-inbox-triage`, 1 "
                      "run(s) ($0.75) whose pass check broke and 1 run(s) ($0.50) the bench stopped before their check "
                      "finished.", section)
        self.assertIn("### `calibration-1`: 1 runs", section)
        self.assertNotRegex(section, r"WIN|LOSE|no difference shown")
        self.assertNotIn("## Results", page)  # a pilot row is never a judged row

    def test_main_reads_configurations_and_tasks_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, out = self.page_args(tmp)
            with unittest.mock.patch.object(run, "load_configs", wraps=run.load_configs) as configs, \
                    unittest.mock.patch.object(run, "load_tasks", wraps=run.load_tasks) as tasks:
                analyze.main([str(path), "--out", str(out)])
        self.assertEqual((configs.call_count, tasks.call_count), (1, 1))

    def test_each_session_model_gets_its_own_table_and_router(self):
        real = full_row(task="bugfix-median", **{"class": "bugfix"})
        n = analyze.MIN_RUNS
        sonnet = [dict(real, main_model="sonnet", config=c, total_cost_usd=usd, passed=True)
                  for c, usd in (("plugin", 0.3), ("defaults", 0.2)) for _ in range(n)]
        opus = [dict(real, main_model="claude-opus-5-5", config=c, total_cost_usd=usd, passed=True)
                for c, usd in (("plugin", 0.5), ("plugin-variant", 0.4), ("defaults", 0.9)) for _ in range(n)]
        with tempfile.TemporaryDirectory() as tmp:
            res = Path(tmp) / "results"
            res.mkdir()
            (res / "a-bugfix-2026-11-22.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sonnet + opus))
            out = Path(tmp) / "page.md"
            analyze.main([str(res / "a-bugfix-2026-11-22.jsonl"), "--out", str(out)])
            page = out.read_text(encoding="utf-8")
            analyze.main([str(res / "a-bugfix-2026-11-22.jsonl"), "--out", str(out), "--router", "plugin-variant"])
            chosen = out.read_text(encoding="utf-8")
        opus_part = page.split("## Results: session model `claude-opus-5-5`")[1].split("## Results: session model")[0]
        sonnet_part = page.split("## Results: session model `sonnet`")[1].split("## How to read it")[0]
        self.assertIn("router: `plugin`", opus_part)
        self.assertIn("$0.9000 per solved", opus_part)  # Opus defaults, not pooled with Sonnet's
        self.assertIn("bugfix: no difference shown against `defaults`", opus_part)
        self.assertIn("router: `plugin`", sonnet_part)
        self.assertIn("bugfix: no difference shown against `defaults`", sonnet_part)
        self.assertNotIn("$0.9000", sonnet_part)
        self.assertIn("router: `plugin-variant`", chosen.split("## Results: session model `claude-opus-5-5`")[1])

    def test_the_page_says_each_run_has_its_own_config_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = analyze.build_page(analyze.load([self.page_args(tmp)[0]]))
        self.assertIn("Each run starts in a fresh copy of the task's files, outside any repository, with a Claude "
                      "Code config folder of its own inside that copy", page)
        self.assertIn("in its plugin data folder, inside the run's own config folder", page)
        self.assertIn("- Sessions are saved in the run's own config folder", page)

    def test_the_page_reports_latency_cache_level_accuracy_and_escalation(self):
        real = full_row(task="bugfix-median", **{"class": "bugfix"})
        usage = {"m": {"inputTokens": 10, "cacheReadInputTokens": 80, "cacheCreationInputTokens": 10}}
        first = {"agent": "model-router:implementer", "level": "standard", "escalated_from": None}
        redo = {"agent": "general-purpose", "level": "complex", "escalated_from": "standard"}
        # A marked redo of a call with no run on record names the level it would get unmarked, which never ran.
        unmarked = dict(redo, on_record=False)
        call = {"agent": "general-purpose", "named_model": None, "named_effort": None, "ran_on": None}
        base = dict(real, main_model="sonnet", models=usage)
        router = [dict(base, config="plugin", wall_ms=10000, passed=True, routed=[first],
                       agent_calls=[dict(call, named_model="sonnet")]),
                  dict(base, config="plugin", wall_ms=20000, passed=False, routed=[first, redo, unmarked],
                       agent_calls=[call, dict(call, named_model="opus", named_effort="high"), call],
                       permission_denials=[{"tool": "Bash", "input": "python3 -m unittest"}]),
                  dict(base, config="plugin", wall_ms=40000, passed=True)]  # no ledger, so no `routed`
        # No `wall_ms`: Claude Code's duration_ms alone is not taken as wall time.
        plain = [dict(base, config="defaults", duration_ms=5000, passed=True) for _ in range(2)]
        for r in plain:
            r.pop("wall_ms")
        with tempfile.TemporaryDirectory() as tmp:
            res = Path(tmp) / "results"
            res.mkdir()
            (res / "a-bugfix-2026-11-22.jsonl").write_text("".join(json.dumps(r) + "\n" for r in router + plain))
            out = Path(tmp) / "page.md"
            analyze.main([str(res / "a-bugfix-2026-11-22.jsonl"), "--out", str(out)])
            page = out.read_text(encoding="utf-8")
        metrics = page.split("### Run metrics")[1].split("##")[0]
        row = next(line for line in metrics.splitlines() if line.startswith("| bugfix "))
        # Nearest-rank p50 and p90 of 10, 20 and 40 s; cache reads 80 of 100 input tokens.
        self.assertIn("p50 20 s, p90 40 s, cache 80%, Bash denied in 1 of 3, model named on 2 of 4 Agent calls and "
                      "effort on 1, accuracy by level: standard 1/2 (2 of 3 runs), escalated 1 of 4 spawns", row)
        self.assertIn("latency n/a, cache 80%, Bash denied in 0 of 2, model named on 0 of 0 Agent calls and effort "
                      "on 0, not routed", row)
        self.assertIn("- Runs with a Bash denial, by configuration: `defaults` 0 of 2; `plugin` 1 of 3.", page)
        never = next(line for line in page.splitlines() if line.startswith("Defined in `bench/configs.json` but"))
        self.assertIn("`haiku-high`", never)
        self.assertNotIn("`defaults`", never)

    def test_the_page_names_set_aside_runs_agent_spend_and_batches(self):
        base = full_row(task="bugfix-median", main_model="sonnet", started_at="2026-11-22T10:00:00+00:00",
                        **{"class": "bugfix"})
        lost = dict(base, config="plugin", task="bugfix-median", started_at="2026-11-22T09:00:00+00:00")
        with tempfile.TemporaryDirectory() as tmp:
            res = Path(tmp) / "results"
            (res / "limited").mkdir(parents=True)
            write = lambda path, items: path.write_text("".join(json.dumps(r) + "\n" for r in items))  # noqa: E731
            write(res / "old-bugfix-2026-11-22.jsonl", [dict(base, config="defaults", batch="old")] * 3)
            write(res / "new-bugfix-2026-11-22.jsonl", [dict(base, config="plugin", task="bugfix-median", batch="new",
                  agent_costs={"main": 0.03, "model-router:implementer": 0.01})] * 2)
            write(res / "limited" / "new-network.jsonl", [dict(lost, limited_reason="network")] * 2)
            write(res / "limited" / "new-bugfix-2026-11-22.jsonl",
                  [dict(lost, task="bugfix-pagination", limited_reason="limit"),
                   dict(lost, task="bugfix-pagination", limited_reason="api_error"),
                   dict(lost, task="bugfix-pagination", limited_reason="plugin_not_loaded"),
                   dict(lost, task="bugfix-pagination", limited_reason="router_silent")])
            out = Path(tmp) / "page.md"
            analyze.main([str(p) for p in sorted(res.glob("*.jsonl"))] + ["--out", str(out)])
            page = out.read_text(encoding="utf-8")
        self.assertIn("4 run(s) are set aside, outside the results, because the API stopped serving them, before or "
                      "during the task: 2 lost the network, 1 hit a rate or usage limit, 1 got an account error (a bad "
                      "or revoked key, no credit, a spend limit or a model the account can't use), whatever was "
                      "spent. 2 of them were run again later", page)
        self.assertIn("`plugin` on `sonnet` (2 runs): main loop $0.0600 (75%), `model-router:implementer` $0.0200 "
                      "(25%)", page)
        self.assertIn("`defaults` old (3); `plugin` new (2)", page)
        self.assertIn("1 router run(s) are set aside, outside the results, because the plugin did not load", page)
        self.assertIn("1 router run(s) are set aside, outside the results, because a subagent ran but the router's "
                      "ledger holds no spawn row", page)

    def test_the_page_says_per_session_model_how_the_runs_were_paid_for(self):
        base = full_row(task="bugfix-median", config="defaults", **{"class": "bugfix"})
        older = {k: v for k, v in base.items() if k != "auth"}  # a row from before the field was written
        data = ([dict(base, main_model="sonnet", auth="ANTHROPIC_API_KEY")] * 3
                + [dict(base, main_model=OPUS, auth="none")] * 2 + [dict(older, main_model=OPUS)])
        page = analyze.build_page(dataclasses.replace(analyze.load([]), rows=data))
        self.assertIn(f"- How the runs were paid for, by session model (`auth`, the `apiKeySource` of the run's "
                      f"`system/init` event): `{OPUS}`: a login token or a cloud provider (`none`: no API key) in 2 "
                      "runs; not recorded in 1 run; `sonnet`: an API key (`ANTHROPIC_API_KEY`) in 3 runs.", page)

    def test_the_page_gives_each_configurations_cache_read_share_and_never_reads_a_missing_field_as_zero(self):
        base = full_row(task="bugfix-median", main_model="sonnet", **{"class": "bugfix"})
        warm = dict(base, cache_read_tokens=80, cache_write_tokens=15, fresh_input_tokens=5)
        older = {k: v for k, v in base.items() if k not in ("cache_read_tokens", "cache_write_tokens",
                                                           "fresh_input_tokens")}
        data = [dict(warm, config="defaults"), dict(warm, config="defaults", cache_read_tokens=0),
                dict(older, config="plugin"), dict(older, config="haiku-high"), dict(warm, config="haiku-high")]
        with tempfile.TemporaryDirectory() as tmp:
            res = Path(tmp) / "a-bugfix-2026-11-22.jsonl"
            res.write_text("".join(json.dumps(r) + "\n" for r in data))
            page = analyze.build_page(analyze.load([res]))
        line = next(line for line in page.splitlines() if line.startswith("- Input tokens read from the prompt cache"))
        self.assertIn("`defaults` 67% (2 runs)", line)  # 80 of 120
        self.assertIn("`haiku-high` 80% (1 of 2 runs)", line)
        self.assertIn("`plugin` not recorded", line)
        self.assertNotIn("`plugin` 0%", line)
        self.assertIn("every run starts cold", line)
        self.assertNotIn("may read what earlier ones wrote", page)
        self.assertIn("Not covered yet: the fan-out, plan-then-build, redo and workflow shapes", page)
        self.assertIn("An agent-team shape can't run", page)


FIXTURE = ROOT / "tests" / "fixtures" / "claude-2.1.330-result.json"
AUTH = {"ANTHROPIC_API_KEY": "sk-test"}  # main refuses to start without a token or a key

# Rows as plugins/model-router/scripts/modelrouter/ledger.py writes them.
LEDGER = [
    {"event": "spawn", "via": "mod", "session": "s", "agent": "model-router:implementer", "action": "rewrite",
     "level": "standard", "model": "sonnet", "was": "opus", "effort": "medium", "source": "caller",
     "escalated_from": None, "reason": "the call names a model"},
    {"event": "spawn", "via": "mod", "session": "s", "agent": "model-router:verifier", "action": "rewrite",
     "level": "complex", "model": "opus", "was": "opus", "effort": "high", "source": "router",
     "escalated_from": "standard", "reason": "a redo of the run it would get unmarked", "on_record": False},
    {"event": "usage", "session": "s", "agent_id": "a1", "agent": "model-router:implementer",
     "models": [], "running": True, "cost_usd": 0.05},
    {"event": "usage", "session": "s", "agent_id": "a2", "models": [], "running": True, "cost_usd": 0.01},
    {"event": "usage", "session": "s", "agent_id": "a3", "agent": "workflow-stage", "models": []},  # no estimate
    {"event": "usage", "session": "s", "agent_id": "a1", "agent": "model-router:implementer",
     "models": [], "running": True, "cost_usd": 0.07}]  # a1's newer total holds its first turn
AGENT_TYPES = {"a1": "general-purpose", "a2": "model-router:verifier"}  # the usage row's own type wins


def stub(tmp: Path, source: str) -> list[str]:
    """A stub claude run as `python stub.py`, the same on every OS."""
    path = Path(tmp) / "stub.py"
    path.write_text(source, encoding="utf-8")
    return [sys.executable, str(path)]


class Recorder(unittest.TestCase):
    task = {"id": "t", "class": "lookup"}
    config = {"id": "plugin", "plugin": True}
    options = {"model": "sonnet", "budget": 0.5, "claude_version": "2.1.330", "commit": "abc", "dirty": False}
    facts = {"started_at": "2026-11-23T00:00:00+00:00", "wall_ms": 7000, "exit_code": 0, "stderr": "",
             "grade": {"verdict": "pass", "reason": "solved", "check_hash": "0123456789abcdef"},
             "final_answer": {"reply": "DONE", "files": {}, "removed": [], "omitted": []},
             "ledger": None, "agent_types": {}, "kept": None}

    def row(self, result, **facts):
        return run.record(self.task, self.config, self.options, result, {**self.facts, **facts})

    def test_a_row_records_which_credential_paid(self):
        init = {"type": "system", "subtype": "init", "apiKeySource": "ANTHROPIC_API_KEY"}
        self.assertEqual(self.row({}, init=init)["auth"], "ANTHROPIC_API_KEY")
        self.assertIsNone(self.row({}, init={})["auth"])  # the stream never began
        self.assertIsNone(self.row({})["auth"])

    def test_a_real_2_1_330_result_becomes_a_row(self):
        row = self.row(json.loads(FIXTURE.read_text(encoding="utf-8")))
        self.assertNotIn("subagents", row)  # subagent_stats is in no documented result type
        self.assertEqual(row["agents_started"], {})
        self.assertEqual((row["subtype"], row["is_error"], row["num_turns"], row["api_error_status"]),
                         ("success", False, 1, None))
        self.assertEqual((row["duration_ms"], row["duration_api_ms"], row["wall_ms"]), (1026, 5062, 7000))
        self.assertEqual(row["permission_denials"], [])
        self.assertEqual(row["models"], {"claude-haiku-5-5": {
            "inputTokens": 8, "outputTokens": 300, "cacheReadInputTokens": 51466, "cacheCreationInputTokens": 19374,
            "costUSD": 0.0037657349999999997, "costBasis": "list"}})
        self.assertEqual((row["total_cost_usd"], row["cost_known"], row["answer"]),
                         (0.0037657349999999997, True, "DONE"))
        self.assertEqual((row["passed"], row["grade"]), (True, self.facts["grade"]))
        self.assertEqual(row["final_answer"], self.facts["final_answer"])
        broken = self.row(json.loads(FIXTURE.read_text(encoding="utf-8")),
                          grade={"verdict": "broken", "reason": "NameError: x", "check_hash": "f" * 16})
        self.assertEqual((broken["passed"], broken["grade"]["verdict"]), (False, "broken"))

    def test_a_row_counts_the_whole_runs_cache_reads_writes_and_fresh_input(self):
        def tokens(result):
            row = self.row(result)
            return row["cache_read_tokens"], row["cache_write_tokens"], row["fresh_input_tokens"]
        self.assertEqual(tokens(json.loads(FIXTURE.read_text(encoding="utf-8"))), (51466, 19374, 8))
        both = {"modelUsage": {"claude-opus-5-5": {"inputTokens": 10, "cacheReadInputTokens": 100,
                                                   "cacheCreationInputTokens": 50},
                               "claude-haiku-5-5": {"inputTokens": 1, "cacheReadInputTokens": 7}}}
        self.assertEqual(tokens(both), (107, 50, 11))  # subagents' models are in modelUsage too
        self.assertEqual(tokens({}), (None, None, None))  # no result line: not known, not zero

    def test_a_zeroed_cost_on_an_error_counts_the_whole_budget(self):
        base = json.loads(FIXTURE.read_text(encoding="utf-8"))
        zeroed = self.row(dict(base, subtype="error_during_execution", is_error=True, total_cost_usd=0))
        self.assertEqual((zeroed["total_cost_usd"], zeroed["cost_known"]), (0.5, False))
        free = self.row(dict(base, subtype="success", total_cost_usd=0))
        self.assertEqual((free["total_cost_usd"], free["cost_known"]), (0, True))
        base.pop("total_cost_usd")
        self.assertEqual(self.row(base)["cost_known"], False)
        self.assertEqual(self.row(base)["total_cost_usd"], 0.5)

    def test_a_refused_run_that_reports_zero_cost_cost_nothing(self):
        base = json.loads(FIXTURE.read_text(encoding="utf-8"))
        limited = self.row(dict(base, subtype="error_during_execution", is_error=True, api_error_status=429,
                                total_cost_usd=0))
        self.assertEqual((limited["total_cost_usd"], limited["cost_known"]), (0, True))
        offline = self.row(dict(base, subtype="error_during_execution", is_error=True, total_cost_usd=0,
                                result="API Error: Can't reach the API server"), exit_code=1)
        self.assertEqual((offline["total_cost_usd"], offline["cost_known"]), (0, True))

    def test_ledger_rows_keep_only_json_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"CLAUDE_CONFIG_DIR": tmp}
            ledger = run.plugin_data_dir(env) / "ledger.jsonl"
            ledger.parent.mkdir(parents=True)
            ledger.write_text('{"event": "spawn"}\n5\nnull\n[]\n"x"\nnot json\n\n{"event": "usage"}\n',
                              encoding="utf-8")
            self.assertEqual(run.ledger_rows(env), [{"event": "spawn"}, {"event": "usage"}])

    def test_a_run_with_no_ledger_gives_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(run.ledger_rows({"CLAUDE_CONFIG_DIR": tmp}))

    def test_a_ledger_that_cannot_be_read_gives_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"CLAUDE_CONFIG_DIR": tmp}
            ledger = run.plugin_data_dir(env) / "ledger.jsonl"
            ledger.parent.mkdir(parents=True)
            ledger.write_bytes(b'{"event": "spawn"}\n\xff\xfe\n')
            self.assertIsNone(run.ledger_rows(env))
            ledger.write_text('{"event": "spawn"}\n', encoding="utf-8")
            real = Path.read_text

            def deny(path, *args, **kwargs):
                if path.name == "ledger.jsonl":
                    raise PermissionError(13, "denied")
                return real(path, *args, **kwargs)

            with unittest.mock.patch.object(Path, "read_text", deny):
                self.assertIsNone(run.ledger_rows(env))

    def test_ledger_rows_give_spend_by_agent_and_spawns(self):
        self.assertEqual(run.spend_by_agent(LEDGER, AGENT_TYPES, 0.11),
                         {"model-router:implementer": 0.07, "model-router:verifier": 0.01, "main": 0.03})
        self.assertNotIn("main", run.spend_by_agent(LEDGER, AGENT_TYPES, None))  # the run's total is unknown
        self.assertEqual(run.spawns(LEDGER), [
            {"agent": "model-router:implementer", "level": "standard", "model": "sonnet", "effort": "medium",
             "source": "caller", "escalated_from": None},
            {"agent": "model-router:verifier", "level": "complex", "model": "opus", "effort": "high",
             "source": "router", "escalated_from": "standard", "on_record": False}])
        # An older router's spawn row keeps a key it didn't log unknown, not None.
        self.assertNotIn("escalated_from", run.spawns([{"event": "spawn", "agent": "x", "level": "lookup"}])[0])
        row = self.row({"total_cost_usd": 0.11}, ledger=LEDGER, agent_types=AGENT_TYPES)
        self.assertEqual(row["agent_costs"]["main"], 0.03)
        self.assertEqual(len(row["routed"]), 2)
        row = self.row({}, ledger=None)
        self.assertEqual((row["agent_costs"], row["routed"]), (None, None))

    def test_the_schedule_is_shuffled_by_its_seed_and_numbers_runs(self):
        tasks, configs = [{"id": "t"}, {"id": "u"}], [{"id": "a"}, {"id": "b"}, {"id": "c"}]

        def order(seed):
            return [(i, t["id"], c["id"]) for i, t, c in run.schedule(tasks, configs, 2, 1, seed)]
        self.assertEqual(order(3), order(3))
        self.assertNotEqual(order(3), order(4))
        self.assertEqual(sorted(order(3)), sorted((i, t, c) for i in (1, 2) for t in "tu" for c in "abc"))

    def test_a_claude_prefix_passes_through_whole(self):
        options = {"claude": ["C:/Program Files/Python313/python.exe", "stub.py"], "model": "sonnet", "budget": 0.5}
        cmd, _ = run.command("x", {"id": "d", "env": {}}, options, {})
        self.assertEqual(cmd[:3], ["C:/Program Files/Python313/python.exe", "stub.py", "-p"])


class Runner(unittest.TestCase):
    def test_each_run_starts_clean_and_only_the_configuration_differs(self):
        base = {"PATH": "/bin", "CLAUDE_CODE_SUBAGENT_MODEL": "opus", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1",
                "CLAUDE_CODE_EFFORT_LEVEL": "max", "CLAUDE_CODE_SUBAGENT_EFFORT": "low", "CLAUDECODE": "1",
                "CLAUDE_CODE_SESSION_ID": "parent", "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/s.sock",
                "CLAUDE_PLUGIN_ROOT": "/elsewhere", "CLAUDE_CODE_USE_BEDROCK": "1"}
        configs = {c["id"]: c for c in run.load_configs()}
        options = {"model": "sonnet", "budget": 0.5, "claude": ["claude"], "plugin_dir": "/p/model-router"}
        cmd, env = run.command("do it", configs["haiku-high"], options, base)
        self.assertEqual((env["CLAUDE_CODE_SUBAGENT_MODEL"], env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"],
                          env["CLAUDE_CODE_SUBAGENT_EFFORT"]), ("haiku", "1", "high"))
        for name in ("CLAUDE_CODE_EFFORT_LEVEL", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
                     "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_PLUGIN_ROOT"):
            self.assertNotIn(name, env)
        self.assertEqual((env["PATH"], env["CLAUDE_CODE_USE_BEDROCK"]), ("/bin", "1"))
        self.assertEqual((env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"], env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"]), ("1", "1"))
        self.assertNotIn("--plugin-dir", cmd)
        self.assertEqual(cmd[cmd.index("--max-budget-usd") + 1], "0.5")
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet")
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "project")

        for config in ("plugin", "defaults"):
            cmd, env = run.command("do it", configs[config], options, base)
            self.assertEqual("--plugin-dir" in cmd, config == "plugin")
            self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", env)
            self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", env)
        self.assertEqual(cmd[1:3], ["-p", "do it"])

    def test_token_auth_and_test_pipes_still_work(self):
        configs = {c["id"]: c for c in run.load_configs()}
        options = {"model": "sonnet", "budget": 0.5, "claude": ["claude"], "plugin_dir": "/p/model-router"}
        cmd, env = run.command("do it", configs["plugin"], options, {"CLAUDE_CODE_OAUTH_TOKEN": "t",
                                                                     "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR": "3"})
        self.assertEqual(env, {"CLAUDE_CODE_OAUTH_TOKEN": "t", **run.ISOLATION})
        tools = cmd[cmd.index("--allowedTools") + 1:]
        self.assertIn("Bash", tools)
        self.assertFalse([t for t in tools if t.startswith("Bash(")])

    def test_a_task_or_configuration_list_that_matches_nothing_is_an_error_naming_it(self):
        for flag, ids in (("--tasks", ["no-such-task"]), ("--configs", ["defualts", "plugn"])):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as tmp:
                with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as caught, \
                        unittest.mock.patch.dict("os.environ", AUTH):
                    run.main([flag, *ids, "--max-total", "3", "--claude", *stub(tmp, LIMIT_STUB),
                              "--out", str(Path(tmp) / "out.jsonl")])
                self.assertEqual(caught.exception.code, 2)
                self.assertIn(", ".join(ids), err.getvalue())

    def test_the_runner_stops_before_a_run_could_pass_the_total_cap(self):
        self.assertTrue(run.can_start(spent=2.0, budget=0.5, max_total=3.0))
        self.assertFalse(run.can_start(spent=2.3, budget=0.5, max_total=3.0))

    def test_a_run_that_times_out_is_a_failed_row_charged_the_whole_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude = stub(tmp, HANGING_STUB)
            task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
            options = {"claude": claude, "model": "sonnet", "budget": 0.5, "timeout": 1,
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            row = run.run_one(task, next(c for c in run.load_configs() if c["id"] == "defaults"), options)
        self.assertEqual((row["exit_code"], row["passed"], row["cost_known"], row["total_cost_usd"], row["answer"]),
                         (None, False, False, 0.5, ""))
        self.assertGreaterEqual(row["wall_ms"], 1000)


    def test_a_run_the_harness_stopped_is_not_solved_whatever_its_files_hold(self):
        task = next(t for t in run.load_tasks() if t["id"] == "security-sql-injection")
        defaults = next(c for c in run.load_configs() if c["id"] == "defaults")
        for how, stopped in (("hang", "timeout"), ("cap", "budget"), ("finish", None)):
            with self.subTest(how=how), tempfile.TemporaryDirectory() as tmp:
                options = {"claude": stub(tmp, STOPPED_STUB), "model": "sonnet", "budget": 0.5, "timeout": 2,
                           "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
                with unittest.mock.patch.dict("os.environ", {"STUB_HOW": how}):
                    row = run.run_one(task, defaults, options)
                self.assertEqual((row["stopped_by"], row["passed"]), (stopped, stopped is None), row["grade"])
                self.assertIn("FINDING.json", row["final_answer"]["files"])  # the files would have passed
                if stopped:
                    self.assertIn(f"the harness stopped the run ({stopped})", row["grade"]["reason"])


    @unittest.skipIf(sys.platform == "win32", "Windows has no process group for the harness to kill")
    def test_a_run_stopped_by_a_timeout_or_an_interrupt_leaves_no_process_behind(self):
        task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
        defaults = next(c for c in run.load_configs() if c["id"] == "defaults")
        real = subprocess.Popen.communicate

        def interrupted(proc, *args, **kwargs):
            """Ctrl-C once the stub has started its child."""
            if "--output-format" not in proc.args:
                return real(proc, *args, **kwargs)
            for _ in range(200):
                if child.is_file() and child.read_text():
                    break
                time.sleep(0.05)
            raise KeyboardInterrupt

        for how in ("timeout", "interrupt"):
            with self.subTest(how=how), tempfile.TemporaryDirectory() as tmp:
                child = Path(tmp) / "child.pid"
                options = {"claude": stub(tmp, ORPHAN_STUB), "model": "sonnet", "budget": 0.5,
                           "timeout": 3 if how == "timeout" else 60, "plugin_dir": str(run.PLUGIN),
                           "claude_version": "9.9.9 (stub)", "commit": None}
                with unittest.mock.patch.dict("os.environ", {"STUB_CHILD": str(child)}), \
                        (unittest.mock.patch.object(subprocess.Popen, "communicate", interrupted) if how == "interrupt"
                         else contextlib.nullcontext()), \
                        (self.assertRaises(KeyboardInterrupt) if how == "interrupt" else contextlib.nullcontext()):
                    run.run_one(task, defaults, options)
                pid = int(child.read_text())
                for _ in range(100):
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.05)
                else:
                    os.kill(pid, 9)
                    self.fail(f"the run's child {pid} outlived the run")


    @unittest.skipIf(sys.platform == "win32", "needs a symlink and a shebang")
    def test_an_update_during_a_round_never_reaches_a_later_run(self):
        # The installer moves the claude symlink on PATH to each new version; the first run here does that.
        with tempfile.TemporaryDirectory() as tmp:
            bin_, out = Path(tmp) / "bin", Path(tmp) / "out.jsonl"
            bin_.mkdir()
            for version in ("1", "2"):
                path = Path(tmp) / f"claude-{version}"
                path.write_text(f"#!{sys.executable}\n" + VERSIONED_STUB.replace("<v>", version), encoding="utf-8")
                path.chmod(0o755)
            (bin_ / "claude").symlink_to(Path(tmp) / "claude-1")
            env = {**AUTH, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}"}
            with unittest.mock.patch.dict("os.environ", env), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = run.main(["--tasks", "lookup-retry-limit", "--configs", "defaults", "--runs", "2",
                                 "--max-total", "3", "--claude", "claude", "--out", str(out)])
            rows = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertEqual(os.readlink(bin_ / "claude"), str(Path(tmp) / "claude-2"))
        self.assertEqual(code, 0)
        self.assertEqual([(r["claude_version"], r["answer"][-4:]) for r in rows], [("1.0.0 (stub)", "(v1)")] * 2)

    @unittest.skipIf(sys.platform == "win32", "Windows ends a process without running its signal handlers")
    def test_a_runner_sent_sigterm_leaves_no_child_no_scratch_and_writes_the_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            child, cwd, out, temp = (Path(tmp) / n for n in ("child.pid", "cwd.txt", "out.jsonl", "temp"))
            temp.mkdir()
            env = {**os.environ, **AUTH, "STUB_CHILD": str(child), "STUB_CWD": str(cwd), "TMPDIR": str(temp)}
            runner = subprocess.Popen([sys.executable, str(run.BENCH / "run.py"), "--tasks", "lookup-retry-limit",
                                       "--configs", "defaults", "--max-total", "3", "--claude", *stub(tmp, ORPHAN_STUB),
                                       "--out", str(out)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(600):
                if child.is_file() and child.read_text():
                    break
                time.sleep(0.05)
            runner.send_signal(signal.SIGTERM)
            runner.wait(timeout=60)
            pid = int(child.read_text())
            for _ in range(100):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                os.kill(pid, 9)
                self.fail(f"the run's child {pid} outlived the runner")
            self.assertFalse(Path(cwd.read_text()).parent.exists())  # the run's scratch folder
            self.assertEqual(list(temp.iterdir()), [])  # the frozen plugin copy
            [row] = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertIn("KeyboardInterrupt", row["grade"]["reason"])


VERSIONED_STUB = '''import json, os, shutil, sys
from pathlib import Path
if "--version" in sys.argv:
    print("<v>.0.0 (stub)"); sys.exit(0)
link = Path(shutil.which("claude"))
if os.readlink(link).endswith("claude-1"):
    link.with_name("next").symlink_to(Path(os.readlink(link)).with_name("claude-2"))
    os.replace(link.with_name("next"), link)
print(json.dumps({"type": "result", "result": "read_retry_limit in app/settings.py (v<v>)", "total_cost_usd": 0.01,
                  "modelUsage": {}}))
'''


ORPHAN_STUB = '''#!/usr/bin/env python3
import os, subprocess, sys, time
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
if os.environ.get("STUB_CWD"):
    with open(os.environ["STUB_CWD"], "w") as fh:
        fh.write(os.getcwd())
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
with open(os.environ["STUB_CHILD"], "w") as fh:
    fh.write(str(child.pid))
time.sleep(60)
'''


HANGING_STUB = '''#!/usr/bin/env python3
import sys, time
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
print('{"result": "half an ans', flush=True)
time.sleep(30)
'''

# Leaves a passing FINDING.json, then hangs, stops at the budget cap as Claude Code does, or finishes.
STOPPED_STUB = '''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
Path("FINDING.json").write_text(json.dumps({"file": "db/users.py", "line": 6}))
how = os.environ["STUB_HOW"]
if how == "hang":
    time.sleep(30)
capped = how == "cap"
print(json.dumps({"type": "result", "subtype": "error_max_budget_usd" if capped else "success", "is_error": capped,
                  "result": "", "total_cost_usd": 0.5 if capped else 0.1, "modelUsage": {}}))
sys.exit(1 if capped else 0)
'''


class Prices(unittest.TestCase):
    """bench/prices.json, from https://platform.claude.com/docs/en/about-claude/pricing.md (checked 2026-11-22)."""

    # The pricing page lists no rate for this one.
    UNLISTED = {"claude-mythos-preview"}

    def test_every_model_still_in_service_and_each_familys_newest_has_a_price(self):
        lifecycle = json.loads((ROOT / "compat" / "baseline.json").read_text(encoding="utf-8"))["lifecycle"]
        missing = [model for model, info in lifecycle.items()
                   if info.get("state") in ("Active", "Deprecated") and model not in self.UNLISTED
                   and run.price(model) is None]
        self.assertEqual(missing, [])
        for family, model_id in analyze.mrconfig.shipped().newest.items():
            self.assertIsNotNone(run.price(model_id), family)

    def test_a_model_id_finds_its_list_price(self):
        self.assertEqual(run.price("claude-opus-4-7")["input"], 5)  # one entry covers Opus 4.x
        self.assertEqual(run.price("claude-opus-4-1-20250805"),  # except 4.1, at its own list price
                         {"input": 15, "output": 75, "cache_read": 1.5, "cache_write": 18.75, "cache_write_1h": 30})
        for opus_4 in ("claude-opus-4-0", "claude-opus-4-20250514"):  # Opus 4 lists at the same price as 4.1
            self.assertEqual(run.price(opus_4), run.price("claude-opus-4-1"), opus_4)
        self.assertEqual(run.price("claude-haiku-4-5-20251001")["input"], 1)
        self.assertEqual(run.price("claude-opus-5-5[1m]")["input"], 4)
        self.assertIsNone(run.price("claude-opus-5-6"))  # a new version is never priced as its neighbour

    def test_mythos_is_priced_like_its_fable_twin(self):
        for version, rates in (("5-2", (8, 40, 0.2, 10, 16)), ("5-1", (10, 50, 0.25, 12.5, 20)),
                               ("5", (10, 50, 1, 12.5, 20))):
            price = run.price(f"claude-mythos-{version}")
            self.assertEqual(tuple(price[k] for k in ("input", "output", "cache_read", "cache_write",
                                                      "cache_write_1h")), rates, version)

    def test_the_plugin_carries_no_price(self):
        sys.path.insert(0, str(run.PLUGIN / "scripts"))
        from modelrouter import config as cfg
        self.assertNotIn("prices", cfg.base_config())
        self.assertFalse(hasattr(cfg.shipped(), "price"))


class Overhead(unittest.TestCase):
    def test_percentile_is_the_nearest_rank(self):
        ten = [10.0, 9, 8, 7, 6, 5, 4, 3, 2, 1]
        for samples, share, want in ((ten, 0.5, 5), (ten, 0.95, 10), (ten, 0.1, 1), (ten, 0.11, 2),
                                     (list(range(1, 21)), 0.95, 19), ([7.0], 0.5, 7), ([7.0], 0.95, 7),
                                     ([3.0, 1, 2], 0.5, 2), ([1.0, 2], 0.5, 1)):
            with self.subTest(samples=samples, share=share):
                self.assertEqual(overhead.percentile(samples, share), want)
        self.assertEqual(ten[0], 10.0)  # sorts a copy

    def test_summary_rounds_each_figure_and_keeps_the_labels(self):
        self.assertEqual(overhead.summary([1.26, 1.24, 3.04], command="pre-agent", warm=True),
                         {"command": "pre-agent", "warm": True, "runs": 3, "p50_ms": 1.3, "p95_ms": 3.0, "max_ms": 3.0})
        self.assertEqual(overhead.summary([10.4, 20.6, 30.2, 40.4], digits=0),
                         {"runs": 4, "p50_ms": 21, "p95_ms": 40, "max_ms": 40})


FILES_STUB = '''#!/usr/bin/env python3
import json, sys
from pathlib import Path
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
Path("FINDING.json").write_text('{"file": "app/settings.py", "line": 7}')
Path("app/client.py").write_bytes(b"changed\\n")
Path("app/backoff.py").unlink()
print(json.dumps({"type": "result", "result": "x" * 3000, "total_cost_usd": 0.01, "modelUsage": {}}))
'''


class FinalAnswer(unittest.TestCase):
    """A row keeps what the check graded, since the work dir is deleted after the run."""

    def test_the_files_a_run_added_changed_or_removed_are_kept_and_the_rest_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            shipped, work = Path(tmp) / "repo", Path(tmp) / "work"
            for root in (shipped, work):
                (root / "pkg").mkdir(parents=True)
                (root / "same.py").write_text("a = 1\n", encoding="utf-8")
                (root / "pkg" / "edit.py").write_text("b = 1\n", encoding="utf-8")
            (shipped / "gone.py").write_text("c = 1\n", encoding="utf-8")
            (work / "pkg" / "edit.py").write_bytes(b"b = 2\n")
            (work / "DESIGN.json").write_text('{"x": 1}', encoding="utf-8")
            (work / "pkg" / "__pycache__").mkdir()
            (work / "pkg" / "__pycache__" / "edit.cpython-39.pyc").write_bytes(b"\0\1")
            (work / ".claude").mkdir()
            (work / ".claude" / "model-router.json").write_text("{}", encoding="utf-8")
            (work / "blob.bin").write_bytes(b"\xff\xfe\x00")
            (work / "big.txt").write_text("z" * (run.FINAL_FILE_CHARS + 1), encoding="utf-8")
            final = run.final_answer(shipped, work, "the reply")
        self.assertEqual(final, {"reply": "the reply", "files": {"DESIGN.json": '{"x": 1}', "pkg/edit.py": "b = 2\n"},
                                 "removed": ["gone.py"], "omitted": ["big.txt", "blob.bin"]})

    def test_a_file_too_big_for_the_cap_is_omitted_unread(self):
        with tempfile.TemporaryDirectory() as tmp:
            shipped, work = Path(tmp) / "repo", Path(tmp) / "work"
            shipped.mkdir()
            work.mkdir()
            big = "z" * (4 * run.FINAL_FILE_CHARS + 1)
            for folder in (shipped, work):
                (folder / "same.txt").write_text(big, encoding="utf-8")
            (work / "huge.txt").write_text(big, encoding="utf-8")
            (work / "small.txt").write_bytes(b"ok\n")
            with unittest.mock.patch.object(Path, "read_bytes", autospec=True, side_effect=Path.read_bytes) as read:
                final = run.final_answer(shipped, work, "")
        self.assertEqual((final["files"], final["omitted"]), ({"small.txt": "ok\n"}, ["huge.txt"]))
        self.assertEqual({p.name for (p,), _ in read.call_args_list}, {"small.txt"})

    def test_the_files_together_stay_under_the_row_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            shipped, work = Path(tmp) / "repo", Path(tmp) / "work"
            shipped.mkdir()
            work.mkdir()
            for n in range(6):
                (work / f"f{n}.txt").write_text("z" * run.FINAL_FILE_CHARS, encoding="utf-8")
            final = run.final_answer(shipped, work, "")
        self.assertEqual(len(final["files"]) * run.FINAL_FILE_CHARS <= run.FINAL_ROW_CHARS, True)
        self.assertEqual(len(final["files"]) + len(final["omitted"]), 6)
        self.assertTrue(final["omitted"])

    def test_the_reply_is_cut_to_the_row_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "repo").mkdir()
            (Path(tmp) / "work").mkdir()
            final = run.final_answer(Path(tmp) / "repo", Path(tmp) / "work", "r" * (run.FINAL_ROW_CHARS + 5))
        self.assertEqual(len(final["reply"]), run.FINAL_ROW_CHARS)

    @unittest.skipIf(sys.platform == "win32" or os.geteuid() == 0, "needs POSIX modes that bind the reader")
    def test_an_unreadable_file_is_omitted_and_the_row_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            shipped, work = Path(tmp) / "repo", Path(tmp) / "work"
            shipped.mkdir()
            work.mkdir()
            (work / "locked.py").write_text("x = 1\n", encoding="utf-8")
            (work / "open.py").write_text("y = 1\n", encoding="utf-8")
            (work / "locked.py").chmod(0)
            try:
                final = run.final_answer(shipped, work, "")
            finally:
                (work / "locked.py").chmod(0o644)
        self.assertEqual((final["files"], final["omitted"]), ({"open.py": "y = 1\n"}, ["locked.py"]))

    def test_a_link_out_of_the_work_dir_is_never_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            shipped, work = Path(tmp) / "repo", Path(tmp) / "work"
            shipped.mkdir()
            work.mkdir()
            (Path(tmp) / "secret.txt").write_text("outside\n", encoding="utf-8")
            (work / "inside.txt").write_bytes(b"inside\n")
            try:
                (work / "out.txt").symlink_to(Path(tmp) / "secret.txt")
                (work / "in.txt").symlink_to(work / "inside.txt")
            except OSError:
                self.skipTest("this system can't make symlinks")
            final = run.final_answer(shipped, work, "")
        self.assertEqual((final["files"], final["omitted"]),
                         ({"in.txt": "inside\n", "inside.txt": "inside\n"}, ["out.txt"]))

    def test_a_run_row_has_the_whole_reply_and_the_files_the_run_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude = stub(tmp, FILES_STUB)
            task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
            options = {"claude": claude, "model": "sonnet", "budget": 0.5, "timeout": 60,
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            row = run.run_one(task, next(c for c in run.load_configs() if c["id"] == "defaults"), options)
        self.assertEqual((len(row["answer"]), len(row["final_answer"]["reply"])), (2000, 3000))
        self.assertEqual(row["final_answer"]["files"], {"FINDING.json": '{"file": "app/settings.py", "line": 7}',
                                                        "app/client.py": "changed\n"})
        self.assertEqual(row["final_answer"]["removed"], ["app/backoff.py"])


class Tasks(unittest.TestCase):
    """Each pass check fails on the task as shipped and passes on a known-good answer."""

    def check(self, task, workdir, answer):
        result = Path(workdir) / ".." / "result.txt"
        result.write_text(answer, encoding="utf-8")
        return run.grade(task["dir"] / "check.py", Path(workdir), result, timeout=60).passed

    def test_every_task_has_a_known_class_and_a_check_that_tells_right_from_wrong(self):
        tasks = gradable_tasks()
        self.assertGreaterEqual(len(tasks), 6)
        for cls in ("lookup", "implementation", "bugfix"):
            self.assertGreaterEqual(sum(t["class"] == cls for t in tasks), 2, cls)
        for task in tasks:
            with self.subTest(task=task["id"]), tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / "work"
                shutil.copytree(task["dir"] / "repo", work)
                self.assertIn(task["class"], run.CLASSES)
                self.assertFalse(self.check(task, work, task.get("wrong_answer", "")))
                solution = task["dir"] / "solution"
                if solution.is_dir():
                    shutil.copytree(solution, work, dirs_exist_ok=True)
                self.assertTrue(self.check(task, work, task.get("reference_answer", "")))



def overlay_dirs(task, kind):
    folder = task["dir"] / kind
    return sorted(p for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []


def build_work(task, overlay, work):
    """repo/, solution/ over it, then an overlay: `remove.txt` lists paths to delete, `answer.txt` is the reply,
    `expect.txt` is part of the reason a wrong answer must fail with, none of them go in the work dir, and every
    other file is copied in. Returns the reply."""
    shutil.copytree(task["dir"] / "repo", work)
    if (task["dir"] / "solution").is_dir():
        shutil.copytree(task["dir"] / "solution", work, dirs_exist_ok=True)
    if overlay is None:
        return ""
    for line in (overlay / "remove.txt").read_text(encoding="utf-8").split("\n") if (overlay / "remove.txt").is_file() else []:
        if line.strip():
            (work / line.strip()).unlink()
    for src in sorted(p for p in overlay.rglob("*") if p.is_file()):
        rel = src.relative_to(overlay)
        if rel.as_posix() in ("answer.txt", "remove.txt", "expect.txt"):
            continue
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, work / rel)
    return (overlay / "answer.txt").read_text(encoding="utf-8") if (overlay / "answer.txt").is_file() else ""


class KnownAnswers(unittest.TestCase):
    """Each task folder keeps the answers its check must turn down (wrong/) and take (right/) beside the check."""

    def grade(self, task, overlay):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            reply = build_work(task, overlay, work)
            answer = Path(tmp) / "answer.txt"
            answer.write_text(reply, encoding="utf-8")
            return run.grade(task["dir"] / "check.py", work, answer, timeout=60)

    def test_every_task_has_known_wrong_answers(self):
        for task in run.load_tasks():
            with self.subTest(task=task["id"]):
                self.assertTrue(task.get("wrong_answer") is not None or overlay_dirs(task, "wrong"))

    def test_every_wrong_answer_fails_and_every_right_answer_passes(self):
        for task in gradable_tasks():
            for kind, verdict in (("wrong", "fail"), ("right", "pass")):
                for overlay in overlay_dirs(task, kind):
                    with self.subTest(task=task["id"], answer=f"{kind}/{overlay.name}"):
                        graded = self.grade(task, overlay)
                        self.assertEqual(graded.verdict, verdict, graded.reason)
                        if kind == "wrong" and (overlay / "expect.txt").is_file():
                            self.assertIn((overlay / "expect.txt").read_text(encoding="utf-8").strip(), graded.reason)
                        elif kind == "wrong":  # a check that gives reasons pins each wrong answer's
                            self.assertEqual(graded.reason, "the check exited 1", "add an expect.txt")

    def test_a_json_answer_nested_too_deep_to_parse_fails_and_never_breaks_the_check(self):
        nested = "[" * 100_000 + "]" * 100_000
        for task in gradable_tasks():
            answers = sorted((task["dir"] / "solution").glob("*.json")) if (task["dir"] / "solution").is_dir() else []
            for name in [a.name for a in answers]:
                with self.subTest(task=task["id"], file=name), tempfile.TemporaryDirectory() as tmp:
                    work = Path(tmp) / "work"
                    build_work(task, None, work)
                    (work / name).write_text(nested, encoding="utf-8")
                    answer = Path(tmp) / "answer.txt"
                    answer.write_text(task.get("reference_answer", ""), encoding="utf-8")
                    graded = run.grade(task["dir"] / "check.py", work, answer, timeout=60)
                    self.assertEqual(graded.verdict, "fail", graded.reason)

    def test_an_answer_folder_changes_something(self):
        for task in run.load_tasks():
            for kind in ("wrong", "right"):
                for overlay in overlay_dirs(task, kind):
                    with self.subTest(task=task["id"], answer=f"{kind}/{overlay.name}"):
                        self.assertTrue(any(p.is_file() for p in overlay.rglob("*")))


class HardTasks(unittest.TestCase):
    """The tasks built to tell a small model from a strong one: a real repository, a test the agent can run, and a
    check that knows more than that test."""

    VISIBLE = {"cachefix-stale-across-services": "test_checkout.py", "perf-nlogn-rewrite": "test_report.py",
               "migrate-ledger-v3": "test_migrate.py", "security-tar-symlink-chain": "test_extract.py"}

    def tasks(self):
        return {t["id"]: t for t in run.load_tasks()}

    def test_each_has_a_thousand_lines_over_ten_files_and_its_visible_test(self):
        tasks = self.tasks()
        for task_id, test in self.VISIBLE.items():
            with self.subTest(task=task_id):
                files = [p for p in (tasks[task_id]["dir"] / "repo").rglob("*") if p.is_file()]
                self.assertGreaterEqual(len(files), 10)
                self.assertGreaterEqual(sum(len(p.read_text(encoding="utf-8").splitlines()) for p in files), 1000)
                self.assertIn(test, {p.name for p in files})
                self.assertIn(f'"{test}"', (tasks[task_id]["dir"] / "check.py").read_text(encoding="utf-8"))

    def test_some_wrong_answer_passes_the_visible_test_and_still_fails_the_check(self):
        tasks = {t["id"]: t for t in gradable_tasks()}
        for task_id, test in self.VISIBLE.items():
            if task_id not in tasks:
                continue
            with self.subTest(task=task_id):
                task = tasks[task_id]
                wrong = overlay_dirs(task, "wrong")
                self.assertGreaterEqual(len(wrong), 2)

                def visible_passes(overlay):
                    with tempfile.TemporaryDirectory() as tmp:
                        work = Path(tmp) / "work"
                        build_work(task, overlay, work)
                        return passcheck.tests_pass(work, task["dir"] / "repo", test)

                self.assertTrue(any(visible_passes(o) for o in wrong), "the visible test already catches every one")

    @unittest.skipIf(sys.platform == "win32", "the archive task needs POSIX symlinks")
    def test_the_archive_answers_refuse_members_that_collide_before_writing_anything(self):
        """Not an escape, so the check doesn't grade it, but a right answer must not fail halfway through either."""
        task = self.tasks()["security-tar-symlink-chain"]
        cases = [{"name": "a file under a link to a file", "via": "extract",
                  "members": [["file", "f", "1"], ["link", "g", "f"], ["file", "g/x", "2"]]},
                 {"name": "a link on a folder the archive made", "via": "extract",
                  "members": [["file", "a/x", "1"], ["link", "a", "."]]},
                 {"name": "two names for one file", "via": "extract",
                  "members": [["link", "a", "."], ["file", "x", "1"], ["file", "a/x", "2"]]}]
        for answer in [None] + overlay_dirs(task, "right"):
            with self.subTest(answer=answer and answer.name), tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / "work"
                build_work(task, answer, work)
                got = passcheck.probe(work, task["dir"] / "probe.py", {"cases": cases})
                for case in cases:
                    self.assertEqual(got[case["name"]]["error"], "UnsafeArchive", case["name"])
                    self.assertEqual(sorted(got[case["name"]]["tree"]),
                                     ["l1", "l1/l2", "l1/l2/l3", "l1/l2/l3/dest", "outside.txt"], case["name"])

    def test_the_archive_task_is_graded_everywhere_but_windows(self):
        everything = len(run.load_tasks())
        for platform, kept in (("win32", False), ("linux", True), ("darwin", True)):
            with self.subTest(platform=platform), unittest.mock.patch.object(sys, "platform", platform):
                ids = {t["id"] for t in gradable_tasks()}
                self.assertEqual("security-tar-symlink-chain" in ids, kept)
                self.assertEqual(len(ids), everything - (not kept))


STUB = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
answer = "read_retry_limit in app/settings.py"
if "--plugin-dir" in sys.argv:
    root = os.environ.get("CLAUDE_CODE_PLUGIN_CACHE_DIR") or str(Path(os.environ["CLAUDE_CONFIG_DIR"]) / "plugins")
    data = Path(root) / "data" / "model-router-inline"
    answer += " (had state)" if (data / "state.json").exists() else " (fresh)"
    data.mkdir(parents=True, exist_ok=True)
    (data / "state.json").write_text('{"max_tier": "haiku"}')
    (data / "ledger.jsonl").write_text("written by the run")
print(json.dumps({"type": "result", "result": answer, "total_cost_usd": 0.01, "modelUsage": {}}))
'''


class PluginState(unittest.TestCase):
    """Router runs keep their state in a plugins root of their own, never the one other sessions share."""

    def test_router_runs_start_fresh_and_never_touch_the_shared_data_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "config"
            shared = home / "plugins" / "data" / "model-router-inline"
            shared.mkdir(parents=True)
            (shared / "state.json").write_text('{"max_tier": "opus"}')
            os.utime(shared / "state.json", (1000, 1000))
            claude = stub(tmp, STUB)
            task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
            configs = {c["id"]: c for c in run.load_configs()}
            options = {"claude": claude, "model": "sonnet", "budget": 0.5, "timeout": 60,
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            shared.chmod(0)  # any read or write of the shared folder now fails
            try:
                with unittest.mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(home)}):
                    first = run.run_one(task, configs["plugin"], options)
                    second = run.run_one(task, configs["plugin"], options)
                    baseline = run.run_one(task, configs["defaults"], options)
            finally:
                shared.chmod(0o755)
            self.assertEqual([p.name for p in shared.iterdir()], ["state.json"])
            self.assertEqual((shared / "state.json").read_text(), '{"max_tier": "opus"}')
            self.assertEqual((shared / "state.json").stat().st_mtime, 1000)
            self.assertTrue(first["passed"])
            self.assertIn("(fresh)", first["answer"])
            self.assertIn("(fresh)", second["answer"])
            self.assertTrue(baseline["passed"])
            self.assertIn("bench_dirty", first)

    def test_every_run_gets_its_own_config_folder_and_project_name(self):
        options = {"claude": ["claude"], "model": "sonnet", "budget": 0.5, "plugin_dir": "/p", "config_dir": "/s/config"}
        host = {"CLAUDE_CONFIG_DIR": "/home/me/.claude", "CLAUDE_CODE_PLUGIN_CACHE_DIR": "/elsewhere"}
        for config in run.load_configs():
            _, env = run.command("do it", config, options, host)
            self.assertEqual((env["CLAUDE_CONFIG_DIR"], env["CLAUDE_CODE_PROJECT_DIR_NAME"]), ("/s/config", "run"),
                             config["id"])
            self.assertNotIn("CLAUDE_CODE_PLUGIN_CACHE_DIR", env)
            # It would strip CLAUDE_CONFIG_DIR from the router's hooks and turn sandbox auto-allow off.
            self.assertNotIn("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", env)

    def test_the_data_folder_is_named_from_the_manifest(self):
        self.assertEqual(run.plugin_data_dir({"CLAUDE_CONFIG_DIR": "/c"}),
                         Path("/c/plugins/data/model-router-inline"))


SESSION_STUB = '''#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
folder = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / os.environ["CLAUDE_CODE_PROJECT_DIR_NAME"]
(folder / "s1" / "subagents").mkdir(parents=True)
(folder / "s1.jsonl").write_text("{}")
(folder / "s1" / "subagents" / "agent-a1.jsonl").write_text("{}")
print(json.dumps({"type": "result", "result": "read_retry_limit in app/settings.py", "total_cost_usd": 0.01, "modelUsage": {}}))
'''


class Sessions(unittest.TestCase):
    """Runs keep their transcripts, as real sessions do, and the bench removes only its own afterwards."""

    def test_runs_save_their_sessions(self):
        cmd, _ = run.command("do it", next(c for c in run.load_configs() if c["id"] == "plugin"), {"claude": ["claude"], "model": "sonnet", "budget": 0.5,
                                                             "plugin_dir": "/p"}, {})
        self.assertNotIn("--no-session-persistence", cmd)

    def test_the_transcripts_sit_under_the_project_name_the_runner_gives(self):
        self.assertEqual(run.session_dir({"CLAUDE_CONFIG_DIR": "/c", "CLAUDE_CODE_PROJECT_DIR_NAME": "run"}),
                         Path("/c/projects/run"))

    def test_the_hosts_transcripts_are_never_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "config"
            other = home / "projects" / "-Users-someone-project"
            other.mkdir(parents=True)
            (other / "keep.jsonl").write_text("{}")
            claude = stub(tmp, SESSION_STUB)
            task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
            options = {"claude": claude, "model": "sonnet", "budget": 0.5, "timeout": 60,
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(home)}):
                row = run.run_one(task, next(c for c in run.load_configs() if c["id"] == "plugin"), options)
            self.assertEqual([p.name for p in (home / "projects").iterdir()], ["-Users-someone-project"])
            self.assertTrue((other / "keep.jsonl").is_file())
            self.assertNotIn("session_dir_removed", row)

    def test_keep_saves_the_scratch_folder_and_the_transcripts_before_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, kept = Path(tmp) / "config", Path(tmp) / "kept"
            claude = stub(tmp, SESSION_STUB)
            task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
            options = {"claude": claude, "model": "sonnet", "budget": 0.5, "timeout": 60, "keep": str(kept),
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(home)}):
                row = run.run_one(task, next(c for c in run.load_configs() if c["id"] == "plugin"), options)
            [folder] = list(kept.iterdir())
            self.assertEqual(row["kept"], str(folder))
            self.assertTrue((folder / "work" / "app" / "settings.py").is_file())
            self.assertEqual((folder / "answer.txt").read_text(), "read_retry_limit in app/settings.py")
            self.assertTrue((folder / "session" / "s1" / "subagents" / "agent-a1.jsonl").is_file())
            self.assertFalse((home / "projects").exists())  # the host's folder is never used


class Stopped(unittest.TestCase):
    """Once claude starts the run costs money: whatever stops it or the steps after it, its row is written and
    its scratch and session folders are removed."""

    def stop(self, error: type, *argv: str, source: str = SESSION_STUB,
             **patches) -> tuple[list[dict], list[Path], list[Path]]:
        with tempfile.TemporaryDirectory() as tmp:
            home, scratch, out = Path(tmp) / "config", Path(tmp) / "scratch", Path(tmp) / "out.jsonl"
            runs = Path(tmp) / "runs"  # each run's own scratch folder is made here
            scratch.mkdir()
            (Path(tmp) / "a-file").write_text("not a folder")
            (Path(tmp) / "read-only").mkdir(mode=0o500)  # the runner can see it but no run can be kept in it
            argv = tuple(a.replace("<tmp>", tmp) for a in argv)
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(home), **AUTH}), \
                    unittest.mock.patch.object(tempfile, "tempdir", str(scratch)), \
                    unittest.mock.patch.object(run, "RUNS", runs), \
                    (unittest.mock.patch.multiple(run, **patches) if patches else contextlib.nullcontext()), \
                    self.assertRaises(error):
                run.main(["--tasks", "lookup-retry-limit", "--configs", "defaults", "--runs", "1", "--max-total", "3",
                          "--claude", *stub(tmp, source), "--out", str(out), *argv])
            rows = [json.loads(line) for line in out.read_text().splitlines()] if out.exists() else []
            sessions = list((home / "projects").iterdir()) if (home / "projects").exists() else []
            # The frozen plugin copy is made there too, and goes however the bench stops.
            return rows, list(scratch.iterdir()) + list(runs.iterdir()), sessions

    def test_an_interrupt_during_the_claude_call_writes_a_stopped_row_priced_from_its_transcripts_and_cleans_up(self):
        def interrupted(cmd, work, env, timeout):
            """Ctrl-C mid-call, once the session has started. Raised here, since a real SIGINT that lands inside a
            finalizer of this long-lived process is ignored."""
            folder = Path(env["CLAUDE_CONFIG_DIR"]) / "projects" / env["CLAUDE_CODE_PROJECT_DIR_NAME"]
            (folder / "s1").mkdir(parents=True)
            usage = {"input_tokens": 0, "output_tokens": 20000}  # $0.10 on Haiku 4.5
            (folder / "s1.jsonl").write_text(json.dumps({"message": {"id": "m", "model": "claude-haiku-4-5",
                                                                     "usage": usage}}) + "\n")
            raise KeyboardInterrupt

        with unittest.mock.patch.object(run, "run_claude", interrupted):
            rows, scratch, sessions = self.stop(KeyboardInterrupt)
        # A SIGTERM reaches the run the same way, so neither is charged the whole budget.
        self.assertEqual([(r["grade"]["verdict"], r["exit_code"], r["cost_known"], r["cost_source"], r["total_cost_usd"])
                          for r in rows], [("stopped", None, False, "transcripts", 0.1)])
        self.assertIn("KeyboardInterrupt", rows[0]["grade"]["reason"])
        self.assertEqual((scratch, sessions), ([], []))

    def test_an_interrupt_while_grading_still_writes_the_row_and_cleans_up(self):
        rows, scratch, sessions = self.stop(KeyboardInterrupt, grade=unittest.mock.Mock(side_effect=KeyboardInterrupt))
        self.assertEqual([(r["grade"]["verdict"], r["total_cost_usd"], r["passed"]) for r in rows],
                         [("stopped", 0.01, False)])
        self.assertIn("KeyboardInterrupt", rows[0]["grade"]["reason"])
        self.assertEqual((scratch, sessions), ([], []))

    @unittest.skipIf(sys.platform == "win32" or getattr(os, "geteuid", lambda: 1)() == 0,
                     "needs POSIX file modes, which root ignores")
    def test_a_keep_folder_that_cannot_be_written_keeps_the_graded_row_and_cleans_up(self):
        rows, scratch, sessions = self.stop(OSError, "--keep", str(Path("<tmp>") / "read-only"))
        self.assertEqual([(r["grade"]["verdict"], r["total_cost_usd"], "kept" in r) for r in rows], [("pass", 0.01, False)])
        self.assertEqual((scratch, sessions), ([], []))


class SuiteCoverage(unittest.TestCase):
    """Every task class and baseline the suite claims to cover is there and can't be passed by gaming."""

    def check(self, task, work, answer=""):
        return Tasks.check(self, task, work, answer)

    def shipped(self, task_id, tmp):
        task = next(t for t in run.load_tasks() if t["id"] == task_id)
        work = Path(tmp) / "work"
        shutil.copytree(task["dir"] / "repo", work)
        return task, work

    def test_every_task_class_has_two_tasks(self):
        tasks = run.load_tasks()
        for cls in run.CLASSES:
            least = 1 if cls in ("document", "operations", "redo") else 2  # one task each so far
            self.assertGreaterEqual(sum(t["class"] == cls for t in tasks), least, cls)

    def test_work_dir_code_cannot_exit_the_check_with_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, work = self.shipped("rootcause-cart", tmp)
            (work / "cart.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
            self.assertFalse(self.check(task, work))
            task, work = self.shipped("cross-retry-setting", Path(tmp) / "b")
            (work / "json.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
            self.assertFalse(self.check(task, work))
        for task_id, module in (("bugfix-median", "stats"), ("bugfix-pagination", "pages"),
                                ("impl-parse-duration", "durations"), ("impl-slugify", "text_utils")):
            with self.subTest(task=task_id), tempfile.TemporaryDirectory() as tmp:
                task, work = self.shipped(task_id, tmp)
                (work / f"{module}.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
                self.assertFalse(self.check(task, work))

    def test_the_forced_baseline_sets_what_its_name_says(self):
        configs = {c["id"]: c for c in run.load_configs()}
        options = {"model": "sonnet", "budget": 0.5, "claude": ["claude"], "plugin_dir": "/p/model-router"}
        cmd, env = run.command("x", configs["haiku-high"], options, {})
        self.assertEqual((env["CLAUDE_CODE_SUBAGENT_MODEL"], env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"],
                          env["CLAUDE_CODE_SUBAGENT_EFFORT"]), ("haiku", "1", "high"))
        self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", env)  # that one would move the main loop too
        self.assertNotIn("--plugin-dir", cmd)
        self.assertEqual(sorted(configs), ["defaults", "haiku-high", "plugin"])


LIMIT_STUB = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
calls = Path(os.environ["STUB_CALLS"])
n = int(calls.read_text()) if calls.exists() else 0
calls.write_text(str(n + 1))
if n < int(os.environ["STUB_LIMITED"]):
    print(json.dumps({"type": "result", "is_error": True, "subtype": "error_during_execution", "api_error_status": 429,
                      "result": "API Error: 429 rate limit reached",
                      "total_cost_usd": float(os.environ.get("STUB_LIMIT_COST", "0.001")), "modelUsage": {}}))
    sys.exit(1)
print(json.dumps({"type": "result", "is_error": False, "subtype": "success", "api_error_status": None, "total_cost_usd": 0.01,
                  "result": "read_retry_limit in app/settings.py; a rate limit design is off topic", "modelUsage": {}}))
'''


COST_STUB = '''#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
data = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "plugins" / "data" / "model-router-inline"
data.mkdir(parents=True)
rows = [{"event": "spawn", "via": "mod", "agent": "model-router:implementer", "action": "rewrite",
         "level": "standard", "model": "sonnet", "was": "opus", "effort": "medium", "source": "caller",
         "escalated_from": None, "reason": "the call names a model"},
        {"event": "spawn", "via": "hook", "agent": "model-router:verifier", "action": "keep", "level": "lookup",
         "model": "haiku", "was": "haiku", "effort": None, "source": "pin", "escalated_from": None, "reason": "x"},
        {"event": "usage", "agent_id": "a1", "agent": "model-router:implementer", "models": [], "running": True,
         "cost_usd": 0.05},
        {"event": "usage", "agent_id": "a2", "models": [], "running": True, "cost_usd": 0.01},
        {"event": "usage", "agent_id": "a1", "agent": "model-router:implementer", "models": [], "running": True,
         "cost_usd": 0.07}]
(data / "ledger.jsonl").write_text("\\n".join(json.dumps(r) for r in rows))
folder = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / os.environ["CLAUDE_CODE_PROJECT_DIR_NAME"]
sub = folder / "s1" / "subagents"
sub.mkdir(parents=True)
(sub / "agent-a2.meta.json").write_text(json.dumps({"agentType": "model-router:verifier"}))
import time
time.sleep(0.3)  # Claude Code's own duration_ms can leave time out
print(json.dumps({"type": "result", "result": "read_retry_limit in app/settings.py", "total_cost_usd": 0.11, "modelUsage": {},
                  "duration_ms": 1}))
'''


class AgentCosts(unittest.TestCase):
    def test_a_router_run_records_its_spend_by_agent_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude = stub(tmp, COST_STUB)
            task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
            options = {"claude": claude, "model": "sonnet", "budget": 0.5, "timeout": 60,
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(Path(tmp) / "config")}):
                row = run.run_one(task, next(c for c in run.load_configs() if c["id"] == "plugin"), options)
        self.assertEqual(row["agent_costs"], {"model-router:implementer": 0.07, "model-router:verifier": 0.01,
                                              "main": 0.03})
        self.assertEqual(row["duration_ms"], 1)
        self.assertGreaterEqual(row["wall_ms"], 300)  # the runner's own clock around the claude process
        self.assertEqual(row["routed"], [
            {"agent": "model-router:implementer", "level": "standard", "model": "sonnet", "effort": "medium",
             "source": "caller", "escalated_from": None},
            {"agent": "model-router:verifier", "level": "lookup", "model": "haiku", "effort": None, "source": "pin",
             "escalated_from": None}])


    def test_the_ledger_is_read_before_the_work_dir_code_runs(self):
        def rewrite(check, work, answer, timeout=120):
            data = run.plugin_data_dir({"CLAUDE_CONFIG_DIR": str(work.parent / "config")})
            (data / "ledger.jsonl").write_text('{"event": "usage", "cost_usd": 9}\n', encoding="utf-8")
            return run.Grade("pass", "", "0" * 16)

        with tempfile.TemporaryDirectory() as tmp:
            claude = stub(tmp, COST_STUB)
            task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
            options = {"claude": claude, "model": "sonnet", "budget": 0.5, "timeout": 60,
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(Path(tmp) / "config")}), \
                    unittest.mock.patch.object(run, "grade", rewrite):
                row = run.run_one(task, next(c for c in run.load_configs() if c["id"] == "plugin"), options)
        self.assertEqual(row["agent_costs"], {"model-router:implementer": 0.07, "model-router:verifier": 0.01,
                                              "main": 0.03})


class Limits(unittest.TestCase):
    """A rate or usage limit pauses the bench and retries the run; it is never recorded as a failed task."""

    def bench(self, limited: int, cost: str = "0.001", *spend: str) -> tuple[int, list[dict], list[dict], list[float]]:
        with tempfile.TemporaryDirectory() as tmp:
            claude = stub(tmp, LIMIT_STUB)
            out = Path(tmp) / "out.jsonl"
            env = {"STUB_CALLS": str(Path(tmp) / "calls"), "STUB_LIMITED": str(limited), "STUB_LIMIT_COST": cost}
            pauses: list[float] = []
            with unittest.mock.patch.dict("os.environ", {**env, **AUTH}), \
                    unittest.mock.patch.object(run.time, "sleep", pauses.append):
                code = run.main(["--tasks", "lookup-retry-limit", "--configs", "defaults", "--runs", "1",
                                 "--max-total", "3", *spend, "--claude", *claude, "--out", str(out)])
            read = lambda p: [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []  # noqa: E731
            self.assertEqual(sorted(p.name for p in Path(tmp).glob("*.jsonl")), ["out.jsonl"] if out.exists() else [])
            # subprocess also calls time.sleep in short steps while it waits on a slow child.
            return code, read(out), read(Path(tmp) / "limited" / "out.jsonl"), [p for p in pauses if p >= run.LIMIT_PAUSE]

    def test_a_limited_run_pauses_and_is_retried(self):
        code, rows, limited, pauses = self.bench(limited=2)
        self.assertEqual(code, 0)
        self.assertEqual([r["passed"] for r in rows], [True])
        self.assertEqual([r["api_error_status"] for r in limited], [429, 429])
        self.assertEqual(len(pauses), 2)
        self.assertLess(pauses[0], pauses[1])

    def test_a_limit_that_does_not_lift_stops_the_bench_cleanly(self):
        code, rows, limited, pauses = self.bench(limited=99)
        self.assertEqual(code, 2)
        self.assertEqual(rows, [])
        self.assertEqual(len(limited), run.LIMIT_RETRIES + 1)

    def test_a_retry_never_starts_past_the_total_cap(self):
        code, rows, limited, pauses = self.bench(99, "0.5", "--budget", "0.5", "--max-total", "1")
        self.assertEqual((code, rows), (1, []))
        self.assertLessEqual(sum(r["total_cost_usd"] for r in limited), 1)
        self.assertEqual(len(limited), 1)

    def test_a_lost_network_is_retried_like_a_limit(self):
        # The answers Claude Code gave during the 2026-11-22 outage, plus a dropped connection.
        for answer in ("API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)",
                       "API Error: No internet route — check your connection or VPN (ENETUNREACH)",
                       "Connection lost"):
            row = {"is_error": True, "exit_code": 1, "api_error_status": None, "stderr": "", "answer": answer}
            self.assertEqual(run.refusal(row), "network", answer)
        self.assertEqual(run.refusal({"is_error": True, "exit_code": 1, "api_error_status": 429, "stderr": "",
                                      "answer": ""}), "limit")
        for row in ({"is_error": True, "exit_code": 1, "api_error_status": None, "stderr": "",
                     "answer": "The fix fails: socket code raises ENOTFOUND when DNS is down."},
                    {"is_error": False, "exit_code": 0, "api_error_status": None, "stderr": "",
                     "answer": "Connection lost errors come from the retry wrapper."}):
            self.assertIsNone(run.refusal(row), row["answer"])

    def test_an_answer_about_rate_limits_is_not_a_limit(self):
        self.assertIsNone(run.refusal({"is_error": False, "api_error_status": None, "stderr": "",
                                       "answer": "Use a token bucket rate limiter; on the usage limit, return 429."}))
        # An errored run whose task text talks about limits failed the task; it wasn't refused by the API.
        for answer in ("Draft: a token bucket rate limiter keyed per client, 429 when over.",
                       "Too many requests hit the cache; the overloaded node needs a usage limit."):
            self.assertIsNone(run.refusal({"is_error": True, "exit_code": 1, "api_error_status": None,
                                           "stderr": "", "answer": answer}), answer)

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_the_bench_s_own_results_do_not_make_the_code_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "bench" / "results").mkdir(parents=True)
            (repo / "bench" / "run.py").write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm",
                            "base"], check=True)
            with unittest.mock.patch.object(run, "ROOT", repo):
                (repo / "bench" / "results" / "m6-lookup.jsonl").write_text("{}\n", encoding="utf-8")
                self.assertFalse(run._git_dirty())
                (repo / "bench" / "run.py").write_text("x = 2\n", encoding="utf-8")
                self.assertTrue(run._git_dirty())

    def test_the_messages_claude_code_prints_for_a_limit_are_limits(self):
        # https://code.claude.com/docs/en/errors.md, the usage-limit and server-error rows.
        for answer in ("API Error: Repeated 529 Overloaded errors", "Request rejected (429)",
                       "Server is temporarily limiting requests", "You've hit your session limit · resets 3pm",
                       "You've hit your weekly limit", "You've hit your Sonnet limit"):
            self.assertEqual(run.refusal({"is_error": True, "exit_code": 1, "api_error_status": None,
                                          "stderr": "", "answer": answer}), "limit", answer)
        self.assertEqual(run.refusal({"is_error": True, "api_error_status": 529, "stderr": "", "answer": ""}), "limit")
        self.assertEqual(run.refusal({"is_error": False, "exit_code": 1, "api_error_status": None,
                                      "stderr": "Error: API Error: Repeated 529 Overloaded errors", "answer": ""}),
                         "limit")

    def test_an_account_error_is_set_aside_not_retried(self):
        # What Claude Code 2.1.330 printed for an invalid API key: an error, nothing spent, no model usage.
        result = json.loads(API_ERROR.read_text(encoding="utf-8"))
        row = run.record({"id": "t", "class": "lookup"}, {"id": "defaults"}, Recorder.options,
                         result, dict(Recorder.facts, exit_code=1))
        self.assertEqual(run.refusal(row), "api_error")
        self.assertEqual((row["total_cost_usd"], row["cost_known"]), (0, True))
        errored = {"is_error": True, "exit_code": 1, "api_error_status": None, "stderr": "", "subtype": "success"}
        # The messages for the other two at https://code.claude.com/docs/en/errors.md.
        for answer in ("Credit balance is too low", "Model 'claude-fable-5-2' not found"):
            self.assertEqual(run.refusal(dict(errored, answer=answer, total_cost_usd=0)), "api_error", answer)
        # What ends a run part-way when the account can't pay or may not use the model, at
        # https://code.claude.com/docs/en/errors.md and https://platform.claude.com/docs/en/api/errors.md: it is set
        # aside whatever it spent.
        spent = dict(errored, total_cost_usd=0.41, api_error_status=400)
        for answer in ("Credit balance is too low", "Credit balance too low · Add funds: https://x",
                       "Usage credits ran out before the response was finished.",
                       "You've hit your org's monthly spend limit · visit https://x to raise it",
                       "You've hit your individual usage limit", "You've hit your team's shared budget · ask your admin",
                       'API Error: 400 {"type":"error","error":{"type":"invalid_request_error","message":"You have '
                       'reached your specified API usage limits. You will regain access on 2026-12-01"}}',
                       "Failed to authenticate: OAuth token revoked. Please log in again or contact your administrator.",
                       "Your ANTHROPIC_API_KEY belongs to a disabled organization · Update or unset the environment variable",
                       "There's an issue with the selected model (claude-x). It may not exist or you may not have access "
                       "to it. Run --model to pick a different model.", "Model 'claude-fable-5-2' not found"):
            self.assertEqual(run.refusal(dict(spent, answer=answer)), "api_error", answer)
        for status in (401, 402, 403):  # authentication, billing, permission
            self.assertEqual(run.refusal(dict(spent, answer="", api_error_status=status)), "api_error", status)
        self.assertEqual(run.refusal(dict(spent, answer="You've hit your session limit · resets 3pm")), "limit")
        # Task text that talks about credit or keys, or a run that didn't error, is a task's own answer.
        for row in (dict(spent, answer="The credit balance is too low to cover the refund, so it fails."),
                    dict(spent, answer="Draft: Invalid API key handling returns 401."),
                    dict(spent, is_error=False, exit_code=0, answer="Credit balance is too low")):
            self.assertIsNone(run.refusal(row), row["answer"])
        # A run that spent tried the task, and a zero cost on error_during_execution may have been zeroed.
        self.assertIsNone(run.refusal(dict(errored, answer="The fix is incomplete.", total_cost_usd=0.02)))
        self.assertIsNone(run.refusal(dict(errored, answer="", subtype="error_during_execution", total_cost_usd=0)))
        self.assertIsNone(run.refusal(dict(errored, is_error=False, exit_code=0, answer="hi", total_cost_usd=0)))

    def refused(self, refuse: str, configs: list[str], runs: int) -> tuple[int, list[dict], list[dict], list[float]]:
        with tempfile.TemporaryDirectory() as tmp:
            claude = stub(tmp, API_ERROR_STUB)
            out = Path(tmp) / "out.jsonl"
            pauses: list[float] = []
            with unittest.mock.patch.dict("os.environ", {"STUB_ERROR": str(API_ERROR), "STUB_REFUSED": refuse, **AUTH}), \
                    unittest.mock.patch.object(run.time, "sleep", pauses.append):
                # Seed 0 never puts three haiku-high runs in a row, so the streak of errors stays under the stop.
                code = run.main(["--tasks", "lookup-retry-limit", "--configs", *configs, "--runs", str(runs),
                                 "--seed", "0", "--max-total", "3", "--claude", *claude, "--out", str(out)])
            read = lambda p: [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []  # noqa: E731
            return code, read(out), read(Path(tmp) / "limited" / "out.jsonl"), [p for p in pauses if p >= run.LIMIT_PAUSE]

    def test_an_account_error_part_way_is_set_aside_with_its_spend_and_run_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.jsonl"
            argv = ["--tasks", "lookup-retry-limit", "--configs", "defaults", "--runs", str(run.API_ERROR_STOP + 1),
                    "--max-total", "9", "--claude", *stub(tmp, CREDIT_STUB), "--out", str(out)]
            limited = Path(tmp) / "limited" / "out.jsonl"
            for again in (1, 2):
                with unittest.mock.patch.dict("os.environ", AUTH), contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(run.main(argv), 2)
                self.assertIn(f"stopped: {run.API_ERROR_STOP} runs in a row set aside", err.getvalue())
                rows = [json.loads(line) for line in limited.read_text().splitlines()]
                # None is on record as a failed task, so the same command runs them again.
                self.assertFalse(out.exists())
                self.assertEqual([(r["limited_reason"], r["total_cost_usd"], r["passed"]) for r in rows],
                                 [("api_error", 0.41, False)] * run.API_ERROR_STOP * again)

    def test_api_errors_in_a_row_stop_the_bench_without_a_failed_task(self):
        code, rows, limited, pauses = self.refused("every run", ["defaults"], runs=run.API_ERROR_STOP + 2)
        self.assertEqual(code, 2)
        self.assertEqual(rows, [])
        self.assertEqual([r["limited_reason"] for r in limited], ["api_error"] * run.API_ERROR_STOP)
        self.assertEqual(pauses, [])

    def test_a_configuration_the_account_cannot_run_does_not_stop_the_others(self):
        code, rows, limited, pauses = self.refused("haiku", ["defaults", "haiku-high"], runs=run.API_ERROR_STOP)
        self.assertEqual(code, 0)
        self.assertEqual([(r["config"], r["passed"]) for r in rows], [("defaults", True)] * run.API_ERROR_STOP)
        self.assertEqual([(r["config"], r["limited_reason"]) for r in limited],
                         [("haiku-high", "api_error")] * run.API_ERROR_STOP)
        self.assertEqual(pauses, [])


API_ERROR = ROOT / "tests" / "fixtures" / "claude-2.1.330-api-error.json"

API_ERROR_STUB = '''#!/usr/bin/env python3
import json, os, sys
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
if os.environ["STUB_REFUSED"] in ("every run", os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL")):
    with open(os.environ["STUB_ERROR"], encoding="utf-8") as fh:
        print(json.dumps(json.load(fh)))  # stream-json puts each event on one line
    sys.exit(1)
print(json.dumps({"type": "result", "is_error": False, "subtype": "success", "api_error_status": None, "total_cost_usd": 0.01,
                  "result": "read_retry_limit in app/settings.py", "modelUsage": {}}))
'''


# The account runs dry part-way: $0.41 spent, then the API refuses for credit.
CREDIT_STUB = '''#!/usr/bin/env python3
import json, sys
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
print(json.dumps({"type": "system", "subtype": "init", "apiKeySource": "ANTHROPIC_API_KEY", "plugins": []}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "api_error_status": 400,
                  "terminal_reason": "api_error", "total_cost_usd": 0.41, "num_turns": 4,
                  "result": "Credit balance is too low", "permission_denials": [], "modelUsage": {}}))
sys.exit(1)
'''


CWD_STUB ='''#!/usr/bin/env python3
import json, os, sys
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
print(json.dumps({"type": "result", "result": os.getcwd(), "total_cost_usd": 0.0, "modelUsage": {}}))
'''


class TaskIdentity(unittest.TestCase):
    """A run's folder doesn't carry its task id, and a changed task is never pooled silently."""

    def test_no_task_id_reaches_a_runs_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude = stub(tmp, CWD_STUB)
            options = {"claude": claude, "model": "sonnet", "budget": 0.5, "timeout": 60,
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            base = str(Path(tempfile.gettempdir()).resolve())
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(Path(tmp) / "config")}):
                for task in run.load_tasks():
                    with self.subTest(task=task["id"]):
                        row = run.run_one(task, next(c for c in run.load_configs() if c["id"] == "plugin"), options)
                        ours = str(Path(row["answer"]).resolve()).replace(base, "")
                        self.assertNotIn(task["id"], ours)
                        self.assertEqual(row["task_revision"], task.get("revision", 1))

    def test_every_task_has_a_revision_and_the_changed_one_moved_on(self):
        for task in run.load_tasks():
            self.assertIsInstance(task.get("revision"), int, task["id"])
            self.assertGreaterEqual(task["revision"], 1, task["id"])
        tasks = {t["id"]: t for t in run.load_tasks()}
        self.assertEqual(tasks["design-cache-invalidation"]["revision"], 3)

    def test_rows_on_an_earlier_task_revision_are_left_out_of_the_tables(self):
        old = full_row(task_revision=1, **{"class": "design"})
        rows = [dict(old, task="design-cache-invalidation", config="plugin"),
                dict(old, task="design-cache-invalidation", config="defaults"),
                graded_today(dict(old, task="design-rate-limiter", config="plugin", total_cost_usd=0.5))]
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results"
            results.mkdir()
            (results / "a-design.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            loaded = analyze.load([results / "a-design.jsonl"])
            out = Path(tmp) / "page.md"
            analyze.main([str(results / "a-design.jsonl"), "--out", str(out)])
            page = out.read_text(encoding="utf-8")
        self.assertEqual(sorted(r["task"] for r in loaded.stale), ["design-cache-invalidation"] * 2)
        self.assertEqual([r["task"] for r in loaded.rows], ["design-rate-limiter"])
        self.assertIn("`design-cache-invalidation` is at revision 3; 2 of its runs ($", page)
        self.assertIn("are left out of every table", page)


class Isolation(unittest.TestCase):
    """Code a run leaves behind can't shadow the check's modules or fake a pass."""

    PLANT = 'import sys\nsys.stderr.write("Ran 1 test\\n\\nOK\\n")\nimport os\nos._exit(0)\n'
    FAILS = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.fail('no')\n"
    PASSES = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        pass\n"

    def test_tests_pass_ignores_a_planted_unittest_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            shipped, work = Path(tmp) / "shipped", Path(tmp) / "work"
            shipped.mkdir()
            work.mkdir()
            for text, want in ((self.FAILS, False), (self.PASSES, True)):
                (shipped / "test_x.py").write_text(text, encoding="utf-8")
                (work / "test_x.py").write_text(text, encoding="utf-8")
                for name in ("unittest.py", "json.py"):
                    (work / name).write_text(self.PLANT, encoding="utf-8")
                self.assertEqual(passcheck.tests_pass(work, shipped, "test_x.py"), want)

    def test_tests_pass_needs_the_shipped_test_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            shipped, work = Path(tmp) / "shipped", Path(tmp) / "work"
            shipped.mkdir()
            work.mkdir()
            (shipped / "test_x.py").write_text(self.FAILS, encoding="utf-8")
            (work / "test_x.py").write_text(self.PASSES, encoding="utf-8")
            self.assertFalse(passcheck.tests_pass(work, shipped, "test_x.py"))

    def test_tests_pass_fails_a_removed_test_and_raises_for_a_missing_shipped_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            shipped, work = Path(tmp) / "shipped", Path(tmp) / "work"
            shipped.mkdir()
            work.mkdir()
            (shipped / "test_x.py").write_text(self.PASSES, encoding="utf-8")
            self.assertFalse(passcheck.tests_pass(work, shipped, "test_x.py"))
            (work / "test_x.py").write_text(self.PASSES, encoding="utf-8")
            with self.assertRaises(OSError):  # a check bug, which run.grade reads as broken
                passcheck.tests_pass(work, shipped, "test_y.py")

    def test_planted_stdlib_names_cannot_fake_a_pass(self):
        for task in gradable_tasks():
            with self.subTest(task=task["id"]), tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / "work"
                shutil.copytree(task["dir"] / "repo", work)
                for name in ("json.py", "unittest.py"):
                    (work / name).write_text(self.PLANT, encoding="utf-8")
                answer = Path(tmp) / "answer.txt"
                answer.write_text(task.get("wrong_answer", ""), encoding="utf-8")
                self.assertFalse(run.grade(task["dir"] / "check.py", work, answer).passed)
                if (task["dir"] / "solution").is_dir():  # the same planted files don't cost an honest run its pass
                    shutil.copytree(task["dir"] / "solution", work, dirs_exist_ok=True)
                    answer.write_text(task.get("reference_answer", ""), encoding="utf-8")
                    self.assertTrue(run.grade(task["dir"] / "check.py", work, answer).passed)

    def test_a_check_runs_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            check = Path(tmp) / "check.py"
            check.write_text("import sys\nsys.exit(0 if sys.flags.isolated else 1)\n", encoding="utf-8")
            self.assertTrue(run.grade(check, Path(tmp), Path(tmp) / "answer.txt").passed)

    def test_a_check_sees_no_credentials(self):
        secret = {"CLAUDE_CODE_OAUTH_TOKEN": "t", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR": "3", "ANTHROPIC_API_KEY": "k",
                  "ANTHROPIC_BASE_URL": "u", "GITHUB_TOKEN": "g", "AWS_SECRET_ACCESS_KEY": "s", "DB_PASSWORD": "p",
                  "GH_PAT": "a", "PAT": "b", "NPM_AUTH": "c", "HTTP_AUTHORIZATION": "d", "GCP_CREDENTIALS": "e"}
        kept = {"MRBENCH_KEPT": "yes", "PATHEXT": ".COM", "MRBENCH_PATCHES": "1"}
        with tempfile.TemporaryDirectory() as tmp:
            check = Path(tmp) / "check.py"
            check.write_text("import os, sys\n"
                             f"sys.exit(0 if not set({sorted(secret)!r}) & set(os.environ) "
                             f"and set({sorted(kept)!r}) <= set(os.environ) and 'PATH' in os.environ else 1)\n",
                             encoding="utf-8")
            with unittest.mock.patch.dict("os.environ", {**secret, **kept}):
                self.assertTrue(run.grade(check, Path(tmp), Path(tmp) / "answer.txt").passed)
                self.assertEqual(sorted(set(secret) & set(run.grading_env(dict(os.environ)))), [])

    def test_a_check_runs_with_no_network_no_credential_files_and_writes_only_to_its_scratch(self):
        import socket
        server = socket.socket()
        self.addCleanup(server.close)
        server.bind(("127.0.0.1", 0))
        server.listen()
        with tempfile.TemporaryDirectory() as tmp:
            secret, outside = Path(tmp) / "netrc", Path(tmp) / "outside.txt"
            secret.write_text("machine example.test password p", encoding="utf-8")
            check = Path(tmp) / "check.py"
            check.write_text(f"""import socket, tempfile
from pathlib import Path
blocked = []
for name, act in (("credentials", lambda: Path({str(secret)!r}).read_text()),
                  ("network", lambda: socket.create_connection(("127.0.0.1", {server.getsockname()[1]}), 5).close()),
                  ("write", lambda: Path({str(outside)!r}).write_text("x")),
                  ("scratch", lambda: Path(tempfile.mkdtemp(), "x").write_text("x"))):
    try:
        act()
    except OSError:
        blocked.append(name)
print(" ".join(blocked) or "nothing")
""", encoding="utf-8")
            with unittest.mock.patch.object(run, "CREDENTIAL_FILES", (*run.CREDENTIAL_FILES, str(secret))):
                graded = run.grade(check, Path(tmp), Path(tmp) / "answer.txt")
                wrote = outside.exists()
                with unittest.mock.patch.object(run, "_can_sandbox", return_value=False):
                    bare = run.grade(check, Path(tmp), Path(tmp) / "answer.txt")
            if graded.sandbox is None:  # Windows, or a Linux host where bwrap can't start
                self.assertNotEqual(sys.platform, "darwin")
            else:
                self.assertEqual((graded.sandbox, graded.reason, wrote),
                                 ({"darwin": "sandbox-exec"}.get(sys.platform, "bwrap"), "credentials network write", False))
        # Where no sandbox can start the check still runs, and the row says it ran without one.
        self.assertEqual((bare.verdict, bare.sandbox, bare.reason), ("pass", None, "nothing"))
        self.assertIn("graded without a sandbox", (ROOT / "bench" / "README.md").read_text(encoding="utf-8"))

    def test_bwrap_hides_the_credential_files_and_the_network_and_binds_only_the_scratch_writable(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, file = Path(tmp) / "ssh", Path(tmp) / "netrc"
            folder.mkdir()
            file.write_text("x", encoding="utf-8")
            with unittest.mock.patch.object(run.sys, "platform", "linux"), \
                    unittest.mock.patch.object(run, "_can_sandbox", return_value=True), \
                    unittest.mock.patch.object(run, "CREDENTIAL_FILES", (str(folder), str(file), str(Path(tmp) / "gone"))):
                argv, name = run.grade_sandbox(Path(tmp) / "scratch")
        own = os.path.realpath(Path(tmp) / "scratch")
        self.assertEqual((name, argv[0], argv[-1]), ("bwrap", "bwrap", "--"))
        self.assertEqual(argv[1:4], ["--ro-bind", "/", "/"])
        self.assertIn("--unshare-net", argv)
        joined = " ".join(argv)
        self.assertIn(f"--bind {own} {own}", joined)
        self.assertIn(f"--tmpfs {os.path.realpath(folder)}", joined)
        self.assertIn(f"--ro-bind /dev/null {os.path.realpath(file)}", joined)
        self.assertNotIn("gone", joined)

    def test_credential_names_are_matched_in_any_case(self):
        # A plain dict: environment names are upper-cased on Windows, so os.environ can't hold these there.
        env = {"api_key": "1", "Github_Pat": "2", "my_auth": "3", "Db_Credential": "4", "keep": "5", "Path": "6"}
        self.assertEqual(run.grading_env(env), {"keep": "5", "Path": "6"})

    def test_a_check_that_hangs_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            check = Path(tmp) / "check.py"
            check.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            self.assertFalse(run.grade(check, Path(tmp), Path(tmp) / "answer.txt", timeout=1).passed)

    def test_no_work_module_name_resolves_without_the_work_dir(self):
        names = set()
        for task in run.load_tasks():
            for folder in ("repo", "solution"):
                if (task["dir"] / folder).is_dir():
                    names |= {p.stem if p.is_file() else p.name for p in (task["dir"] / folder).iterdir()
                              if p.suffix == ".py" or p.is_dir()}
        names = {n for n in names if n.isidentifier()} - {"__pycache__"}  # `.claude` can't be imported
        res = subprocess.run([sys.executable, "-I", "-c", "import importlib.util, sys; "
                              "print([n for n in sys.argv[1:] if importlib.util.find_spec(n)])", *sorted(names)],
                             capture_output=True, text=True)
        self.assertEqual(res.stdout.strip(), "[]")

    def test_checks_load_the_helper_by_path_and_append_the_work_dir(self):
        tasks = run.BENCH / "tasks"
        for check in [*tasks.glob("*/check.py"), *tasks.glob("*/probe.py")]:
            text = check.read_text(encoding="utf-8")
            self.assertNotIn("sys.path.insert", text, check)
            if "passcheck" in text:
                self.assertIn('sys.modules["passcheck"] = passcheck', text, check)


class Grading(unittest.TestCase):
    """A check's exit code is its verdict: 0 solved, 1 not solved, 2 the check itself broke."""

    def grade(self, body, timeout=60):
        with tempfile.TemporaryDirectory() as tmp:
            check = Path(tmp) / "check.py"
            check.write_text(body, encoding="utf-8")
            return run.grade(check, Path(tmp), Path(tmp) / "answer.txt", timeout=timeout)

    def test_exit_0_passes_exit_1_fails_and_the_last_line_printed_is_the_reason(self):
        ok = self.grade("print('noise')\nprint('both rows match')\nraise SystemExit(0)\n")
        self.assertEqual((ok.verdict, ok.passed, ok.reason), ("pass", True, "both rows match"))
        no = self.grade("print('the count is 5, not 4')\nraise SystemExit(1)\n")
        self.assertEqual((no.verdict, no.passed, no.reason), ("fail", False, "the count is 5, not 4"))
        self.assertEqual(self.grade("import os\nos._exit(3)\n").verdict, "fail")

    def test_finish_records_why_a_task_failed_and_solved_for_a_pass(self):
        for ok, want in ((True, "solved"), (False, "the count is 5"), (False, "not solved")):
            with self.subTest(ok=ok):
                body = ("import importlib.util\n_s = importlib.util.spec_from_file_location('passcheck', "
                        f"{str(run.BENCH / 'tasks' / 'passcheck.py')!r})\npasscheck = importlib.util.module_from_spec(_s)\n"
                        f"_s.loader.exec_module(passcheck)\npasscheck.finish({ok}, {'the count is 5' if want != 'not solved' else ''!r})\n")
                graded = self.grade(body)
                self.assertEqual((graded.verdict, graded.reason), ("pass" if ok else "fail", want))

    def test_an_error_in_the_check_is_broken_not_failed(self):
        for body in ("undefined_name\n", "import no_such_module\n", "raise SystemExit('x' + 1)\n",
                     "def f(:\n"):
            with self.subTest(body=body):
                graded = self.grade(body)
                self.assertEqual((graded.verdict, graded.passed), ("broken", False))
        self.assertIn("NameError", self.grade("undefined_name\n").reason)

    def test_work_dir_code_that_raises_or_exits_fails_instead_of_breaking_the_check(self):
        for fault in ("raise ValueError('bad')", "raise SystemExit(0)", "import sys; sys.exit(0)", "1 / 0"):
            with self.subTest(fault=fault):
                graded = self.grade("import importlib.util, sys\nfrom pathlib import Path\n"
                                    f"_s = importlib.util.spec_from_file_location('passcheck', {str(run.BENCH / 'tasks' / 'passcheck.py')!r})\n"
                                    "passcheck = importlib.util.module_from_spec(_s)\n_s.loader.exec_module(passcheck)\n"
                                    f"with passcheck.agent_code():\n    {fault}\n"
                                    "passcheck.finish(True)\n")
                self.assertEqual(graded.verdict, "fail")
                self.assertIn("the work dir's code raised", graded.reason)

    def test_no_check_swallows_an_error_of_its_own(self):
        import ast
        for path in sorted((run.BENCH / "tasks").glob("*/check.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ExceptHandler):
                    caught = {n.id for n in ast.walk(node.type) if isinstance(n, ast.Name)} if node.type else {"bare"}
                    self.assertFalse(caught & {"bare", "Exception", "BaseException"}, f"{path.parent.name}:{node.lineno}")

    def test_the_check_runs_on_a_copy_of_the_work_dir_away_from_the_harness_cwd(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as here:
            work = Path(tmp) / "work"
            work.mkdir()
            (work / "a.txt").write_text("a", encoding="utf-8")
            check = Path(tmp) / "check.py"
            check.write_text("import os, sys\nfrom pathlib import Path\n"
                             "if (Path(sys.argv[1]) / 'a.txt').read_text() != 'a':\n    raise SystemExit(1)\n"
                             "(Path(sys.argv[1]) / 'left.txt').write_text('x')\nPath('stray.txt').write_text('x')\n"
                             "print(os.getcwd())\n", encoding="utf-8")
            old = os.getcwd()
            os.chdir(here)
            try:
                graded = run.grade(check, work, Path(tmp) / "answer.txt")
            finally:
                os.chdir(old)
            self.assertEqual(graded.verdict, "pass", graded.reason)
            self.assertEqual(sorted(p.name for p in work.iterdir()), ["a.txt"])
            self.assertEqual(os.listdir(here), [])
            self.assertNotEqual(Path(graded.reason).resolve(), Path(here).resolve())
            self.assertFalse(Path(graded.reason).exists())

    def test_a_check_that_hangs_fails_with_a_reason(self):
        graded = self.grade("import time\ntime.sleep(30)\n", timeout=1)
        self.assertEqual(graded.verdict, "fail")
        self.assertIn("timed out", graded.reason)

    def test_the_hash_names_the_check_the_helper_and_any_script_beside_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = Path(tmp) / "tasks"
            (tasks / "t").mkdir(parents=True)
            (tasks / "passcheck.py").write_text("A = 1\n", encoding="utf-8")
            (tasks / "t" / "check.py").write_text("print(1)\n", encoding="utf-8")
            first = run.check_hash(tasks / "t" / "check.py")
            self.assertEqual(first, run.check_hash(tasks / "t" / "check.py"))
            self.assertEqual(len(first), 16)
            for path, text in (("t/check.py", "print(2)\n"), ("passcheck.py", "A = 2\n"), ("t/probe.py", "x\n")):
                (tasks / path).write_text(text, encoding="utf-8")
                changed = run.check_hash(tasks / "t" / "check.py")
                self.assertNotEqual(changed, first, path)
                first = changed

    def test_analyze_sets_aside_a_run_whose_check_broke_or_that_the_bench_stopped(self):
        good = full_row(config="defaults", task="design-rate-limiter", task_revision=1, passed=True, **{"class": "design"})
        bad = dict(good, passed=False, total_cost_usd=0.25, grade=dict(good["grade"], verdict="broken", reason="NameError: x"))
        stopped = dict(good, task="design-event-partitions", passed=False, total_cost_usd=0.5,
                       grade=dict(good["grade"], verdict="stopped", reason="the bench stopped before the check finished"))
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results"
            results.mkdir()
            (results / "a.jsonl").write_text("".join(json.dumps(graded_today(r)) + "\n" for r in (good, bad, stopped)))
            loaded = analyze.load([results / "a.jsonl"])
            out = Path(tmp) / "page.md"
            analyze.main([str(results / "a.jsonl"), "--out", str(out)])
            page = out.read_text(encoding="utf-8")
        self.assertEqual((len(loaded.rows), len(loaded.broken)), (1, 2))
        self.assertIn("1 run(s) ($0.25) are set aside, though they stay in the results files: the pass check of "
                      "`design-rate-limiter` broke on them", page)
        self.assertIn("1 run(s) ($0.50) are set aside, though they stay in the results files: the bench itself stopped "
                      "them before their check finished", page)
        self.assertNotIn("`design-event-partitions` broke", page)


class Pilot(unittest.TestCase):
    def test_the_committed_page_is_what_analyze_writes(self):
        raw = sorted((ROOT / "bench" / "results").glob("*.jsonl"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = analyze.main([*map(str, raw), "--out", str(ROOT / "docs" / "BENCHMARKS.md"), "--check"])
        self.assertEqual(code, 0, err.getvalue())

    def test_the_page_keeps_its_cost_and_isolation_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = analyze.build_page(analyze.load([Analysis.page_args(tmp)[0]]))
        self.assertIn("plus anything that subagents still running", page)
        self.assertIn("margin, not a bound", page)
        self.assertIn("`sandbox.autoAllowBashIfSandboxed`", page)
        self.assertIn("`plugin` (Model Router with today's defaults", page)
        self.assertIn("server-managed settings", page)
        self.assertNotIn("Bash(tail *)", page)
        self.assertNotIn("`--no-session-persistence`, `--permission-mode", page)
        self.assertNotIn("puts it back afterwards", page)

    def test_recorded_lookup_answers_still_grade_the_same(self):
        raw = sorted((ROOT / "bench" / "results").glob("*.jsonl"))
        data = [json.loads(line) for path in raw for line in path.read_text(encoding="utf-8").splitlines() if line]
        revisions = {t["id"]: t.get("revision", 1) for t in run.load_tasks()}
        for row in data:  # a lookup is graded from its answer alone, so today's check must agree with the record
            if (row["class"] == "lookup" and len(row["answer"]) < 2000  # the line keeps 2,000 characters
                    and row.get("task_revision", 1) == revisions[row["task"]]):  # an older revision's check differed
                with tempfile.TemporaryDirectory() as tmp:
                    answer = Path(tmp) / "answer.txt"
                    answer.write_text(row["answer"], encoding="utf-8")
                    check = ROOT / "bench" / "tasks" / row["task"] / "check.py"
                    self.assertEqual(run.grade(check, Path(tmp), answer).passed, row["passed"], row["config"])


class HardTier(unittest.TestCase):
    """Each harder task's check rejects the mistake the task is built around and takes any fair right answer."""

    def check(self, task, work, answer=""):
        return Tasks.check(self, task, work, answer)

    def shipped(self, task_id, tmp):
        return SuiteCoverage.shipped(self, task_id, tmp)

    def source(self, task_id, rel):
        return (run.BENCH / "tasks" / task_id / rel).read_text(encoding="utf-8")

    def run_with(self, task_id, files, answer=""):
        """The check's verdict on the shipped repo with the solution and then `files` written over it."""
        with tempfile.TemporaryDirectory() as tmp:
            task, work = self.shipped(task_id, tmp)
            solution = task["dir"] / "solution"
            if solution.is_dir():
                shutil.copytree(solution, work, dirs_exist_ok=True)
            for rel, text in files.items():
                (work / rel).parent.mkdir(parents=True, exist_ok=True)
                (work / rel).write_text(text if isinstance(text, str) else json.dumps(text), encoding="utf-8")
            return self.check(task, work, answer)

    def test_the_harder_tier_has_a_task_in_every_class(self):
        hard = {"lookup-effective-timeout", "impl-semver-compare", "bugfix-split-amount", "cross-tenant-cache",
                "security-prefix-check", "rootcause-cached-rates", "design-event-partitions",
                "research-yanked-release"}
        tasks = {t["id"]: t for t in run.load_tasks()}
        self.assertEqual({tasks[t]["class"] for t in hard}, set(run.CLASSES) - {"document", "analysis", "operations", "redo"})


class ProbeHelper(unittest.TestCase):
    CASES = (
        ('import sys, json\nprint("noise")\nprint(json.dumps(json.load(sys.stdin)))', {"x": 1}),
        ('print("{\\"a\\": 1}")\nprint("trailing")', None),
        ('import os\nprint("{\\"a\\": 1}", flush=True)\nos._exit(3)', None),
        ('import os\nos._exit(0)', None),
        ('import time\ntime.sleep(30)', None),
        ('import sys, json\nprint(json.dumps(sys.flags.isolated))', 1),
        ('print("[" * 100000 + "]" * 100000)', None),  # too deep for json.loads
    )

    def test_probe_returns_the_last_json_line_and_none_on_anything_else(self):
        for body, want in self.CASES:
            with self.subTest(body=body), tempfile.TemporaryDirectory() as tmp:
                script = Path(tmp) / "probe.py"
                script.write_text(body, encoding="utf-8")
                self.assertEqual(passcheck.probe(Path(tmp), script, {"x": 1}, timeout=5), want)


class QualityTier(unittest.TestCase):
    """Each quality-tier task's check rejects the mistakes the task is built around and takes a fair right answer."""
    check, shipped, source, run_with = HardTier.check, HardTier.shipped, HardTier.source, HardTier.run_with

    def test_the_quality_tier_has_one_task_in_each_of_six_classes(self):
        want = {"impl-merge-customers": "implementation", "bugfix-statement-queries": "bugfix",
                "security-admin-route": "security-review", "rootcause-wallet-race": "root-cause",
                "cross-amount-cents": "cross-module", "design-checkout-retries": "design"}
        tasks = {t["id"]: t for t in run.load_tasks()}
        self.assertEqual({i: tasks[i]["class"] for i in want}, want)

    def test_sqlite_carries_a_rename_into_foreign_keys(self):
        self.assertGreaterEqual(sqlite3.sqlite_version_info, (3, 26, 0))
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA legacy_alter_table = OFF")  # some builds, such as Apple's, default to ON
        conn.executescript("CREATE TABLE parent (id INTEGER PRIMARY KEY);"
                           "CREATE TABLE child (id INTEGER PRIMARY KEY, p INTEGER REFERENCES parent(id));"
                           "ALTER TABLE parent RENAME TO old_parent;")
        self.assertEqual([row[2] for row in conn.execute("PRAGMA foreign_key_list(child)")], ["old_parent"])

    def test_the_migration_probe_pins_legacy_transaction_control(self):
        probe = self.source("impl-merge-customers", "probe.py")
        db = self.source("impl-merge-customers", "repo/store/db.py")
        for text in (probe, db):
            self.assertIn("LEGACY_TRANSACTION_CONTROL", text)
            self.assertIn("legacy_alter_table", text)

    def test_sqlite_lower_folds_ascii_only(self):
        conn = sqlite3.connect(":memory:")
        self.assertEqual(conn.execute("SELECT lower('\u00c9'), lower('A')").fetchone(), ("\u00c9", "a"),
                         "this SQLite is built with ICU, so lower() folds non-ASCII letters and the task's data is wrong")

    def test_the_shipped_repo_has_no_comment_that_names_the_trap_or_clears_a_decoy(self):
        for path in (run.BENCH / "tasks" / "security-admin-route" / "repo").rglob("*.py"):
            with self.subTest(path=path.name):
                self.assertFalse([l for l in path.read_text(encoding="utf-8").splitlines() if l.lstrip().startswith("#")])

    def test_the_shipped_route_serves_the_admin_handler_to_a_user_on_a_double_encoded_path(self):
        code = ("import json, sys\nsys.path.append(sys.argv[1])\nfrom app import handle\nfrom auth import session\n"
                "print(json.dumps([list(handle({'cookie': session.issue(who), 'path': path})) for who, path in "
                "json.loads(sys.argv[2])]))")
        cases = [["bob", "/%2561dmin/users"], ["bob", "/admin/users"], ["bob", "/%61dmin/users"], ["alice", "/admin/users"]]
        with tempfile.TemporaryDirectory() as tmp:
            task, work = self.shipped("security-admin-route", tmp)
            res = subprocess.run([sys.executable, "-I", "-c", code, str(work), json.dumps(cases)], cwd=tmp,
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        sneaked, plain, once, admin = json.loads(res.stdout)
        self.assertEqual((plain[0], once[0], admin[0], sneaked[0]), (403, 403, 200, 200))
        self.assertTrue(admin[1])
        self.assertEqual(sneaked[1], admin[1])

    def test_cents_check_gives_the_same_verdicts_on_other_seeds(self):
        spec = importlib.util.spec_from_file_location("cents_check", run.BENCH / "tasks" / "cross-amount-cents" / "check.py")
        check, saved = importlib.util.module_from_spec(spec), sys.modules.get("passcheck")
        try:
            spec.loader.exec_module(check)
        finally:
            sys.modules["passcheck"] = saved
        task = next(t for t in run.load_tasks() if t["id"] == "cross-amount-cents")
        builds = [("the solution", None, True)] + [(f"{kind}/{p.name}", p, kind == "right")
                                                     for kind in ("right", "wrong") for p in overlay_dirs(task, kind)]
        with tempfile.TemporaryDirectory() as tmp:
            for n, (name, overlay, expected) in enumerate(builds):
                work = Path(tmp) / str(n)
                build_work(task, overlay, work)
                for seed in range(1, 11):
                    with self.subTest(build=name, seed=seed):
                        self.assertEqual(check.passes(work, seed), expected)

    def test_the_cents_repo_has_no_comment(self):
        for path in (run.BENCH / "tasks" / "cross-amount-cents" / "repo").rglob("*.py"):
            with self.subTest(path=path.name):
                self.assertFalse([l for l in path.read_text(encoding="utf-8").splitlines() if l.lstrip().startswith("#")])

    def test_the_shipped_cents_repo_still_works_in_dollars(self):
        code = ("import json, sys\nsys.path.append(sys.argv[1])\nfrom shop import events, invoice\n"
                "log = []\nevents.record(log, 'charge', 'o1', 19.99)\n"
                "print(json.dumps([log, invoice.lines(log, 'o1')]))")
        with tempfile.TemporaryDirectory() as tmp:
            task, work = self.shipped("cross-amount-cents", tmp)
            res = subprocess.run([sys.executable, "-I", "-c", code, str(work)], cwd=tmp, capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout), [[{"kind": "charge", "order_id": "o1", "amount": 19.99}], ["charge 19.99"]])

    def test_each_rejected_retry_design_breaks_a_fact_spec_md_states(self):
        good = json.loads(self.source("design-checkout-retries", "solution/DESIGN.json"))
        spec = " ".join(self.source("design-checkout-retries", "repo/SPEC.md").split())
        for change, facts in (({"retry_at": "checkout"}, ("running the whole handler again", "reserves the stock twice")),
                              ({"retry_at": "gateway"}, ("calling the checkout service again", "reserves the stock twice")),
                              ({"gateway_retries": "on"}, ("on by default with 3 attempts", "twice the normal request rate")),
                              ({"attempts": 3}, ("twice the normal request rate",)),
                              ({"attempts": 1}, ("survive one failed or slow provider call",)),
                              ({"idempotency_key": "per_attempt"}, ("may still have charged", "idempotent only when"))):
            with self.subTest(change=change):
                for fact in facts:
                    self.assertIn(fact, spec)
                self.assertFalse(self.run_with("design-checkout-retries", {"DESIGN.json": {**good, **change}}))

        def ms(pattern):
            return int(re.search(pattern, spec).group(1).replace(",", ""))

        self.assertIn("At most 1 in 100 charges may need a second try", spec)
        self.assertIn("even when every try times out", spec)
        p99, gap = ms(r"p99 ([\d,]+) ms"), ms(r"at least ([\d,]+) ms between attempts")
        before, after, deadline = ms(r"up to ([\d,]+) ms before"), ms(r"([\d,]+) ms after"), ms(r"after ([\d,]+) ms\.")
        self.assertEqual((p99, gap, before, after, deadline), (600, 50, 200, 100, 3000))

        def holds(d):
            return (d["per_try_timeout_ms"] >= p99 and d["backoff_ms"] >= gap and before + d["attempts"]
                    * d["per_try_timeout_ms"] + (d["attempts"] - 1) * d["backoff_ms"] + after <= deadline)

        self.assertTrue(holds(good))
        for change in ({"per_try_timeout_ms": 550}, {"backoff_ms": 40}, {"per_try_timeout_ms": 1400, "backoff_ms": 50},
                       {"per_try_timeout_ms": 1326, "backoff_ms": 50}):
            with self.subTest(change=change):
                self.assertFalse(holds({**good, **change}))
                self.assertFalse(self.run_with("design-checkout-retries", {"DESIGN.json": {**good, **change}}))


    def wallet_phases(self, files):
        """The probe output for the wallet solution with `files` written over it, and the phases the check calls wrong."""
        with tempfile.TemporaryDirectory() as tmp:
            task, work = self.shipped("rootcause-wallet-race", tmp)
            shutil.copytree(task["dir"] / "solution", work, dirs_exist_ok=True)
            for rel, text in files.items():
                (work / rel).write_text(text, encoding="utf-8")
            kept = sys.modules.get("passcheck")
            try:
                spec = importlib.util.spec_from_file_location("wallet_check", task["dir"] / "check.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                got = module.run_probe(work)
            finally:
                sys.modules["passcheck"] = kept
            return got, module.wrong_phases(got)

    def test_wallet_check_rejects_each_known_mistake(self):
        task = "rootcause-wallet-race"
        rel = "ledger/wallet.py"
        solution = self.source(task, "solution/ledger/wallet.py")
        shipped = self.source(task, "repo/ledger/wallet.py")
        self.assertEqual(self.wallet_phases({rel: solution})[1], [])
        guard = "import threading\n\n_MUTEX = threading.Lock()\n"
        credit = solution[solution.index("def credit"):solution.index("def transfer")]
        local = solution.replace("store.lock(wallet)", "_MUTEX").replace(
            "contextlib.ExitStack() as locks", "_MUTEX").replace("            locks.enter_context(store.lock(key))\n", "").replace(
            "        for key in sorted({src, dst}):\n", "")
        self.assertIn("with _MUTEX:\n        available", local)
        keys = "sorted({src, dst})"
        cas_add = ("def _add(store, wallet, cents):\n    while True:\n        seen = store.get(wallet)\n"
                   "        if store.compare_and_put(wallet, seen or 0, (seen or 0) + cents):\n            return\n\n\n")
        livelock = ("class InsufficientFunds(Exception):\n    pass\n\n\ndef balance(store, wallet):\n    return store.get(wallet) or 0\n\n\n"
                    + cas_add + "def credit(store, wallet, cents):\n    _add(store, wallet, cents)\n\n\n"
                    "def transfer(store, src, dst, cents):\n    while True:\n        seen = store.get(src)\n"
                    "        if (seen or 0) < cents:\n            raise InsufficientFunds(src)\n        if src == dst:\n            return\n"
                    "        if store.compare_and_put(src, seen, seen - cents):\n            break\n    _add(store, dst, cents)\n")
        # name: (files, phase letters of which the check must call at least one wrong)
        mutants = {
            "shipped": ({rel: shipped}, "C"),
            "locks in argument order": ({rel: solution.replace(keys, "dict.fromkeys((src, dst))")}, "C"),
            "process-local lock": ({rel: guard + local}, "A"),
            "process-local lock in a helper module": ({"ledger/guard.py": guard, rel: "from ledger.guard import _MUTEX\n" + local}, "A"),
            "one global key": ({rel: solution.replace("store.lock(wallet)", 'store.lock("wallets")').replace(
                "store.lock(key)", 'store.lock("wallets")').replace(keys, "{src}")}, "B"),
            "global transfers lock": ({rel: solution.replace(
                "        for key in " + keys, '        locks.enter_context(store.lock("transfers"))\n        for key in ' + keys)}, "G"),
            "locks source only": ({rel: solution.replace(keys, "[src]")}, "H"),
            "locks destination only": ({rel: solution.replace(keys, "[dst]")}, "D"),
            "self transfer locks twice": ({rel: solution.replace(keys, "sorted([src, dst])")}, "E"),
            "self transfer creates money": ({rel: solution.replace(
                "        if src != dst:\n            store.put(src, available - cents)\n            store.put(dst, balance(store, dst) + cents)",
                "        target = balance(store, dst)\n        store.put(src, available - cents)\n        store.put(dst, target + cents)")}, "E"),
            "funds check refuses an exact balance": ({rel: solution.replace("available < cents", "available <= cents")}, "E"),
            "funds read outside the lock": ({rel: solution.replace(
                "    with contextlib.ExitStack() as locks:", "    available = balance(store, src)\n    with contextlib.ExitStack() as locks:").replace(
                "        available = balance(store, src)\n", "")}, "D"),
            "credit unlocked": ({rel: solution.replace(credit, "def credit(store, wallet, cents):\n    store.put(wallet, balance(store, wallet) + cents)\n\n\n")}, "A"),
            "credit put and verify": ({rel: solution.replace(credit, (
                "def credit(store, wallet, cents):\n    while True:\n        want = balance(store, wallet) + cents\n"
                "        store.put(wallet, want)\n        if balance(store, wallet) == want:\n            return\n\n\n"))}, "AFH"),
            "no funds check": ({rel: solution.replace("        if available < cents:\n            raise InsufficientFunds(src)\n", "")}, "D"),
            "compare and put that never takes an absent wallet": ({rel: livelock}, "A"),
        }
        for name, (files, phase) in mutants.items():
            with self.subTest(name):
                self.assertNotEqual(files[rel], solution if name != "shipped" else "")
                got, wrong = self.wallet_phases(files)
                self.assertIsNotNone(got, "the probe must answer inside its own timeout")
                self.assertTrue(set(wrong) & set(phase), wrong)
                if name.startswith("compare and put"):
                    self.assertIs(got["A"]["hung"], True)
                    self.assertEqual(wrong[0], "A")

    def test_wallet_check_takes_a_compare_and_put_solution(self):
        task = "rootcause-wallet-race"
        rel = "ledger/wallet.py"
        solution = self.source(task, "solution/ledger/wallet.py")
        cas = (
            "class InsufficientFunds(Exception):\n    pass\n\n\n"
            "def balance(store, wallet):\n    return store.get(wallet) or 0\n\n\n"
            "def _add(store, wallet, cents):\n    while True:\n        seen = store.get(wallet)\n"
            "        if store.compare_and_put(wallet, seen, (seen or 0) + cents):\n            return\n\n\n"
            "def credit(store, wallet, cents):\n    _add(store, wallet, cents)\n\n\n"
            "def transfer(store, src, dst, cents):\n    while True:\n        seen = store.get(src)\n"
            "        if (seen or 0) < cents:\n            raise InsufficientFunds(src)\n"
            "        if src == dst:\n            return\n"
            "        if store.compare_and_put(src, seen, seen - cents):\n            break\n    _add(store, dst, cents)\n")
        self.assertTrue(self.run_with(task, {rel: cas}))
        noisy = solution.replace("    with store.lock(wallet):", '    print("credit")\n    with store.lock(wallet):', 1)
        self.assertNotEqual(noisy, solution)
        self.assertTrue(self.run_with(task, {rel: noisy}))
        ordered = {"ledger/order.py": "def ordered(*keys):\n    return sorted(set(keys))\n",
                   rel: "from . import order\n" + solution.replace("sorted({src, dst})", "order.ordered(src, dst)")}
        self.assertTrue(self.run_with(task, ordered))

    def test_wallet_check_takes_a_same_wallet_transfer_that_returns_early(self):
        task = "rootcause-wallet-race"
        rel = "ledger/wallet.py"
        solution = self.source(task, "solution/ledger/wallet.py")
        early = solution.replace(
            "    with contextlib.ExitStack() as locks:",
            "    if src == dst:\n        return\n    with contextlib.ExitStack() as locks:")
        self.assertNotEqual(early, solution)
        got, wrong = self.wallet_phases({rel: early})
        self.assertEqual(wrong, [])
        self.assertEqual(got["E"]["raised"], [])

    def test_a_locked_wallet_solution_does_not_wait_out_the_barriers(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, work = self.shipped("rootcause-wallet-race", tmp)
            shutil.copytree(task["dir"] / "solution", work, dirs_exist_ok=True)
            (Path(tmp) / "result.txt").write_text("", encoding="utf-8")
            start = time.monotonic()
            verdict = run.grade(task["dir"] / "check.py", work, Path(tmp) / "result.txt").passed
            took = time.monotonic() - start
        self.assertTrue(verdict)
        self.assertLess(took, 15)  # five 5 s barriers would take 25 s

    def test_the_wallet_check_ends_inside_the_grade_timeout_on_the_shipped_deadlock(self):
        task_id = "rootcause-wallet-race"
        with tempfile.TemporaryDirectory() as tmp:
            task, work = self.shipped(task_id, tmp)
            (Path(tmp) / "result.txt").write_text("", encoding="utf-8")
            start = time.monotonic()
            verdict = run.grade(task["dir"] / "check.py", work, Path(tmp) / "result.txt").passed
            took = time.monotonic() - start
        self.assertFalse(verdict)
        self.assertLess(took, 40)


WORK_TASKS = {"legal-services-agreement": ("document", "TERMS.json"),
              "finance-ledger-reconcile": ("analysis", "MISMATCHES.json"),
              "finance-runway-forecast": ("analysis", "RUNWAY.json"),
              "research-study-synthesis": ("research", "SYNTHESIS.json"),
              "ops-inbox-triage": ("operations", "TRIAGE.json")}


class WorkTasks(unittest.TestCase):
    """The tasks of work that isn't code: an answer file a check grades, built around traps a careless pass falls into."""

    check, shipped, run_with = HardTier.check, HardTier.shipped, HardTier.run_with

    def test_each_work_task_has_its_class_and_names_its_answer_file(self):
        tasks = {t["id"]: t for t in run.load_tasks()}
        for task_id, (cls, answer) in WORK_TASKS.items():
            with self.subTest(task=task_id):
                task = tasks[task_id]
                self.assertEqual((task["class"], task["revision"]), (cls, 2 if task_id == "ops-inbox-triage" else 1))
                self.assertIn(answer, task["brief"])
                self.assertTrue((task["dir"] / "solution" / answer).is_file())
                self.assertFalse(list((task["dir"] / "repo").rglob(answer)))

    def test_each_work_task_has_two_wrong_answers_that_fail_for_a_stated_reason(self):
        tasks = {t["id"]: t for t in run.load_tasks()}
        for task_id in WORK_TASKS:
            with self.subTest(task=task_id):
                wrong = overlay_dirs(tasks[task_id], "wrong")
                self.assertGreaterEqual(len(wrong), 2)
                self.assertTrue(all((w / "expect.txt").is_file() for w in wrong))

    def test_the_work_tasks_ship_the_documents_and_data_their_briefs_describe(self):
        tasks = {t["id"]: t["dir"] / "repo" for t in run.load_tasks()}
        for name in ("bank.csv", "gl.csv"):
            rows = (tasks["finance-ledger-reconcile"] / name).read_text(encoding="utf-8").splitlines()
            self.assertTrue(380 <= len(rows) - 1 <= 420, name)
        self.assertEqual(len(list((tasks["research-study-synthesis"] / "studies").glob("*.md"))), 10)
        self.assertEqual(len(list((tasks["ops-inbox-triage"] / "inbox").glob("*.txt"))), 30)
        words = len((tasks["legal-services-agreement"] / "Services-Agreement.md").read_text(encoding="utf-8").split())
        self.assertGreater(words, 3500)  # about eight pages

    def test_a_number_that_isnt_finite_is_no_number(self):
        for value in (float("nan"), float("inf"), float("-inf"), "nan", "NaN", "inf", "-Infinity"):
            with self.subTest(value=value):
                self.assertIsNone(passcheck.numeric(value))
        self.assertEqual(passcheck.numeric("10.2"), 10.2)

    def test_the_runway_check_fails_a_runway_that_isnt_a_finite_number(self):
        for months in ("NaN", '"nan"', "Infinity", '"inf"'):
            with self.subTest(months=months):
                body = '{"months_of_runway": ' + months + ', "cash_out_month": "2027-08"}'
                self.assertFalse(self.run_with("finance-runway-forecast", {"RUNWAY.json": body}))

    def test_the_finance_checks_work_their_answer_out_from_the_shipped_data(self):
        edits = {"finance-ledger-reconcile": ("gl.csv", "TRX-200492,1010 Operating cash,-2412.10",
                                              "TRX-200492,1010 Operating cash,-2412.61", "TRX-200492 reconciles but is listed"),
                 "finance-runway-forecast": ("actuals.csv", ",2150000\n", ",2250000\n",
                                             "months_of_runway is 10.2, expected 10.66")}
        for task_id, (name, old, new, reason) in edits.items():
            with self.subTest(task=task_id), tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / "tasks" / task_id
                shutil.copytree(run.BENCH / "tasks" / task_id, copy)
                shutil.copy(run.BENCH / "tasks" / "passcheck.py", copy.parent)
                data = copy / "repo" / name
                text = data.read_text(encoding="utf-8")
                self.assertIn(old, text)
                data.write_text(text.replace(old, new), encoding="utf-8")
                task = {"dir": copy}
                work = Path(tmp) / "work"
                build_work(task, None, work)
                answer = Path(tmp) / "answer.txt"
                answer.write_text("", encoding="utf-8")
                graded = run.grade(copy / "check.py", work, answer, timeout=60)
                self.assertEqual(graded.verdict, "fail", graded.reason)
                self.assertIn(reason, graded.reason)


class DelegationTasks(unittest.TestCase):
    """Tasks that measure how work is handed out: a redo after a failing check, calls started together, a plan
    and then a build. Each one's prompt lets the session start more than one subagent."""
    WANT = {"redo-failing-check": ("redo", "redo"), "fanout-three-lookups": ("lookup", "fanout"),
            "mixed-plan-then-build": ("cross-module", "plan-build"),
            "workflow-two-stage-report": ("cross-module", "workflow")}

    def task(self, task_id):
        return next(t for t in run.load_tasks() if t["id"] == task_id)

    def test_each_delegation_task_has_its_class_and_shape(self):
        tasks = {t["id"]: t for t in run.load_tasks()}
        self.assertEqual({i: (tasks[i]["class"], tasks[i].get("shape")) for i in self.WANT}, self.WANT)

    def test_a_task_without_a_shape_gets_the_one_subagent_prompt(self):
        self.assertEqual(run.prompt({"brief": "Do x."}), run.PROMPT.format(brief="Do x."))

    def test_each_shape_keeps_the_brief_and_lets_the_session_start_more_than_one_subagent(self):
        self.assertEqual(set(run.SHAPES), {"redo", "fanout", "plan-build", "workflow"})
        for shape in run.SHAPES:
            task = {"brief": "Do x.", "shape": shape}
            with self.subTest(shape=shape):
                prompt = run.prompt(task)
                self.assertTrue(prompt.endswith("\n\nTask: Do x."))
                self.assertNotIn("one subagent", prompt)
                self.assertNotRegex(prompt, "[\u2013\u2014]")

    def test_the_workflow_prompt_has_the_session_write_and_run_a_script_of_stages(self):
        task = self.task("workflow-two-stage-report")
        prompt = run.prompt(task)
        self.assertIn("one Workflow tool call", prompt)
        self.assertIn("one `agent()` stage for each step", prompt)
        head = prompt.split("\n\nTask: ")[0]
        self.assertNotRegex(head, r"haiku|sonnet|opus|fable|model|effort")  # which model a stage names is measured
        self.assertTrue(prompt.endswith(f"\n\nTask: {task['brief']}"))
        self.assertFalse((task["dir"] / "repo" / ".claude").exists())  # a saved script would fix the stages' models

    def test_only_a_workflow_task_may_call_workflow_and_read(self):
        defaults = next(c for c in run.load_configs() if c["id"] == "defaults")
        for task_id, extra in (("workflow-two-stage-report", ["Workflow", "Read(./**)"]), ("fanout-three-lookups", [])):
            with self.subTest(task=task_id), tempfile.TemporaryDirectory() as tmp:
                seen = Path(tmp) / "seen.json"
                options = {"claude": stub(Path(tmp), STREAM_STUB), "model": "sonnet", "budget": 0.5, "timeout": 60,
                           "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
                with unittest.mock.patch.dict("os.environ", {"STUB_SEEN": str(seen)}):
                    run.run_one(self.task(task_id), defaults, options)
                argv = json.loads(seen.read_text())["argv"]
                self.assertEqual(argv[argv.index("--allowedTools") + 1:], [*run.TOOLS, *extra])
                self.assertNotIn("Read", argv)  # a bare Read would approve reading anywhere

    def test_an_unknown_shape_is_an_error(self):
        with self.assertRaises(KeyError):
            run.prompt({"brief": "Do x.", "shape": "swarm"})
        for task in run.load_tasks():
            run.prompt(task)

    def test_a_run_sends_its_tasks_shape_prompt_and_its_row_records_the_shape(self):
        defaults = next(c for c in run.load_configs() if c["id"] == "defaults")
        for task_id, shape in (("redo-failing-check", "redo"), ("lookup-retry-limit", "single")):
            task = self.task(task_id)
            with self.subTest(task=task_id), tempfile.TemporaryDirectory() as tmp:
                seen = Path(tmp) / "seen.json"
                options = {"claude": stub(Path(tmp), STREAM_STUB), "model": "sonnet", "budget": 0.5, "timeout": 60,
                           "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
                with unittest.mock.patch.dict("os.environ", {"STUB_SEEN": str(seen)}):
                    row = run.run_one(task, defaults, options)
                argv = json.loads(seen.read_text())["argv"]
                self.assertEqual(argv[argv.index("-p") + 1], run.prompt(task))
                self.assertEqual(row["shape"], shape)

    def test_level_accuracy_counts_single_agent_runs_and_the_page_gives_the_redo_rate(self):
        real = full_row()
        tasks = {t["id"]: t for t in run.load_tasks()}

        def row(task_id, config, passed, spawned, started=None, **extra):
            task = tasks[task_id]
            return graded_today(dict(real, main_model="sonnet", config=config, passed=passed, task=task_id,
                                     wall_ms=1000, task_revision=task.get("revision", 1),
                                     agents_started={"general-purpose": spawned} if started is None else started,
                                     **{"class": task["class"]}, **extra))
        first = {"agent": "general-purpose", "level": "standard", "escalated_from": None}
        design = {"agent": "general-purpose", "level": "lookup", "escalated_from": None}
        rows = [row("cross-amount-cents", "plugin", True, 1, routed=[first]),
                row("mixed-plan-then-build", "plugin", False, 2, routed=[design, first], shape="plan-build"),
                row("redo-failing-check", "plugin", True, 2, routed=[first, first], shape="redo"),
                row("redo-failing-check", "plugin", True, 1, routed=[first], shape="redo"),
                # A worker and a verifier is two agents, not the work sent back.
                row("redo-failing-check", "plugin", True, 2, {"model-router:implementer": 1, "model-router:verifier": 1},
                    routed=[first, first], shape="redo"),
                row("redo-failing-check", "defaults", False, 1, shape="redo")]
        with tempfile.TemporaryDirectory() as tmp:
            res = Path(tmp) / "a-2026-11-24.jsonl"
            res.write_text("".join(json.dumps(r) + "\n" for r in rows))
            out = Path(tmp) / "page.md"
            analyze.main([str(res), "--out", str(out)])
            page = out.read_text(encoding="utf-8")
        metrics = page.split("### Run metrics")[1].split("##")[0]
        cross = next(line for line in metrics.splitlines() if line.startswith("| cross-module "))
        self.assertIn("accuracy by level: standard 1/1 (1 of 2 runs)", cross)
        self.assertNotIn("redo", cross)
        redo = next(line for line in metrics.splitlines() if line.startswith("| redo "))
        self.assertIn("redo 1 of 3 runs, accuracy by level n/a", redo)
        self.assertIn("redo 0 of 1 runs, not routed", redo)

    def lint(self, work):
        return subprocess.run([sys.executable, "-I", "tools/check_changelog.py", "2.4.0"], cwd=work,
                              capture_output=True, text=True, timeout=60)

    # The grade also turns down commit text in a line; the visible check reads only a line's place and its PR number.
    WORDING = {"commit-subjects-pasted", "commit-lines-with-hashes", "space-before-the-colon", "type-in-brackets",
               "hashes-left-on-lines"}

    def test_the_visible_changelog_check_agrees_with_the_grade_on_shape_and_says_why(self):
        task = self.task("redo-failing-check")
        self.assertLessEqual(self.WORDING, {o.name for o in overlay_dirs(task, "wrong")})
        for overlay in [None, *overlay_dirs(task, "wrong"), *overlay_dirs(task, "right")]:
            with self.subTest(answer=overlay and overlay.name), tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / "work"
                build_work(task, overlay, work)
                answer = Path(tmp) / "answer.txt"
                answer.write_text("", encoding="utf-8")
                graded = run.grade(task["dir"] / "check.py", work, answer, timeout=60)
                linted = self.lint(work)
                if overlay is not None and overlay.name in self.WORDING:
                    self.assertEqual((linted.returncode, graded.verdict), (0, "fail"), linted.stdout + graded.reason)
                    continue
                self.assertEqual(linted.returncode == 0, graded.passed, linted.stdout + graded.reason)
                if not graded.passed:
                    self.assertTrue(linted.stdout.strip(), "the visible check printed no reason")

    def test_the_first_answer_contributing_md_leads_to_fails_the_visible_check_with_the_fix(self):
        task = self.task("redo-failing-check")
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            build_work(task, task["dir"] / "wrong" / "breaking-change-not-marked", work)
            res = self.lint(work)
        self.assertEqual(res.returncode, 1)
        self.assertIn("**Breaking:**", res.stdout)
        self.assertNotIn("breaking", (task["dir"] / "repo" / "CONTRIBUTING.md").read_text(encoding="utf-8").lower())

    # A retry, then the key reused with another amount, through the solution's handle_post with a mutant installed.
    SCENARIO = """import importlib.util, json, os, sys, tempfile
sys.path.append(sys.argv[1])
if sys.argv[3]:
    spec = importlib.util.spec_from_file_location("_mutant", sys.argv[2])
    mutant = importlib.util.module_from_spec(spec); spec.loader.exec_module(mutant); mutant.install(sys.argv[3])
import db
from api.payments import handle_post
path = os.path.join(tempfile.mkdtemp(), "shop.db"); db.init(path); conn = db.connect(path)
conn.execute("INSERT INTO customers (id, email) VALUES (1, 'ana@example.com')"); conn.commit()
got = [handle_post(conn, {"headers": {"Idempotency-Key": "k"}, "body": {"customer_id": 1, "cents": c}}) for c in (5, 5, 7)]
print(json.dumps([got, *(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("gateway_log", "payments"))]))
"""

    def test_each_mutant_breaks_the_feature_without_breaking_the_work(self):
        task = self.task("mixed-plan-then-build")
        mutants = task["dir"] / "mutants.py"
        spec = importlib.util.spec_from_file_location("plan_build_mutants", mutants)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        names = list(module.MUTANTS)
        self.assertEqual(names, ["ignores-the-key", "no-body-check", "charges-the-retry", "charges-every-repeat",
                                 "charges-without-a-payment", "replays-another-answer"])
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            build_work(task, None, work)
            outcomes = {}
            for name in ("", *names):
                res = subprocess.run([sys.executable, "-I", "-c", self.SCENARIO, str(work), str(mutants), name],
                                     cwd=work, capture_output=True, text=True, timeout=60)
                self.assertEqual(res.returncode, 0, res.stderr)
                (first, retry, reused), charges, payments = json.loads(res.stdout)
                outcomes[name] = (first[0], retry == first, reused[0], charges, payments)
        self.assertEqual(outcomes, {"": (201, True, 409, 1, 1), "ignores-the-key": (201, False, 201, 3, 3),
                                    "no-body-check": (201, True, 201, 1, 1),
                                    "charges-the-retry": (201, False, 409, 2, 2),
                                    "charges-every-repeat": (201, True, 409, 3, 3),
                                    "charges-without-a-payment": (201, True, 409, 3, 1),
                                    "replays-another-answer": (201, False, 409, 1, 1)})
        self.assertFalse((task["dir"] / "mutants").exists())

    def test_the_fanout_ini_decoy_is_wrong_read_as_retries_or_as_attempts(self):
        task = self.task("fanout-three-lookups")
        ini = configparser.ConfigParser()
        ini.read(task["dir"] / "repo" / "config" / "base.ini")
        decoy = ini.getint("webhooks", "max_retries")
        want = json.loads((task["dir"] / "solution" / "ANSWERS.json").read_text())["webhook_attempts"]
        self.assertNotIn(want, (decoy, decoy + 1))

    def test_the_visible_changelog_check_fails_the_shipped_changelog_with_a_reason(self):
        task = self.task("redo-failing-check")
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(task["dir"] / "repo", Path(tmp) / "work")
            res = self.lint(Path(tmp) / "work")
        self.assertEqual(res.returncode, 1)
        self.assertIn("2.4.0", res.stdout)

    # Per task, the brief or a glob of shipped files, and the words that would give an answer away there.
    LEAKS = {
        "redo-failing-check": {"brief": ["save card", "#220", "revert", "breaking", "link"],
                               "CHANGELOG.md": ["save card", "#220", "revert", "breaking"],
                               "CONTRIBUTING.md": ["revert", "breaking", "link", "!:"]},
        "fanout-three-lookups": {"brief": ["send_refund_receipt", "emails.py", "100"],
                                 "README.md": ["send_refund_receipt", "emails.py", "DEFAULT_RETRIES", "retries"],
                                 "docs/*": ["send_refund_receipt", "emails.py", "DEFAULT_RETRIES", "retries"],
                                 "*": ["4 attempts", "attempts = 4", "four attempts", "tried 4 times"]},
        "mixed-plan-then-build": {"brief": ["choose b", "option b is", "pick b", "architecture", "worker", "host"],
                                  "*": ["choose b", "option b is", "pick b", "recommend", "prefer"],
                                  "docs/PROPOSAL.md": ["worker", "process", "host"],
                                  "test_payments.py": ["idempotency"]},
        "workflow-two-stage-report": {"brief": ["3.4.1", "25", "settings.py", "version.py"]},
    }

    def test_no_shipped_file_names_an_answer(self):
        for task_id, places in self.LEAKS.items():
            task = self.task(task_id)
            files = {p.relative_to(task["dir"] / "repo").as_posix(): p
                     for p in (task["dir"] / "repo").rglob("*") if p.is_file() and "__pycache__" not in p.parts}
            with self.subTest(task=task_id):
                self.assertFalse({Path(f).name for f in files} & {"ANSWERS.json", "DESIGN.json", "test_idempotency.py"})
            for where, words in places.items():
                texts = {"brief": task["brief"]} if where == "brief" else {
                    f: p.read_text(encoding="utf-8") for f, p in files.items() if fnmatch.fnmatch(f, where)}
                with self.subTest(task=task_id, where=where):
                    self.assertTrue(texts, "the pattern matches no shipped file")
                    for name, text in texts.items():
                        for word in words:
                            self.assertNotIn(word.lower(), text.lower(), name)


if __name__ == "__main__":
    unittest.main()


STREAM_STUB = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
Path(os.environ["STUB_SEEN"]).write_text(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ)}))
plugins = json.loads(os.environ.get("STUB_PLUGINS", "[]"))
init = {"type": "system", "subtype": "init", "plugins": plugins}
if os.environ.get("STUB_PLUGIN_ERRORS"):
    init["plugin_errors"] = [{"plugin": "model-router", "type": "load", "message": "bad"}]
folder = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / os.environ["CLAUDE_CODE_PROJECT_DIR_NAME"]
(folder / "s1" / "subagents").mkdir(parents=True)
usage = {"input_tokens": 1000000, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
line = {"type": "assistant", "message": {"id": "m1", "model": "claude-haiku-4-5", "usage": usage}}
(folder / "s1.jsonl").write_text(json.dumps(line) + "\\n" + json.dumps(line) + "\\n")  # streamed lines repeat a message
sub = dict(line, message=dict(line["message"], id="m2"))
(folder / "s1" / "subagents" / "agent-a1.jsonl").write_text(json.dumps(sub) + "\\n")
if os.environ.get("STUB_LEDGER"):  # the rows the router's hooks write
    data = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "plugins" / "data" / "model-router-inline"
    data.mkdir(parents=True, exist_ok=True)
    (data / "ledger.jsonl").write_text(os.environ["STUB_LEDGER"])
events = [init,
          {"type": "assistant", "message": {"content": [
              {"type": "tool_use", "id": "t1", "name": "Agent", "input": {"subagent_type": "Explore", "prompt": "x"}},
              {"type": "tool_use", "id": "t2", "name": "Agent", "input": {"prompt": "y"}}]}},
          {"type": "assistant", "message": {"content": [
              {"type": "tool_use", "id": "t1", "name": "Agent", "input": {"subagent_type": "Explore"}}]}},
          {"type": "user", "message": {"content": []}}]
for event in events:
    print(json.dumps(event), flush=True)
if os.environ.get("STUB_HANG"):
    import time
    time.sleep(30)
answer = os.environ.get("STUB_ANSWER", "read_retry_limit in app/settings.py")
denials = json.loads(os.environ.get("STUB_DENIALS", "[]"))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": answer, "total_cost_usd": 0.01,
                  "modelUsage": json.loads(os.environ.get("STUB_MODEL_USAGE", "{}")), "permission_denials": denials,
                  "terminal_reason": "completed"}))
'''


# The result Claude Code writes when failIfUnavailable is on and the sandbox can't start
# (https://code.claude.com/docs/en/agent-sdk/typescript, startup_failure_reason).
STARTUP_FAILURE = {"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 0,
                   "duration_ms": 0, "duration_api_ms": 0, "total_cost_usd": 0, "modelUsage": {}, "usage": {},
                   "permission_denials": [], "errors": ["Sandbox required but unavailable"],
                   "startup_failure_reason": "sandbox_required_unavailable"}

NO_START_STUB = '''#!/usr/bin/env python3
import json, sys
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
print(json.dumps(%r))
sys.exit(1)
''' % (STARTUP_FAILURE,)

SILENT_STUB = '''#!/usr/bin/env python3
import sys
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
print("Error: sandbox required but unavailable", file=sys.stderr)
sys.exit(1)
'''


class Harness(unittest.TestCase):
    """How a run is started and recorded: the sandbox, its own config folder, the stream and the batch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.seen = self.dir / "seen.json"
        self.task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
        self.configs = {c["id"]: c for c in run.load_configs()}

    def options(self, **extra):
        return {"claude": stub(self.dir, STREAM_STUB), "model": "sonnet", "budget": 0.5, "timeout": 60,
                "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None, **extra}

    def one(self, config="defaults", env=None, **options):
        with unittest.mock.patch.dict("os.environ", {"STUB_SEEN": str(self.seen), **(env or {})}):
            row = run.run_one(self.task, self.configs[config], self.options(**options))
        return row, json.loads(self.seen.read_text())

    def main(self, *argv, env=None):
        self.calls = getattr(self, "calls", 0) + 1
        out = self.dir / f"out-{self.calls}.jsonl"
        with unittest.mock.patch.dict("os.environ", {"STUB_SEEN": str(self.seen), **AUTH, **(env or {})}), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = run.main(["--tasks", "lookup-retry-limit", "--max-total", "3", "--claude", *stub(self.dir, STREAM_STUB),
                             "--out", str(out), *argv])
        read = lambda p: [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []  # noqa: E731
        return code, read(out), read(self.dir / "limited" / out.name)

    def test_bash_runs_in_the_sandbox_with_one_settings_json(self):
        options = {"claude": ["claude"], "model": "sonnet", "budget": 0.5, "plugin_dir": "/p"}
        cmd, env = run.command("x", self.configs["plugin"], options,
                               {"ANTHROPIC_API_KEY": "k", "GITHUB_TOKEN": "g", "PATH": "/bin"})
        self.assertEqual(cmd.count("--settings"), 1)
        settings = json.loads(cmd[cmd.index("--settings") + 1])
        sandbox = settings["sandbox"]
        self.assertEqual({k: sandbox[k] for k in ("enabled", "autoAllowBashIfSandboxed", "allowUnsandboxedCommands",
                                                  "failIfUnavailable")},
                         {"enabled": True, "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False,
                          "failIfUnavailable": True})
        self.assertEqual(sandbox["credentials"]["envVars"], [{"name": "ANTHROPIC_API_KEY", "mode": "deny"},
                                                             {"name": "GITHUB_TOKEN", "mode": "deny"}])
        denied = {f["path"] for f in sandbox["credentials"]["files"]}
        self.assertTrue({"~/.ssh", "~/.aws", "~/.claude", "~/.claude.json"} <= denied)
        tools = cmd[cmd.index("--allowedTools") + 1:]
        self.assertEqual(tools, list(run.TOOLS))
        self.assertIn("Bash", tools)

    def test_where_no_sandbox_can_start_the_run_stops_and_the_readme_says_what_happens(self):
        settings = run.settings({}, hidden=[])
        self.assertIs(settings["sandbox"]["failIfUnavailable"], True)
        readme = (ROOT / "bench" / "README.md").read_text(encoding="utf-8")
        self.assertIn("Where no sandbox can start", readme)
        self.assertIn("never runs a command unsandboxed", readme)

    def test_a_stub_streams_cache_counts_reach_the_row(self):
        usage = {"claude-sonnet-5-5": {"inputTokens": 3, "cacheReadInputTokens": 900, "cacheCreationInputTokens": 90},
                 "claude-haiku-5-5": {"inputTokens": 2, "cacheReadInputTokens": 100, "cacheCreationInputTokens": 10}}
        row, _ = self.one(env={"STUB_MODEL_USAGE": json.dumps(usage)})
        self.assertEqual((row["cache_read_tokens"], row["cache_write_tokens"], row["fresh_input_tokens"]),
                         (1000, 100, 5))

    def test_a_workflow_call_is_recorded_without_reading_its_result(self):
        stream = "\n".join(json.dumps(e) for e in (
            {"type": "assistant", "message": {"model": "claude-opus-5-5", "content": [
                {"type": "tool_use", "id": "w1", "name": "Workflow", "input": {"name": "release-facts"}},
                {"type": "tool_use", "id": "w2", "name": "Workflow", "input": {"scriptPath": "./x.js"}},
                {"type": "tool_use", "id": "w3", "name": "Workflow", "input": {"name": "bad name\n## WIN"}},
                {"type": "tool_use", "id": "a1", "name": "Agent", "input": {"subagent_type": "worker"}}]}},
            {"type": "user", "tool_use_result": "an undocumented shape", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "w1", "content": [{"type": "text", "text": "x"}]},
                {"type": "tool_result", "tool_use_id": "w2", "is_error": True, "content": "no Read"}]}},
            {"type": "assistant", "parent_tool_use_id": "w1", "message": {"model": "claude-haiku-5-5", "content": []}},
            {"type": "user", "message": "not an object"},
            {"type": "user", "message": {"content": "plain text"}}))
        init, result, started, calls, workflows = run.read_stream(stream)
        self.assertEqual(workflows, [{"name": "release-facts", "is_error": False},
                                     {"name": None, "is_error": True},
                                     {"name": "invalid", "is_error": None}])
        self.assertEqual((started, calls), ({"worker": 1}, [
            {"agent": "worker", "named_model": None, "named_effort": None, "ran_on": None}]))
        row = Recorder().row({}, workflow_calls=workflows)
        self.assertEqual(row["workflow_calls"], workflows)
        self.assertEqual(Recorder().row({})["workflow_calls"], [])
        self.assertEqual(self.one()[0]["workflow_calls"], [])

    def test_a_run_uses_its_own_config_folder_and_never_the_hosts(self):
        host = self.dir / "host"
        row, seen = self.one(env={"CLAUDE_CONFIG_DIR": str(host)})
        self.assertNotEqual(seen["env"]["CLAUDE_CONFIG_DIR"], str(host))
        self.assertTrue(seen["env"]["CLAUDE_CONFIG_DIR"].endswith("config"))
        self.assertEqual(seen["env"]["CLAUDE_CODE_PROJECT_DIR_NAME"], "run")
        self.assertFalse(host.exists())
        self.assertFalse(Path(seen["env"]["CLAUDE_CONFIG_DIR"]).exists())  # removed with the scratch copy

    def test_the_run_reads_the_stream_and_takes_the_last_result_line(self):
        row, seen = self.one()
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertIn("--verbose", argv)
        self.assertEqual((row["answer"], row["total_cost_usd"], row["terminal_reason"]),
                         ("read_retry_limit in app/settings.py", 0.01, "completed"))
        self.assertEqual(row["agents_started"], {"Explore": 1, "general-purpose": 1})  # each tool_use id once

    def test_a_router_run_records_whether_its_plugin_loaded(self):
        loaded = json.dumps([{"name": "model-router", "path": "/p"}])
        self.assertTrue(self.one("plugin", env={"STUB_PLUGINS": loaded})[0]["plugin_loaded"])
        self.assertFalse(self.one("plugin")[0]["plugin_loaded"])
        self.assertFalse(self.one("plugin", env={"STUB_PLUGINS": loaded, "STUB_PLUGIN_ERRORS": "1"})[0]["plugin_loaded"])
        self.assertIsNone(self.one("defaults")[0]["plugin_loaded"])

    def test_a_router_run_with_no_stream_is_not_filed_as_a_plugin_that_did_not_load(self):
        stopped = run.record(Recorder.task, self.configs["plugin"], Recorder.options, {},
                             dict(Recorder.facts, init={}, exit_code=None))
        self.assertIsNone(stopped["plugin_loaded"])
        self.assertNotEqual(run.set_aside(stopped), "plugin_not_loaded")
        (self.dir / "hang").mkdir()
        hang = stub(self.dir / "hang", HANGING_STUB)
        row = run.run_one(self.task, self.configs["plugin"], self.options(timeout=1, claude=hang))
        self.assertEqual((row["plugin_loaded"], row["stopped_by"]), (None, "timeout"))

    def test_a_router_run_whose_plugin_did_not_load_is_set_aside(self):
        code, rows, limited = self.main("--configs", "plugin", "defaults")
        self.assertEqual(code, 0)
        self.assertEqual([r["config"] for r in rows], ["defaults"])
        self.assertEqual([(r["config"], r["limited_reason"]) for r in limited], [("plugin", "plugin_not_loaded")])
        loaded = json.dumps([{"name": "model-router", "path": "/p"}])
        code, rows, limited = self.main("--configs", "plugin", env={"STUB_PLUGINS": loaded})
        self.assertEqual([r["config"] for r in rows], ["plugin"])

    def test_denials_keep_the_tool_and_the_first_200_characters_of_its_input(self):
        denials = [{"tool_name": "Bash", "tool_use_id": "u1", "tool_input": {"command": "python3 " + "x" * 300}},
                   {"tool_name": "Write", "tool_use_id": "u2", "tool_input": {"file_path": "/etc/x"}}]
        row = run.record(Recorder.task, Recorder.config, Recorder.options,
                         {"permission_denials": denials, "terminal_reason": "completed", "errors": ["e"]}, Recorder.facts)
        self.assertEqual(row["permission_denials"], [{"tool": "Bash", "input": ("python3 " + "x" * 300)[:200]},
                                                     {"tool": "Write", "input": '{"file_path": "/etc/x"}'}])
        self.assertEqual((row["terminal_reason"], row["errors"]), ("completed", ["e"]))

    def test_failed_or_denied_runs_keep_their_transcripts(self):
        keep = self.dir / "transcripts"
        passed, _ = self.one(transcripts=str(keep))
        self.assertNotIn("transcripts", passed)
        failed, _ = self.one(transcripts=str(keep), env={"STUB_ANSWER": "no idea"})
        self.assertTrue((Path(failed["transcripts"]) / "s1" / "subagents" / "agent-a1.jsonl").is_file())
        denied, _ = self.one(transcripts=str(keep), env={"STUB_DENIALS": json.dumps(
            [{"tool_name": "Bash", "tool_use_id": "u", "tool_input": {"command": "ls"}}])})
        self.assertTrue(denied["passed"])
        self.assertTrue((Path(denied["transcripts"]) / "s1.jsonl").is_file())

    def test_a_run_with_no_result_is_priced_from_its_transcripts(self):
        row, _ = self.one(timeout=2, env={"STUB_HANG": "1"})
        # Two messages of a million input tokens on Haiku 4.5; the repeated line counts once.
        self.assertEqual((row["cost_source"], row["cost_known"]), ("transcripts", False))
        self.assertAlmostEqual(row["total_cost_usd"], 2.0)
        self.assertEqual(self.one()[0]["cost_source"], "result")

    def test_transcript_cost_counts_each_message_once(self):
        folder = self.dir / "projects" / "run"
        (folder / "s").mkdir(parents=True)
        usage = {"input_tokens": 0, "output_tokens": 1000000}
        first = {"type": "assistant", "message": {"id": "a", "model": "claude-haiku-4-5", "usage": usage}}
        (folder / "s.jsonl").write_text("\n".join(json.dumps(x) for x in (first, first, {"type": "user"})) + "\nnot json\n")
        self.assertAlmostEqual(run.transcript_cost(folder), 5.0)
        self.assertIsNone(run.transcript_cost(self.dir / "none"))

    def test_main_needs_a_token_or_an_api_key(self):
        bare = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
                and not k.startswith("CLAUDE_CODE_USE_")}
        with unittest.mock.patch.dict("os.environ", bare, clear=True), \
                contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
            run.main(["--max-total", "3", "--claude", *stub(self.dir, STREAM_STUB), "--out", str(self.dir / "o.jsonl")])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY", err.getvalue())

    def test_rows_say_whether_managed_settings_are_on_the_machine(self):
        present = self.dir / "managed-settings.json"
        present.write_text("{}")
        with unittest.mock.patch.object(run, "MANAGED_SETTINGS", (str(present), str(self.dir / "absent.json"))):
            self.assertEqual(run.managed_settings(), [str(present)])
        code, rows, _ = self.main("--configs", "defaults")
        self.assertIn("managed_settings", rows[0])

    def test_the_plugin_is_frozen_once_and_its_hash_is_on_every_row(self):
        source = self.dir / "plugin"
        shutil.copytree(run.PLUGIN, source, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (source / "scripts" / "__pycache__").mkdir(exist_ok=True)
        (source / "scripts" / "__pycache__" / "x.pyc").write_bytes(b"cache")
        def freeze():
            frozen, digest = run.freeze_plugin(source)
            self.addCleanup(shutil.rmtree, Path(frozen).parent)
            return frozen, digest
        frozen, digest = freeze()
        self.assertEqual(freeze()[1], digest)
        self.assertFalse(list(Path(frozen).rglob("*.pyc")))
        (source / "hooks" / "hooks.json").write_text("{}")  # an edit after the batch starts
        self.assertNotEqual((Path(frozen) / "hooks" / "hooks.json").read_text(), "{}")
        self.assertNotEqual(freeze()[1], digest)
        loaded = json.dumps([{"name": "model-router", "path": "/p"}])
        code, rows, _ = self.main("--configs", "plugin", "defaults", "--runs", "2", "--seed", "5",
                                  env={"STUB_PLUGINS": loaded})
        self.assertEqual(sorted({r["config"] for r in rows}), ["defaults", "plugin"])
        self.assertEqual(len({r["plugin_hash"] for r in rows if r["config"] != "defaults"}), 1)
        self.assertTrue(next(r["plugin_hash"] for r in rows if r["config"] == "plugin"))
        self.assertEqual({r["plugin_hash"] for r in rows if r["config"] == "defaults"}, {None})

    def never_started(self, source, configs, runs="4"):
        out = self.dir / f"never-{len(configs)}.jsonl"
        with unittest.mock.patch.dict("os.environ", AUTH), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = run.main(["--tasks", "lookup-retry-limit", "--configs", *configs, "--runs", runs,
                             "--max-total", "99", "--claude", *stub(self.dir, source), "--out", str(out)])
        limited = [json.loads(line) for line in (self.dir / "limited" / out.name).read_text().splitlines()]
        return code, out.exists(), limited, err.getvalue()

    def test_a_run_that_never_started_is_set_aside_and_three_in_a_row_stop_the_bench(self):
        code, written, limited, err = self.never_started(NO_START_STUB, ["defaults", "haiku-high", "plugin"])
        self.assertEqual((code, written), (2, False))
        self.assertEqual([r["limited_reason"] for r in limited], ["no_start"] * 3)
        self.assertEqual({r["startup_failure_reason"] for r in limited}, {"sandbox_required_unavailable"})
        # The start never reached the API, so it costs nothing and the cap isn't charged.
        self.assertEqual({(r["total_cost_usd"], r["cost_known"], r["cost_source"]) for r in limited},
                         {(0, True, "result")})
        self.assertIn("stopped: 3 runs in a row", err)
        code, written, limited, err = self.never_started(SILENT_STUB, ["defaults"])
        self.assertEqual((code, written, [r["limited_reason"] for r in limited]), (2, False, ["no_start"] * 3))

    def test_a_documented_startup_failure_is_no_start_under_every_configuration(self):
        for config in ("defaults", "haiku-high", "plugin"):
            row = run.record(Recorder.task, self.configs[config], Recorder.options,
                             STARTUP_FAILURE, dict(Recorder.facts, init={}, exit_code=1))
            self.assertEqual((run.set_aside(row), row["claude_started"], row["total_cost_usd"], row["cost_known"]),
                             ("no_start", False, 0, True), config)
        # Without the reason field (an older Claude Code), the same shape with no init event is still no start.
        bare = {k: v for k, v in STARTUP_FAILURE.items() if k != "startup_failure_reason"}
        row = run.record(Recorder.task, self.configs["defaults"], Recorder.options, bare,
                         dict(Recorder.facts, init={}, exit_code=1))
        self.assertEqual(run.set_aside(row), "no_start")
        started = run.record(Recorder.task, self.configs["defaults"], Recorder.options, bare,
                             dict(Recorder.facts, init={"type": "system", "subtype": "init"}, exit_code=1))
        self.assertIsNone(run.set_aside(started))
        self.assertFalse(started["cost_known"])

    def settings_for(self, config="defaults", **options):
        cmd, env = run.command("x", self.configs[config], {"claude": ["claude"], "model": "sonnet", "budget": 0.5,
                                                           "plugin_dir": "/p", **options},
                               {"PATH": "/bin", "PWD": str(run.ROOT), "OLDPWD": str(run.ROOT / "bench")})
        return json.loads(cmd[cmd.index("--settings") + 1]), env, cmd

    @unittest.skipIf(sys.platform == "win32", "the OS sandbox these paths are for runs on macOS, Linux and WSL2")
    def test_runs_cannot_read_the_repository(self):
        settings, env, _ = self.settings_for()
        self.assertIn(str(run.ROOT), settings["sandbox"]["filesystem"]["denyRead"])
        self.assertIn(f"Read(//{run.ROOT.as_posix().lstrip('/')}/**)", settings["permissions"]["deny"])
        self.assertIn(f"Edit(//{run.ROOT.as_posix().lstrip('/')}/**)", settings["permissions"]["deny"])
        self.assertEqual((env.get("PWD"), env.get("OLDPWD"), env["PATH"]), (None, None, "/bin"))

    @unittest.skipIf(sys.platform == "win32", "the OS sandbox these paths are for runs on macOS, Linux and WSL2")
    def test_runs_cannot_read_any_checkout_worktree_or_shared_temp_folder(self):
        settings, _, cmd = self.settings_for()
        denied = settings["sandbox"]["filesystem"]["denyRead"]
        common = subprocess.run(["git", "-C", str(run.ROOT), "rev-parse", "--git-common-dir"], capture_output=True,
                                text=True).stdout.strip()
        git_dir = (run.ROOT / common).resolve()
        worktrees = [line.split(" ", 1)[1] for line in subprocess.run(
            ["git", "-C", str(run.ROOT), "worktree", "list", "--porcelain"], capture_output=True, text=True
        ).stdout.splitlines() if line.startswith("worktree ")]
        expected = [str(git_dir), str(git_dir.parent), *worktrees, str(run.RUNS), str(run.RUNS.resolve())]
        if hasattr(os, "getuid"):  # Claude Code's own temp folder, where other sessions leave copies of files
            expected += [f"/tmp/claude-{os.getuid()}", str(Path(f"/tmp/claude-{os.getuid()}").resolve())]
        for path in expected:
            self.assertIn(path, denied)
        rules = settings["permissions"]["deny"]
        for path in (str(git_dir.parent), *worktrees):
            self.assertIn(f"Read(//{path.lstrip('/')}/**)", rules)
        # The file tools get no blanket approval: in dontAsk mode they then read and edit only the work dir.
        tools = cmd[cmd.index("--allowedTools") + 1:]
        self.assertFalse({"Read", "Edit", "Write", "Glob", "Grep"} & set(tools))
        self.assertEqual(tools, list(run.TOOLS))

    @unittest.skipIf(sys.platform == "win32", "the OS sandbox these paths are for runs on macOS, Linux and WSL2")
    def test_a_run_reads_only_its_own_scratch_folder_and_has_a_temp_folder_of_its_own(self):
        row, seen = self.one()
        argv = seen["argv"]
        settings = json.loads(argv[argv.index("--settings") + 1])
        own = settings["sandbox"]["filesystem"]["allowRead"]
        scratch = Path(own[0])
        self.assertEqual(own, list(dict.fromkeys([str(scratch), str(scratch.resolve())])))
        self.assertEqual(scratch.parent, run.RUNS)
        self.assertEqual(Path(seen["env"]["CLAUDE_CODE_TMPDIR"]), scratch)
        # Claude Code falls back to the shared temp folder when CLAUDE_CODE_TMPDIR is over about 30 bytes.
        self.assertLessEqual(len(seen["env"]["CLAUDE_CODE_TMPDIR"].encode()), 30)
        if hasattr(os, "getuid"):
            self.assertEqual(run.RUNS, Path("/tmp") / f"mrb-{os.getuid()}")
        denied = settings["sandbox"]["filesystem"]["denyRead"]
        self.assertIn(str(run.RUNS), denied)
        self.assertIn(str(run.RUNS.resolve()), denied)
        self.assertFalse(scratch.exists())

    def test_the_harness_own_claude_temp_folder_is_hidden(self):
        if not hasattr(os, "getuid"):
            self.skipTest("Claude Code names its temp folder by uid only where there is one")
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.dict("os.environ", {"CLAUDE_CODE_TMPDIR": tmp}):
            hidden = run.answer_paths()
        own = Path(tmp) / f"claude-{os.getuid()}"
        self.assertIn(str(own), hidden)
        self.assertIn(str(own.resolve()), hidden)

    def test_results_and_kept_runs_are_hidden_from_later_runs(self):
        keep = self.dir / "keep"
        code, rows, _ = self.main("--configs", "defaults", "--keep", str(keep))
        argv = json.loads(self.seen.read_text())["argv"]
        denied = json.loads(argv[argv.index("--settings") + 1])["sandbox"]["filesystem"]["denyRead"]
        self.assertIn(str(self.dir.resolve()), denied)  # the results file's folder, its transcripts and limited/
        self.assertIn(str(keep.resolve()), denied)

    def test_only_valid_variable_names_go_into_the_credentials(self):
        cmd, _ = run.command("x", self.configs["defaults"], {"claude": ["claude"], "model": "sonnet", "budget": 0.5},
                             {"PATH": "/bin", "GH_TOKEN": "t", "BASH_FUNC_TOKEN%%": "() { :; }", "1_KEY": "x"})
        names = [v["name"] for v in json.loads(cmd[cmd.index("--settings") + 1])["sandbox"]["credentials"]["envVars"]]
        self.assertEqual(names, ["GH_TOKEN"])

    def test_a_message_on_a_model_with_no_price_leaves_the_transcripts_unpriced(self):
        folder = self.dir / "projects" / "run"
        folder.mkdir(parents=True)
        usage = {"input_tokens": 0, "output_tokens": 1000000}
        lines = [{"type": "assistant", "message": {"id": "a", "model": "claude-haiku-4-5", "usage": usage}},
                 {"type": "assistant", "message": {"id": "b", "model": "claude-unpriced-9-9", "usage": usage}}]
        (folder / "s.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        self.assertIsNone(run.transcript_cost(folder))

    def test_each_agent_call_records_what_it_named_and_what_it_ran_on(self):
        stream = "\n".join(json.dumps(e) for e in (
            {"type": "assistant", "parent_tool_use_id": None, "message": {"model": "claude-opus-5-5", "content": [
                {"type": "tool_use", "id": "a", "name": "Agent", "input": {"subagent_type": "worker", "model": "opus"}},
                {"type": "tool_use", "id": "b", "name": "Agent", "input": {"subagent_type": "worker", "effort": "low"}},
                {"type": "tool_use", "id": "c", "name": "Agent", "input": {"subagent_type": "worker"}}]}},
            {"type": "assistant", "parent_tool_use_id": "b", "message": {"model": "claude-haiku-5-5", "content": []}},
            {"type": "assistant", "parent_tool_use_id": "b", "message": {"model": "claude-sonnet-5-5", "content": []}},
            {"type": "assistant", "parent_tool_use_id": "a", "message": "not an object"},
            {"type": "assistant", "parent_tool_use_id": "a", "message": {"model": "claude-opus-5-5", "content": []}}))
        init, result, started, calls, _ = run.read_stream(stream)
        self.assertEqual(started, {"worker": 3})
        self.assertEqual(calls, [
            {"agent": "worker", "named_model": "opus", "named_effort": None, "ran_on": "claude-opus-5-5"},
            {"agent": "worker", "named_model": None, "named_effort": "low", "ran_on": "claude-haiku-5-5"},
            {"agent": "worker", "named_model": None, "named_effort": None, "ran_on": None}])  # no message seen
        row = self.one()[0]
        self.assertEqual(row["agent_overrides"], 0)
        self.assertEqual(row["agent_calls"], [
            {"agent": "Explore", "named_model": None, "named_effort": None, "ran_on": None},
            {"agent": "general-purpose", "named_model": None, "named_effort": None, "ran_on": None}])

    def test_kept_transcripts_are_never_committed(self):
        for path in ("bench/results/transcripts/out/s/s1.jsonl", "bench/results/pilot/transcripts/smoke/s/s1.jsonl"):
            res = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", path])
            self.assertEqual(res.returncode, 0, path)

    def test_every_row_records_the_seed_and_the_batch(self):
        code, rows, _ = self.main("--configs", "defaults", "haiku-high", "--runs", "2", "--seed", "5", "--batch", "b1")
        self.assertEqual(code, 0)
        self.assertEqual({(r["order_seed"], r["batch"]) for r in rows}, {(5, "b1")})
        code, again, _ = self.main("--configs", "defaults", "haiku-high", "--runs", "2", "--seed", "5")
        self.assertEqual(len({r["batch"] for r in again}), 1)
        self.assertNotEqual(again[0]["batch"], "b1")

    def test_gate_rows_in_the_ledger_are_counted(self):
        ledger = [{"event": "gate"}, {"event": "spawn"}, {"event": "gate", "kind": "verify"}]
        row = run.record(Recorder.task, Recorder.config, Recorder.options, {},
                         dict(Recorder.facts, ledger=ledger))
        self.assertEqual(row["gate_events"], 2)
        self.assertEqual(run.record(Recorder.task, Recorder.config, Recorder.options, {},
                                    Recorder.facts)["gate_events"], 0)


class Configurations(unittest.TestCase):
    """The configurations a batch can run, with only the options and agents the plugin has."""

    configs = {c["id"]: c for c in run.load_configs()}
    options = {"model": "sonnet", "budget": 0.5, "claude": ["claude"], "plugin_dir": "/p"}

    def test_no_configuration_names_an_option_or_agent_the_plugin_lacks(self):
        text = (ROOT / "bench" / "configs.json").read_text(encoding="utf-8")
        for gone in ("priority", "agent_pins", "main_effort", "update_check", "strong_agent_gate", "advise",
                     "deep-solver", "docs-researcher"):
            self.assertIsNone(re.search(rf"\b{gone}\b", text), gone)
        agents = {p.stem for p in (run.PLUGIN / "agents").glob("*.md")}
        for name in re.findall(r"model-router:([a-z-]+)", text):
            self.assertIn(name, agents)

    def test_no_configuration_sets_an_option_the_plugin_drops(self):
        for config in self.configs.values():
            self.assertNotIn("plugin_options", config, config["id"])  # run.py passes no plugin options
            self.assertNotIn("router_config", config, config["id"])
        for gone in ("plugin-cheap", "plugin-cheap-nomain", "plugin-cheap-nomain-nogate", "plugin-m6-08-b",
                     "plugin-m6-08-b-nogate", "plugin-m6-10", "opusplan", "plugin-nogate"):
            self.assertNotIn(gone, self.configs)


class RunEdges(unittest.TestCase):
    """What a run, its row and the page do at the edges."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def main(self, *argv, out=None, run_one=None):
        out = out or self.dir / "out.jsonl"
        seen = self.dir / "seen.json"
        patch = unittest.mock.patch.object(run, "run_one", run_one) if run_one else contextlib.nullcontext()
        with unittest.mock.patch.dict("os.environ", {"STUB_SEEN": str(seen), **AUTH}), patch, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            try:
                code = run.main(["--max-total", "3", "--claude", *stub(self.dir, STREAM_STUB), "--out", str(out),
                                 *argv])
            except SystemExit as exc:
                code = exc.code
        return code, err.getvalue(), seen.exists()

    def test_a_router_run_whose_router_logged_no_spawn_for_a_subagent_that_ran_is_set_aside(self):
        ran = [{"agent": "general-purpose", "named_model": None, "named_effort": None, "ran_on": "claude-sonnet-5"}]
        unseen = [dict(ran[0], ran_on=None)]
        loaded = {"init": {"type": "system", "subtype": "init", "plugins": [{"name": "model-router"}]}}
        for ledger in (None, [], [LEDGER[2]]):
            with self.subTest(ledger=ledger):
                row = Recorder().row({"total_cost_usd": 0.1}, ledger=ledger, agent_calls=ran, **loaded)
                self.assertEqual((row["plugin_loaded"], row["router_silent"], run.set_aside(row)),
                                 (True, True, "router_silent"))
        self.assertIn("router_silent", run.NO_RETRY)
        routed = Recorder().row({"total_cost_usd": 0.1}, ledger=LEDGER, agent_calls=ran, **loaded)
        self.assertEqual((routed["router_silent"], run.set_aside(routed)), (False, None))
        # A subagent whose transcript shows it ran, though the stream shows no message from it.
        hidden = Recorder().row({"total_cost_usd": 0.1}, ledger=None, agent_calls=unseen,
                                agent_types={"a1": "general-purpose"}, **loaded)
        self.assertEqual((hidden["router_silent"], run.set_aside(hidden)), (True, "router_silent"))
        # No subagent shown running, or no plugin at all: nothing says the router should have acted.
        self.assertFalse(Recorder().row({"total_cost_usd": 0.1}, ledger=None, agent_calls=unseen)["router_silent"])
        plain = run.record(Recorder.task, {"id": "defaults"}, Recorder.options,
                           {"total_cost_usd": 0.1}, {**Recorder.facts, "agent_calls": ran})
        self.assertFalse(plain["router_silent"])

    def test_the_cost_lose_rule_is_the_mirror_of_the_win_rule_and_the_docs_say_so(self):
        # Dearer beyond noise: LOSE only once the pass rate's upper bound is below +10 points.
        self.assertEqual(analyze._judge("defaults", (-0.08, 0.09), (0.1, 0.2), False, (9, 10, 9, 10))[0], "LOSE")
        self.assertEqual(analyze._judge("defaults", (-0.12, 0.11), (0.1, 0.2), False, (9, 10, 9, 10))[0], "NONE")
        stated = "the upper bound of its pass rate minus the baseline's is below plus 10 points"
        self.assertIn(stated, (ROOT / "bench" / "README.md").read_text(encoding="utf-8"))
        page = analyze.build_page(analyze.load([]))
        self.assertIn(stated, page)
        self.assertIn("The non-inferiority margin is 10 points of pass rate", page)
        self.assertNotIn("10%", page)
        self.assertNotIn("isn't shown", page)

    def test_a_paid_row_that_stops_the_bench_is_filed_by_the_set_aside_rules(self):
        def stops(task, config, options):
            exc = OSError("disk full")
            exc.row = dict(Recorder().row({"total_cost_usd": 0.4}), plugin_loaded=False)
            raise exc
        out = self.dir / "out.jsonl"
        with self.assertRaises(OSError):
            self.main("--tasks", "lookup-retry-limit", "--configs", "plugin", out=out, run_one=stops)
        limited = [json.loads(line) for line in (self.dir / "limited" / "out.jsonl").read_text().splitlines()]
        self.assertEqual([r["limited_reason"] for r in limited], ["plugin_not_loaded"])
        self.assertFalse(out.exists())

    @unittest.skipIf(sys.platform == "win32" or getattr(os, "geteuid", lambda: 1)() == 0,
                     "needs POSIX file modes, which root ignores")
    def test_files_a_run_left_that_cannot_be_copied_fail_the_run_and_never_break_the_check(self):
        import socket
        task = run.BENCH / "tasks" / "bugfix-statement-queries"
        work = self.dir / "work"
        shutil.copytree(task / "repo", work)
        (work / "notes.txt").write_text("x")
        (work / "notes.txt").chmod(0)
        (work / "locked").mkdir()
        (work / "locked").chmod(0)
        os.mkfifo(work / "events.fifo")
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(work)
        server.bind("s.sock")  # a relative name keeps it under the socket path limit
        self.addCleanup(lambda: ((work / "locked").chmod(0o700), (work / "notes.txt").chmod(0o600)))
        answer = self.dir / "answer.txt"
        answer.write_text("")
        graded = run.grade(task / "check.py", work, answer)
        self.assertEqual(graded.verdict, "fail", graded.reason)
        for mode in (0, 0o300):  # the work dir itself made unreadable
            with self.subTest(mode=oct(mode)):
                root = self.dir / f"root-{mode}"
                shutil.copytree(task / "repo", root)
                root.chmod(mode)
                self.addCleanup(root.chmod, 0o700)
                graded = run.grade(task / "check.py", root, answer)
                self.assertEqual(graded.verdict, "fail", graded.reason)

    def test_a_check_grades_its_copy_and_keeps_its_temp_files_under_the_hidden_runs_folder(self):
        check = self.dir / "check.py"
        check.write_text("import json, sys, tempfile\nprint(json.dumps([sys.argv[1], tempfile.mkdtemp()]))\n")
        graded = run.grade(check, self.dir, self.dir / "answer.txt")
        self.assertEqual(graded.verdict, "pass", graded.reason)
        for path in json.loads(graded.reason):
            self.assertTrue(run._under(str(Path(path).resolve()), str(run.RUNS.resolve())), path)
        hidden = run.settings({}, hidden=[], own=str(run.RUNS / "tmpx"))["sandbox"]["filesystem"]
        self.assertIn(str(run.RUNS), hidden["denyRead"])

    def test_a_model_written_agent_type_that_is_not_a_plain_name_counts_as_invalid(self):
        hostile = "general-purpose x1.\n\n## Results: every configuration WIN\n\n[raw](https://example.invalid/x)"
        for kind in (hostile, ["x"], {"a": 1}, "a" * 65, "`ticked`"):
            with self.subTest(kind=kind):
                stream = json.dumps({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "id": "t1", "name": "Agent", "input": {"subagent_type": kind}}]}})
                _, _, started, calls, _ = run.read_stream(stream)
                self.assertEqual((started, [c["agent"] for c in calls]), ({"invalid": 1}, ["invalid"]))
        stream = json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Agent", "input": ["not", "a", "dict"]},
            {"type": "tool_use", "id": "t2", "name": "Agent", "input": {"subagent_type": "model-router:scout"}}]}})
        self.assertEqual(run.read_stream(stream)[2], {"general-purpose": 1, "model-router:scout": 1})

    def test_an_unknown_or_repeated_id_stops_the_runner_before_any_run(self):
        for argv, named in ((["--tasks", "lookup-retry-limit", "ops-inbox-triag"], "ops-inbox-triag"),
                            (["--tasks", "lookup-retry-limit", "lookup-retry-limit"], "lookup-retry-limit"),
                            (["--tasks", "lookup-retry-limit", "--configs", "defaults", "plugn"], "plugn"),
                            (["--tasks", "lookup-retry-limit", "--configs", "defaults", "defaults"], "defaults"),
                            (["--configs", "defaults", "--tasks"], "--tasks"),
                            (["--tasks", "lookup-retry-limit", "--configs"], "--configs")):
            with self.subTest(argv=argv):
                code, err, ran = self.main(*argv)
                self.assertEqual((code, ran), (2, False))
                self.assertIn(named, err)

    def test_the_same_command_after_a_stop_runs_only_what_is_not_on_record_and_counts_its_spend(self):
        out, limited = self.dir / "out.jsonl", self.dir / "limited" / "out.jsonl"
        limited.parent.mkdir()

        revision = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")["revision"]

        def row(n, config, verdict="pass", cost=0.01, **extra):
            return json.dumps({"run": n, "task": "lookup-retry-limit", "task_revision": revision, "config": config,
                               "total_cost_usd": cost, "grade": {"verdict": verdict}, "claude_version": "9.9.9 (stub)",
                               **extra}) + "\n"
        out.write_text(row(0, "defaults") + row(0, "haiku-high", "stopped", 0.5)
                       + row(1, "haiku-high", task_revision=revision - 1))
        limited.write_text(row(1, "defaults", cost=0.0, limited_reason="network"))
        argv = ("--tasks", "lookup-retry-limit", "--configs", "defaults", "haiku-high", "--runs", "2", "--seed", "1")

        def triples(kept=3):
            return [(r["run"], r["config"]) for r in map(json.loads, out.read_text().splitlines())][kept:]
        self.assertEqual(self.main(*argv)[0], 0)
        self.assertEqual(sorted(triples()), [(0, "haiku-high"), (1, "defaults"), (1, "haiku-high")])
        code, err, _ = self.main(*argv)
        self.assertEqual(code, 0)
        self.assertIn("skipped 4 runs already in the results", err)
        self.assertEqual(len(triples()), 3)
        # What the rows on record cost counts toward --max-total: room for one more $0.50 run, not two.
        out.write_text(row(0, "defaults", cost=2.0))
        limited.write_text("")
        self.assertEqual(self.main(*argv, "--budget", "0.5")[0], 1)
        self.assertEqual(len(triples(kept=1)), 1)

    def test_a_rerun_on_another_claude_code_or_plugin_build_stops_before_any_run(self):
        out = self.dir / "out.jsonl"
        revision = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")["revision"]

        def row(n=0, config="defaults", **extra):
            return json.dumps({"run": n, "task": "lookup-retry-limit", "task_revision": revision, "config": config,
                               "total_cost_usd": 0.01, "grade": {"verdict": "pass"}, "claude_version": "9.9.9 (stub)",
                               "plugin_hash": None, **extra}) + "\n"
        argv = ("--tasks", "lookup-retry-limit", "--configs", "defaults", "--runs", "2", "--seed", "1")
        # Rows on two versions or two plugin builds get no verdict, so the rerun that would add them never starts.
        out.write_text(row(claude_version="2.1.331 (Claude Code)"))
        code, err, ran = self.main(*argv)
        self.assertEqual((code, ran), (2, False))
        self.assertIn("runs on Claude Code `2.1.331 (Claude Code)` (`claude_version`)", err)
        self.assertIn("--claude ~/.local/share/claude/versions/2.1.331", err)
        out.write_text(row(config="plugin", plugin_hash="0" * 16, bench_commit="abc1234"))
        code, err, ran = self.main(*argv)
        self.assertEqual((code, ran), (2, False))
        self.assertIn(f"plugin build {'0' * 16} (`plugin_hash`)", err)
        self.assertIn("abc1234", err)
        # A stopped run is run again and left out of the page, so its version doesn't matter, nor does any when
        # nothing is left to run.
        out.write_text(row(claude_version="2.1.331 (Claude Code)", grade={"verdict": "stopped"}))
        self.assertEqual(self.main(*argv)[0], 0)
        out.write_text(row(0, claude_version="2.1.331 (Claude Code)") + row(1, claude_version="2.1.331 (Claude Code)"))
        (self.dir / "seen.json").unlink()
        code, err, ran = self.main(*argv)
        self.assertEqual((code, ran), (0, False))
        self.assertIn("skipped 2 runs already in the results", err)

    def test_uncommitted_code_is_one_warning_at_the_start_and_no_stop(self):
        warned = {}
        for dirty in (True, False):
            with unittest.mock.patch.object(run, "_git_dirty", return_value=dirty):
                code, err, ran = self.main("--tasks", "lookup-retry-limit", "--configs", "defaults",
                                           out=self.dir / f"out-{dirty}.jsonl")
            self.assertEqual((code, ran), (0, True))
            warned[dirty] = [line for line in err.splitlines() if "uncommitted" in line]
        self.assertEqual(warned[False], [])
        [line] = warned[True]
        self.assertIn("the page gives no verdict on rows from uncommitted code (`bench_dirty`)", line)

    def test_a_results_or_keep_folder_that_cannot_be_written_stops_the_runner_before_any_run(self):
        (self.dir / "a-file").write_text("not a folder")
        for argv, out in ((["--keep", str(self.dir / "a-file")], None), ([], self.dir / "a-file" / "out.jsonl")):
            with self.subTest(argv=argv, out=out):
                code, err, ran = self.main("--tasks", "lookup-retry-limit", "--configs", "defaults", *argv, out=out)
                self.assertEqual((code, ran), (2, False))
                self.assertIn("can't write", err)
        readme = (ROOT / "bench" / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("$TMPDIR", readme)
