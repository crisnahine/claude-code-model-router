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
        # Built-ins inherit; with no known parent model there's nothing to check.
        self.assertIsNone(orch.resolve_agent_model(self.config, "general-purpose", None))
        self.assertIsNone(orch.resolve_agent_model(self.config, "Explore", None))


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


class EdgeCases(TempDataDir):
    """Coexistence with other plugins, user agents, env overrides and bad input."""

    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.home_patch = mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)})
        self.home_patch.start()
        self.config = orch.load_config({"cwd": str(self.project)})

    def tearDown(self):
        self.home_patch.stop()
        super().tearDown()

    def resolve(self, agent, requested=None, parent=None):
        return orch.resolve_agent_model(self.config, agent, requested, parent, self.project)

    def test_builtins_inherit_the_session_model(self):
        self.assertEqual(self.resolve("general-purpose", parent="claude-opus-5-5"), "opus")
        self.assertEqual(self.resolve("Plan", parent="claude-sonnet-5-5"), "sonnet")
        self.assertEqual(self.resolve("Explore", parent="claude-sonnet-5-5"), "sonnet")
        self.assertEqual(self.resolve("Explore", parent="claude-fable-5-2"), "opus")

    def test_other_plugins_agent_with_same_name_is_not_ours(self):
        self.assertIsNone(self.resolve("acme:reviewer"))
        self.assertIsNone(self.resolve("acme:deep-solver"))

    def test_bare_name_is_ours_only_without_a_local_agent(self):
        self.assertEqual(self.resolve("reviewer"), "opus")
        agents = self.project / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "my-reviewer.md").write_text("---\nname: reviewer\ndescription: mine\nmodel: haiku\n---\nbody\n")
        self.assertIsNone(self.resolve("reviewer"))
        self.assertEqual(self.resolve("orchestrator:reviewer"), "opus")

    def test_user_level_agent_also_shadows_bare_name(self):
        agents = self.home / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "scout.md").write_text("---\nname: \"scout\"\ndescription: x\n---\n")
        self.assertIsNone(self.resolve("scout"))

    def test_forced_subagent_model_wins_over_everything(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SUBAGENT_MODEL": "haiku",
                                          "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1"}):
            self.assertEqual(self.resolve("orchestrator:deep-solver", requested="opus"), "haiku")

    def test_unforced_subagent_model_only_replaces_inherit(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"}):
            self.assertEqual(self.resolve("general-purpose", parent="claude-opus-5-5"), "haiku")
            self.assertEqual(self.resolve("orchestrator:architect"), "opus")

    def test_malformed_override_is_ignored_not_fatal(self):
        claude = self.project / ".claude"
        claude.mkdir()
        for bad in ('{"agents": "oops"}', '{"agents": {"scout": "haiku"}}', "[1, 2]", "{not json"):
            (claude / "orchestrator.json").write_text(bad)
            config = orch.load_config({"cwd": str(self.project)})
            self.assertEqual(config["agents"]["scout"]["model"], "haiku")
            orch.routing_table(config)

    def test_model_switch_changes_what_inherit_means(self):
        payload = {"session_id": "s9", "cwd": str(self.project), "tool_name": "Agent",
                   "tool_input": {"subagent_type": "general-purpose", "description": "d", "prompt": "p"}}
        env = {**self.env, "HOME": str(self.home), "USERPROFILE": str(self.home),
               "CLAUDE_PLUGIN_OPTION_MAX_TIER": "sonnet", "CLAUDE_PLUGIN_OPTION_ROUTING_MODE": "guide"}
        run_hook("session-start", {"session_id": "s9", "source": "startup", "model": "claude-sonnet-5-5"}, env)
        self.assertEqual(run_hook("pre-agent", payload, env).stdout.strip(), "")
        run_hook("model-switch", {"session_id": "s9", "from_model": "claude-sonnet-5-5",
                                  "to_model": "claude-opus-5-5"}, env)
        out = json.loads(run_hook("pre-agent", payload, env).stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")

    def test_session_model_from_transcript(self):
        transcript = Path(self.tmp.name) / "t.jsonl"
        transcript.write_text("\n".join(json.dumps(r) for r in [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "message": {"role": "assistant", "model": "claude-sonnet-5-5"}},
            {"type": "assistant", "message": {"role": "assistant", "model": "claude-opus-5-5"}},
            {"type": "user", "message": {"role": "user", "content": "x"}},
        ]) + "\n")
        self.assertEqual(orch.session_model("nope", str(transcript)), "claude-opus-5-5")
        self.assertIsNone(orch.session_model("nope", str(transcript) + ".missing"))
        payload = {"session_id": "s7", "transcript_path": str(transcript), "cwd": str(self.project),
                   "tool_input": {"subagent_type": "general-purpose", "description": "d", "prompt": "p"}}
        out = run_hook("pre-agent", payload, {**self.env, "HOME": str(self.home), "USERPROFILE": str(self.home),
                                              "CLAUDE_PLUGIN_OPTION_MAX_TIER": "sonnet"})
        self.assertEqual(json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_hooks_ignore_a_stale_recorded_cap(self):
        orch.save_json(orch.state_path(), {"max_tier": "haiku"})
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_OPTION_MAX_TIER"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(orch.max_tier(), "fable")
            self.assertEqual(orch.max_tier(from_state=True), "haiku")

    def test_invalid_cap_means_no_cap(self):
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_MAX_TIER": "gpt-9"}):
            self.assertEqual(orch.max_tier(), "fable")
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_MAX_TIER": " Opus "}):
            self.assertEqual(orch.max_tier(), "opus")

    def test_ledger_rotates(self):
        with mock.patch.object(orch, "LEDGER_MAX_BYTES", 2000):
            for i in range(100):
                orch.append_ledger({"event": "spawn", "agent": f"a{i}"})
        size = orch.ledger_path().stat().st_size
        self.assertLess(size, 4000)
        self.assertEqual(self.ledger()[-1]["agent"], "a99")

    def test_corrupt_state_files_are_tolerated(self):
        self.data.mkdir(parents=True, exist_ok=True)
        for name in ("state.json", "sessions.json"):
            (self.data / name).write_text("{{{")
        (self.data / "ledger.jsonl").write_text("garbage\n{\"event\": \"spawn\"}\n")
        res = run_hook("session-start", {"session_id": "s", "source": "startup", "model": "opus"}, self.env)
        self.assertEqual(res.returncode, 0)
        self.assertIn("additionalContext", res.stdout)
        orch.usage_report(7)

    def test_offline_refresh_fails_quietly(self):
        orch.save_json(orch.state_path(), {"cache": {"fetched_at": 0}, "max_tier": "opus"})
        res = run_hook("refresh-cache", {}, {**self.env, "HTTPS_PROXY": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9"})
        self.assertEqual(res.returncode, 0)
        self.assertEqual(orch.load_json(orch.state_path(), {})["max_tier"], "opus")


@unittest.skipIf(os.name == "nt", "POSIX shell launcher")
class LauncherScript(unittest.TestCase):
    launcher = PLUGIN / "scripts" / "orchestrator.sh"

    def test_no_python_on_path_exits_zero(self):
        with tempfile.TemporaryDirectory() as empty:
            res = subprocess.run(["/bin/sh", str(self.launcher), "pre-agent"], input='{"tool_input": {}}',
                                 capture_output=True, text=True, env={"PATH": empty})
        self.assertEqual((res.returncode, res.stdout), (0, ""))

    def test_runs_from_a_path_with_spaces(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "My Plugins" / "orchestrator"
            shutil.copytree(PLUGIN, root, ignore=shutil.ignore_patterns("evals"))
            res = subprocess.run(["sh", str(root / "scripts" / "orchestrator.sh"), "session-start"],
                                 input='{"source": "startup"}', capture_output=True, text=True,
                                 env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(root),
                                      "CLAUDE_PLUGIN_DATA": str(Path(tmp) / "data")})
        self.assertIn("additionalContext", res.stdout, res.stderr)


class AutoRouting(TempDataDir):
    """Generic built-in agent calls are redirected to the orchestrator's agents."""

    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.env.update({"HOME": str(self.home), "USERPROFILE": str(self.home)})

    def call(self, agent="general-purpose", mode=None, session="r1", cap=None, batch="0",
             permission_mode="default", **tool_input):
        env = {**self.env, "ORCHESTRATOR_REDIRECT_BATCH_SECONDS": batch}
        if mode:
            env["CLAUDE_PLUGIN_OPTION_ROUTING_MODE"] = mode
        if cap:
            env["CLAUDE_PLUGIN_OPTION_MAX_TIER"] = cap
        ti = {"description": "d", "prompt": "p", **tool_input}
        if agent is not None:
            ti["subagent_type"] = agent
        payload = {"session_id": session, "cwd": str(self.project), "tool_name": "Agent",
                   "permission_mode": permission_mode, "tool_input": ti}
        out = run_hook("pre-agent", payload, env).stdout.strip()
        return json.loads(out)["hookSpecificOutput"] if out else None

    def prompt(self, text, session="r1", mode=None):
        env = dict(self.env)
        if mode:
            env["CLAUDE_PLUGIN_OPTION_ROUTING_MODE"] = mode
        return run_hook("user-prompt", {"session_id": session, "prompt_id": "p1", "prompt": text}, env).stdout

    def test_steer_redirects_once_then_allows(self):
        self.prompt("fix the login bug")
        first = self.call("general-purpose")
        self.assertEqual(first["permissionDecision"], "deny")
        self.assertIn("orchestrator:implementer", first["permissionDecisionReason"])
        self.assertIsNone(self.call("general-purpose"))
        # A new prompt steers again.
        self.prompt("now add tests")
        self.assertEqual(self.call("general-purpose")["permissionDecision"], "deny")

    def test_explore_and_plan_have_specific_targets(self):
        self.prompt("look around")
        self.assertIn("orchestrator:scout", self.call("Explore")["permissionDecisionReason"])
        plan = self.call("Plan")["permissionDecisionReason"]
        self.assertIn("orchestrator:architect", plan)
        self.assertNotIn("orchestrator:scout", plan)

    def test_user_naming_the_agent_wins(self):
        self.prompt("Use the Explore agent to map the repo, and a general-purpose agent for the rest")
        self.assertIsNone(self.call("Explore"))
        self.assertIsNone(self.call("general-purpose"))

    def test_plain_words_are_not_naming(self):
        self.assertFalse(orch.named_in_prompt("explore the code and plan the work", "Explore"))
        self.assertFalse(orch.named_in_prompt("explore the code and plan the work", "Plan"))
        self.assertTrue(orch.named_in_prompt("have the Plan subagent do it", "Plan"))
        self.assertTrue(orch.named_in_prompt("spawn a general purpose agent", "general-purpose"))

    def test_other_agents_are_never_redirected(self):
        self.prompt("go")
        for agent in ("acme:reviewer", "my-own-agent", "orchestrator:scout"):
            self.assertIsNone(self.call(agent), agent)

    def test_explicit_model_passes_in_steer_but_not_strict(self):
        self.prompt("go")
        self.assertIsNone(self.call("general-purpose", model="haiku"))
        self.assertEqual(self.call("general-purpose", mode="strict", model="haiku")["permissionDecision"], "deny")

    def test_strict_always_refuses_unless_named(self):
        self.prompt("go", mode="strict")
        for _ in range(3):
            out = self.call("Explore", mode="strict")
            self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("by name", out["permissionDecisionReason"])
        self.prompt("use the Explore agent", mode="strict")
        self.assertIsNone(self.call("Explore", mode="strict"))

    def test_guide_and_off_never_redirect(self):
        for mode in ("guide", "off"):
            self.assertIsNone(self.call("general-purpose", mode=mode))
        self.assertEqual(self.prompt("hi", mode="off"), "")
        self.assertIn("additionalContext", self.prompt("hi", mode="guide"))
        off = run_hook("session-start", {"source": "startup"},
                       {**self.env, "CLAUDE_PLUGIN_OPTION_ROUTING_MODE": "off"})
        self.assertEqual(off.stdout.strip(), "")

    def test_redirect_respects_the_cap(self):
        self.prompt("go")
        reason = self.call("general-purpose", cap="haiku")["permissionDecisionReason"]
        self.assertIn("orchestrator:scout", reason)
        self.assertNotIn("implementer", reason)
        # Plan's only target is the opus architect, which a haiku cap rules out.
        self.assertIsNone(self.call("Plan", cap="haiku"))

    def test_redirect_without_a_prior_prompt_still_cannot_loop(self):
        self.assertEqual(self.call("Explore", session="fresh")["permissionDecision"], "deny")
        self.assertIsNone(self.call("Explore", session="fresh"))

    def test_bad_mode_falls_back_to_steer(self):
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_ROUTING_MODE": "banana"}):
            self.assertEqual(orch.routing_mode(), "steer")

    def test_parallel_batch_is_redirected_as_a_whole(self):
        self.prompt("go")
        for i in range(3):
            out = self.call("Explore", batch="30", prompt=f"part {i}")
            self.assertEqual(out["permissionDecision"], "deny")
        # The same call issued again is Claude insisting: allowed at once, however fast.
        self.assertIsNone(self.call("Explore", batch="30", prompt="part 1"))
        # A reworded call is allowed once the batch window has passed.
        self.assertEqual(self.call("Explore", batch="30", prompt="reworded")["permissionDecision"], "deny")
        self.assertIsNone(self.call("Explore", batch="0", prompt="reworded again"))

    def test_task_notifications_do_not_reset_state(self):
        self.prompt("use the Explore agent to map the repo")
        self.assertEqual(self.prompt("<task-notification><task-id>x</task-id></task-notification>"), "")
        self.assertIsNone(self.call("Explore"))
        self.assertEqual(self.call("general-purpose")["permissionDecision"], "deny")
        self.prompt("<task-notification>done</task-notification>")
        self.assertIsNone(self.call("general-purpose"))  # still the same prompt: no second redirect

    def test_named_agent_stays_allowed_for_the_session(self):
        self.prompt("use the Explore agent for searches in this session")
        self.prompt("now look at the tests")
        self.assertIsNone(self.call("Explore"))

    def test_negated_mention_is_not_a_request(self):
        for text in ("don't use the Explore agent", "never use a general-purpose agent here",
                     "do it without the Explore agent", "use scout instead of the Explore agent"):
            self.assertFalse(orch.named_in_prompt(text, "Explore") or orch.named_in_prompt(text, "general-purpose"), text)
        self.assertTrue(orch.named_in_prompt("don't guess; use the Explore agent", "Explore"))

    def test_fork_and_omitted_type(self):
        self.prompt("go")
        self.assertIsNone(self.call("fork"))
        self.assertIsNone(self.call("fork", mode="strict", cap="haiku"))
        self.assertEqual(self.call(None)["permissionDecision"], "deny")  # no type means general-purpose

    def test_users_own_agent_named_like_a_builtin_is_left_alone(self):
        agents = self.project / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "explore.md").write_text("---\nname: Explore\ndescription: mine\nmodel: haiku\n---\nx\n")
        self.prompt("go")
        self.assertIsNone(self.call("Explore"))
        self.assertIsNone(self.call("Explore", mode="strict"))
        self.assertEqual(self.call("general-purpose")["permissionDecision"], "deny")

    def test_plan_agent_is_left_alone_in_plan_mode(self):
        self.prompt("go")
        self.assertIsNone(self.call("Plan", permission_mode="plan"))
        self.assertEqual(self.call("Explore", permission_mode="plan")["permissionDecision"], "deny")
        self.assertEqual(self.call("Plan")["permissionDecision"], "deny")

    def test_project_can_opt_out_but_not_force_stricter(self):
        claude = self.project / ".claude"
        claude.mkdir()
        (claude / "orchestrator.json").write_text('{"routing_mode": "off"}')
        self.assertIsNone(self.call("Explore"))
        self.assertIsNone(self.call("Explore", mode="strict"))
        (claude / "orchestrator.json").write_text('{"routing_mode": "strict"}')
        self.assertEqual(self.call("Explore", mode="steer")["permissionDecision"], "deny")
        self.assertIsNone(self.call("Explore", mode="steer"))  # still steer: repeat allowed
        self.assertIsNone(self.call("Explore", mode="guide", session="g"))

    def test_forced_subagent_model_over_cap_is_refused_whatever_the_agent(self):
        env = {"CLAUDE_CODE_SUBAGENT_MODEL": "opus", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1"}
        with mock.patch.dict(self.env, env):
            out = self.call("orchestrator:scout", cap="sonnet")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", out["permissionDecisionReason"])

    def test_redirect_is_logged(self):
        self.call("Explore")
        self.assertEqual(self.ledger()[0]["decision"], "redirect")


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
