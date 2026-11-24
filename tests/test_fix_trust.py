"""What a project's files and the hook's cwd can and can't change."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from test_model_router import HARD, Sandbox
from modelrouter import agents, allowlist, request
from modelrouter import config as cfg
from modelrouter import mod as modpath
from modelrouter.config import Level


class ProjectFromASubfolder(Sandbox):
    def setUp(self):
        super().setUp()
        (self.project / ".git").mkdir()
        self.sub = self.project / "src" / "pkg"
        self.sub.mkdir(parents=True)
        self.override({"routing_mode": "off"})
        self.agent("pinned-helper", "haiku")

    def test_a_subfolder_cwd_still_reads_the_roots_file_agents_and_settings(self):
        self.assertEqual(cfg.load({"cwd": str(self.sub)}).routing_mode, "off")
        self.assertEqual(agents.find("pinned-helper", self.sub)["model"], "haiku")
        (self.project / ".claude" / "settings.json").write_text(json.dumps({"availableModels": ["haiku"]}))
        self.assertEqual(allowlist.file_layers(self.sub)["project"], {"availableModels": ["haiku"]})
        del os.environ["CLAUDE_PROJECT_DIR"]  # a skill command run through Bash
        with mock.patch.object(os, "getcwd", return_value=str(self.sub)):
            self.assertEqual(cfg.load().routing_mode, "off")
        self.assertEqual(agents.find("pinned-helper", self.sub)["model"], "haiku")

    def test_a_session_started_in_a_monorepo_package_finds_the_roots_agents(self):
        package = self.project / "packages" / "api"
        package.mkdir(parents=True)
        os.environ["CLAUDE_PROJECT_DIR"] = str(package)
        self.assertEqual(agents.find("pinned-helper", package)["model"], "haiku")

    def test_the_walk_stops_at_the_repository_root(self):
        outer = self.project.parent / ".claude" / "agents"
        self.agent("outsider", "opus", folder=outer)
        self.assertIsNone(agents.find("outsider", self.sub))


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
        d = self.decide("worker", HARD)
        self.assertEqual((d["level"], d["model"]), ("lookup", "haiku"))
        self.assertIn(("rule", "worker"), [(h.kind, h.name) for h in cfg.load().held])
        self.override({"agents": {"worker": {"model": "haiku", "level": "lookup"}}}, user=True)
        self.override({"agents": {"worker": {"level": "frontier", "model": "opus"}}})
        d = self.decide("worker", HARD)
        self.assertEqual((d["level"], d["model"]), ("lookup", "haiku"))
        self.assertEqual(len(cfg.load().held), 2)  # the project's level and model, each said once


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
