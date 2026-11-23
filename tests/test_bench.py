"""Tests for the cost and quality benchmark under bench/. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
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
        self.assertEqual((row["status"], row["rank"], row["best_baseline"]), ("PASS", 1, "defaults"))

    def test_too_few_runs_is_never_a_win(self):
        data = rows("plugin", "lookup", [0.10], [True]) + rows("defaults", "lookup", [0.50], [True])
        row = self.summary(data)["lookup"]
        self.assertEqual(row["status"], "PARTIAL")
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

    def test_the_table_has_a_row_per_class_and_a_column_per_configuration(self):
        data = (rows("plugin", "lookup", [0.1], [True]) + rows("defaults", "lookup", [0.2], [True])
                + rows("plugin", "bugfix", [0.3], [False]) + rows("defaults", "bugfix", [0.4], [True]))
        summary = analyze.summarize(data, routers=frozenset({"plugin"}), router="plugin", draws=100, seed=0)
        text = analyze.render(summary, {"total_cost_usd": 1.0})
        header = next(line for line in text.splitlines() if line.startswith("| Task class"))
        self.assertIn("| plugin |", header)
        self.assertIn("| defaults |", header)
        class_rows = [line for line in text.splitlines() if line.startswith(("| lookup ", "| bugfix "))]
        self.assertEqual(len(class_rows), 2)
        for line in class_rows:
            self.assertTrue(line.endswith("| PARTIAL |"), line)

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

    def test_a_class_with_no_router_or_baseline_has_no_differences(self):
        self.assertEqual(self.summary(rows("defaults", "lookup", [0.3], [True]))["lookup"]["differences"], {})

    def page_args(self, tmp, name="page.md"):
        res = Path(tmp) / "results"
        res.mkdir(exist_ok=True)
        data = rows("plugin", "lookup", [0.1, 0.2], [True, False]) + rows("defaults", "lookup", [0.3, 0.4], [True, True])
        records = [dict(r, main_model="sonnet", started_at="2026-11-22T10:00:00+00:00", claude_version="2.1.330",
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
            relative = Path(os.path.relpath(path))
            results = analyze.load([path, relative])
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
        real = json.loads((ROOT / "bench" / "tasks" / "lookup-retry-limit" / "task.json").read_text())
        assert real["class"] == "lookup"
        base = {"class": "lookup", "task": "lookup-retry-limit", "config": "defaults", "total_cost_usd": 0.05,
                "passed": True, "claude_version": "2.1.330", "main_model": "sonnet", "models": {},
                "started_at": "2026-11-23T01:00:00+00:00", "budget_usd": 1.0, "plugin_state": None,
                "plugin_data_own": True, "sessions_saved": True, "bench_commit": "c" * 40, "task_revision": 1}
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
        self.assertIn("- `bench/results/archive/early-builds/` holds 3 rows left out of every table, plus 1 set-aside "
                      "line under its `infra/` and `limited/`: 2 ran on 2 other commits (older builds of the router "
                      "and the bench), 1 on an earlier revision of `design-cache-invalidation`, and 2 were recorded "
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
                  [dict(lost, task="bugfix-pagination", limited_reason="limit")])
            out = Path(tmp) / "page.md"
            analyze.main([str(p) for p in sorted(res.glob("*.jsonl"))] + ["--out", str(out)])
            page = out.read_text(encoding="utf-8")
        self.assertIn("3 run(s) are set aside, outside the results, because the API never got to the task: "
                      "2 lost the network, 1 hit a rate or usage limit. 2 of them were run again later", page)
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
    facts = {"started_at": "2026-11-23T00:00:00+00:00", "wall_ms": 7000, "exit_code": 0, "stderr": "", "passed": True,
             "plugin_state": None, "ledger": None, "agent_types": {}, "session_dir_removed": True, "kept": None}

    def row(self, result, **facts):
        return run.record(self.task, self.config, self.options, {}, result, {**self.facts, **facts})

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

    def test_the_runner_stops_before_a_run_could_pass_the_total_cap(self):
        self.assertTrue(run.can_start(spent=2.0, budget=0.5, max_total=3.0))
        self.assertFalse(run.can_start(spent=2.3, budget=0.5, max_total=3.0))


class Tasks(unittest.TestCase):
    """Each pass check fails on the task as shipped and passes on a known-good answer."""

    def check(self, task, workdir, answer):
        result = Path(workdir) / ".." / "result.txt"
        result.write_text(answer, encoding="utf-8")
        return run.grade(task["dir"] / "check.py", Path(workdir), result, timeout=60)

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


    def test_a_lookup_answer_listing_every_candidate_fails(self):
        task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            shutil.copytree(task["dir"] / "repo", work)
            self.assertFalse(self.check(task, work, "app/settings.py: read_retry_limit, read_timeout; "
                                                    "app/backoff.py: retries_left, retry_delay"))
            for answer in ("read_retry_limit in app/settings.py; app/client.py calls it",
                           "`read_retry_limit` in `app/settings.py`. `read_timeout` next to it reads REQUEST_TIMEOUT "
                           "instead.",
                           "`read_retry_limit` in app/settings.py; `fetch` in app/client.py passes it to "
                           "`backoff.retries_left`."):
                self.assertTrue(self.check(task, work, answer), answer)


STUB = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
answer = "read_retry_limit in app/settings.py"
if "--plugin-dir" in sys.argv:
    root = os.environ.get("CLAUDE_CODE_PLUGIN_CACHE_DIR") or str(Path(os.environ["CLAUDE_CONFIG_DIR"]) / "plugins")
    data = Path(root) / "data" / "model-router-inline"
    answer += " (had state)" if (data / "learned.json").exists() else " (fresh)"
    data.mkdir(parents=True, exist_ok=True)
    (data / "learned.json").write_text('{"lookup": {"bump": 1}}')
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
            (shared / "learned.json").write_text('{"lookup": {"calls": 2}}')
            os.utime(shared / "learned.json", (1000, 1000))
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
            self.assertEqual([p.name for p in shared.iterdir()], ["learned.json"])
            self.assertEqual((shared / "learned.json").read_text(), '{"lookup": {"calls": 2}}')
            self.assertEqual((shared / "learned.json").stat().st_mtime, 1000)
            self.assertTrue(first["passed"])
            self.assertIn("(fresh)", first["answer"])
            self.assertIn("(fresh)", second["answer"])
            self.assertEqual(first["plugin_state"], {"state.json": None, "learned.json": None})
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

    def test_a_root_cause_check_fails_when_the_test_is_edited_to_pass(self):
        for task in (t for t in run.load_tasks() if t["class"] == "root-cause"):
            with self.subTest(task=task["id"]), tempfile.TemporaryDirectory() as tmp:
                task, work = self.shipped(task["id"], tmp)
                for test_file in work.glob("test_*.py"):
                    test_file.write_text("import unittest\n", encoding="utf-8")
                self.assertFalse(self.check(task, work))

    def test_a_security_finding_given_only_in_the_reply_fails(self):
        for task_id, reply in (("security-sql-injection", "db/users.py:6 builds the SQL with an f-string."),
                               ("security-path-traversal", "files/server.py:8 joins the name unresolved.")):
            with self.subTest(task=task_id), tempfile.TemporaryDirectory() as tmp:
                task, work = self.shipped(task_id, tmp)
                self.assertFalse(self.check(task, work, reply))

    JSON_ANSWERS = {  # task: (answer files that must pass, answer files that must fail); a str is written as is
        "security-sql-injection": (
            [{"file": "db/users.py", "line": 6}, {"file": "db/users.py", "line": 6, "why": "an f-string builds the SQL"},
             {"file": "./db/users.py", "line": 6}, {"file": "db\\users.py", "line": 6},
             {"file": "/tmp/x/work/db/users.py", "line": 6}],
            [{"file": "db/orders.py", "line": 3}, {"file": "db/users.py", "line": 7}, {"file": "app.py", "line": 8},
             {"file": "db/users.py", "line": "6 or 7"}, {"file": "db/users.py", "line": [6, 7, 11]},
             {"file": "db/users.py", "line": "6-11"}, {"file": "db/users.py", "line": "6 to 11"},
             {"file": "db/users.py", "line": 6, "maybe": [7, 11]}, {"file": "db/users.py", "line": [4, 6, 11]},
             [{"file": "db/users.py", "line": 6}, {"file": "db/orders.py", "line": 4}],
             {"file": ["db/users.py", "db/orders.py"], "line": 6}, {"file": "db/users.py", "line": "6"},
             {"file": "db/users.py", "line": 6.0}, {"file": "db/users.py", "line": 6, "why": ["f-string"]},
             {"file": "db/users.py"}, "db/users.py:6", ""]),
        "security-path-traversal": (
            [{"file": "files/server.py", "line": 8}, {"file": "files/server.py", "line": 9, "why": "opens it"},
             {"file": "files\\server.py", "line": 8}, {"file": "/tmp/x/work/files/server.py", "line": 8}],
            [{"file": "files/server.py", "line": "8 or 9"}, {"file": "files/server.py", "line": [8, 9]},
             {"file": "files/server.py", "line": "8-9"}, {"file": "files/server.py", "line": 6},
             {"file": "files/thumbs.py", "line": 8}, {"file": "web.py", "line": 7},
             {"file": "files/server.py", "line": 8, "also": "web.py:5"},
             [{"file": "files/server.py", "line": 8}, {"file": "files/thumbs.py", "line": 9}],
             "files/server.py:8"]),
        "research-changelog": (
            [{"default_changed_in": "v1.3", "configurable_in": "2", "variable": "FETCHKIT_RETRIES",
              "sources": ["/tmp/x/work/docs/changelog/v1.3.md", "changelog\\v2.0.md"]},
             {"default_changed_in": "1.3.0", "configurable_in": 2, "variable": "FETCHKIT_RETRIES",
              "sources": ["./docs/changelog/v1.3.md:3", "docs/changelog/v2.0.md#L4"]}],
            [{"default_changed_in": "1.4", "configurable_in": "2.0", "variable": "FETCHKIT_RETRIES",
              "sources": ["docs/changelog/v1.3.md", "docs/changelog/v2.0.md"]},
             {"default_changed_in": "1.3", "configurable_in": "2.0", "variable": "FETCHKIT_RETRIES",
              "sources": ["docs/changelog/v2.0.md"]}]),
        "design-cache-invalidation": (
            [{"strategy": "event_invalidation", "ttl_seconds": None, "max_staleness_seconds": 1, "reasons": ["bus"]},
             {"strategy": "event_invalidation", "ttl_seconds": 600, "max_staleness_seconds": 2, "reasons": ["x"]}],
            [{"strategy": "event_invalidation", "ttl_seconds": 5, "max_staleness_seconds": 1, "reasons": ["x"]},
             {"strategy": "ttl_only", "ttl_seconds": 5, "max_staleness_seconds": 5, "reasons": ["x"]}]),
        "research-latency": (
            [{"deploy": "d3", "commit": "c0912ee", "p95_ms": 310}, {"deploy": "d3", "commit": "c0912ee",
                                                                     "p95_ms": "310"}],
            [{"deploy": "d2", "commit": "b77e210", "p95_ms": 280}, {"deploy": "d3", "commit": "c0912ee",
                                                                     "p95_ms": 330}]),
    }

    def test_checks_take_every_fair_form_of_a_right_answer_and_no_wrong_one(self):
        files = {"research-changelog": "RESEARCH.json", "research-latency": "RESEARCH.json",
                 "design-cache-invalidation": "DESIGN.json", "security-sql-injection": "FINDING.json",
                 "security-path-traversal": "FINDING.json"}
        for task_id, (right, wrong) in self.JSON_ANSWERS.items():
            for answer, expected in [(a, True) for a in right] + [(a, False) for a in wrong]:
                with self.subTest(task=task_id, answer=answer), tempfile.TemporaryDirectory() as tmp:
                    task, work = self.shipped(task_id, tmp)
                    text = answer if isinstance(answer, str) else json.dumps(answer)
                    (work / files[task_id]).write_text(text, encoding="utf-8")
                    self.assertEqual(self.check(task, work), expected)

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
        prices = {"claude-sonnet-5-5": {"output": 15}, "claude-opus-5-5": {"output": 75},
                  "claude-opus-4-1": {"output": 99}, "claude-haiku-5-5": {"output": 5}, "claude-opus-*": {"output": 1}}
        plan = analyze.estimate(data, tasks, configs, prices)
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
                      "result": "API Error: 429 rate limit reached", "total_cost_usd": 0.001, "modelUsage": {}}))
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


class Limits(unittest.TestCase):
    """A rate or usage limit pauses the bench and retries the run; it is never recorded as a failed task."""

    def bench(self, limited: int) -> tuple[int, list[dict], list[dict], list[float]]:
        with tempfile.TemporaryDirectory() as tmp:
            claude = stub(tmp, LIMIT_STUB)
            out = Path(tmp) / "out.jsonl"
            env = {"STUB_CALLS": str(Path(tmp) / "calls"), "STUB_LIMITED": str(limited)}
            pauses: list[float] = []
            with unittest.mock.patch.dict("os.environ", env), \
                    unittest.mock.patch.object(run.time, "sleep", pauses.append):
                code = run.main(["--tasks", "lookup-retry-limit", "--configs", "defaults", "--runs", "1",
                                 "--max-total", "3", "--claude", *claude, "--out", str(out)])
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


CWD_STUB = '''#!/usr/bin/env python3
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

    def test_the_page_names_each_tasks_revision_and_the_folder_name_bias(self):
        template = json.loads((ROOT / "bench" / "results" / "archive" / "early-builds" / "m6-05-design-2026-11-22.jsonl")
                              .read_text(encoding="utf-8").splitlines()[0])
        old = {k: v for k, v in template.items() if k != "task_revision"}
        rows = [dict(old, task="design-cache-invalidation", config="plugin"),
                dict(old, task="design-cache-invalidation", config="defaults"),
                dict(old, task="design-rate-limiter", config="plugin", task_revision=1)]
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results"
            results.mkdir()
            (results / "a-design.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            out = Path(tmp) / "page.md"
            analyze.main([str(results / "a-design.jsonl"), "--out", str(out)])
            page = out.read_text(encoding="utf-8")
        self.assertIn("`design-cache-invalidation` is at revision 2; 2 of its runs here ran on an earlier revision", page)
        self.assertIn("- Bias: 1 router-arm runs", page)  # the one router row with no task_revision
        self.assertIn("`design-cache-invalidation`", page.split("- Bias: ")[1].split("\n")[0])


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

    def test_planted_stdlib_names_cannot_fake_a_pass(self):
        for task in run.load_tasks():
            with self.subTest(task=task["id"]), tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / "work"
                shutil.copytree(task["dir"] / "repo", work)
                for name in ("json.py", "unittest.py"):
                    (work / name).write_text(self.PLANT, encoding="utf-8")
                answer = Path(tmp) / "answer.txt"
                answer.write_text(task.get("wrong_answer", ""), encoding="utf-8")
                self.assertFalse(run.grade(task["dir"] / "check.py", work, answer))
                if (task["dir"] / "solution").is_dir():  # the same planted files don't cost an honest run its pass
                    shutil.copytree(task["dir"] / "solution", work, dirs_exist_ok=True)
                    answer.write_text(task.get("reference_answer", ""), encoding="utf-8")
                    self.assertTrue(run.grade(task["dir"] / "check.py", work, answer))

    def test_a_check_runs_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            check = Path(tmp) / "check.py"
            check.write_text("import sys\nsys.exit(0 if sys.flags.isolated else 1)\n", encoding="utf-8")
            self.assertTrue(run.grade(check, Path(tmp), Path(tmp) / "answer.txt"))

    def test_a_check_sees_no_credentials(self):
        secret = {"CLAUDE_CODE_OAUTH_TOKEN": "t", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR": "3", "ANTHROPIC_API_KEY": "k",
                  "ANTHROPIC_BASE_URL": "u", "GITHUB_TOKEN": "g", "AWS_SECRET_ACCESS_KEY": "s", "DB_PASSWORD": "p",
                  "api_key": "lower"}
        with tempfile.TemporaryDirectory() as tmp:
            check = Path(tmp) / "check.py"
            check.write_text("import os, sys\n"
                             f"sys.exit(0 if not set({sorted(secret)!r}) & set(os.environ) "
                             "and os.environ.get('MRBENCH_KEPT') == 'yes' and 'PATH' in os.environ else 1)\n",
                             encoding="utf-8")
            with unittest.mock.patch.dict("os.environ", {**secret, "MRBENCH_KEPT": "yes"}):
                self.assertTrue(run.grade(check, Path(tmp), Path(tmp) / "answer.txt"))
                self.assertEqual(sorted(set(secret) & set(run.grading_env(dict(os.environ)))), [])

    def test_a_check_that_hangs_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            check = Path(tmp) / "check.py"
            check.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            self.assertFalse(run.grade(check, Path(tmp), Path(tmp) / "answer.txt", timeout=1))

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
        for check in (run.BENCH / "tasks").glob("*/check.py"):
            text = check.read_text(encoding="utf-8")
            self.assertNotIn("sys.path.insert", text, check)
            if "passcheck" in text:
                self.assertIn('sys.modules["passcheck"] = passcheck', text, check)


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
        for row in data:  # a lookup is graded from its answer alone, so today's check must agree with the record
            if row["class"] == "lookup" and len(row["answer"]) < 2000:  # the line keeps 2,000 characters
                with tempfile.TemporaryDirectory() as tmp:
                    answer = Path(tmp) / "answer.txt"
                    answer.write_text(row["answer"], encoding="utf-8")
                    check = ROOT / "bench" / "tasks" / row["task"] / "check.py"
                    self.assertEqual(run.grade(check, Path(tmp), answer), row["passed"], row["config"])


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
                (work / rel).write_text(text if isinstance(text, str) else json.dumps(text), encoding="utf-8")
            return self.check(task, work, answer)

    def test_the_harder_tier_has_a_task_in_every_class(self):
        hard = {"lookup-effective-timeout", "impl-semver-compare", "bugfix-split-amount", "cross-tenant-cache",
                "security-prefix-check", "rootcause-cached-rates", "design-event-partitions",
                "research-yanked-release"}
        tasks = {t["id"]: t for t in run.load_tasks()}
        self.assertEqual({tasks[t]["class"] for t in hard}, set(run.CLASSES))

    def test_the_timeout_lookup_wants_the_capped_value_and_the_client(self):
        for answer, expected in (("25 app/client.py", True), ("It is capped.\n`25 app/client.py`", True),
                                 ("25 seconds, app/client.py", True), ("25.0 ./app/client.py", True),
                                 ("<25 app/client.py>", True), ("<25> <app/client.py>", True), ("<45 app/client.py>", False),
                                 ("45 app/client.py", False), ("45 app/resolve.py", False), ("25 app/limits.py", False),
                                 ("10 app/env.py", False), ("45000 app/env.py", False),
                                 ("25 app/client.py\n45 app/resolve.py", False),
                                 ("The answer is 25 seconds in app/client.py.", False)):
            with self.subTest(answer=answer):
                self.assertEqual(self.run_with("lookup-effective-timeout", {}, answer), expected)

    def test_semver_needs_numeric_identifiers_validation_and_the_prerelease_rule(self):
        solution = self.source("impl-semver-compare", "solution/semver.py")
        loose = solution.replace("(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)", "(\\d+)\\.(\\d+)\\.(\\d+)")
        any_prerelease = solution.replace(
            "    return version[1] is None or any(bound[1] is not None and bound[0] == version[0] for _, bound in "
            "comparators)", "    return True")
        caret_major_only = solution.replace(
            "upper = (major + 1, 0, 0) if major else (0, minor + 1, 0) if minor else (0, 0, patch + 1)",
            "upper = (major + 1, 0, 0)")
        for name, text in (("leading zeros allowed", loose), ("prereleases always match", any_prerelease),
                           ("caret ignores 0.x", caret_major_only)):
            with self.subTest(name):
                self.assertNotEqual(text, solution)
                self.assertFalse(self.run_with("impl-semver-compare", {"semver.py": text}))

    def test_an_allocation_that_rounds_floors_negatives_or_skips_validation_fails(self):
        solution = self.source("bugfix-split-amount", "solution/money.py")
        to_last = ("def allocate(total_cents, weights):\n    s = sum(weights)\n"
                   "    shares = [total_cents * w // s for w in weights]\n    shares[-1] += total_cents - sum(shares)\n"
                   "    return shares\n")
        floored = solution.replace("total, sign = sum(weights), (-1 if total_cents < 0 else 1)",
                                   "total, sign = sum(weights), 1").replace("abs(total_cents)", "total_cents")
        unchecked = "\n".join(l for l in solution.splitlines() if "raise ValueError" not in l and "if (not " not in l
                              and "or sum(weights) <= 0" not in l) + "\n"
        for name, text in (("remainder to the last share", to_last), ("negatives floored", floored),
                           ("no validation", unchecked)):
            with self.subTest(name):
                self.assertNotEqual(text, solution)
                self.assertFalse(self.run_with("bugfix-split-amount", {"money.py": text}))

    def test_tenant_keys_must_reach_every_hand_built_key_and_never_collide(self):
        joined = "def price_key(tenant_id, product_id):\n    return f\"price:{tenant_id}:{product_id}\"\n"
        report = ("from cache import store\n\n\ndef cached_products(tenant_id):\n    prefix = f\"price:{tenant_id}:\"\n"
                  "    return sorted(k[len(prefix):] for k in store.keys() if k.startswith(prefix))\n")
        sweep = ("from cache import store\n\n\ndef sweep(tenant_id, keep):\n    prefix = f\"price:{tenant_id}:\"\n"
                 "    for key in store.keys():\n        if key.startswith(prefix) and key[len(prefix):] not in keep:\n"
                 "            store.delete(key)\n")
        events = self.source("cross-tenant-cache", "solution/admin/events.py")
        missed_delete = events.replace(
            "    db.remove(tenant_id, product_id)\n    store.delete(keys.price_key(tenant_id, product_id))",
            "    db.remove(tenant_id, product_id)\n    store.delete(\"price:\" + product_id)")
        untenanted_sweep = ("from cache import keys, store\n\n\ndef sweep(tenant_id, keep):\n"
                            "    for key in store.keys():\n"
                            "        if key[2] not in keep:\n            store.delete(key)\n")
        for name, files in (("colliding string key", {"cache/keys.py": joined, "reports/cached.py": report,
                                                      "jobs/sweep.py": sweep}),
                            ("hand-built key left in the delete handler", {"admin/events.py": missed_delete}),
                            ("sweep across tenants", {"jobs/sweep.py": untenanted_sweep})):
            with self.subTest(name):
                self.assertFalse(self.run_with("cross-tenant-cache", files))

    def test_the_import_finding_names_the_tar_extract_and_not_a_decoy(self):
        for finding, expected in (({"file": "files/importer.py", "line": 17}, True),
                                  ({"file": "files/importer.py", "line": 15}, True),
                                  ({"file": "files/importer.py", "line": 13}, False),
                                  ({"file": "files/server.py", "line": 9}, False),
                                  ({"file": "files/server.py", "line": 8}, False),
                                  ({"file": "files/thumbs.py", "line": 23}, False),
                                  ({"file": "files/thumbs.py", "line": 12}, False),
                                  ({"file": "auth/tokens.py", "line": 22}, False)):
            with self.subTest(finding=finding):
                self.assertEqual(self.run_with("security-prefix-check", {"FINDING.json": finding}), expected)

    def test_dropping_or_clearing_the_cache_does_not_fix_the_shared_rows(self):
        rates = self.source("rootcause-cached-rates", "repo/rates.py")
        pricing = self.source("rootcause-cached-rates", "repo/pricing.py")
        uncached = rates.replace("@functools.lru_cache(maxsize=None)\n", "")
        cleared = pricing.replace("    rows = rate_table(region)\n    for row in rows:",
                                  "    rate_table.cache_clear()\n    rows = rate_table(region)\n    for row in rows:")
        for name, files in (("cache dropped", {"rates.py": uncached, "pricing.py": pricing}),
                            ("cache cleared on each sale", {"pricing.py": cleared})):
            with self.subTest(name):
                self.assertNotEqual(list(files.values())[0], rates if name == "cache dropped" else pricing)
                self.assertFalse(self.run_with("rootcause-cached-rates", files))
        copied = rates.replace("    return rows\n", "    return rows\n\n\n_cached = rate_table\n\n\n"
                                                    "def rate_table(region):\n"
                                                    "    return [dict(row) for row in _cached(region)]\n")
        self.assertTrue(self.run_with("rootcause-cached-rates", {"rates.py": copied, "pricing.py": pricing}))

    def test_the_topic_design_must_hold_every_number(self):
        good = {"partitions": 30, "key": "order_id", "retention_hours": 80, "replication_factor": 3, "reasons": ["x"]}
        for change, expected in (({}, True), ({"partitions": 36}, True), ({"retention_hours": 72}, True),
                                 ({"retention_hours": 82}, True),
                                 ({"partitions": 24}, False), ({"partitions": 22}, False), ({"partitions": 32}, False),
                                 ({"partitions": 42}, False), ({"partitions": 12}, False),
                                 ({"key": "customer_id"}, False), ({"replication_factor": 2}, False),
                                 ({"replication_factor": 4}, False), ({"retention_hours": 168}, False),
                                 ({"retention_hours": 48}, False), ({"partitions": 30.0}, False),
                                 ({"extra": 1}, False)):
            with self.subTest(change=change):
                self.assertEqual(self.run_with("design-event-partitions", {"DESIGN.json": {**good, **change}}),
                                 expected)

    def test_the_release_research_needs_the_yank_the_errata_and_the_advisory(self):
        good = {"stream_since": "2.3.1", "timeout_since": "2.4.2", "safe_since": "2.4.3",
                "sources": ["docs/changelog/v2.3.1.md", "docs/errata.md", "docs/security/SA-2025-01.md"]}
        for change, expected in (({}, True), ({"stream_since": "v2.3.1", "safe_since": "2.4.3"}, True),
                                 ({"sources": ["./docs/changelog/v2.3.1.md:3", "errata.md", "security/SA-2025-01.md",
                                               "docs/security/SA-2025-02.md"]}, True),
                                 ({"stream_since": "2.3.0"}, False), ({"timeout_since": "2.4.0"}, False),
                                 ({"safe_since": "2.4.2"}, False), ({"safe_since": "2.5.0"}, False),
                                 ({"sources": ["docs/changelog/v2.3.1.md", "docs/errata.md"]}, False),
                                 ({"sources": good["sources"] + ["docs/nope.md"]}, False)):
            with self.subTest(change=change):
                self.assertEqual(self.run_with("research-yanked-release", {"RESEARCH.json": {**good, **change}}),
                                 expected)


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
                "rootcause-wallet-race": "root-cause"}
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

    def test_migration_rejects_each_known_mistake(self):
        solution = self.source("impl-merge-customers", "solution/store/migrations/m0002_merge_customers.py")
        rel = "store/migrations/m0002_merge_customers.py"
        self.assertTrue(self.run_with("impl-merge-customers", {rel: solution}))

        def between(first, last, replacement=""):
            return solution[:solution.index(first)] + replacement + solution[solution.index(last):]

        rebuild, view, fill = ('        conn.execute("""\n            CREATE TABLE new_customers', '        conn.execute("""\n            CREATE VIEW',
                               '        conn.execute("""\n            UPDATE customers SET name')
        copy = 'INSERT INTO {} SELECT id, lower(trim(email)), name, created_at FROM {}'
        schema = "id INTEGER PRIMARY KEY, email TEXT NOT NULL COLLATE NOCASE UNIQUE, name TEXT, created_at TEXT NOT NULL"
        mutants = {
            "pragma inside the transaction": solution.replace(
                '    conn.execute("PRAGMA foreign_keys = OFF")\n    conn.execute("BEGIN")\n',
                '    conn.execute("BEGIN")\n').replace(
                "WHERE name IS NULL OR name = \'\'\"\"\")\n",
                "WHERE name IS NULL OR name = \'\'\"\"\")\n        conn.execute(\"PRAGMA foreign_keys = OFF\")\n"),
            "old table renamed first": between(rebuild, view, (
                '        conn.execute("ALTER TABLE customers RENAME TO old_customers")\n'
                f'        conn.execute("CREATE TABLE customers ({schema})")\n'
                f'        conn.execute("{copy.format("customers", "old_customers")}")\n'
                '        conn.execute("DROP TABLE old_customers")\n')),
            "newest kept": solution.replace("ORDER BY k.created_at, k.id", "ORDER BY k.created_at DESC, k.id"),
            "python lower": solution.replace(
                '    conn.execute("BEGIN")\n',
                '    conn.execute("BEGIN")\n    for key, email in conn.execute("SELECT id, email FROM customers").fetchall():\n'
                '        conn.execute("UPDATE customers SET email = ? WHERE id = ?", (email.strip().lower(), key))\n'),
            "view not recreated": between(view, '        if conn.execute("PRAGMA foreign_key_check")'),
            "foreign keys left off": solution.replace(
                '    finally:\n        conn.execute("PRAGMA foreign_keys = ON")\n', ""),
            "unique index": between(rebuild, view, (
                '        conn.execute("UPDATE customers SET email = lower(trim(email))")\n'
                '        conn.execute("CREATE UNIQUE INDEX customers_email ON customers(email COLLATE NOCASE)")\n')),
            "names not filled": between(fill, '        conn.execute("UPDATE orders'),
            "tie goes to the highest id": solution.replace("ORDER BY k.created_at, k.id", "ORDER BY k.created_at, k.id DESC"),
            "latest donor names the kept customer": solution.replace(
                "ORDER BY d.created_at, d.id LIMIT 1), name)", "ORDER BY d.created_at DESC, d.id LIMIT 1), name)"),
            "backup table left behind": solution.replace(
                '        conn.execute("DROP TABLE customers")\n',
                '        conn.execute("CREATE TABLE customers_backup AS SELECT * FROM customers")\n'
                '        conn.execute("DROP TABLE customers")\n'),
            "view turned into a table": between(view, '        if conn.execute("PRAGMA foreign_key_check")', (
                '        conn.execute("""\n            CREATE TABLE customer_totals AS\n'
                '            SELECT c.id, c.email, COUNT(o.id) AS orders, COALESCE(SUM(o.total_cents), 0) AS cents\n'
                '            FROM customers c LEFT JOIN orders o ON o.customer_id = c.id\n'
                '            GROUP BY c.id""")\n')),
            "created_at no longer required": solution.replace(
                "created_at TEXT NOT NULL\n            )", "created_at TEXT\n            )"),
            "primary key lost": solution.replace("id INTEGER PRIMARY KEY,\n                email", "id INTEGER,\n                email"),
            "view left in place": solution.replace('        conn.execute("DROP VIEW customer_totals")\n', ""),
            "not committed": solution.replace('        conn.execute("COMMIT")\n', ""),
        }
        for name, text in mutants.items():
            with self.subTest(name):
                self.assertNotEqual(text, solution)
                self.assertFalse(self.run_with("impl-merge-customers", {rel: text}))
        noise = {
            "writes to stdout": solution.replace("def upgrade(conn):\n", 'def upgrade(conn):\n    import sys\n'
                                                 '    sys.stdout.write("done")\n'),
            "swaps stdout": solution.replace("def upgrade(conn):\n", 'def upgrade(conn):\n    import os, sys\n'
                                             '    sys.stdout = open(os.devnull, "w")\n'),
            "prints at exit": solution.replace("import sqlite3\n", 'import atexit\nimport sqlite3\n\n'
                                               'atexit.register(lambda: print(\'{"error": "late"}\'))\n', 1),
        }
        for name, text in noise.items():
            with self.subTest(name):
                self.assertNotEqual(text, solution)
                self.assertTrue(self.run_with("impl-merge-customers", {rel: text}))
        legacy_rename = between(rebuild, view, (
            '        conn.execute("PRAGMA legacy_alter_table = ON")\n'
            '        conn.execute("ALTER TABLE customers RENAME TO old_customers")\n'
            f'        conn.execute("CREATE TABLE customers ({schema})")\n'
            f'        conn.execute("{copy.format("customers", "old_customers")}")\n'
            '        conn.execute("DROP TABLE old_customers")\n'
            '        conn.execute("PRAGMA legacy_alter_table = OFF")\n'))
        self.assertTrue(self.run_with("impl-merge-customers", {rel: legacy_rename}))


    def test_statement_check_rejects_each_known_mistake(self):
        task = "bugfix-statement-queries"
        rel = "billing/statement.py"
        solution = self.source(task, "solution/billing/statement.py")
        shipped = self.source(task, "repo/billing/statement.py")
        self.assertTrue(self.run_with(task, {rel: solution}))
        header = solution[:solution.index("    result = {")]
        mutants = {
            "shipped": shipped,
            "join fan-out": header + (
                '    sql = ("SELECT a.id, COALESCE(SUM(i.amount_cents), 0) - COALESCE(SUM(p.amount_cents), 0) FROM accounts a "\n'
                '           "LEFT JOIN invoices i ON i.account_id = a.id AND i.voided = 0 "\n'
                '           "LEFT JOIN payments p ON p.invoice_id = i.id GROUP BY a.id")\n'
                '    return {a: c for a, c in conn.execute(sql)}\n'),
            "rule dropped": solution.replace("if rules.counts(status, method):", "if True:"),
            "pending bank counted": solution.replace("if rules.counts(status, method):", 'if status != "failed":'),
            "voided kept": solution.replace(" WHERE voided = 0", "").replace(" WHERE i.voided = 0", ""),
            "voided invoices' payments kept": solution.replace(" WHERE i.voided = 0", ""),
            "payments per invoice": header + (
                '    result = {a: 0 for (a,) in conn.execute("SELECT id FROM accounts")}\n'
                '    for invoice_id, account_id, cents in conn.execute("SELECT id, account_id, amount_cents FROM invoices WHERE voided = 0").fetchall():\n'
                '        result[account_id] += cents\n'
                '        for paid, status, method in conn.execute("SELECT amount_cents, status, method FROM payments WHERE invoice_id = ?", (invoice_id,)).fetchall():\n'
                '            if rules.counts(status, method):\n'
                '                result[account_id] -= paid\n'
                '    return result\n'),
            "trace removed": shipped.replace("    result = {}", "    conn.set_trace_callback(None)\n    result = {}"),
            "trace and progress removed": shipped.replace(
                "    result = {}", "    conn.set_trace_callback(None)\n    conn.set_progress_handler(None, 0)\n    result = {}"),
            "progress handler removed": solution.replace("    result = {", "    conn.set_progress_handler(None, 0)\n    result = {", 1),
            "line tracing removed": solution.replace("    result = {", "    import sys\n    sys.settrace(None)\n    result = {", 1),
            "index added": solution.replace(
                "    result = {", '    conn.execute("CREATE INDEX payments_by_status ON payments(status)")\n    result = {', 1),
            "temp table left": header + (
                '    conn.execute("CREATE TEMP TABLE owed AS SELECT a.id AS id, COALESCE((SELECT SUM(amount_cents) FROM invoices '
                'WHERE account_id = a.id AND voided = 0), 0) AS cents FROM accounts a")\n'
                '    result = {a: c for a, c in conn.execute("SELECT id, cents FROM owed")}\n'
                '    for account_id, paid, status, method in conn.execute("SELECT i.account_id, p.amount_cents, p.status, p.method FROM payments p '
                'JOIN invoices i ON i.id = p.invoice_id WHERE i.voided = 0"):\n'
                '        if rules.counts(status, method):\n'
                '            result[account_id] -= paid\n'
                '    return result\n'),
            "string keys": solution.replace("    return result", "    return {str(k): v for k, v in result.items()}"),
            "bool values": solution.replace("    return result", "    return {k: (True if v == 0 else v) for k, v in result.items()}"),
            "float values": solution.replace("    return result", "    return {k: float(v) for k, v in result.items()}"),
            "accounts without invoices left out": solution.replace("    return result", "    return {k: v for k, v in result.items() if v}"),
            "not a dict": solution.replace("    return result", "    return list(result.items())"),
            "raises": solution.replace("    return result", "    raise RuntimeError('no')"),
        }
        for name, text in mutants.items():
            with self.subTest(name):
                self.assertNotEqual(text, solution if name != "shipped" else "")
                self.assertFalse(self.run_with(task, {rel: text}))
        noise = {
            "writes to stdout": solution.replace("    result = {", '    print("done")\n    result = {', 1),
            "prints at exit": "import atexit\n" + solution.replace(
                "from billing import rules\n", "from billing import rules\n\natexit.register(lambda: print('[]'))\n", 1),
        }
        for name, text in noise.items():
            with self.subTest(name):
                self.assertNotEqual(text, solution)
                self.assertTrue(self.run_with(task, {rel: text}))

    def test_statement_check_rejects_quadratic_python_and_takes_a_linear_rewrite(self):
        task = "bugfix-statement-queries"
        rel = "billing/statement.py"
        solution = self.source(task, "solution/billing/statement.py")
        header = solution[:solution.index("    result = {")]
        quadratic = header + (
            '    result = {a: 0 for (a,) in conn.execute("SELECT id FROM accounts")}\n'
            '    invoices = conn.execute("SELECT id, account_id, amount_cents FROM invoices WHERE voided = 0").fetchall()\n'
            '    payments = conn.execute("SELECT invoice_id, amount_cents, status, method FROM payments").fetchall()\n'
            '    for invoice_id, account_id, cents in invoices:\n'
            '        result[account_id] += cents - sum(p[1] for p in payments if p[0] == invoice_id and rules.counts(p[2], p[3]))\n'
            '    return result\n')
        self.assertFalse(self.run_with(task, {rel: quadratic}))
        one_statement = header + (
            '    sql = ("SELECT a.id, COALESCE(i.cents, 0) - COALESCE(p.cents, 0) FROM accounts a "\n'
            '           "LEFT JOIN (SELECT account_id, SUM(amount_cents) AS cents FROM invoices WHERE voided = 0 GROUP BY account_id) i ON i.account_id = a.id "\n'
            '           "LEFT JOIN (SELECT i.account_id AS account_id, SUM(p.amount_cents) AS cents FROM payments p JOIN invoices i ON i.id = p.invoice_id "\n'
            '           "WHERE i.voided = 0 AND (p.status = \'settled\' OR (p.status = \'pending\' AND p.method = \'card\')) GROUP BY i.account_id) p "\n'
            '           "ON p.account_id = a.id")\n'
            '    return dict(conn.execute(sql).fetchall())\n')
        self.assertTrue(self.run_with(task, {rel: one_statement}))
        by_invoice = header + (
            '    import collections\n'
            '    paid = collections.defaultdict(int)\n'
            '    for invoice_id, cents, status, method in conn.execute("SELECT invoice_id, amount_cents, status, method FROM payments"):\n'
            '        if rules.counts(status, method):\n'
            '            paid[invoice_id] += cents\n'
            '    result = collections.defaultdict(int, {a: 0 for (a,) in conn.execute("SELECT id FROM accounts")})\n'
            '    for invoice_id, account_id, cents in conn.execute("SELECT id, account_id, amount_cents FROM invoices WHERE voided = 0"):\n'
            '        result[account_id] += cents - paid[invoice_id]\n'
            '    return result\n')
        self.assertTrue(self.run_with(task, {rel: by_invoice}))

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
        # name: (files, a phase the check must call wrong)
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
                "        store.put(wallet, want)\n        if balance(store, wallet) == want:\n            return\n\n\n"))}, "A"),
            "no funds check": ({rel: solution.replace("        if available < cents:\n            raise InsufficientFunds(src)\n", "")}, "D"),
            "compare and put that never takes an absent wallet": ({rel: livelock}, "A"),
        }
        for name, (files, phase) in mutants.items():
            with self.subTest(name):
                self.assertNotEqual(files[rel], solution if name != "shipped" else "")
                got, wrong = self.wallet_phases(files)
                self.assertIsNotNone(got, "the probe must answer inside its own timeout")
                self.assertIn(phase, wrong, wrong)
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

    def test_a_locked_wallet_solution_does_not_wait_out_the_barriers(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, work = self.shipped("rootcause-wallet-race", tmp)
            shutil.copytree(task["dir"] / "solution", work, dirs_exist_ok=True)
            (Path(tmp) / "result.txt").write_text("", encoding="utf-8")
            start = time.monotonic()
            verdict = run.grade(task["dir"] / "check.py", work, Path(tmp) / "result.txt")
            took = time.monotonic() - start
        self.assertTrue(verdict)
        self.assertLess(took, 15)  # five 5 s barriers would take 25 s

    def test_the_wallet_check_ends_inside_the_grade_timeout_on_the_shipped_deadlock(self):
        task_id = "rootcause-wallet-race"
        with tempfile.TemporaryDirectory() as tmp:
            task, work = self.shipped(task_id, tmp)
            (Path(tmp) / "result.txt").write_text("", encoding="utf-8")
            start = time.monotonic()
            verdict = run.grade(task["dir"] / "check.py", work, Path(tmp) / "result.txt")
            took = time.monotonic() - start
        self.assertFalse(verdict)
        self.assertLess(took, 40)

if __name__ == "__main__":
    unittest.main()
