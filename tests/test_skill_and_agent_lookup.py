"""Regression tests for the refresh command and plugin agent lookup."""
from __future__ import annotations

import io
import json
import urllib.error
from contextlib import redirect_stdout
from unittest import mock

from test_model_router import PLUGIN, Sandbox
from modelrouter import agents, cli, store, upstream


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
