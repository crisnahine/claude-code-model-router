"""Tests for the cost and quality benchmark under bench/. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import json
import os
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


@unittest.skipIf(sys.platform == "win32", "the stub claude is a shebang script")
class PluginState(unittest.TestCase):
    """Router runs keep their state in a plugins root of their own, never the one other sessions share."""

    def test_router_runs_start_fresh_and_never_touch_the_shared_data_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "config"
            shared = home / "plugins" / "data" / "model-router-inline"
            shared.mkdir(parents=True)
            (shared / "learned.json").write_text('{"lookup": {"calls": 2}}')
            os.utime(shared / "learned.json", (1000, 1000))
            stub = Path(tmp) / "claude"
            stub.write_text(STUB)
            stub.chmod(0o755)
            task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
            configs = {c["id"]: c for c in run.load_configs()}
            options = {"claude": str(stub), "model": "sonnet", "budget": 0.5, "timeout": 60,
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
        options = {"claude": "claude", "model": "sonnet", "budget": 0.5, "plugin_dir": "/p", "plugins_root": "/s/plugins"}
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


@unittest.skipIf(sys.platform == "win32", "the stub claude is a shebang script")
class Sessions(unittest.TestCase):
    """Runs keep their transcripts, as real sessions do, and the bench removes only its own afterwards."""

    def test_runs_save_their_sessions(self):
        cmd, _ = run.command("do it", run.load_configs()[0], {"claude": "claude", "model": "sonnet", "budget": 0.5,
                                                             "plugin_dir": "/p"}, {})
        self.assertNotIn("--no-session-persistence", cmd)

    def test_a_folder_is_named_as_claude_code_names_it(self):
        self.assertEqual(run.session_dir(Path("/private/var/folders/ab/x_y.z/T/mrbench-a-b/work"), {"CLAUDE_CONFIG_DIR": "/c"}),
                         Path("/c/projects/-private-var-folders-ab-x-y-z-T-mrbench-a-b-work"))

    def test_only_the_runs_own_transcripts_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "config"
            other = home / "projects" / "-Users-someone-project"
            other.mkdir(parents=True)
            (other / "keep.jsonl").write_text("{}")
            stub = Path(tmp) / "claude"
            stub.write_text(SESSION_STUB)
            stub.chmod(0o755)
            task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
            options = {"claude": str(stub), "model": "sonnet", "budget": 0.5, "timeout": 60,
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(home)}):
                row = run.run_one(task, run.load_configs()[1], options, {})
            self.assertEqual([p.name for p in (home / "projects").iterdir()], ["-Users-someone-project"])
            self.assertTrue((other / "keep.jsonl").is_file())
            self.assertEqual((row["sessions_saved"], row["session_dir_removed"]), (True, True))


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
        options = {"model": "sonnet", "budget": 0.5, "claude": "claude", "plugin_dir": "/p/model-router"}
        cmd, env = run.command("x", configs["opusplan"], options, {})
        self.assertEqual(cmd[cmd.index("--model") + 1], "opusplan")
        cmd, env = run.command("x", configs["advisor"], options, {})
        self.assertEqual((cmd[cmd.index("--model") + 1], cmd[cmd.index("--advisor") + 1]), ("sonnet", "opus"))
        for name, effort in (("sonnet-medium", "medium"), ("sonnet-high", "high")):
            cmd, env = run.command("x", configs[name], options, {})
            self.assertEqual((env["CLAUDE_CODE_SUBAGENT_MODEL"], env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"],
                              env["CLAUDE_CODE_SUBAGENT_EFFORT"]), ("sonnet", "1", effort))
            self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", env)  # that one would move the main loop too
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


@unittest.skipIf(sys.platform == "win32", "the stub claude is a shebang script")
class RouterVariants(unittest.TestCase):
    """A router variant is the shipped plugin plus a project model-router.json and plugin option values."""

    def test_a_variant_gets_its_router_file_and_options_and_nothing_else_does(self):
        variant = {"id": "v", "plugin": True, "env": {}, "router_config": {"levels": {"standard": {"effort": "medium"}}},
                   "plugin_options": {"main_effort": False}}
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "claude"
            stub.write_text(VARIANT_STUB)
            stub.chmod(0o755)
            seen = Path(tmp) / "seen.json"
            task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
            options = {"claude": str(stub), "model": "sonnet", "budget": 0.5, "timeout": 60,
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            with unittest.mock.patch.dict("os.environ", {"STUB_SEEN": str(seen)}):
                row = run.run_one(task, variant, options, {})
                got = json.loads(seen.read_text())
                run.run_one(task, run.load_configs()[0], options, {})
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
rows = [{"event": "spawn", "agent": "model-router:implementer"},
        {"event": "usage", "agent_id": "a1", "cost_usd": 0.05}, {"event": "usage", "agent_id": "a2", "cost_usd": 0.01},
        {"event": "usage", "agent_id": None, "cost_usd": 0.03}, {"event": "usage", "agent_id": "a1", "cost_usd": 0.02}]
(data / "ledger.jsonl").write_text("\\n".join(json.dumps(r) for r in rows))
folder = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd().resolve()))
sub = folder / "s1" / "subagents"
sub.mkdir(parents=True)
(sub / "agent-a1.meta.json").write_text(json.dumps({"agentType": "model-router:implementer"}))
(sub / "agent-a2.meta.json").write_text(json.dumps({"agentType": "model-router:verifier"}))
print(json.dumps({"result": "read_retry_limit in app/settings.py", "total_cost_usd": 0.11, "modelUsage": {}}))
'''


class PluginRef(unittest.TestCase):
    def test_a_configuration_can_run_the_plugin_from_a_commit(self):
        options = {"model": "sonnet", "budget": 0.5, "claude": "claude", "plugin_dir": "/p/model-router"}
        cmd, _ = run.command("x", {"id": "r", "plugin": True, "env": {}, "plugin_ref": "HEAD"}, options, {})
        exported = Path(cmd[cmd.index("--plugin-dir") + 1])
        self.assertNotEqual(exported, Path("/p/model-router"))
        self.assertTrue((exported / ".claude-plugin" / "plugin.json").is_file())
        again, _ = run.command("x", {"id": "r", "plugin": True, "env": {}, "plugin_ref": "HEAD"}, options, {})
        self.assertEqual(again[again.index("--plugin-dir") + 1], str(exported))  # exported once
        plain, _ = run.command("x", run.load_configs()[0], options, {})
        self.assertEqual(plain[plain.index("--plugin-dir") + 1], "/p/model-router")


@unittest.skipIf(sys.platform == "win32", "the stub claude is a shebang script")
class AgentCosts(unittest.TestCase):
    def test_a_router_run_records_its_spend_by_agent_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "claude"
            stub.write_text(COST_STUB)
            stub.chmod(0o755)
            task = next(t for t in run.load_tasks() if t["id"] == "lookup-retry-limit")
            options = {"claude": str(stub), "model": "sonnet", "budget": 0.5, "timeout": 60,
                       "plugin_dir": str(run.PLUGIN), "claude_version": "9.9.9 (stub)", "commit": None}
            with unittest.mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(Path(tmp) / "config")}):
                row = run.run_one(task, run.load_configs()[0], options, {})
        self.assertEqual(row["agent_costs"], {"model-router:implementer": 0.07, "model-router:verifier": 0.01,
                                              "main": 0.03})


@unittest.skipIf(sys.platform == "win32", "the stub claude is a shebang script")
class Limits(unittest.TestCase):
    """A rate or usage limit pauses the bench and retries the run; it is never recorded as a failed task."""

    def bench(self, limited: int) -> tuple[int, list[dict], list[dict], list[float]]:
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "claude"
            stub.write_text(LIMIT_STUB)
            stub.chmod(0o755)
            out = Path(tmp) / "out.jsonl"
            env = {"STUB_CALLS": str(Path(tmp) / "calls"), "STUB_LIMITED": str(limited)}
            pauses: list[float] = []
            with unittest.mock.patch.dict("os.environ", env), \
                    unittest.mock.patch.object(run.time, "sleep", pauses.append):
                code = run.main(["--tasks", "lookup-retry-limit", "--configs", "defaults", "--runs", "1",
                                 "--max-total", "3", "--claude", str(stub), "--out", str(out)])
            read = lambda p: [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []  # noqa: E731
            self.assertEqual(sorted(p.name for p in Path(tmp).glob("*.jsonl")), ["out.jsonl"] if out.exists() else [])
            return code, read(out), read(Path(tmp) / "limited" / "out.jsonl"), pauses

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

    def test_an_answer_about_rate_limits_is_not_a_limit(self):
        self.assertFalse(run.rate_limited({"is_error": False, "api_error_status": None, "stderr": "",
                                           "answer": "Use a token bucket rate limiter; on the usage limit, return 429."}))


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
        stale = sum("plugin_state" not in r for r in data)
        if stale:
            self.assertIn(f"{stale} run(s) here predate", page)
        self.assertIn("`Bash(tail *)`", page)
        self.assertNotIn("`--no-session-persistence`, `--permission-mode", page)
        self.assertNotIn("puts it back afterwards", page)
        borrowed = sum(r.get("plugin_state") is not None and "plugin_data_own" not in r for r in data)
        if borrowed:
            self.assertIn(f"{borrowed} router run(s) here borrowed the shared folder", page)
        unsaved = sum("sessions_saved" not in r for r in data)
        if unsaved:
            self.assertIn(f"{unsaved} run(s) here ran with `--no-session-persistence`", page)
        for row in data:  # a lookup is graded from its answer alone, so today's check must agree with the record
            if row["class"] == "lookup" and len(row["answer"]) < 2000:  # the line keeps 2,000 characters
                with tempfile.TemporaryDirectory() as tmp:
                    answer = Path(tmp) / "answer.txt"
                    answer.write_text(row["answer"], encoding="utf-8")
                    check = ROOT / "bench" / "tasks" / row["task"] / "check.py"
                    res = subprocess.run([sys.executable, str(check), tmp, str(answer)], capture_output=True)
                    self.assertEqual(res.returncode == 0, row["passed"], row["config"])


if __name__ == "__main__":
    unittest.main()
