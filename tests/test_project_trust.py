"""What a project's files and the hook's cwd can and can't change."""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_model_router import BUILD, HARD, Sandbox
from modelrouter import agents, cli, request, store
from modelrouter import config as cfg
from modelrouter import mod as modpath
from modelrouter.config import Level


class ProjectFromASubfolder(Sandbox):
    def setUp(self):
        super().setUp()
        (self.project / ".git").mkdir()
        self.sub = self.project / "src" / "pkg"
        self.sub.mkdir(parents=True)
        self.override({"levels": {"complex": {"effort": "max"}}})
        self.agent("pinned-helper", "haiku")

    def test_a_subfolder_cwd_still_reads_the_roots_file_and_agents(self):
        self.assertEqual(cfg.load({"cwd": str(self.sub)}).levels[Level.complex].effort, "max")
        self.assertEqual(agents.find("pinned-helper", self.sub)["model"], "haiku")
        del os.environ["CLAUDE_PROJECT_DIR"]  # a skill command run through Bash
        with mock.patch.object(os, "getcwd", return_value=str(self.sub)):
            self.assertEqual(cfg.load().levels[Level.complex].effort, "max")
        self.assertEqual(agents.find("pinned-helper", self.sub)["model"], "haiku")

    def test_a_session_started_in_a_monorepo_package_finds_the_roots_agents(self):
        package = self.project / "packages" / "api"
        package.mkdir(parents=True)
        os.environ["CLAUDE_PROJECT_DIR"] = str(package)
        self.assertEqual(agents.find("pinned-helper", package)["model"], "haiku")
        self.assertEqual(cfg.load({"cwd": str(package)}).levels[Level.complex].effort, "max")
        self.assertEqual(cfg.load().levels[Level.complex].effort, "max")

    def test_the_walk_stops_at_the_repository_root(self):
        outer = self.project.parent / ".claude" / "agents"
        self.agent("outsider", "opus", folder=outer)
        self.assertIsNone(agents.find("outsider", self.sub))

    def test_the_walk_stops_where_the_root_or_its_git_or_claude_folder_is_not_mine(self):
        del os.environ["CLAUDE_PROJECT_DIR"]
        with mock.patch.object(cfg, "_mine", return_value=True):
            self.assertEqual(agents.find("pinned-helper", self.sub)["model"], "haiku")
        for planted in (self.project, self.project / ".git", self.project / ".claude"):
            with self.subTest(planted=planted.name), mock.patch.object(cfg, "_mine", lambda p: p != planted):
                self.assertIsNone(agents.find("pinned-helper", self.sub))
                self.assertNotEqual(cfg.load({"cwd": str(self.sub)}).levels[Level.complex].effort, "max")

    @unittest.skipUnless(hasattr(os, "getuid"), "st_uid is a real owner on POSIX only")
    def test_the_walk_stops_where_the_roots_git_or_claude_folder_is_another_users(self):
        del os.environ["CLAUDE_PROJECT_DIR"]
        real = os.stat
        for planted in (self.project / ".git", self.project / ".claude"):
            def stat(path, *args, **kwargs):
                found = real(path, *args, **kwargs)
                if Path(path) != planted:
                    return found
                return os.stat_result((*found[:4], found.st_uid + 1, *found[5:]))
            with self.subTest(planted=planted.name), mock.patch.object(os, "stat", stat):
                self.assertIsNone(agents.find("pinned-helper", self.sub))

    def test_after_cd_into_another_repository_the_sessions_agent_still_wins(self):
        other = Path(self.tmp.name) / "other"
        (other / ".git").mkdir(parents=True)
        self.agent("pinned-helper", "opus", folder=other / ".claude" / "agents")
        self.assertEqual(agents.find("pinned-helper", other)["model"], "haiku")
        self.agent("pinned-helper", "sonnet", folder=self.sub / ".claude" / "agents")
        self.assertEqual(agents.find("pinned-helper", self.sub)["model"], "sonnet")


class Owner(Sandbox):
    def test_a_folder_and_file_this_process_made_are_mine(self):
        made = self.project / "made"
        made.mkdir()
        (made / "file").write_text("x")
        self.assertIs(cfg._mine(made), True)
        self.assertIs(cfg._mine(made / "file"), True)

    def test_a_path_that_does_not_exist_is_not_mine(self):
        self.assertIs(cfg._mine(self.project / "gone"), False)

    @unittest.skipUnless(os.name == "nt", "the Windows folder exists on Windows only")
    def test_the_windows_folder_is_not_mine(self):
        self.assertIs(cfg._mine(Path(os.environ["SystemRoot"])), False)


class ProjectEffortOverAUserNull(Sandbox):
    def test_a_project_cannot_set_effort_below_claude_codes_default_on_a_level_the_user_left_open(self):
        self.override({"levels": {"complex": {"effort": None}}}, user=True)
        self.override({"levels": {"complex": {"effort": "low"}}})
        reg = cfg.load()
        self.assertIsNone(reg.levels[Level.complex].effort)
        self.assertEqual([(h.kind, h.name) for h in reg.held], [("level", "complex")])
        self.override({"levels": {"complex": {"effort": None}}})
        self.assertEqual(cfg.load().held, ())


class UsersRuleOverAProjectFloor(Sandbox):
    def test_a_project_level_does_not_override_the_users_own_rule(self):
        self.agent("worker")
        self.override({"agents": {"worker": {"level": "lookup"}}}, user=True)
        self.override({"agents": {"worker": {"level": "frontier"}}})
        d = self.decide("worker", HARD)  # the user's lookup floor leaves the pick at standard
        self.assertEqual((d["level"], d["model"]), ("standard", "haiku"))
        self.assertIn(("rule", "worker"), [(h.kind, h.name) for h in cfg.load().held])
        self.override({"agents": {"worker": {"model": "haiku", "level": "lookup"}}}, user=True)
        self.override({"agents": {"worker": {"level": "frontier", "model": "opus"}}})
        d = self.decide("worker", HARD)
        self.assertEqual((d["level"], d["model"]), ("lookup", "haiku"))
        self.assertEqual(len(cfg.load().held), 2)  # the project's level and model, each said once

    def test_a_users_level_or_model_for_an_agent_holds_both_of_the_projects(self):
        self.agent("worker")
        self.override({"agents": {"worker": {"level": "lookup"}}}, user=True)
        self.override({"agents": {"worker": {"model": "opus"}}})
        d = self.decide("worker", HARD)
        self.assertEqual((d["level"], d["model"]), ("standard", "haiku"))
        self.assertEqual([(h.kind, h.name) for h in cfg.load().held], [("rule", "worker")])
        self.override({"agents": {"worker": {"model": "haiku"}}}, user=True)
        self.override({"agents": {"worker": {"level": "frontier"}}})
        self.assertEqual(self.decide("worker", HARD)["model"], "haiku")
        self.assertEqual([(h.kind, h.name) for h in cfg.load().held], [("rule", "worker")])


class ProjectModelNames(Sandbox):
    def test_a_model_only_the_project_adds_is_not_read_out_of_the_prompt(self):
        self.override({"models": {"this": {"strength": 1}, "best": {"strength": 9}}})
        reg = cfg.load()
        self.assertIsNone(request.named_model(reg, "fix the login bug with this"))
        self.assertIsNone(request.named_model(reg, "use the best approach to fix the login bug"))
        self.override({"models": {"this": {"strength": 1}}}, user=True)
        self.assertEqual(request.named_model(cfg.load(), "fix the login bug with this"), "this")


class ModSpawnElsewhere(Sandbox):
    def test_a_subagent_running_in_another_folder_still_finds_the_project_agent(self):
        self.agent("scout", "opus")
        other = Path(self.tmp.name) / "elsewhere"
        other.mkdir()
        out = modpath.spawn({"session_id": "s1", "cwd": str(other), "parent_model": "claude-opus-5-5",
                             "kind": "subagent", "tool_input": {"subagent_type": "scout", "prompt": "list files"}})
        self.assertEqual(out["model"], "opus")


def run_hook(name: str, hook_input: dict) -> str:
    """A hook as the launcher runs it: JSON on stdin, and cli.main catching what a handler raises."""
    stdin = io.TextIOWrapper(io.BytesIO(json.dumps(hook_input).encode()), encoding="utf-8")
    with mock.patch.object(sys, "stdin", stdin), redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
        cli.main([name])
    return out.getvalue()


class UnreadableProjectFiles(Sandbox):
    """A project file that can't be read with confidence says nothing: routing, the cap and the session note go on."""

    def setUp(self):
        super().setUp()
        (self.project / ".claude").mkdir()
        self.rules = self.project / ".claude" / "model-router.json"

    def test_a_file_nested_past_the_parsers_limit_says_nothing(self):
        self.options(max_tier="sonnet")
        call = self.call("general-purpose", BUILD, model="opus")
        for junk in ("[" * 100000, "{\"a\":" * 100000):
            with self.subTest(junk=junk[:5]):
                self.rules.write_text(junk)
                self.assertIn('"model": "sonnet"', run_hook("pre-agent", call))
                note = json.loads(run_hook("session-start", {"session_id": "s1", "cwd": str(self.project)}))
                self.assertIn("Here every Agent call names both", note["hookSpecificOutput"]["additionalContext"])

    def test_a_file_over_the_size_limit_or_of_endless_digits_says_nothing_and_reads_at_once(self):
        raised = json.dumps({"levels": {"complex": {"effort": "max"}}})
        with mock.patch.object(store, "FILE_MAX_BYTES", len(raised)):
            self.rules.write_text(raised)
            self.assertEqual(cfg.load().levels[Level.complex].effort, "max")
            self.rules.write_text(raised + " ")
            self.assertNotEqual(cfg.load().levels[Level.complex].effort, "max")
        self.rules.write_text('{"levels": {"complex": {"needs": ' + "9" * 1000000 + '}}}')
        started = time.monotonic()
        reg = cfg.load()
        self.assertLess(time.monotonic() - started, 1)  # a million digits as an int would take seconds
        self.assertEqual(reg.levels, cfg.shipped().levels)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs FIFOs")
    def test_a_file_that_is_a_fifo_or_a_device_says_nothing_and_never_blocks(self):
        fifo = Path(self.tmp.name) / "fifo"
        os.mkfifo(fifo)
        for target in (fifo, Path("/dev/zero")):
            with self.subTest(target=target):
                self.rules.unlink(missing_ok=True)
                os.symlink(target, self.rules)
                done = threading.Event()
                threading.Thread(target=lambda: (cfg.load(), done.set()), daemon=True).start()
                self.assertTrue(done.wait(5), "the read blocked")
                self.assertEqual(cfg.load().levels, cfg.shipped().levels)

    def test_a_file_in_a_folder_this_user_cant_enter_says_nothing(self):
        real = Path.is_file

        def is_file(path):
            if path == self.rules:
                raise PermissionError(13, "Permission denied")
            return real(path)
        with mock.patch.object(Path, "is_file", is_file):
            self.assertEqual(cfg.load().levels, cfg.shipped().levels)


class VersionAfterTheCap(Sandbox):
    def test_a_capped_call_naming_a_full_id_gets_the_capped_familys_newest_id(self):
        self.options(max_tier="sonnet", CLAUDE_CODE_USE_BEDROCK="1")
        tool_input = {"subagent_type": "general-purpose", "prompt": HARD, "model": "claude-opus-5-5"}
        out = modpath.spawn({"session_id": "s1", "parent_model": "claude-opus-5-5", "kind": "subagent",
                             "tool_input": tool_input})
        self.assertEqual(out["model"], "claude-sonnet-5-5")
        self.assertIsNone(self.decide("general-purpose", HARD, model="claude-opus-5-5")["version"])


if __name__ == "__main__":
    unittest.main()
