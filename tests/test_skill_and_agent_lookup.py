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
