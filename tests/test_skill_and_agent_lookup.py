"""Tests for plugin agent lookup."""
from __future__ import annotations

import json

from test_model_router import Sandbox
from modelrouter import agents


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


class SkippedAgentFiles(Sandbox):
    def test_a_project_file_with_no_name_is_skipped_so_a_user_agent_of_its_stem_is_found(self):
        folder = self.project / ".claude" / "agents"
        folder.mkdir(parents=True)
        (folder / "probe.md").write_text("---\ndescription: does a job\nmodel: haiku\n---\nbody\n")
        self.assertIsNone(agents.find("probe", self.project))
        self.agent("probe", "opus", self.home / "agents")
        found = agents.find("probe", self.project)
        self.assertEqual((found["source"], found["model"]), ("user", "opus"))

    def test_a_file_whose_name_claude_code_refuses_or_that_has_no_description_is_skipped(self):
        # sub-agents.md, "Subagent files Claude Code skips"
        folder = self.project / ".claude" / "agents"
        folder.mkdir(parents=True)
        # The name and description forms are as Claude Code 2.1.332 reads them.
        none = ("", "description:\n", "description:   \n", 'description: ""\n', "description: ~\n", "description: null\n",
                "description: true\n", "description: 5\n", "description: 1.5\n", "description: # none\n")
        for i, head in enumerate(("name: -probe\ndescription: does a job\n", "name: a:probe\ndescription: does a job\n",
                                  f"name: {'p' * 257}\ndescription: does a job\n", "name: 12345\ndescription: does a job\n",
                                  "name: true\ndescription: does a job\n", *("name: probe\n" + d for d in none))):
            path = folder / f"skipped{i}.md"
            path.write_text(f"---\n{head}model: haiku\n---\nbody\n")
            self.assertIsNone(agents.parse(path, "project"), head)
            self.assertIsNotNone(agents.parse(path, "plugin"), head)
        for i, described in enumerate(("description: >\n  does a job\n", "description: |\n  does a job\n",
                                       "description:\n  does a job\n", "description: yes\n", 'description: "does a job"\n',
                                       "description: does a job  # why\n", "model: haiku\ndescription: 5 jobs\n")):
            kept = folder / f"kept{i}.md"
            kept.write_text(f"---\nname: {'p' * 256}\n{described}---\nbody\n")
            self.assertEqual(agents.parse(kept, "user")["name"], "p" * 256, described)
