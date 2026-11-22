"""Tests for the cost and quality benchmark under bench/. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import analyze  # noqa: E402
import run  # noqa: E402


def rows(config, cls, costs, passes):
    return [{"config": config, "class": cls, "task": f"{cls}-1", "total_cost_usd": c, "passed": p}
            for c, p in zip(costs, passes)]


class Analysis(unittest.TestCase):
    def summary(self, data):
        return {s["class"]: s for s in analyze.summarize(data, router="plugin", draws=500, seed=1)}

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
        first = analyze.summarize(data, router="plugin", draws=300, seed=7)
        self.assertEqual(first, analyze.summarize(data, router="plugin", draws=300, seed=7))

    def test_the_table_has_a_row_per_class_and_a_column_per_configuration(self):
        data = (rows("plugin", "lookup", [0.1], [True]) + rows("defaults", "lookup", [0.2], [True])
                + rows("plugin", "bugfix", [0.3], [False]) + rows("defaults", "bugfix", [0.4], [True]))
        text = analyze.render(analyze.summarize(data, router="plugin", draws=100, seed=0), {"total_cost_usd": 1.0})
        header = next(line for line in text.splitlines() if line.startswith("| Task class"))
        self.assertIn("| plugin |", header)
        self.assertIn("| defaults |", header)
        class_rows = [line for line in text.splitlines() if line.startswith(("| lookup ", "| bugfix "))]
        self.assertEqual(len(class_rows), 2)
        for line in class_rows:
            self.assertTrue(line.endswith("| PARTIAL |"), line)


class Runner(unittest.TestCase):
    def test_each_run_starts_clean_and_only_the_configuration_differs(self):
        base = {"PATH": "/bin", "CLAUDE_CODE_SUBAGENT_MODEL": "opus", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1",
                "CLAUDE_CODE_EFFORT_LEVEL": "max", "CLAUDE_CODE_SUBAGENT_EFFORT": "low", "CLAUDECODE": "1",
                "CLAUDE_CODE_SESSION_ID": "parent", "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/s.sock",
                "CLAUDE_PLUGIN_ROOT": "/elsewhere", "CLAUDE_CODE_USE_BEDROCK": "1"}
        configs = {c["id"]: c for c in run.load_configs()}
        options = {"model": "sonnet", "budget": 0.5, "claude": "claude", "plugin_dir": "/p/model-router"}
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
        options = {"model": "sonnet", "budget": 0.5, "claude": "claude", "plugin_dir": "/p/model-router"}
        cmd, env = run.command("do it", configs["plugin"], options, {"CLAUDE_CODE_OAUTH_TOKEN": "t"})
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "t")
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
        res = subprocess.run([sys.executable, str(task["dir"] / "check.py"), str(workdir), str(result)],
                             capture_output=True, text=True, timeout=60)
        return res.returncode == 0

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
            self.assertTrue(self.check(task, work, "read_retry_limit in app/settings.py; app/client.py calls it"))


STUB = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if "--version" in sys.argv:
    print("9.9.9 (stub)"); sys.exit(0)
if "--plugin-dir" in sys.argv:
    data = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "plugins" / "data" / "model-router-inline"
    data.mkdir(parents=True, exist_ok=True)
    (data / "learned.json").write_text('{"lookup": {"bump": 1}}')
    (data / "ledger.jsonl").write_text("written by the run")
print(json.dumps({"result": "read_retry_limit in app/settings.py", "total_cost_usd": 0.01, "modelUsage": {}}))
'''


@unittest.skipIf(sys.platform == "win32", "the stub claude is a shebang script")
class PluginState(unittest.TestCase):
    """A plugin run can't leave the router's learned state behind for the next run."""

    def test_plugin_runs_start_from_the_same_state_and_record_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "config"
            data = home / "plugins" / "data" / "model-router-inline"
            data.mkdir(parents=True)
            (data / "learned.json").write_text('{"lookup": {"calls": 2}}')
            stub = Path(tmp) / "claude"
            stub.write_text(STUB)
            stub.chmod(0o755)
            task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
            configs = {c["id"]: c for c in run.load_configs()}
            options = {"claude": str(stub), "model": "sonnet", "budget": 0.5, "timeout": 60,
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(home)}):
                first = run.run_one(task, configs["plugin"], options, {})
                second = run.run_one(task, configs["plugin"], options, {})
                baseline = run.run_one(task, configs["defaults"], options, {})
            self.assertEqual((data / "learned.json").read_text(), '{"lookup": {"calls": 2}}')
            self.assertFalse((data / "ledger.jsonl").exists())
            self.assertTrue(first["passed"])
            self.assertEqual(first["plugin_state"], second["plugin_state"])
            self.assertIsNotNone(first["plugin_state"]["learned.json"])
            self.assertIsNone(first["plugin_state"]["state.json"])
            self.assertIsNone(baseline["plugin_state"])


class Pilot(unittest.TestCase):
    def test_committed_results_match_the_published_table(self):
        raw = sorted((ROOT / "bench" / "results").glob("*.jsonl"))
        self.assertTrue(raw, "no raw results committed")
        data = [json.loads(line) for path in raw for line in path.read_text(encoding="utf-8").splitlines() if line]
        page = (ROOT / "docs" / "BENCHMARKS.md").read_text(encoding="utf-8")
        for path in raw:
            self.assertIn(f"bench/results/{path.name}", page)
        self.assertIn(f"${sum(r['total_cost_usd'] for r in data):.2f}", page)
        self.assertIn(f"{len(data)} runs", page)
        self.assertIn("plus anything that subagents still running", page)
        self.assertIn("margin, not a bound", page)
        if any(r["config"] == "plugin" and "plugin_state" not in r for r in data):
            self.assertIn("predate", page)
        self.assertIn("`Bash(tail *)`", page)
        for row in data:  # a lookup is graded from its answer alone, so today's check must agree with the record
            if row["class"] == "lookup":
                with tempfile.TemporaryDirectory() as tmp:
                    answer = Path(tmp) / "answer.txt"
                    answer.write_text(row["answer"], encoding="utf-8")
                    check = ROOT / "bench" / "tasks" / row["task"] / "check.py"
                    res = subprocess.run([sys.executable, str(check), tmp, str(answer)], capture_output=True)
                    self.assertEqual(res.returncode == 0, row["passed"], row["config"])


if __name__ == "__main__":
    unittest.main()
