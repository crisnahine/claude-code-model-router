"""Regression tests for the typed-skill intake, the refresh command and plugin agent lookup."""
from __future__ import annotations

import io
import json
import urllib.error
from contextlib import redirect_stdout
from unittest import mock

from test_model_router import PLUGIN, Sandbox
from modelrouter import agents, cli, intake, store, upstream
from modelrouter import mod as modpath


class BareTemplateSkill(Sandbox):
    LINE = "<command-message>{0}</command-message>\n<command-name>/{0}</command-name>\n<command-args>{1}</command-args>"

    def turn(self, name: str, args: str, body: str) -> dict | None:
        command = {"name": name, "args": args, "body": body}
        return modpath.turn({"session_id": "s1", "text": self.LINE.format(name, args), "command": command})

    def test_a_template_skill_typed_bare_caps_its_body_at_complex(self):
        # ship's own advice ("research-grade", "data schema", "security") is the same on every use.
        self.options(main_effort="true")
        self.session_on("claude-opus-5-5")
        self.assertEqual(intake.from_prompt("/model-router:ship", self.project).level, "complex")
        raw = (PLUGIN / "skills" / "ship" / "SKILL.md").read_text(encoding="utf-8")
        rendered = "Base directory for this skill: /p/skills/ship\n\n" + raw.split("---\n", 2)[2].replace("$ARGUMENTS", "")
        self.assertEqual(self.turn("model-router:ship", "", rendered)["level"], "complex")
        skill = self.project / ".claude" / "skills" / "build"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(raw, encoding="utf-8")
        self.assertEqual(intake.from_prompt("/build", self.project).level, "complex")
        self.assertEqual(self.turn("build", "", rendered)["level"], "complex")

    def test_a_template_whose_only_placeholder_is_a_later_argument_is_capped_when_typed_bare(self):
        self.options(main_effort="true")
        self.session_on("claude-opus-5-5")
        raw = (PLUGIN / "skills" / "ship" / "SKILL.md").read_text(encoding="utf-8")
        rendered = "Base directory for this skill: /p/skills/ship\n\n" + raw.split("---\n", 2)[2].replace("$ARGUMENTS", "")
        skill = self.project / ".claude" / "skills" / "build"
        skill.mkdir(parents=True)
        for placeholder in ("$2", "$ARGUMENTS[1]"):
            with self.subTest(placeholder=placeholder):
                (skill / "SKILL.md").write_text(raw.replace("$ARGUMENTS", placeholder), encoding="utf-8")
                self.assertEqual(intake.from_prompt("/build", self.project).level, "complex")
                self.assertEqual(self.turn("build", "", rendered)["level"], "complex")


class SkillFileLookup(Sandbox):
    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_a_personal_skill_beats_a_project_one_and_a_skill_beats_a_command(self):
        project, home = self.project / ".claude", self.home
        files = [(project / "commands" / "deploy.md", "project command"),
                 (home / "commands" / "deploy.md", "home command"),
                 (project / "skills" / "deploy" / "SKILL.md", "project skill"),
                 (home / "skills" / "deploy" / "SKILL.md", "home skill")]
        for path, text in files:
            self.write(path, text)
            self.assertEqual(intake._skill_file(None, "deploy", self.project), text)

    def test_a_plugin_command_file_is_found_after_its_skill(self):
        root = self.home / "plugins" / "cache" / "market" / "acme" / "1.0"
        (self.home / "plugins").mkdir(parents=True, exist_ok=True)
        (self.home / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {"acme@market": [{"installPath": str(root)}]}}))
        self.write(root / "commands" / "tpl.md", "Do $ARGUMENTS")
        self.assertEqual(intake._skill_file("acme", "tpl", self.project), "Do $ARGUMENTS")
        self.write(root / "skills" / "tpl" / "SKILL.md", "skill")
        self.assertEqual(intake._skill_file("acme", "tpl", self.project), "skill")


class RefreshOffline(Sandbox):
    def test_refresh_offline_exits_zero_so_the_skill_loads_and_reads_the_notice(self):
        store.save_json(upstream.path(), {"cache": {"fetched_at": 1, "releases": []}})
        with mock.patch.object(upstream, "http_get", side_effect=urllib.error.URLError("offline")), \
                redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(cli.main(["refresh", "--force"]), 0)
        self.assertTrue(buf.getvalue().startswith("Couldn't reach the Claude Code docs"))
        skill = (PLUGIN / "skills" / "refresh" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn('starts with "Couldn\'t reach the Claude Code docs"', skill)


class ManifestAgents(Sandbox):
    def install(self, manifest: dict | None, entry: dict | None = None):
        root = self.home / "plugins" / "cache" / "market" / "acme" / "1.0"
        root.mkdir(parents=True)
        if manifest is not None:
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest))
        market = self.home / "plugins" / "marketplaces" / "market"
        (market / ".claude-plugin").mkdir(parents=True)
        (market / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
            {"name": "market", "plugins": [{"name": "acme", "source": "./acme", **(entry or {})}]}))
        plugins = self.home / "plugins"
        (plugins / "known_marketplaces.json").write_text(json.dumps({"market": {"installLocation": str(market)}}))
        (plugins / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {"acme@market": [{"installPath": str(root)}]}}))
        return root

    def test_agents_the_manifest_lists_are_found_and_replace_the_agents_folder(self):
        root = self.install({"name": "acme", "agents": ["./custom/reviewer.md"]})
        self.agent("reviewer", "opus", root / "custom")
        self.agent("stale", "haiku", root / "agents")
        self.assertEqual(agents.find("acme:reviewer", self.project)["model"], "opus")
        self.assertIsNone(agents.find("acme:stale", self.project))

    def test_agents_a_marketplace_entry_lists_are_found(self):
        root = self.install({"name": "acme"}, {"agents": "./extra/tester.md"})
        self.agent("tester", "sonnet", root / "extra")
        self.assertEqual(agents.find("acme:tester", self.project)["model"], "sonnet")

    def test_a_listed_agent_outside_the_plugin_root_is_skipped(self):
        root = self.install({"name": "acme", "agents": ["../../outside/reviewer.md", "./custom/tester.md"]})
        self.agent("reviewer", "opus", root.parent.parent / "outside")
        self.agent("tester", "sonnet", root / "custom")
        self.assertIsNone(agents.find("acme:reviewer", self.project))
        self.assertEqual(agents.find("acme:tester", self.project)["model"], "sonnet")
