"""Tests for the cost and quality benchmark under bench/. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import dataclasses
import importlib.util
import io
import json
import os
import re
import shutil
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


def rows(config, cls, costs, passes):
    return [{"config": config, "class": cls, "task": f"{cls}-1", "total_cost_usd": c, "passed": p}
            for c, p in zip(costs, passes)]


class Analysis(unittest.TestCase):
    def summary(self, data):
        routers = analyze.router_ids(run.load_configs())
        return {s["class"]: s for s in analyze.summarize(data, routers=routers, router="plugin", draws=500, seed=1)}

    def test_pass_rate_and_cost_per_solved_task(self):
        cell = self.summary(rows("plugin", "lookup", [1, 1, 1, 1], [True, True, False, True]))["lookup"]["configs"]["plugin"]
        self.assertEqual((cell["runs"], cell["solved"]), (4, 3))
        self.assertAlmostEqual(cell["pass_rate"], 0.75)
        self.assertAlmostEqual(cell["cost_per_solved"], 4 / 3)

    def test_nothing_solved_has_no_cost_per_solved_task(self):
        cell = self.summary(rows("plugin", "lookup", [0.2, 0.3], [False, False]))["lookup"]["configs"]["plugin"]
        self.assertIsNone(cell["cost_per_solved"])

    def test_a_clear_win_passes_and_ranks_first(self):
        data = rows("plugin", "lookup", [0.10] * 12, [True] * 12) + rows("defaults", "lookup", [0.50] * 12, [True] * 12)
        row = self.summary(data)["lookup"]
        self.assertEqual((row["status"], row["why"], row["rank"], row["best_baseline"]), ("PASS", "cost_below", 1, "defaults"))

    def test_too_few_runs_is_never_a_win(self):
        data = rows("plugin", "lookup", [0.10], [True]) + rows("defaults", "lookup", [0.50], [True])
        row = self.summary(data)["lookup"]
        self.assertEqual((row["status"], row["why"]), ("PARTIAL", "few_runs"))
        self.assertIn("too few runs", row["reason"])

    def test_cheaper_but_solving_less_is_not_a_win(self):
        data = (rows("plugin", "bugfix", [0.05] * 12, [True] * 4 + [False] * 8)
                + rows("defaults", "bugfix", [0.50] * 12, [True] * 12))
        row = self.summary(data)["bugfix"]
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("pass rate", row["reason"])

    def test_dearer_at_the_same_pass_rate_is_behind(self):
        data = rows("plugin", "lookup", [0.50] * 12, [True] * 12) + rows("opus", "lookup", [0.10] * 12, [True] * 12)
        row = self.summary(data)["lookup"]
        self.assertEqual((row["status"], row["best_baseline"], row["rank"]), ("FAIL", "opus", 2))
        self.assertIn("cost per solved task", row["reason"])

    def test_overlapping_results_are_not_significant(self):
        data = (rows("plugin", "lookup", [0.10, 0.90] * 6, [True] * 12)
                + rows("defaults", "lookup", [0.20, 0.80] * 6, [True] * 12))
        self.assertEqual(self.summary(data)["lookup"]["status"], "PARTIAL")

    def test_the_same_data_and_seed_give_the_same_intervals(self):
        data = rows("plugin", "lookup", [0.1, 0.3, 0.2, 0.4, 0.1], [True, False, True, True, True])
        routers = frozenset({"plugin"})
        first = analyze.summarize(data, routers=routers, router="plugin", draws=300, seed=7)
        self.assertEqual(first, analyze.summarize(data, routers=routers, router="plugin", draws=300, seed=7))

    def test_another_router_configuration_is_never_the_baseline(self):
        n = analyze.MIN_RUNS
        data = (rows("plugin", "bugfix", [0.3] * n, [True] * n) + rows("plugin-cheap", "bugfix", [0.1] * n, [True] * n)
                + rows("defaults", "bugfix", [0.2] * n, [True] * n))
        verdict = self.summary(data)["bugfix"]
        self.assertEqual(verdict["best_baseline"], "defaults")
        self.assertEqual(verdict["rank"], 2)
        self.assertIn("plugin-cheap", verdict["configs"])  # still shown on the page

    def test_the_bootstrap_draws_with_random_only(self):
        # randrange has no cross-version guarantee; random() does.
        with unittest.mock.patch.object(analyze.random.Random, "randrange", side_effect=AssertionError):
            analyze.summarize(rows("plugin", "lookup", [0.1, 0.2], [True, False]), routers=frozenset({"plugin"}))

    def test_every_baseline_gets_its_difference_interval(self):
        n = analyze.MIN_RUNS
        data = rows("plugin", "lookup", [0.1] * n, [True] * n) + rows("defaults", "lookup", [0.3] * n, [True] * n)
        row = self.summary(data)["lookup"]
        low, high = row["differences"]["defaults"]
        self.assertAlmostEqual(low, -0.2)
        self.assertAlmostEqual(high, -0.2)
        self.assertNotIn("plugin", row["differences"])

    def test_pass_rate_intervals_are_exact_binomial(self):
        self.assertAlmostEqual(analyze.exact_interval(10, 10)[0], 0.025 ** 0.1, places=6)
        self.assertEqual(analyze.exact_interval(10, 10)[1], 1.0)
        self.assertEqual(analyze.exact_interval(0, 10)[0], 0.0)
        low, high = analyze.exact_interval(3, 10)  # the textbook Clopper-Pearson interval
        self.assertAlmostEqual(low, 0.0667, places=3)
        self.assertAlmostEqual(high, 0.6525, places=3)
        cell = self.summary(rows("plugin", "lookup", [1] * 10, [True] * 10))["lookup"]["configs"]["plugin"]
        self.assertAlmostEqual(cell["pass_ci"][0], 0.6915, places=3)

    def quality(self, data, forced):
        summary = analyze.summarize(data, routers=frozenset({"plugin"}), router="plugin", draws=200, seed=1, forced=forced)
        return {s["class"]: s for s in summary}["lookup"]

    FORCED = {"haiku": (1, -1), "opus": (3, -1)}

    def test_a_class_where_the_weakest_forced_baseline_keeps_up_is_saturated(self):
        n = analyze.MIN_RUNS
        data = (rows("plugin", "lookup", [0.1] * n, [True] * n) + rows("haiku", "lookup", [0.2] * n, [True] * n)
                + rows("opus", "lookup", [0.4] * n, [True] * n))
        row = self.quality(data, self.FORCED)
        q = row["quality"]
        self.assertEqual((q["saturated"], q["claim"], q["weakest"], q["strongest"]), (True, None, "haiku", "opus"))
        self.assertIn("no quality claim", q["reason"])
        self.assertEqual(row["status"], "PASS")  # the cost verdict stands beside it
        self.assertEqual(set(q["against"]), {"haiku", "opus"})  # the strongest is compared too

    def test_a_class_that_tells_the_models_apart_gets_a_quality_claim(self):
        n = analyze.MIN_RUNS
        weak = rows("haiku", "lookup", [0.2] * n, [True] * 4 + [False] * 6)
        strong = rows("opus", "lookup", [0.4] * n, [True] * n)
        level = self.quality(rows("plugin", "lookup", [0.3] * n, [True] * n) + weak + strong, self.FORCED)["quality"]
        self.assertEqual((level["saturated"], level["claim"]), (False, "NOT SHOWN BEHIND"))
        self.assertIn("isn't parity", level["reason"])
        behind = self.quality(rows("plugin", "lookup", [0.3] * n, [True] * 5 + [False] * 5) + weak + strong,
                              self.FORCED)["quality"]
        self.assertEqual((behind["saturated"], behind["claim"]), (False, "BEHIND"))
        self.assertTrue(behind["against"]["opus"]["behind"])
        self.assertFalse(behind["against"]["haiku"]["behind"])

    def test_a_result_that_is_not_significant_is_not_parity(self):
        n = analyze.MIN_RUNS
        data = (rows("plugin", "lookup", [0.3] * n, [True] * 7 + [False] * 3)  # 7 of 10 against 10 of 10: p = 0.105
                + rows("haiku", "lookup", [0.2] * n, [True] * 4 + [False] * 6) + rows("opus", "lookup", [0.4] * n, [True] * n))
        q = self.quality(data, self.FORCED)["quality"]
        self.assertEqual((q["saturated"], q["claim"]), (False, "NOT SHOWN BEHIND"))
        self.assertFalse(q["against"]["opus"]["behind"])
        self.assertIn(f"{n} runs per cell can't show it", q["reason"])

    def test_behind_is_adjusted_across_the_baselines_and_needs_runs(self):
        for name, adjusted in {"a": 0.09, "b": 0.09, "c": 0.5}.items():
            self.assertAlmostEqual(analyze._holm({"a": 0.03, "b": 0.04, "c": 0.5})[name], adjusted)
        n = analyze.MIN_RUNS
        weak = rows("haiku", "lookup", [0.2] * n, [True] * 3 + [False] * 7)
        strong = rows("opus", "lookup", [0.4] * n, [True] * n)

        def against(solved):  # the router at `solved` of 10, a weak and a strong baseline: two tests
            data = rows("plugin", "lookup", [0.3] * n, [True] * solved + [False] * (n - solved))
            return self.quality(data + weak + strong, self.FORCED)["quality"]["against"]["opus"]

        six = against(6)  # p = 0.043 alone, 0.087 once adjusted for the second baseline
        self.assertLess(six["p_behind"], analyze.ALPHA)
        self.assertFalse(six["behind"])
        self.assertTrue(against(5)["behind"])  # p = 0.016, 0.033 adjusted
        few = rows("plugin", "lookup", [0.3] * 5, [False] * 5) + rows("opus", "lookup", [0.4] * 5, [True] * 5)
        self.assertFalse(self.quality(few, self.FORCED)["quality"]["against"]["opus"]["behind"])  # p = 0.004, 5 runs

    def test_the_page_says_ten_runs_cannot_show_parity_and_names_the_baseline_of_pass_and_fail(self):
        page = analyze.build_page(self.committed())
        self.assertIn("NOT SHOWN BEHIND means none did, which is not parity", page)
        self.assertIn("pass rate is not behind the cheapest baseline's beyond noise", page)
        self.assertIn("The router and some baselines ran in different batches.", page)

    def test_forced_baselines_on_one_model_cannot_show_saturation(self):
        n = analyze.MIN_RUNS
        data = (rows("plugin", "lookup", [0.1] * n, [True] * n) + rows("opus", "lookup", [0.4] * n, [True] * n)
                + rows("opus-high", "lookup", [0.4] * n, [True] * n))
        q = self.quality(data, {"opus": (3, -1), "opus-high": (3, 2)})["quality"]
        self.assertEqual((q["saturated"], q["claim"]), (None, None))

    def test_cells_that_cover_different_tasks_or_counts_are_partial(self):
        n = analyze.MIN_RUNS
        other = [dict(r, task="lookup-2") for r in rows("defaults", "lookup", [0.5] * n, [True] * n)]
        row = self.quality(rows("plugin", "lookup", [0.1] * n, [True] * n) + other, self.FORCED)
        self.assertEqual(row["status"], "PARTIAL")
        self.assertIn("different tasks or run counts", row["reason"])
        self.assertIsNone(row["quality"]["claim"])
        short = rows("defaults", "lookup", [0.5] * (n + 2), [True] * (n + 2))
        self.assertEqual(self.quality(rows("plugin", "lookup", [0.1] * n, [True] * n) + short, self.FORCED)["status"],
                         "PARTIAL")

    def test_a_cells_draws_do_not_depend_on_the_other_cells(self):
        n = analyze.MIN_RUNS
        plugin = rows("plugin", "lookup", [0.1, 0.2, 0.3, 0.4, 0.5, 0.9, 0.15, 0.35, 0.6, 0.7], [True] * n)
        alone = self.quality(plugin, {})["configs"]["plugin"]
        beside = self.quality(plugin + rows("defaults", "lookup", [0.2, 0.9] * (n // 2), [True] * n), {})["configs"]["plugin"]
        self.assertEqual(alone["cps_ci"], beside["cps_ci"])
        elsewhere = analyze.summarize(plugin, routers=frozenset({"plugin"}), draws=200, seed=1, session="sonnet")
        self.assertNotEqual(elsewhere[0]["configs"]["plugin"]["cps_ci"], alone["cps_ci"])

    def test_a_task_splits_the_models_when_the_strong_one_solves_most_and_the_weak_one_few(self):
        def task_rows(task, strong, weak):
            return [{"task": task, "config": c, "passed": p} for c, ps in (("opus", strong), ("sonnet", weak)) for p in ps]
        data = (task_rows("a", [True, True, False], [False, True, False])   # 2 of 3 and 1 of 3: splits
                + task_rows("b", [True, True, True], [True, False, False])  # 1 of 3: splits
                + task_rows("c", [True, False, False], [False, False, False])  # strong under 2 of 3
                + task_rows("d", [True, True, True], [True, True, False]))  # weak over 1 of 3
        self.assertEqual(analyze.splitting_tasks(data), ["a", "b"])
        self.assertIsNone(analyze.splitting_tasks([r for r in data if r["config"] == "opus"]))

    def test_forced_baselines_rank_by_model_strength_then_effort(self):
        ranks = analyze.forced_ranks(run.load_configs(), analyze.mrconfig.shipped())
        self.assertLess(ranks["haiku"], ranks["haiku-high"])
        self.assertLess(ranks["haiku-high"], ranks["sonnet-medium"])
        self.assertLess(ranks["sonnet-high"], ranks["opus"])
        self.assertLess(ranks["opus-high"], ranks["fable"])
        self.assertNotIn("defaults", ranks)
        self.assertNotIn("opusplan", ranks)

    def test_a_class_with_no_router_or_baseline_has_no_differences(self):
        self.assertEqual(self.summary(rows("defaults", "lookup", [0.3], [True]))["lookup"]["differences"], {})

    def page_args(self, tmp, name="page.md"):
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
            (path.parent / "infra").mkdir()
            line = json.dumps({"task": "t", "config": "plugin", "started_at": "2026-11-22T10:00:00+00:00",
                               "infra_reason": "x"})
            (path.parent / "infra" / "a.jsonl").write_text(line + "\n", encoding="utf-8")
            results = analyze.load([path, path.parent / ".." / path.parent.name / path.name])
        self.assertEqual(len(results.files), 1)
        self.assertEqual(len(results.aside), 1)

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

    @classmethod
    def committed(cls):
        if not hasattr(cls, "_committed"):
            cls._committed = analyze.load(analyze.default_paths())
        return cls._committed

    def changed(self, edit):
        """The committed results with `edit(row)` applied to every row, as a new Results."""
        rows = [dict(r) for r in self.committed().rows]
        for row in rows:
            edit(row)
        return dataclasses.replace(self.committed(), rows=rows)

    def session(self, results, model=None):
        return next(s for s in analyze.figures(results)["sessions"] if s["model"] == (model or analyze.OPUS))

    def test_figures_are_keyed_by_role(self):
        opus = self.session(self.committed())
        self.assertEqual(opus["roles"], {"router": "plugin-next", "defaults": "defaults", "best": "opusplan",
                                         "strongest": "fable-high"})
        self.assertEqual((opus["saturated"], opus["against"]["defaults"]["level"]), (5, ["design"]))
        self.assertEqual(opus["pooled_less"]["defaults"], 1 - (opus["pooled"]["plugin-next"]["cost_usd"] / 70)
                         / (opus["pooled"]["defaults"]["cost_usd"] / 70))

    def test_the_defaults_are_the_configuration_with_no_plugin_and_no_forced_model(self):
        base = self.committed()
        rename = lambda row: dict(row, config="plain") if row["config"] == "defaults" else row  # noqa: E731
        results = dataclasses.replace(
            base, rows=[rename(r) for r in base.rows],
            configs=[dict(c, id="plain") if c["id"] == "defaults" else c for c in base.configs])
        self.assertEqual(self.session(results)["roles"]["defaults"], "plain")

    def test_renaming_the_router_moves_no_role_fact_or_status(self):
        base = self.committed()
        swap = lambda name: "renamed" if name == "plugin-next" else name  # noqa: E731
        results = dataclasses.replace(
            base, rows=[dict(r, config=swap(r["config"])) for r in base.rows],
            configs=[dict(c, id=swap(c["id"])) for c in base.configs],
            routers=frozenset(map(swap, base.routers)))
        old, new = analyze.figures(base), analyze.figures(results)
        for a, b in zip(old["sessions"], new["sessions"]):
            self.assertEqual((b["router"], b["roles"]["router"], b["facts"]), ("renamed", "renamed", a["facts"]))
            self.assertEqual((a["pooled_less"], a["behind"]), (b["pooled_less"], b["behind"]))
            self.assertEqual([c["status"] for c in a["classes"]], [c["status"] for c in b["classes"]])

    def test_the_facts_the_docs_state_hold_for_the_committed_results(self):
        data = analyze.figures(self.committed())
        self.assertEqual(analyze.broken_facts(data), [])
        self.assertEqual({s["model"]: len(s["facts"]) for s in data["sessions"]}, {analyze.OPUS: 14, analyze.SONNET: 9})

    def test_a_fact_turning_false_fails_the_check(self):
        first = []

        def lose_a_run(row):
            if row["config"] == "plugin-next" and row["main_model"] == analyze.OPUS and not first:
                first.append(row)
                row["passed"] = False

        results = self.changed(lose_a_run)
        problems = analyze.check(results, Path("page.md"), analyze.build_page(results))
        self.assertIn(f"{analyze.OPUS}: {analyze.FACT_TEXT['solved_all']} is false now", problems)
        self.assertEqual(analyze.check(self.committed(), Path("page.md"), analyze.build_page(self.committed())), [])

    def test_each_claim_about_the_opus_session_breaks_on_its_own_row(self):
        scale = lambda config, by: lambda row: row.update(total_cost_usd=row["total_cost_usd"] * by) \
            if row["config"] == config else None  # noqa: E731
        drop = lambda row: row.update(config="gone") if row["config"] == "advisor" else None  # noqa: E731
        for name, edit in (("strongest_beaten", scale("fable-high", 0.1)), ("not_cheapest", scale("plugin-next", 0.1)),
                           ("cheaper_everywhere", scale("opusplan", 10)), ("ran", drop)):
            with self.subTest(name):
                facts = self.session(self.changed(edit))["facts"]
                self.assertFalse(facts[name])

    @staticmethod
    def first(n, who, cls=None, model=analyze.OPUS, **changes):
        """An edit that applies `changes` to the first `n` rows of configuration `who` (and class) in a session,
        or in every session for `model=None`, as for opusplan, which joins each one."""
        done = []

        def edit(row):
            if (len(done) < n and row["config"] == who and model in (None, row["main_model"])
                    and cls in (None, row["class"])):
                done.append(row)
                row.update({k: v(row) if callable(v) else v for k, v in changes.items()})
        return edit

    def test_a_class_with_too_few_runs_is_never_read_as_a_win_or_a_loss(self):
        cheap = lambda row: row["total_cost_usd"] * 0.1  # noqa: E731
        for config in ("fable-high", "opus"):  # cheaper than the router in lookup only, which has 5 runs a cell
            with self.subTest(config):
                facts = self.session(self.changed(self.first(99, config, "lookup", total_cost_usd=cheap)))["facts"]
                self.assertTrue(facts["strongest_beaten"] and facts["level_as_defaults"])

    def test_level_as_defaults_needs_the_printed_classes_and_level_there(self):
        cheap = lambda row: row["total_cost_usd"] * 0.5  # noqa: E731
        for cls in ("implementation", "design"):  # Opus beats the router where it was beaten, and where it was level
            with self.subTest(cls):
                facts = self.session(self.changed(self.first(99, "opus", cls, total_cost_usd=cheap)))["facts"]
                self.assertFalse(facts["level_as_defaults"])

    def test_each_other_fact_turns_false_on_a_row_edit(self):
        times = lambda by: lambda row: row["total_cost_usd"] * by  # noqa: E731
        both = lambda *edits: lambda row: [edit(row) for edit in edits]  # noqa: E731
        cases = (
            ("best_named", None, self.first(99, "opus", "implementation", total_cost_usd=times(0.1))),
            ("example", None, self.first(99, "opusplan", "bugfix", None, total_cost_usd=times(3))),
            ("partial_for_runs", None, self.first(1, "defaults", "implementation", config="gone")),  # cells differ
            ("fail_on_cost", None, self.first(5, "plugin-next", "implementation", passed=False)),
            ("not_behind", None, self.first(8, "plugin-next", "implementation", passed=False)),
            # opusplan solves 4 of 10 implementation runs and stays cheapest, so the router is ahead of it
            ("no_gain", None, both(self.first(6, "opusplan", "implementation", None, passed=False),
                                   self.first(99, "opusplan", "implementation", None, total_cost_usd=times(0.1)))),
            ("no_pass", None, self.first(999, "plugin-next", total_cost_usd=times(0.01))),
            ("solved_as_many", analyze.SONNET,
             self.first(2, "plugin-next", "implementation", analyze.SONNET, passed=False)),
            ("one_miss", analyze.SONNET, self.first(1, "sonnet-medium", "design", analyze.SONNET, passed=False)),
        )
        for name, model, edit in cases:
            with self.subTest(name):
                self.assertFalse(self.session(self.changed(edit), model)["facts"][name])

    def test_not_behind_reads_the_unadjusted_p(self):
        session = self.session(self.changed(self.first(4, "plugin-next", "implementation", passed=False)))
        row = next(c for c in session["classes"] if c["class"] == "implementation")
        mine = row["quality"]["against"][row["best_baseline"]]  # 6 of 10 against 10 of 10: p = 0.043
        self.assertEqual((mine["p_behind"] < analyze.ALPHA, mine["behind"]), (True, False))  # not BEHIND after Holm
        self.assertFalse(session["facts"]["not_behind"])

    def test_the_facts_about_fail_classes_are_false_with_none(self):
        facts = self.session(self.changed(self.first(999, "plugin-next", total_cost_usd=lambda row: 0.0001)))["facts"]
        self.assertEqual((facts["no_pass"], facts["fail_on_cost"], facts["best_named"]), (False, False, False))

    def test_nearly_all_reads_each_configuration_the_docs_name(self):
        lose = self.first(4, "haiku-high", passed=False)  # 66 of 70, under NEARLY, while opusplan stays best
        session = self.session(self.changed(lose))
        self.assertEqual(session["roles"]["best"], "opusplan")
        self.assertFalse(session["facts"]["nearly_all"])

    def test_a_session_the_docs_say_nothing_about_has_no_facts(self):
        results = self.changed(lambda row: row.update(main_model="elsewhere"))
        data = analyze.figures(results)
        self.assertEqual([s["facts"] for s in data["sessions"]], [{}])
        self.assertEqual(analyze.broken_facts(data), [])

    def test_main_reads_configurations_and_tasks_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, out = self.page_args(tmp)
            with unittest.mock.patch.object(run, "load_configs", wraps=run.load_configs) as configs, \
                    unittest.mock.patch.object(run, "load_tasks", wraps=run.load_tasks) as tasks:
                analyze.main([str(path), "--out", str(out)])
        self.assertEqual((configs.call_count, tasks.call_count), (1, 1))

    def test_each_session_model_gets_its_own_table_and_router(self):
        real = json.loads((ROOT / "bench" / "results" / "archive" / "early-builds" / "m6-05-bugfix-2026-11-22.jsonl").read_text().splitlines()[0])
        n = analyze.MIN_RUNS
        sonnet = [dict(real, main_model="sonnet", config=c, total_cost_usd=usd, passed=True)
                  for c, usd in (("plugin", 0.3), ("defaults", 0.2)) for _ in range(n)]
        opus = [dict(real, main_model="claude-opus-5-5", config=c, total_cost_usd=usd, passed=True)
                for c, usd in (("plugin-m6-08-b", 0.5), ("defaults", 0.9)) for _ in range(n)]
        with tempfile.TemporaryDirectory() as tmp:
            res = Path(tmp) / "results"
            res.mkdir()
            (res / "a-bugfix-2026-11-22.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sonnet + opus))
            out = Path(tmp) / "page.md"
            analyze.main([str(res / "a-bugfix-2026-11-22.jsonl"), "--out", str(out)])
            page = out.read_text(encoding="utf-8")
        opus_part = page.split("## Results: session model `claude-opus-5-5`")[1].split("## Results: session model")[0]
        sonnet_part = page.split("## Results: session model `sonnet`")[1].split("## How to read it")[0]
        self.assertIn("router: `plugin-m6-08-b`", opus_part)
        self.assertIn("$0.9000 per solved", opus_part)  # Opus defaults, not pooled with Sonnet's
        self.assertIn("bugfix: PASS, cost per solved task below defaults's", opus_part)
        self.assertIn("router: `plugin`", sonnet_part)
        self.assertIn("bugfix: FAIL", sonnet_part)
        self.assertNotIn("$0.9000", sonnet_part)

    def test_a_configuration_with_its_own_session_model_joins_every_table(self):
        # opusplan sets its own --model, so its runs are the same whichever session the batch used.
        real = json.loads((ROOT / "bench" / "results" / "archive" / "early-builds" / "m6-05-bugfix-2026-11-22.jsonl")
                          .read_text().splitlines()[0])
        n = analyze.MIN_RUNS
        rows = [dict(real, main_model=m, config=c, total_cost_usd=usd, passed=True)
                for m, c, usd in (("claude-opus-5-5", "plugin-next", 0.5), ("claude-opus-5-5", "defaults", 0.9),
                                  ("sonnet", "plugin-next", 0.3), ("sonnet", "defaults", 0.2),
                                  ("opusplan", "opusplan", 0.1)) for _ in range(n)]
        with tempfile.TemporaryDirectory() as tmp:
            res = Path(tmp) / "results"
            res.mkdir()
            (res / "a-bugfix.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            out = Path(tmp) / "page.md"
            analyze.main([str(res / "a-bugfix.jsonl"), "--out", str(out)])
            page = out.read_text(encoding="utf-8")
        self.assertNotIn("## Results: session model `opusplan`", page)
        for model in ("claude-opus-5-5", "sonnet"):
            part = page.split(f"## Results: session model `{model}`")[1].split("## ")[0]
            self.assertIn("| opusplan |", part)
            self.assertIn("bugfix: FAIL, cost per solved task above opusplan's", part)

    def test_the_page_reports_latency_cache_level_accuracy_and_escalation(self):
        real = json.loads((ROOT / "bench" / "results" / "archive" / "early-builds" / "m6-05-bugfix-2026-11-22.jsonl").read_text().splitlines()[0])
        usage = {"m": {"inputTokens": 10, "cacheReadInputTokens": 80, "cacheCreationInputTokens": 10}}
        first = {"agent": "model-router:implementer", "level": "standard", "escalated_from": None}
        redo = {"agent": "general-purpose", "level": "complex", "escalated_from": "standard"}
        base = dict(real, main_model="sonnet", models=usage)
        router = [dict(base, config="plugin", wall_ms=10000, passed=True, routed=[first]),
                  dict(base, config="plugin", wall_ms=20000, passed=False, routed=[first, redo]),
                  dict(base, config="plugin", wall_ms=40000, passed=True)]  # recorded before `routed`
        # Recorded before `wall_ms`: Claude Code's duration_ms alone is not taken as wall time.
        plain = [dict(base, config="defaults", duration_ms=5000, passed=True) for _ in range(2)]
        for r in router[2:] + plain:
            r.pop("routed", None)
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
        self.assertIn("p50 20 s, p90 40 s, cache 80%, accuracy by level: standard 1/2 (2 of 3 runs), "
                      "escalated 1 of 3 spawns", row)
        self.assertIn("latency n/a, cache 80%, not routed", row)
        self.assertIn("1 router run(s) here carry no `routed`", page)
        self.assertIn("2 run(s) here carry no `wall_ms`", page)
        never = next(line for line in page.splitlines() if line.startswith("Defined in `bench/configs.json` but"))
        for name in ("`haiku-high`", "`opus-high`", "`fable-high`", "`sonnet-high`"):
            self.assertIn(name, never)
        self.assertNotIn("`defaults`", never)

    def test_the_page_says_what_the_archive_holds_and_why_it_is_left_out(self):
        real = json.loads((ROOT / "bench" / "tasks" / "lookup-todo-count" / "task.json").read_text())
        revision = real["revision"]  # the rows without a task_revision count as revision 1
        base = {"class": "lookup", "task": "lookup-todo-count", "config": "defaults", "total_cost_usd": 0.05,
                "passed": True, "claude_version": "2.1.330", "main_model": "sonnet", "models": {},
                "started_at": "2026-11-23T01:00:00+00:00", "budget_usd": 1.0, "plugin_state": None,
                "plugin_data_own": True, "sessions_saved": True, "bench_commit": "c" * 40, "task_revision": revision}
        old = {k: v for k, v in base.items() if k != "task_revision"}
        kept = [dict(old, bench_commit="a" * 40, task="design-cache-invalidation", **{"class": "design"}),
                dict(old, bench_commit="b" * 40), dict(base)]
        with tempfile.TemporaryDirectory() as tmp:
            res = Path(tmp) / "results"
            (res / "archive" / "early-builds" / "infra").mkdir(parents=True)
            (res / "new-lookup.jsonl").write_text(json.dumps(base) + "\n")
            (res / "archive" / "early-builds" / "old-lookup.jsonl").write_text("".join(json.dumps(r) + "\n" for r in kept))
            (res / "archive" / "early-builds" / "infra" / "net.jsonl").write_text(json.dumps(old) + "\n")
            out = Path(tmp) / "page.md"
            analyze.main([str(res / "new-lookup.jsonl"), "--out", str(out)])
            page = out.read_text(encoding="utf-8")
        raw = page.split("## Raw data and commands")[1]
        earlier = ("1 on an earlier revision of `design-cache-invalidation`" if revision == 1 else
                   "2 on an earlier revision of `design-cache-invalidation`, `lookup-todo-count`")
        self.assertIn("- `bench/results/archive/early-builds/` holds 3 rows left out of every table, plus 1 set-aside "
                      "line under its `infra/` and `limited/`: 2 ran on 2 other commits (older builds of the router "
                      f"and the bench), {earlier}, and 2 were recorded "
                      "while the run's folder name held its task id.", raw)
        self.assertNotIn("$0.15", page.split("## Raw data and commands")[0])  # archived spend is in no table

    def test_the_page_names_set_aside_runs_agent_spend_and_batches(self):
        real = [json.loads(line) for line in (ROOT / "bench" / "results" / "archive" / "early-builds" / "m6-05-bugfix-2026-11-22.jsonl")
                .read_text(encoding="utf-8").splitlines() if line]
        base = dict(real[0], started_at="2026-11-22T10:00:00+00:00")
        lost = dict(base, config="plugin", task="bugfix-median", started_at="2026-11-22T09:00:00+00:00")
        with tempfile.TemporaryDirectory() as tmp:
            res = Path(tmp) / "results"
            (res / "infra").mkdir(parents=True)
            (res / "limited").mkdir()
            write = lambda path, items: path.write_text("".join(json.dumps(r) + "\n" for r in items))  # noqa: E731
            write(res / "old-bugfix-2026-11-22.jsonl", [dict(base, config="defaults")] * 3)
            write(res / "new-bugfix-2026-11-22.jsonl", [dict(base, config="plugin", task="bugfix-median",
                  agent_costs={"main": 0.03, "model-router:implementer": 0.01})] * 2)
            write(res / "infra" / "new-network.jsonl", [dict(lost, infra_reason="network outage")] * 2)
            write(res / "limited" / "new-bugfix-2026-11-22.jsonl",
                  [dict(lost, task="bugfix-pagination", limited_reason="limit"),
                   dict(lost, task="bugfix-pagination", limited_reason="api_error")])
            out = Path(tmp) / "page.md"
            analyze.main([str(p) for p in sorted(res.glob("*.jsonl"))] + ["--out", str(out)])
            page = out.read_text(encoding="utf-8")
        self.assertIn("4 run(s) are set aside, outside the results, because the API never got to the task: "
                      "2 lost the network, 1 hit a rate or usage limit, 1 got an API error before any spend (a bad "
                      "key, no credit or a model the account can't use). 2 of them were run again later", page)
        self.assertIn("`plugin` on `sonnet` (2 runs): main loop $0.0600 (75%), `model-router:implementer` $0.0200 "
                      "(25%)", page)
        self.assertIn("`defaults` old (3); `plugin` new (2)", page)


FIXTURE = ROOT / "tests" / "fixtures" / "claude-2.1.330-result.json"

LEDGER = [
    {"event": "spawn", "agent": "model-router:implementer", "level": "standard", "model": "sonnet",
     "effort": "medium", "escalated_from": None, "reason": "standard subtask"},
    {"event": "spawn", "agent": "model-router:verifier", "level": "lookup", "model": "haiku", "effort": "low"},
    {"event": "usage", "agent_id": "a1", "cost_usd": 0.05}, {"event": "usage", "agent_id": "a2", "cost_usd": 0.01},
    {"event": "usage", "agent_id": None, "cost_usd": 0.03}, {"event": "usage", "agent_id": "a1", "cost_usd": 0.02}]
AGENT_TYPES = {"a1": "model-router:implementer", "a2": "model-router:verifier"}


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
             "plugin_state": None, "ledger": None, "agent_types": {}, "session_dir_removed": True, "kept": None}

    def row(self, result, **facts):
        return run.record(self.task, self.config, self.options, run.mrconfig.build({}), result, {**self.facts, **facts})

    def test_a_real_2_1_330_result_becomes_a_row(self):
        row = self.row(json.loads(FIXTURE.read_text(encoding="utf-8")))
        self.assertEqual(row["subagents"], {"spawned": 1, "completed": 1, "failed": 0,
                                            "by_type": {"general-purpose": 1}})
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
            env = {"CLAUDE_CODE_PLUGIN_CACHE_DIR": tmp}
            ledger = run.plugin_data_dir(env) / "ledger.jsonl"
            ledger.parent.mkdir(parents=True)
            ledger.write_text('{"event": "spawn"}\n5\nnull\n[]\n"x"\nnot json\n\n{"event": "usage"}\n',
                              encoding="utf-8")
            self.assertEqual(run.ledger_rows(env), [{"event": "spawn"}, {"event": "usage"}])

    def test_a_run_with_no_ledger_gives_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(run.ledger_rows({"CLAUDE_CODE_PLUGIN_CACHE_DIR": tmp}))

    def test_a_ledger_that_cannot_be_read_gives_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"CLAUDE_CODE_PLUGIN_CACHE_DIR": tmp}
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
        self.assertEqual(run.spend_by_agent(LEDGER, AGENT_TYPES),
                         {"model-router:implementer": 0.07, "model-router:verifier": 0.01, "main": 0.03})
        spawned = run.spawns(LEDGER)
        self.assertIsNone(spawned[0]["escalated_from"])
        self.assertNotIn("escalated_from", spawned[1])
        row = self.row({}, ledger=LEDGER, agent_types=AGENT_TYPES)
        self.assertEqual(row["agent_costs"]["main"], 0.03)
        self.assertEqual(len(row["routed"]), 2)
        row = self.row({}, ledger=None)
        self.assertEqual((row["agent_costs"], row["routed"]), (None, None))

    def test_the_schedule_rotates_configurations_and_numbers_runs(self):
        got = [(i, t["id"], c["id"]) for i, t, c in
               run.schedule([{"id": "t"}], [{"id": "a"}, {"id": "b"}, {"id": "c"}], 2, 1)]
        self.assertEqual(got, [(1, "t", "b"), (1, "t", "c"), (1, "t", "a"), (2, "t", "c"), (2, "t", "a"), (2, "t", "b")])

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
        cmd, env = run.command("do it", configs["haiku"], options, base)
        self.assertEqual((env["CLAUDE_CODE_SUBAGENT_MODEL"], env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"]), ("haiku", "1"))
        for name in ("CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_SUBAGENT_EFFORT", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
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
        for rule in ("Bash(python3 *)", "Bash(tail *)", "Bash(head *)", "Bash(echo *)"):
            self.assertIn(rule, cmd)

    def test_a_task_or_configuration_list_that_matches_nothing_is_an_error_naming_it(self):
        for flag, ids in (("--tasks", ["no-such-task"]), ("--configs", ["defualts", "plugn"])):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as tmp:
                with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
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
            row = run.run_one(task, next(c for c in run.load_configs() if c["id"] == "defaults"), options, {})
        self.assertEqual((row["exit_code"], row["passed"], row["cost_known"], row["total_cost_usd"], row["answer"]),
                         (None, False, False, 0.5, ""))
        self.assertGreaterEqual(row["wall_ms"], 1000)


HANGING_STUB = '''#!/usr/bin/env python3
import sys, time
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
print('{"result": "half an ans', flush=True)
time.sleep(30)
'''


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
print(json.dumps({"result": "x" * 3000, "total_cost_usd": 0.01, "modelUsage": {}}))
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
            row = run.run_one(task, next(c for c in run.load_configs() if c["id"] == "defaults"), options, {})
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
        tasks = run.load_tasks()
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
        for task in run.load_tasks():
            for kind, verdict in (("wrong", "fail"), ("right", "pass")):
                for overlay in overlay_dirs(task, kind):
                    with self.subTest(task=task["id"], answer=f"{kind}/{overlay.name}"):
                        graded = self.grade(task, overlay)
                        self.assertEqual(graded.verdict, verdict, graded.reason)
                        if kind == "wrong" and (overlay / "expect.txt").is_file():
                            self.assertIn((overlay / "expect.txt").read_text(encoding="utf-8").strip(), graded.reason)
                        elif kind == "wrong":  # a check that gives reasons pins each wrong answer's
                            self.assertEqual(graded.reason, "the check exited 1", "add an expect.txt")

    def test_an_answer_folder_changes_something(self):
        for task in run.load_tasks():
            for kind in ("wrong", "right"):
                for overlay in overlay_dirs(task, kind):
                    with self.subTest(task=task["id"], answer=f"{kind}/{overlay.name}"):
                        self.assertTrue(any(p.is_file() for p in overlay.rglob("*")))


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
print(json.dumps({"result": answer, "total_cost_usd": 0.01, "modelUsage": {}}))
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
                    first = run.run_one(task, configs["plugin"], options, {})
                    second = run.run_one(task, configs["plugin"], options, {})
                    baseline = run.run_one(task, configs["defaults"], options, {})
            finally:
                shared.chmod(0o755)
            self.assertEqual([p.name for p in shared.iterdir()], ["state.json"])
            self.assertEqual((shared / "state.json").read_text(), '{"max_tier": "opus"}')
            self.assertEqual((shared / "state.json").stat().st_mtime, 1000)
            self.assertTrue(first["passed"])
            self.assertIn("(fresh)", first["answer"])
            self.assertIn("(fresh)", second["answer"])
            self.assertEqual(first["plugin_state"], {"state.json": None})
            self.assertIsNone(baseline["plugin_state"])
            self.assertIn("bench_dirty", first)

    def test_every_run_gets_the_same_plugins_root_setting(self):
        options = {"claude": ["claude"], "model": "sonnet", "budget": 0.5, "plugin_dir": "/p", "plugins_root": "/s/plugins"}
        # A plugin_ref export needs that commit in the clone, which a shallow CI checkout lacks.
        with unittest.mock.patch.object(run, "plugin_dir_for", return_value="/p"):
            for config in run.load_configs():
                _, env = run.command("do it", config, options, {"CLAUDE_CODE_PLUGIN_CACHE_DIR": "/elsewhere"})
                self.assertEqual(env["CLAUDE_CODE_PLUGIN_CACHE_DIR"], "/s/plugins", config["id"])

    def test_the_data_folder_is_named_from_the_manifest(self):
        self.assertEqual(run.plugin_data_dir({"CLAUDE_CONFIG_DIR": "/c"}),
                         Path("/c/plugins/data/model-router-inline"))
        self.assertEqual(run.plugin_data_dir({"CLAUDE_CONFIG_DIR": "/c", "CLAUDE_CODE_PLUGIN_CACHE_DIR": "/s/plugins"}),
                         Path("/s/plugins/data/model-router-inline"))


SESSION_STUB = '''#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
folder = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd().resolve()))
(folder / "s1" / "subagents").mkdir(parents=True)
(folder / "s1.jsonl").write_text("{}")
(folder / "s1" / "subagents" / "agent-a1.jsonl").write_text("{}")
print(json.dumps({"result": "read_retry_limit in app/settings.py", "total_cost_usd": 0.01, "modelUsage": {}}))
'''


class Sessions(unittest.TestCase):
    """Runs keep their transcripts, as real sessions do, and the bench removes only its own afterwards."""

    def test_runs_save_their_sessions(self):
        cmd, _ = run.command("do it", next(c for c in run.load_configs() if c["id"] == "plugin"), {"claude": ["claude"], "model": "sonnet", "budget": 0.5,
                                                             "plugin_dir": "/p"}, {})
        self.assertNotIn("--no-session-persistence", cmd)

    @unittest.skipIf(sys.platform == "win32", "the folder name is built from a POSIX absolute path")
    def test_a_folder_is_named_as_claude_code_names_it(self):
        self.assertEqual(run.session_dir(Path("/private/var/folders/ab/x_y.z/T/mrbench-a-b/work"), {"CLAUDE_CONFIG_DIR": "/c"}),
                         Path("/c/projects/-private-var-folders-ab-x-y-z-T-mrbench-a-b-work"))

    def test_only_the_runs_own_transcripts_are_removed(self):
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
                row = run.run_one(task, run.load_configs()[1], options, {})
            self.assertEqual([p.name for p in (home / "projects").iterdir()], ["-Users-someone-project"])
            self.assertTrue((other / "keep.jsonl").is_file())
            self.assertEqual((row["sessions_saved"], row["session_dir_removed"]), (True, True))

    def test_keep_saves_the_scratch_folder_and_the_transcripts_before_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, kept = Path(tmp) / "config", Path(tmp) / "kept"
            claude = stub(tmp, SESSION_STUB)
            task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
            options = {"claude": claude, "model": "sonnet", "budget": 0.5, "timeout": 60, "keep": str(kept),
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(home)}):
                row = run.run_one(task, run.load_configs()[1], options, {})
            [folder] = list(kept.iterdir())
            self.assertEqual(row["kept"], str(folder))
            self.assertTrue((folder / "work" / "app" / "settings.py").is_file())
            self.assertEqual((folder / "answer.txt").read_text(), "read_retry_limit in app/settings.py")
            self.assertTrue((folder / "session" / "s1" / "subagents" / "agent-a1.jsonl").is_file())
            self.assertEqual(list((home / "projects").iterdir()), [])  # the shared folder is still cleaned up


# Ctrl-C while claude runs: the session has started, then the bench gets SIGINT mid-call.
INTERRUPT_STUB = '''#!/usr/bin/env python3
import os, re, signal, sys, time
from pathlib import Path
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
folder = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd().resolve()))
(folder / "s1").mkdir(parents=True)
(folder / "s1.jsonl").write_text("{}")
os.kill(os.getppid(), signal.SIGINT)
time.sleep(30)
'''


class Stopped(unittest.TestCase):
    """Once claude starts the run costs money: whatever stops it or the steps after it, its row is written and
    its scratch and session folders are removed."""

    def stop(self, error: type, *argv: str, source: str = SESSION_STUB,
             **patches) -> tuple[list[dict], list[Path], list[Path]]:
        with tempfile.TemporaryDirectory() as tmp:
            home, scratch, out = Path(tmp) / "config", Path(tmp) / "scratch", Path(tmp) / "out.jsonl"
            scratch.mkdir()
            (Path(tmp) / "a-file").write_text("not a folder")
            argv = tuple(a.replace("<tmp>", tmp) for a in argv)
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(home)}), \
                    unittest.mock.patch.object(tempfile, "tempdir", str(scratch)), \
                    (unittest.mock.patch.multiple(run, **patches) if patches else contextlib.nullcontext()), \
                    self.assertRaises(error):
                run.main(["--tasks", "lookup-retry-limit", "--configs", "defaults", "--runs", "1", "--max-total", "3",
                          "--claude", *stub(tmp, source), "--out", str(out), *argv])
            rows = [json.loads(line) for line in out.read_text().splitlines()] if out.exists() else []
            sessions = list((home / "projects").iterdir()) if (home / "projects").exists() else []
            return rows, list(scratch.iterdir()), sessions

    @unittest.skipIf(sys.platform == "win32", "needs a POSIX SIGINT sent to the bench")
    def test_an_interrupt_during_the_claude_call_writes_a_broken_row_charged_the_budget_and_cleans_up(self):
        rows, scratch, sessions = self.stop(KeyboardInterrupt, source=INTERRUPT_STUB)
        self.assertEqual([(r["grade"]["verdict"], r["exit_code"], r["cost_known"], r["total_cost_usd"]) for r in rows],
                         [("broken", None, False, 0.5)])
        self.assertIn("KeyboardInterrupt", rows[0]["grade"]["reason"])
        self.assertEqual((scratch, sessions), ([], []))

    def test_an_interrupt_while_grading_still_writes_the_row_and_cleans_up(self):
        rows, scratch, sessions = self.stop(KeyboardInterrupt, grade=unittest.mock.Mock(side_effect=KeyboardInterrupt))
        self.assertEqual([(r["grade"]["verdict"], r["total_cost_usd"], r["passed"]) for r in rows],
                         [("broken", 0.01, False)])
        self.assertIn("KeyboardInterrupt", rows[0]["grade"]["reason"])
        self.assertEqual((scratch, sessions), ([], []))

    def test_a_keep_folder_that_cannot_be_written_keeps_the_graded_row_and_cleans_up(self):
        rows, scratch, sessions = self.stop(OSError, "--keep", str(Path("<tmp>") / "a-file"))
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
            self.assertGreaterEqual(sum(t["class"] == cls for t in tasks), 2, cls)

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

    def test_each_new_baseline_sets_what_its_name_says(self):
        configs = {c["id"]: c for c in run.load_configs()}
        options = {"model": "sonnet", "budget": 0.5, "claude": ["claude"], "plugin_dir": "/p/model-router"}
        cmd, env = run.command("x", configs["opusplan"], options, {})
        self.assertEqual(cmd[cmd.index("--model") + 1], "opusplan")
        cmd, env = run.command("x", configs["advisor"], options, {})
        self.assertEqual((cmd[cmd.index("--model") + 1], cmd[cmd.index("--advisor") + 1]), ("sonnet", "opus"))
        for name, model, effort in (("sonnet-medium", "sonnet", "medium"), ("sonnet-high", "sonnet", "high"),
                                    ("haiku-high", "haiku", "high"), ("opus-high", "opus", "high"),
                                    ("fable-high", "fable", "high")):
            cmd, env = run.command("x", configs[name], options, {})
            self.assertEqual((env["CLAUDE_CODE_SUBAGENT_MODEL"], env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"],
                              env["CLAUDE_CODE_SUBAGENT_EFFORT"]), (model, "1", effort), name)
            self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", env)  # that one would move the main loop too
            self.assertNotIn("--plugin-dir", cmd)
            self.assertEqual(configs[name]["price_model"], model)
        for name in ("haiku", "sonnet", "opus"):
            cmd, env = run.command("x", configs[name], options, {})
            self.assertEqual((env["CLAUDE_CODE_SUBAGENT_MODEL"], env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"]),
                             (name, "1"), name)
            self.assertNotIn("CLAUDE_CODE_SUBAGENT_EFFORT", env)
            self.assertNotIn("--plugin-dir", cmd)
            self.assertEqual(configs[name]["price_model"], name)
        cmd, env = run.command("x", configs["fable"], options, {})
        self.assertEqual(env["CLAUDE_CODE_SUBAGENT_MODEL"], "fable")
        for config in configs.values():
            self.assertIn(config.get("price_model"), ("haiku", "sonnet", "opus", "fable"), config["id"])

    def test_the_full_suite_estimate_prices_each_class_and_model(self):
        data = (rows("defaults", "lookup", [0.04, 0.06], [True, True])
                + rows("defaults", "bugfix", [0.10], [True]))
        tasks = [{"id": "l", "class": "lookup"}, {"id": "b", "class": "bugfix"}, {"id": "d", "class": "design"}]
        configs = [{"id": "a", "price_model": "sonnet"}, {"id": "o", "price_model": "opus"},
                   {"id": "h", "price_model": "haiku"}]
        registry_config = analyze.mrconfig.build({
            "prices": {"claude-sonnet-5-5": {"output": 15}, "claude-opus-5-5": {"output": 75},
                       "claude-opus-4-1": {"output": 99}, "claude-haiku-5-5": {"output": 5},
                       "claude-opus-*": {"output": 1}},
            "newest": {"sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5", "haiku": "claude-haiku-5-5"}})
        plan = analyze.estimate(data, tasks, configs, registry_config)
        self.assertEqual(plan["runs"], 90)
        # 10 runs x (0.05 + 0.10 + 0.0667 for design, from the mean of all runs) x (1 + 5 + 1, haiku never priced down)
        self.assertAlmostEqual(plan["usd"], 10 * (0.05 + 0.10 + 0.2 / 3) * 7, places=6)
        self.assertEqual(plan["factors"], {"a": 1.0, "o": 5.0, "h": 1.0})


LIMIT_STUB = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
calls = Path(os.environ["STUB_CALLS"])
n = int(calls.read_text()) if calls.exists() else 0
calls.write_text(str(n + 1))
if n < int(os.environ["STUB_LIMITED"]):
    print(json.dumps({"is_error": True, "subtype": "error_during_execution", "api_error_status": 429,
                      "result": "API Error: 429 rate limit reached",
                      "total_cost_usd": float(os.environ.get("STUB_LIMIT_COST", "0.001")), "modelUsage": {}}))
    sys.exit(1)
print(json.dumps({"is_error": False, "subtype": "success", "api_error_status": None, "total_cost_usd": 0.01,
                  "result": "read_retry_limit in app/settings.py; a rate limit design is off topic", "modelUsage": {}}))
'''


VARIANT_STUB = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
seen = {"argv": sys.argv[1:], "project": None}
project = Path.cwd() / ".claude" / "model-router.json"
if project.is_file():
    seen["project"] = json.loads(project.read_text())
Path(os.environ["STUB_SEEN"]).write_text(json.dumps(seen))
print(json.dumps({"result": "read_retry_limit in app/settings.py", "total_cost_usd": 0.01, "modelUsage": {}}))
'''


class RouterVariants(unittest.TestCase):
    """A router variant is the shipped plugin plus a project model-router.json and plugin option values."""

    def test_a_variant_gets_its_router_file_and_options_and_nothing_else_does(self):
        variant = {"id": "v", "plugin": True, "env": {}, "router_config": {"levels": {"standard": {"effort": "medium"}}},
                   "plugin_options": {"main_effort": False}}
        with tempfile.TemporaryDirectory() as tmp:
            claude = stub(tmp, VARIANT_STUB)
            seen = Path(tmp) / "seen.json"
            task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
            options = {"claude": claude, "model": "sonnet", "budget": 0.5, "timeout": 60,
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            with unittest.mock.patch.dict("os.environ", {"STUB_SEEN": str(seen)}):
                row = run.run_one(task, variant, options, {})
                got = json.loads(seen.read_text())
                run.run_one(task, next(c for c in run.load_configs() if c["id"] == "plugin"), options, {})
                plain = json.loads(seen.read_text())
        self.assertEqual(got["project"], {"levels": {"standard": {"effort": "medium"}}})
        settings = json.loads(got["argv"][got["argv"].index("--settings") + 1])
        self.assertEqual(settings, {"pluginConfigs": {"model-router@inline": {"options": {"main_effort": False}}}})
        self.assertTrue(row["passed"])
        self.assertIsNone(plain["project"])
        self.assertNotIn("--settings", plain["argv"])


COST_STUB = '''#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
data = Path(os.environ["CLAUDE_CODE_PLUGIN_CACHE_DIR"]) / "data" / "model-router-inline"
data.mkdir(parents=True)
rows = [{"event": "spawn", "agent": "model-router:implementer", "level": "standard", "model": "sonnet",
         "effort": "medium", "escalated_from": None, "reason": "standard subtask"},
        {"event": "spawn", "agent": "model-router:verifier", "level": "lookup", "model": "haiku", "effort": "low"},
        {"event": "usage", "agent_id": "a1", "cost_usd": 0.05}, {"event": "usage", "agent_id": "a2", "cost_usd": 0.01},
        {"event": "usage", "agent_id": None, "cost_usd": 0.03}, {"event": "usage", "agent_id": "a1", "cost_usd": 0.02}]
(data / "ledger.jsonl").write_text("\\n".join(json.dumps(r) for r in rows))
folder = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd().resolve()))
sub = folder / "s1" / "subagents"
sub.mkdir(parents=True)
(sub / "agent-a1.meta.json").write_text(json.dumps({"agentType": "model-router:implementer"}))
(sub / "agent-a2.meta.json").write_text(json.dumps({"agentType": "model-router:verifier"}))
import time
time.sleep(0.3)  # Claude Code's own duration_ms can leave time out
print(json.dumps({"result": "read_retry_limit in app/settings.py", "total_cost_usd": 0.11, "modelUsage": {},
                  "duration_ms": 1}))
'''


class PluginRef(unittest.TestCase):
    def test_a_configuration_can_run_the_plugin_from_a_commit(self):
        options = {"model": "sonnet", "budget": 0.5, "claude": ["claude"], "plugin_dir": "/p/model-router"}
        cmd, _ = run.command("x", {"id": "r", "plugin": True, "env": {}, "plugin_ref": "HEAD"}, options, {})
        exported = Path(cmd[cmd.index("--plugin-dir") + 1])
        self.assertNotEqual(exported, Path("/p/model-router"))
        self.assertTrue((exported / ".claude-plugin" / "plugin.json").is_file())
        again, _ = run.command("x", {"id": "r", "plugin": True, "env": {}, "plugin_ref": "HEAD"}, options, {})
        self.assertEqual(again[again.index("--plugin-dir") + 1], str(exported))  # exported once
        plain, _ = run.command("x", next(c for c in run.load_configs() if c["id"] == "plugin"), options, {})
        self.assertEqual(plain[plain.index("--plugin-dir") + 1], "/p/model-router")

    def test_the_exported_plugin_is_removed_when_the_bench_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "TMPDIR": tmp, "TMP": tmp, "TEMP": tmp}
            script = ("import sys, pathlib; sys.path.insert(0, sys.argv[1]); import run; "
                      "p = pathlib.Path(run.plugin_dir_for({'plugin_ref': 'HEAD'}, {})); "
                      "print((p / '.claude-plugin' / 'plugin.json').is_file())")
            out = subprocess.run([sys.executable, "-c", script, str(ROOT / "bench")], env=env, capture_output=True,
                                 text=True, check=True).stdout
            self.assertEqual(out.strip(), "True")
            self.assertEqual([p.name for p in Path(tmp).iterdir() if p.name.startswith("mrbench-plugin-")], [])


class AgentCosts(unittest.TestCase):
    def test_a_router_run_records_its_spend_by_agent_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude = stub(tmp, COST_STUB)
            task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
            options = {"claude": claude, "model": "sonnet", "budget": 0.5, "timeout": 60,
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(Path(tmp) / "config")}):
                row = run.run_one(task, next(c for c in run.load_configs() if c["id"] == "plugin"), options, {})
        self.assertEqual(row["agent_costs"], {"model-router:implementer": 0.07, "model-router:verifier": 0.01,
                                              "main": 0.03})
        self.assertEqual(row["duration_ms"], 1)
        self.assertGreaterEqual(row["wall_ms"], 300)  # the runner's own clock around the claude process
        # A spawn row from a router that doesn't log escalated_from keeps it unknown, not None.
        self.assertEqual(row["routed"], [
            {"agent": "model-router:implementer", "level": "standard", "model": "sonnet", "effort": "medium",
             "escalated_from": None},
            {"agent": "model-router:verifier", "level": "lookup", "model": "haiku", "effort": "low"}])


    def test_the_ledger_is_read_before_the_work_dir_code_runs(self):
        def rewrite(check, work, answer, timeout=120):
            data = run.plugin_data_dir({"CLAUDE_CODE_PLUGIN_CACHE_DIR": str(work.parent / "plugins")})
            (data / "ledger.jsonl").write_text('{"event": "usage", "cost_usd": 9}\n', encoding="utf-8")
            return run.Grade("pass", "", "0" * 16)

        with tempfile.TemporaryDirectory() as tmp:
            claude = stub(tmp, COST_STUB)
            task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
            options = {"claude": claude, "model": "sonnet", "budget": 0.5, "timeout": 60,
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(Path(tmp) / "config")}), \
                    unittest.mock.patch.object(run, "grade", rewrite):
                row = run.run_one(task, next(c for c in run.load_configs() if c["id"] == "plugin"), options, {})
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
            with unittest.mock.patch.dict("os.environ", env), \
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
            self.assertTrue(run.rate_limited(row), answer)
            self.assertEqual(run.refusal(row), "network", answer)
        self.assertEqual(run.refusal({"is_error": True, "exit_code": 1, "api_error_status": 429, "stderr": "",
                                      "answer": ""}), "limit")
        for row in ({"is_error": True, "exit_code": 1, "api_error_status": None, "stderr": "",
                     "answer": "The fix fails: socket code raises ENOTFOUND when DNS is down."},
                    {"is_error": False, "exit_code": 0, "api_error_status": None, "stderr": "",
                     "answer": "Connection lost errors come from the retry wrapper."}):
            self.assertFalse(run.rate_limited(row), row["answer"])

    def test_an_answer_about_rate_limits_is_not_a_limit(self):
        self.assertFalse(run.rate_limited({"is_error": False, "api_error_status": None, "stderr": "",
                                           "answer": "Use a token bucket rate limiter; on the usage limit, return 429."}))
        # An errored run whose task text talks about limits failed the task; it wasn't refused by the API.
        for answer in ("Draft: a token bucket rate limiter keyed per client, 429 when over.",
                       "Too many requests hit the cache; the overloaded node needs a usage limit."):
            self.assertFalse(run.rate_limited({"is_error": True, "exit_code": 1, "api_error_status": None,
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
            self.assertTrue(run.rate_limited({"is_error": True, "exit_code": 1, "api_error_status": None,
                                              "stderr": "", "answer": answer}), answer)
        self.assertTrue(run.rate_limited({"is_error": True, "api_error_status": 529, "stderr": "", "answer": ""}))
        self.assertTrue(run.rate_limited({"is_error": False, "exit_code": 1, "api_error_status": None,
                                          "stderr": "Error: API Error: Repeated 529 Overloaded errors", "answer": ""}))

    def test_an_api_error_before_any_spend_is_set_aside_not_retried(self):
        # What Claude Code 2.1.330 printed for an invalid API key: an error, nothing spent, no model usage.
        result = json.loads(API_ERROR.read_text(encoding="utf-8"))
        row = run.record({"id": "t", "class": "lookup"}, {"id": "defaults"}, Recorder.options, run.mrconfig.build({}),
                         result, dict(Recorder.facts, exit_code=1))
        self.assertEqual(run.refusal(row), "api_error")
        self.assertFalse(run.rate_limited(row))  # waiting fixes no key, balance or model access
        self.assertEqual((row["total_cost_usd"], row["cost_known"]), (0, True))
        errored = {"is_error": True, "exit_code": 1, "api_error_status": None, "stderr": "", "subtype": "success"}
        # The messages for the other two at https://code.claude.com/docs/en/errors.md.
        for answer in ("Credit balance is too low", "Model 'claude-fable-5-2' not found"):
            self.assertEqual(run.refusal(dict(errored, answer=answer, total_cost_usd=0)), "api_error", answer)
        # A run that spent tried the task, and a zero cost on error_during_execution may have been zeroed.
        self.assertIsNone(run.refusal(dict(errored, answer="The fix is incomplete.", total_cost_usd=0.02)))
        self.assertIsNone(run.refusal(dict(errored, answer="", subtype="error_during_execution", total_cost_usd=0)))
        self.assertIsNone(run.refusal(dict(errored, is_error=False, exit_code=0, answer="hi", total_cost_usd=0)))

    def refused(self, refuse: str, configs: list[str], runs: int) -> tuple[int, list[dict], list[dict], list[float]]:
        with tempfile.TemporaryDirectory() as tmp:
            claude = stub(tmp, API_ERROR_STUB)
            out = Path(tmp) / "out.jsonl"
            pauses: list[float] = []
            with unittest.mock.patch.dict("os.environ", {"STUB_ERROR": str(API_ERROR), "STUB_REFUSED": refuse}), \
                    unittest.mock.patch.object(run.time, "sleep", pauses.append):
                code = run.main(["--tasks", "lookup-retry-limit", "--configs", *configs, "--runs", str(runs),
                                 "--max-total", "3", "--claude", *claude, "--out", str(out)])
            read = lambda p: [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []  # noqa: E731
            return code, read(out), read(Path(tmp) / "limited" / "out.jsonl"), [p for p in pauses if p >= run.LIMIT_PAUSE]

    def test_api_errors_in_a_row_stop_the_bench_without_a_failed_task(self):
        code, rows, limited, pauses = self.refused("every run", ["defaults"], runs=run.API_ERROR_STOP + 2)
        self.assertEqual(code, 2)
        self.assertEqual(rows, [])
        self.assertEqual([r["limited_reason"] for r in limited], ["api_error"] * run.API_ERROR_STOP)
        self.assertEqual(pauses, [])

    def test_a_configuration_the_account_cannot_run_does_not_stop_the_others(self):
        code, rows, limited, pauses = self.refused("fable", ["defaults", "fable"], runs=run.API_ERROR_STOP)
        self.assertEqual(code, 0)
        self.assertEqual([(r["config"], r["passed"]) for r in rows], [("defaults", True)] * run.API_ERROR_STOP)
        self.assertEqual([(r["config"], r["limited_reason"]) for r in limited], [("fable", "api_error")] * run.API_ERROR_STOP)
        self.assertEqual(pauses, [])


API_ERROR = ROOT / "tests" / "fixtures" / "claude-2.1.330-api-error.json"

API_ERROR_STUB = '''#!/usr/bin/env python3
import json, os, sys
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
if os.environ["STUB_REFUSED"] in ("every run", os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL")):
    with open(os.environ["STUB_ERROR"], encoding="utf-8") as fh:
        sys.stdout.write(fh.read())
    sys.exit(1)
print(json.dumps({"is_error": False, "subtype": "success", "api_error_status": None, "total_cost_usd": 0.01,
                  "result": "read_retry_limit in app/settings.py", "modelUsage": {}}))
'''


CWD_STUB ='''#!/usr/bin/env python3
import json, os, sys
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
print(json.dumps({"result": os.getcwd(), "total_cost_usd": 0.0, "modelUsage": {}}))
'''


class TaskIdentity(unittest.TestCase):
    """A run's folder can't hand the router signal words, and a changed task is never pooled silently."""

    def test_no_task_id_or_signal_word_reaches_a_runs_working_directory(self):
        sys.path.insert(0, str(ROOT / "plugins" / "model-router" / "scripts"))
        from modelrouter import profile
        with tempfile.TemporaryDirectory() as tmp:
            claude = stub(tmp, CWD_STUB)
            options = {"claude": claude, "model": "sonnet", "budget": 0.5, "timeout": 60,
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            base = str(Path(tempfile.gettempdir()).resolve())
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(Path(tmp) / "config")}):
                for task in run.load_tasks():
                    with self.subTest(task=task["id"]):
                        row = run.run_one(task, run.load_configs()[1], options, {})
                        ours = str(Path(row["answer"]).resolve()).replace(base, "")
                        self.assertNotIn(task["id"], ours)
                        self.assertEqual(profile.signals(ours.lower()), [], ours)
                        self.assertEqual(row["task_revision"], task.get("revision", 1))

    def test_every_task_has_a_revision_and_the_changed_one_moved_on(self):
        for task in run.load_tasks():
            self.assertIsInstance(task.get("revision"), int, task["id"])
            self.assertGreaterEqual(task["revision"], 1, task["id"])
        tasks = {t["id"]: t for t in run.load_tasks()}
        self.assertEqual(tasks["design-cache-invalidation"]["revision"], 2)

    def test_rows_on_an_earlier_task_revision_are_left_out_of_the_tables(self):
        template = json.loads((ROOT / "bench" / "results" / "archive" / "early-builds" / "m6-05-design-2026-11-22.jsonl")
                              .read_text(encoding="utf-8").splitlines()[0])
        old = {k: v for k, v in template.items() if k != "task_revision"}
        rows = [dict(old, task="design-cache-invalidation", config="plugin"),
                dict(old, task="design-cache-invalidation", config="defaults"),
                dict(old, task="design-rate-limiter", config="plugin", total_cost_usd=0.5)]
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
        self.assertIn("`design-cache-invalidation` is at revision 2; 2 of its runs ($", page)
        self.assertIn("are left out of every table", page)
        self.assertIn("- Bias: 1 router-arm runs", page)  # the one router row with no task_revision

    def test_a_missing_key_is_an_older_harness_only_below_the_row_schema(self):
        template = json.loads((ROOT / "bench" / "results" / "archive" / "early-builds" / "m6-05-design-2026-11-22.jsonl")
                              .read_text(encoding="utf-8").splitlines()[0])
        base = dict(template, config="defaults", task="design-rate-limiter", task_revision=1, wall_ms=1000)
        older = {k: v for k, v in base.items() if k != "wall_ms"}
        current = dict(older, schema=run.ROW_SCHEMA)  # a current row never lacks it, so the page doesn't read it as old
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results"
            results.mkdir()
            (results / "a.jsonl").write_text("".join(json.dumps(r) + "\n" for r in (older, current)))
            out = Path(tmp) / "page.md"
            analyze.main([str(results / "a.jsonl"), "--out", str(out)])
            self.assertIn("1 run(s) here carry no `wall_ms`", out.read_text(encoding="utf-8"))
        self.assertEqual(run.record({"id": "t", "class": "lookup"}, {"id": "x"}, Recorder.options, run.mrconfig.build({}), {},
                                    Recorder.facts)["schema"], run.ROW_SCHEMA)


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
        for task in run.load_tasks():
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
        names.discard("__pycache__")
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

    def test_analyze_sets_aside_a_run_whose_check_broke(self):
        template = json.loads((ROOT / "bench" / "results" / "archive" / "early-builds" / "m6-05-design-2026-11-22.jsonl")
                              .read_text(encoding="utf-8").splitlines()[0])
        good = dict(template, config="defaults", task="design-rate-limiter", task_revision=1, schema=run.ROW_SCHEMA,
                    passed=True, grade={"verdict": "pass", "reason": "solved", "check_hash": "a" * 16})
        bad = dict(good, passed=False, total_cost_usd=0.25,
                   grade={"verdict": "broken", "reason": "NameError: x", "check_hash": "b" * 16})
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results"
            results.mkdir()
            (results / "a.jsonl").write_text("".join(json.dumps(r) + "\n" for r in (good, bad)))
            loaded = analyze.load([results / "a.jsonl"])
            out = Path(tmp) / "page.md"
            analyze.main([str(results / "a.jsonl"), "--out", str(out)])
            page = out.read_text(encoding="utf-8")
        self.assertEqual((len(loaded.rows), len(loaded.broken)), (1, 1))
        self.assertIn("1 run(s) ($0.25) are set aside, though they stay in the results files: the pass check of "
                      "`design-rate-limiter` broke on them", page)


class Pilot(unittest.TestCase):
    def test_the_committed_page_is_what_analyze_writes(self):
        raw = sorted((ROOT / "bench" / "results").glob("*.jsonl"))
        self.assertTrue(raw, "no raw results committed")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = analyze.main([*map(str, raw), "--out", str(ROOT / "docs" / "BENCHMARKS.md"), "--check"])
        self.assertEqual(code, 0, err.getvalue())

    def test_the_page_keeps_its_cost_and_isolation_notes(self):
        page = (ROOT / "docs" / "BENCHMARKS.md").read_text(encoding="utf-8")
        self.assertIn("plus anything that subagents still running", page)
        self.assertIn("margin, not a bound", page)
        self.assertIn("`Bash(tail *)`", page)
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
        self.assertEqual({tasks[t]["class"] for t in hard}, set(run.CLASSES))


class ProbeHelper(unittest.TestCase):
    CASES = (
        ('import sys, json\nprint("noise")\nprint(json.dumps(json.load(sys.stdin)))', {"x": 1}),
        ('print("{\\"a\\": 1}")\nprint("trailing")', None),
        ('import os\nprint("{\\"a\\": 1}", flush=True)\nos._exit(3)', None),
        ('import os\nos._exit(0)', None),
        ('import time\ntime.sleep(30)', None),
        ('import sys, json\nprint(json.dumps(sys.flags.isolated))', 1),
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
        self.assertTrue(all(tasks[i]["revision"] == 1 for i in want))

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

if __name__ == "__main__":
    unittest.main()
