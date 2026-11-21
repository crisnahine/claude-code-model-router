"""Tests for plugins/orchestrator/scripts/orchestrator.py. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "orchestrator"
SCRIPT = PLUGIN / "scripts" / "orchestrator.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(SCRIPT.parent))
import orchestrator as orch  # noqa: E402


def run_hook(cmd: str, payload: dict, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), cmd],
        input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(PLUGIN), **env}, timeout=30,
    )


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, f"{path} has no frontmatter"
    fields = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


class TempDataDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        self.env = {"CLAUDE_PLUGIN_DATA": str(self.data)}
        self.patch = mock.patch.dict(os.environ, self.env)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def ledger(self) -> list[dict]:
        path = self.data / "ledger.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class ConfigConsistency(unittest.TestCase):
    """routing.json must describe the agents that actually ship."""

    def test_every_agent_in_config_matches_frontmatter(self):
        config = json.loads((PLUGIN / "config" / "routing.json").read_text())
        files = {p.stem: frontmatter(p) for p in (PLUGIN / "agents").glob("*.md")}
        self.assertEqual(set(config["agents"]), set(files), "routing.json agents != agents/*.md")
        for name, agent in config["agents"].items():
            self.assertEqual(files[name].get("name"), name)
            self.assertEqual(files[name].get("model"), agent["model"], f"model drift for {name}")

    def test_agent_models_are_known_tiers(self):
        config = json.loads((PLUGIN / "config" / "routing.json").read_text())
        for name, agent in config["agents"].items():
            self.assertIn(agent["model"], config["tiers"], name)

    def test_skills_have_descriptions(self):
        for skill in (PLUGIN / "skills").glob("*/SKILL.md"):
            self.assertTrue(frontmatter(skill).get("description"), skill)


class ModelResolution(unittest.TestCase):
    def setUp(self):
        self.config = orch.load_config()

    def test_model_family(self):
        self.assertEqual(orch.model_family("claude-opus-5-5"), "opus")
        self.assertEqual(orch.model_family("sonnet[1m]"), "sonnet")
        self.assertEqual(orch.model_family("best"), "opus")
        self.assertIsNone(orch.model_family("inherit"))
        self.assertIsNone(orch.model_family(None))

    def test_plugin_agent_uses_config_model(self):
        self.assertEqual(orch.resolve_agent_model(self.config, "orchestrator:deep-solver", None), "fable")
        self.assertEqual(orch.resolve_agent_model(self.config, "scout", None), "haiku")

    def test_requested_model_wins(self):
        self.assertEqual(orch.resolve_agent_model(self.config, "orchestrator:scout", "opus"), "opus")

    def test_unknown_or_inherit(self):
        self.assertIsNone(orch.resolve_agent_model(self.config, "other-plugin:thing", None))
        self.assertIsNone(orch.resolve_agent_model(self.config, "general-purpose", None))
        self.assertEqual(orch.resolve_agent_model(self.config, "Explore", None), "haiku")


class PreAgentHook(TempDataDir):
    def payload(self, **tool_input):
        return {"session_id": "s1", "cwd": str(self.project), "hook_event_name": "PreToolUse",
                "tool_name": "Agent", "tool_input": {"description": "d", "prompt": "p", **tool_input}}

    def test_allows_under_cap_and_logs(self):
        res = run_hook("pre-agent", self.payload(subagent_type="orchestrator:scout"),
                       {**self.env, "CLAUDE_PLUGIN_OPTION_MAX_TIER": "sonnet"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "")
        [row] = self.ledger()
        self.assertEqual((row["event"], row["decision"], row["expected_tier"]), ("spawn", "allow", "haiku"))

    def test_denies_over_cap(self):
        res = run_hook("pre-agent", self.payload(subagent_type="orchestrator:deep-solver"),
                       {**self.env, "CLAUDE_PLUGIN_OPTION_MAX_TIER": "sonnet"})
        out = json.loads(res.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("orchestrator:implementer", out["permissionDecisionReason"])
        self.assertNotIn("orchestrator:architect", out["permissionDecisionReason"])
        self.assertEqual(self.ledger()[0]["decision"], "deny")

    def test_denies_explicit_model_over_cap(self):
        res = run_hook("pre-agent", self.payload(subagent_type="general-purpose", model="opus"),
                       {**self.env, "CLAUDE_PLUGIN_OPTION_MAX_TIER": "haiku"})
        self.assertEqual(json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_default_cap_allows_everything(self):
        res = run_hook("pre-agent", self.payload(subagent_type="orchestrator:deep-solver"), self.env)
        self.assertEqual(res.stdout.strip(), "")

    def test_project_override_changes_agent_model(self):
        (self.project / ".claude").mkdir()
        (self.project / ".claude" / "orchestrator.json").write_text(
            json.dumps({"agents": {"implementer": {"model": "opus"}}}))
        res = run_hook("pre-agent", self.payload(subagent_type="orchestrator:implementer"),
                       {**self.env, "CLAUDE_PLUGIN_OPTION_MAX_TIER": "sonnet"})
        self.assertEqual(json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_project_agent_registered_by_override(self):
        (self.project / ".claude").mkdir()
        (self.project / ".claude" / "orchestrator.json").write_text(
            json.dumps({"agents": {"db-migrator": {"model": "opus", "role": "migrations"}}}))
        res = run_hook("pre-agent", self.payload(subagent_type="db-migrator"),
                       {**self.env, "CLAUDE_PLUGIN_OPTION_MAX_TIER": "sonnet"})
        reason = json.loads(res.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("orchestrator:scout", reason)
        self.assertNotIn("orchestrator:db-migrator", reason)

    def test_garbage_input_never_breaks(self):
        res = subprocess.run([sys.executable, str(SCRIPT), "pre-agent"], input="not json",
                             capture_output=True, text=True, env={**os.environ, **self.env})
        self.assertEqual(res.returncode, 0)


class PostAgentHook(TempDataDir):
    def test_logs_resolved_model(self):
        run_hook("post-agent", {"session_id": "s1", "tool_input": {"subagent_type": "orchestrator:reviewer"},
                                "tool_response": {"status": "completed", "resolvedModel": "claude-opus-5-5"}},
                 self.env)
        [row] = self.ledger()
        self.assertEqual(row["resolved_model"], "claude-opus-5-5")
        report = orch.usage_report(7)
        self.assertIn("orchestrator:reviewer", report)
        self.assertIn("claude-opus-5-5 x1", report)


class SessionStartHook(TempDataDir):
    def test_context_without_cache(self):
        res = run_hook("session-start", {"source": "startup", "cwd": str(self.project)}, self.env)
        ctx = json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("orchestrator:route", ctx)
        self.assertNotIn("Upstream:", ctx)

    def test_context_mentions_pending_upstream(self):
        self.data.mkdir(parents=True, exist_ok=True)
        orch.save_json(self.data / "state.json", {
            "reviewed": {"claude_code_version": "2.1.328", "pages": {}},
            "cache": {"fetched_at": 0, "releases": [{"version": "2.1.330", "date": "", "link": ""}],
                      "pages": {}},
        })
        res = run_hook("session-start", {"source": "startup", "cwd": str(self.project)}, self.env)
        self.assertIn("Upstream: 1 Claude Code release", res.stdout)

    def test_update_check_can_be_disabled(self):
        self.data.mkdir(parents=True, exist_ok=True)
        orch.save_json(self.data / "state.json", {
            "reviewed": {"claude_code_version": "2.1.0", "pages": {}},
            "cache": {"fetched_at": 0, "releases": [{"version": "2.1.330", "date": "", "link": ""}], "pages": {}},
        })
        res = run_hook("session-start", {"source": "startup"},
                       {**self.env, "CLAUDE_PLUGIN_OPTION_UPDATE_CHECK": "false"})
        self.assertNotIn("Upstream:", res.stdout)


class UpstreamParsing(TempDataDir):
    def test_parse_rss_fixture(self):
        releases = orch.parse_rss((FIXTURES / "changelog-rss.xml").read_text())
        self.assertGreaterEqual(len(releases), 1)
        self.assertRegex(releases[0]["version"], r"^\d+\.\d+\.\d+$")
        self.assertTrue(releases[0]["link"].startswith("https://code.claude.com/"))
        self.assertTrue(releases[0]["highlights"])

    def test_parse_llms_index(self):
        pages = orch.parse_llms_index((FIXTURES / "llms.txt").read_text())
        self.assertEqual(len(pages), 3)
        self.assertIn("https://code.claude.com/docs/en/hooks.md", pages)

    def test_diff_flags_new_releases_and_watched_pages(self):
        reviewed = {"claude_code_version": "2.1.329",
                    "pages": {"https://code.claude.com/docs/en/hooks.md": "aaa",
                              "https://code.claude.com/docs/en/prompt-library.md": "bbb"}}
        live = {"releases": [{"version": "2.1.330"}, {"version": "2.1.329"}],
                "pages": {"https://code.claude.com/docs/en/hooks.md": "changed",
                          "https://code.claude.com/docs/en/prompt-library.md": "bbb",
                          "https://code.claude.com/docs/en/plugins/new-thing.md": "ccc"}}
        diff = orch.diff_upstream(reviewed, live)
        self.assertEqual([r["version"] for r in diff["new_releases"]], ["2.1.330"])
        self.assertEqual(diff["pages_changed"], ["https://code.claude.com/docs/en/hooks.md"])
        self.assertEqual(diff["pages_added"], ["https://code.claude.com/docs/en/plugins/new-thing.md"])
        self.assertEqual(diff["watched_pages_touched"], sorted([
            "https://code.claude.com/docs/en/hooks.md", "https://code.claude.com/docs/en/plugins/new-thing.md"]))

    def test_version_ordering(self):
        self.assertGreater(orch.version_key("2.1.330"), orch.version_key("2.1.99"))

    def test_first_refresh_marks_present_as_reviewed_then_ack(self):
        rss = (FIXTURES / "changelog-rss.xml").read_text()
        llms = (FIXTURES / "llms.txt").read_text()
        with mock.patch.object(orch, "http_get", side_effect=lambda url: rss if url.endswith(".xml") else llms):
            state = orch.refresh(force=True)
        self.assertFalse(orch.has_pending(orch.pending_changes(state)))
        # Simulate an older reviewed version, then ack.
        state["reviewed"]["claude_code_version"] = "2.0.0"
        orch.save_json(orch.state_path(), state)
        self.assertTrue(orch.has_pending(orch.pending_changes(orch.load_json(orch.state_path(), {}))))
        with redirect_stdout(io.StringIO()):
            orch.main(["ack"])
        self.assertFalse(orch.has_pending(orch.pending_changes(orch.load_json(orch.state_path(), {}))))

    def test_cache_ttl_skips_network(self):
        orch.save_json(orch.state_path(), {"cache": {"fetched_at": 10**12}})
        with mock.patch.object(orch, "http_get", side_effect=AssertionError("network used")):
            orch.refresh(force=False)


class DataDirFlag(TempDataDir):
    def test_skill_commands_use_data_dir_and_recorded_cap(self):
        other = Path(self.tmp.name) / "other"
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(other)}):
            hook_env = {"CLAUDE_PLUGIN_DATA": str(other), "CLAUDE_PLUGIN_OPTION_MAX_TIER": "haiku"}
            run_hook("session-start", {"source": "startup"}, hook_env)
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_PLUGIN_")}
        res = subprocess.run([sys.executable, str(SCRIPT), "--data-dir", str(other), "routing-table"],
                             capture_output=True, text=True, env=env)
        self.assertIn("Model tier cap: **haiku**", res.stdout)


class RoutingTable(unittest.TestCase):
    def test_table_lists_agents_and_flags_cap(self):
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_MAX_TIER": "sonnet"}):
            table = orch.routing_table(orch.load_config())
        self.assertIn("orchestrator:scout | haiku |", table)
        self.assertIn("fable (over cap)", table)


if __name__ == "__main__":
    unittest.main()
