"""Tests for the Model Router plugin runtime. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import dataclasses
import datetime
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "model-router"
SCRIPTS = PLUGIN / "scripts"
SCRIPT = SCRIPTS / "model-router.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ERRORS = json.loads((FIXTURES / "model-errors.json").read_text())
SWAP = json.loads((FIXTURES / "agent-model-swap.json").read_text())
FRAMING = json.loads((FIXTURES / "prompt-framing.json").read_text())

sys.path.insert(0, str(SCRIPTS))
from modelrouter import agents, allowlist, calls, cli, fallback, gates, health, hooks, intake, ladder, learn, ledger, profile, registry, report, request, router, session, settings, spawn, store, upstream  # noqa: E402
from modelrouter import mod as modpath  # noqa: E402
from modelrouter import config as cfg  # noqa: E402
from modelrouter.config import Level  # noqa: E402
from modelrouter.registry import LevelSpec, Model, Rule  # noqa: E402

LOOKUP = "list the files under src"
BUILD = "implement the parser change"
HARD = "fix the auth migration across every service"
HARDEST = "investigate why auth is flaky and redesign it across every service"


def frontmatter(path: Path) -> dict:
    match = re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    assert match, f"{path} has no frontmatter"
    fields = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            value = value.strip()
            fields[key.strip()] = json.loads(value) if value.startswith('"') else value
    return fields


# Slow CI runners take about twice as long as a laptop; the quadratic negation regex took 32 s on 64 KB.
LINEAR_SECONDS = 2

class Sandbox(unittest.TestCase):
    """A private data dir, Claude home and project for each test, and a clean environment."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.data, self.home, self.project = base / "data", base / "home", base / "project"
        self.home.mkdir()
        self.project.mkdir()
        self.env = {"CLAUDE_PLUGIN_DATA": str(self.data), "CLAUDE_CONFIG_DIR": str(self.home),
                    "CLAUDE_PROJECT_DIR": str(self.project), "CLAUDE_PLUGIN_ROOT": str(PLUGIN)}
        self.patch = mock.patch.dict(os.environ, self.env)
        self.patch.start()
        for name in list(os.environ):
            if name.startswith(("CLAUDE_PLUGIN_OPTION_", "CLAUDE_CODE_SUBAGENT_", "CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL", "CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "CLAUDE_CODE_USE_", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "DISABLE_PROMPT_CACHING", "ANTHROPIC_")):
                del os.environ[name]
        self.managed = self.home / "managed-settings.json"
        self.managed_root = mock.patch.object(allowlist, "MANAGED_ROOT", self.home)
        self.managed_root.start()
        self.clock = time.time()
        store.set_clock(lambda: self.clock)

    def tearDown(self):
        store.set_clock(None)
        store.owner = None
        self.managed_root.stop()
        self.patch.stop()
        self.tmp.cleanup()

    # helpers ---------------------------------------------------------------
    def advance(self, seconds: float) -> None:
        self.clock += seconds

    @contextlib.contextmanager
    def clock_at(self, when: float):
        """The state clock reads `when` inside the block."""
        before, self.clock = self.clock, when
        try:
            yield
        finally:
            self.clock = before

    def route_around(self, *names):
        for name in names:
            for _ in range(2):
                health.failed(name, "404 model not found")

    def options(self, **values):
        """Set plugin options, or any CLAUDE_ variable, for the rest of the test."""
        for key, value in values.items():
            os.environ[key if key.startswith("CLAUDE_") else f"CLAUDE_PLUGIN_OPTION_{key.upper()}"] = value

    def agent(self, name: str, model: str | None = None, folder: Path | None = None, extra: str = "",
              description: str = "does a job") -> None:
        folder = folder or self.project / ".claude" / "agents"
        folder.mkdir(parents=True, exist_ok=True)
        pin = f"model: {model}\n" if model else ""
        (folder / f"{name}.md").write_text(f"---\nname: {name}\ndescription: {description}\n{pin}{extra}---\nbody\n")

    def session_on(self, model: str, session_id: str = "s1") -> None:
        session.record(session_id, model)

    def call(self, agent: str | None, prompt: str, session_id: str = "s1", **extra) -> dict:
        tool_input = {"prompt": prompt, **extra}
        if agent:
            tool_input["subagent_type"] = agent
        return {"session_id": session_id, "cwd": str(self.project), "tool_input": tool_input}

    def decide(self, agent: str | None, prompt: str, **extra) -> dict:
        hook_input = self.call(agent, prompt, **extra)
        snap = settings.Snapshot(hook_input)
        return router.decide(spawn.facts(hook_input, snap), snap.registry, snap.options)

    def pre(self, agent: str | None, prompt: str, **extra) -> dict | None:
        out = hooks.pre_agent(self.call(agent, prompt, **extra))
        return out["hookSpecificOutput"] if out else None

    def done(self, agent: str | None, prompt: str, ran_as: str, models_used: object = None, **extra) -> dict | None:
        """The agent call finished and Claude Code reports the model it ran on."""
        response = {"resolvedModel": ran_as, "status": "completed", "totalTokens": 1200}
        if models_used is not None:
            response["modelsUsed"] = models_used
        return hooks.post_agent({**self.call(agent, prompt, **extra), "tool_response": response})

    def launched(self, agent: str | None, prompt: str, ran_as: str, agent_id: str, **extra) -> None:
        """The agent call returned at once: the agent runs on in the background."""
        hooks.post_agent({**self.call(agent, prompt, **extra), "tool_response": {
            "resolvedModel": ran_as, "status": "async_launched", "agentId": agent_id}})

    def spawned(self, kind: str = "subagent", earlier: str | None = None, user_named: bool = False, **facts):
        """One spawn's decision as `learn.spawned` reads it; `earlier` is the model of the call's route before."""
        decision = {"level": "lookup", "pick": "haiku", "model": "haiku", "was": None, "source": "router",
                    "escalated_from": None, "repeat_of": None, **facts}
        before = earlier and calls.Route(model=earlier, sent=None, level=cfg.Level.lookup, effort="low",
                                         source="router", path="mod")
        return learn.spawned(cfg.load(), decision, kind, before, user_named)

    def handback(self, message: str) -> str:
        """A copy of the handback transcript fixture with the delivered report replaced."""
        rows = [json.loads(line) for line in (FIXTURES / "subagent-handback.jsonl").read_text().splitlines()]
        rows[0]["message"]["content"][0]["input"]["message"] = message
        path = Path(self.tmp.name) / f"handback-{abs(hash(message))}.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return str(path)

    def stopped(self, agent_id: str, answer: str | None = "src/a.py and src/b.py", transcript: str | None = None) -> None:
        """A subagent finished, as SubagentStop reports it."""
        hook_input = {"session_id": "s1", "agent_id": agent_id, "agent_type": "general-purpose"}
        if transcript:
            hook_input["agent_transcript_path"] = transcript
        hooks.agent_stopped(hook_input if answer is None else {**hook_input, "last_assistant_message": answer})

    def ledger(self) -> list[dict]:
        return ledger.read()

    def next_message(self) -> None:
        """Claude's next message comes seconds after this one: waiting rewrites are older."""
        self.advance(fallback.REFUSED_AFTER + 1)

    def override(self, value: dict, user: bool = False) -> None:
        folder = self.home if user else self.project / ".claude"
        folder.mkdir(exist_ok=True)
        (folder / "model-router.json").write_text(json.dumps(value))

    def out(self, *argv: str) -> str:
        with redirect_stdout(io.StringIO()) as buf:
            cli.main(list(argv))
        return buf.getvalue()


class Shipped(unittest.TestCase):
    """What ships must be consistent with itself."""

    def test_the_boolean_option_defaults_in_plugin_json_match_settings(self):
        fields = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["userConfig"]
        for name in ("main_effort", "update_check", "verify_gate", "strong_agent_gate"):
            self.assertEqual(fields[name]["default"], getattr(settings.Settings(), name), name)

    def test_the_claude_code_floor_is_the_version_the_manifest_and_teammate_effort_need(self):
        # `network` needs v2.1.328 (plugins/manifest-reference.md); v2.1.329 fixed `effort` for named teammates.
        manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["engines"]["claude-code"], ">=2.1.329")
        for path in (ROOT / "README.md", PLUGIN / "README.md", ROOT / "docs" / "FAQ.md", ROOT / "docs" / "GUIDE.md",
                     ROOT / "site" / "llms.txt", ROOT / "site" / "index.html",
                     ROOT / "docs" / "assets" / "badge-claude-code.svg"):
            self.assertNotIn("2.1.326 or later", path.read_text(encoding="utf-8"), path)
            self.assertIn("2.1.329 or later", path.read_text(encoding="utf-8"), path)

    def test_each_choice_option_is_a_picker_the_runtime_accepts(self):
        # A string option with no `options` list shows as an empty text box in /plugin.
        manifest = json.loads((ROOT / "plugins/model-router/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
        fields = manifest["userConfig"]
        models = list(json.loads((ROOT / "plugins/model-router/config/models.json").read_text(encoding="utf-8"))["models"])
        accepted = {"routing_mode": set(cfg.MODES), "priority": set(cfg.PRIORITIES),
                    "max_tier": {"none", *models}, "agent_pins": {"respect", "override"}}
        for name, values in accepted.items():
            field = fields[name]
            self.assertIn(field["default"], field["options"], name)
            self.assertEqual(set(field["options"]), values, name)
    def test_crew_matches_agent_files(self):
        config = json.loads((PLUGIN / "config" / "models.json").read_text(encoding="utf-8"))
        files = {p.stem: frontmatter(p) for p in (PLUGIN / "agents").glob("*.md")}
        self.assertEqual(set(config["crew"]), set(files))
        for name, agent in config["crew"].items():
            self.assertEqual(files[name].get("name"), name)
            self.assertTrue(agent.get("role"), name)

    def test_the_costly_role_agents_say_when_not_to_use_them(self):
        # Claude delegates from descriptions, so these limits keep the costliest agents for the work they're for.
        # With no note on the request, this is all Claude reads before handing it a task that writes a file.
        for name, limit in (("architect", "Writes nothing, so not for a task that must make a file, or a clear change."),
                            ("reviewer", "A small, tested change doesn't need one."),
                            ("deep-solver", "Not for ordinary hard coding.")):
            self.assertIn(limit, frontmatter(PLUGIN / "agents" / f"{name}.md")["description"], name)

    # Always-cheap agents keep a model, so they stay cheap when the router isn't routing; the rest are routed.
    PINNED = {"scout": ("haiku", "medium"), "verifier": ("haiku", "medium"), "docs-researcher": ("haiku", "medium")}

    def test_only_the_always_cheap_role_agents_carry_a_model(self):
        config = json.loads((PLUGIN / "config" / "models.json").read_text(encoding="utf-8"))
        for path in (PLUGIN / "agents").glob("*.md"):
            fields = frontmatter(path)
            self.assertEqual((fields.get("model"), fields.get("effort")), self.PINNED.get(path.stem, (None, None)),
                             path.name)
            self.assertEqual(config["crew"][path.stem].get("model"), fields.get("model"), path.name)
            # A description is when Claude delegates (sub-agents docs), so none asks to run on every change.
            self.assertNotRegex(fields["description"], r"(?i)\b(?:every|any) (?:diff|code change|change)\b", path.name)

    def test_frontmatter_plain_scalars_are_valid_yaml(self):
        # Claude Code's loader tolerates these, but GitHub and other strict YAML readers reject the whole block.
        indicators = tuple("-?:,[]{}#&*!|>'\"%@`")
        bad = []
        for path in sorted(PLUGIN.rglob("*.md")):
            match = re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
            for line in match.group(1).splitlines() if match else ():
                item = re.match(r"^\s+- (.*)$", line)
                field = re.match(r"^[A-Za-z_][\w-]*:(?: (.*))?$", line)
                value = (item.group(1) if item else (field.group(1) or "") if field else line).strip()
                if not value or value[0] in "\"'" and value[-1] == value[0] and len(value) > 1:
                    continue
                if value[0] in "[{" and value[-1] == {"[": "]", "{": "}"}[value[0]] and ": " not in value:
                    continue
                if re.fullmatch(r"[|>][-+]?", value):
                    continue
                if ": " in value or " #" in value or value.endswith(":") or value.startswith(indicators):
                    bad.append(f"{path.relative_to(PLUGIN)}: {line}")
        self.assertEqual(bad, [])

    def test_levels_and_models_are_complete(self):
        config = json.loads((PLUGIN / "config" / "models.json").read_text(encoding="utf-8"))
        self.assertEqual(tuple(config["levels"]), cfg.LEVELS)
        for level in config["levels"].values():
            self.assertIn(level["effort"], (None, *cfg.EFFORTS))
        for name, model in config["models"].items():
            for field in ("strength", "speed", "cost"):
                self.assertIsInstance(model[field], (int, float), f"{name}.{field}")

    def test_skills_have_descriptions_and_use_the_launcher(self):
        skills = list((PLUGIN / "skills").glob("*/SKILL.md"))
        self.assertEqual({p.parent.name for p in skills}, {"ship", "route", "models", "usage", "refresh"})
        commands = set(hooks.HANDLERS) | {"policy", "models", "explain", "refresh", "status", "ack", "usage"}
        for path in skills:
            self.assertTrue(frontmatter(path).get("description"), path)
            # Third person ("Shows ..."): the description is injected into the system prompt.
            self.assertRegex(frontmatter(path)["description"], r"^[A-Z][a-z]+s\b", path)
            text = path.read_text(encoding="utf-8").split("own repository")[0]
            self.assertNotIn("model-router.py", text, path)
            for used in re.findall(r'model-router\.sh" --data-dir "\$\{CLAUDE_PLUGIN_DATA\}" ([a-z-]+)', text):
                self.assertIn(used, commands, path)

    def test_read_only_role_agents_say_they_write_nothing(self):
        # Claude picks an agent from its description; a task whose deliverable is a file must not go here.
        for path in sorted((PLUGIN / "agents").glob("*.md")):
            front = frontmatter(path)
            if front.get("tools") and not re.search(r"\b(?:Edit|Write|NotebookEdit)\b", front["tools"]):
                self.assertRegex(front["description"], r"(?i)writes nothing|can't write files|doesn't fix code", path.name)

    def test_the_verifier_reports_a_check_that_never_ran_as_not_run(self):
        body = (PLUGIN / "agents" / "verifier.md").read_text(encoding="utf-8").split("---", 2)[2]
        self.assertIn("`NOT RUN`", body)
        self.assertIn("failed to start", body)
        self.assertIn("only parsed or compiled", body)
        self.assertIn("never as a pass", body)

    def test_the_implementer_finishes_the_plan_and_stops_early_only_for_a_wrong_plan(self):
        body = (PLUGIN / "agents" / "implementer.md").read_text(encoding="utf-8").split("---", 2)[2]
        self.assertIn("Finish the planned change. Stop early only when the plan is wrong or needs the user's decision", body)

    def test_the_docs_researcher_brief_carries_todays_date(self):
        # Haiku 5.5 grounds its answers in recent search results when it is given the date with a search tool.
        refresh = (PLUGIN / "skills" / "refresh" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("starting the brief with today's date: !`date +%F`", refresh)
        self.assertIn("\n  - Bash(date +%F)\n", refresh)  # the `!` line runs under the skill's own allowed-tools
        body = (PLUGIN / "agents" / "docs-researcher.md").read_text(encoding="utf-8").split("---", 2)[2]
        self.assertIn("When your brief gives today's date, use it as today.", body)
        self.assertIn("say in the first line of your answer that you don't know today's date", body)

    def test_the_architect_hands_back_a_file_it_was_asked_to_write(self):
        # Misrouted to it, a write-the-file task still ends with the content the caller can write.
        body = (PLUGIN / "agents" / "architect.md").read_text(encoding="utf-8").split("---", 2)[2]
        self.assertIn("asks you to write a file", body)
        self.assertIn("for the caller to write", body)

    def test_the_route_skill_says_when_to_use_it_in_its_description_alone(self):
        front = frontmatter(PLUGIN / "skills" / "route" / "SKILL.md")
        self.assertNotIn("when_to_use", front)  # it repeated the description, in context in every session
        for phrase in ("which model", "why an agent ran", "split", "routed right"):
            self.assertIn(phrase, front["description"])

    def test_hooks_call_existing_commands(self):
        data = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        commands = set()
        for groups in data["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    self.assertEqual(hook["command"], "sh")
                    self.assertEqual(hook["args"][0], "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh")
                    commands.add(hook["args"][1])
        self.assertEqual(data["modules"], ["./router.ts"])
        module = (PLUGIN / "hooks" / "router.ts").read_text(encoding="utf-8")
        self.assertIn("scripts/model-router.sh", module)
        commands |= set(re.findall(r"ask\(\$, opts, '([a-z-]+)'", module))
        self.assertEqual(module.count("await ask($"), module.count("await ask($, opts, '"))
        self.assertEqual(commands, set(hooks.HANDLERS))
        self.assertTrue((SCRIPTS / "model-router.sh").exists())

    def test_manifest_and_marketplace(self):
        manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], cfg.PLUGIN_NAME)
        self.assertFalse(manifest["name"].startswith("claude-"), "reserved for Anthropic's own plugins")
        self.assertEqual(market["name"], "claude-code-model-router")
        self.assertEqual(market["plugins"][0]["name"], manifest["name"])
        self.assertTrue((ROOT / market["plugins"][0]["source"]).is_dir())
        self.assertEqual(manifest["userConfig"]["routing_mode"]["default"], "auto")
        # Claude Code before v2.1.271 can't load a plugin whose userConfig declares options.
        if any("options" in option for option in manifest["userConfig"].values()):
            floor = re.fullmatch(r">=(\d+)\.(\d+)\.(\d+)", manifest["engines"]["claude-code"])
            self.assertGreaterEqual(tuple(map(int, floor.groups())), (2, 1, 271))

    def test_the_installed_plugin_carries_the_license(self):
        # An install copies only the plugin folder, and the license asks for its notice in every copy.
        copy = PLUGIN / "LICENSE"
        self.assertFalse(copy.is_symlink())
        self.assertEqual(copy.read_bytes(), (ROOT / "LICENSE").read_bytes())

    def test_the_marketplace_names_the_product_model_router(self):
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        for text in (market["description"], market["plugins"][0]["description"]):
            self.assertIn("Model Router, a plugin for Claude Code", text)
            self.assertNotIn("Claude Code Model Router", text)
            self.assertNotIn("Claude Code model router", text)

    def test_ci_tests_the_oldest_python_the_launcher_accepts(self):
        entry = (SCRIPTS / "model-router.py").read_text(encoding="utf-8")
        floor = "%s.%s" % re.search(r"sys\.version_info < \((\d+), (\d+)\)", entry).groups()
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        matrix = re.findall(r'"(\d+\.\d+)"', re.search(r"python: \[([^\]]*)\]", ci).group(1))
        self.assertEqual(min(matrix, key=lambda v: tuple(map(int, v.split(".")))), floor)
        self.assertIn(f"Python {floor} or later", (ROOT / "README.md").read_text(encoding="utf-8"))

    def test_shipped_baseline_lists_the_registry_models(self):
        baseline = json.loads((ROOT / "compat" / "baseline.json").read_text(encoding="utf-8"))
        config = json.loads((PLUGIN / "config" / "models.json").read_text(encoding="utf-8"))
        self.assertEqual(set(upstream.model_families(baseline["aliases"])), set(config["models"]))

    def test_readme_and_description_keep_their_search_phrases(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        description = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["description"]
        self.assertTrue(readme.startswith("# Claude Code Model Router"))
        for phrase in ("Claude Code plugin", "multi-model orchestration", "autonomous", "Claude model routing"):
            self.assertIn(phrase, readme)
        for phrase in ("Claude Code model router", "Claude model routing", "multi-model orchestration"):
            self.assertIn(phrase, description)

    def test_nothing_still_carries_the_old_name(self):
        old = re.compile("orchestr" + "ator", re.I)
        skip = {".git", ".superpowers", "superpowers", "fixtures", "__pycache__", "results"}
        try:  # tracked files only, so a local scratch folder can't fail the suite; a tarball has no git
            listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True,
                                    timeout=60).stdout.decode("utf-8").split("\0")
            paths = [ROOT / name for name in listed if name]
        except (OSError, subprocess.SubprocessError):
            paths = list(ROOT.rglob("*"))
        for path in paths:
            if path.is_file() and not skip & set(path.relative_to(ROOT).parts) and path.suffix != ".pyc":
                self.assertIsNone(old.search(str(path.relative_to(ROOT))), path)
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                self.assertIsNone(old.search(text), path)

    def test_agent_stopped_runs_before_the_next_step(self):
        data = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        stop = [h for g in data["hooks"]["SubagentStop"] for h in g["hooks"] if h["args"][1] == "agent-stopped"]
        self.assertEqual([h.get("async", False) for h in stop], [False])

    def test_the_effort_cache_rule_is_registry_data(self):
        rule = {k: v for k, v in cfg.base_config()["main"]["effort_keeps_cache"].items() if not k.startswith("$")}
        self.assertEqual(rule, {"fable": ">=5.1", "opus": "5.5", "sonnet": "5.5", "haiku": "5.5"})
        source = inspect.getsource(modpath._cache_drop)
        for name in cfg.base_config()["models"]:
            self.assertNotIn(f'"{name}"', source)


class OptionText(unittest.TestCase):
    def test_main_effort_names_every_case_it_stays_off(self):
        manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        text = manifest["userConfig"]["main_effort"]["description"]
        for case in ("CLAUDE_CODE_EFFORT_LEVEL", "Bedrock", "Vertex", "Foundry", "ANTHROPIC_BASE_URL",
                     "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "Fable 5.1", "Opus 5.5", "Sonnet 5.5", "Haiku 5.5",
                     "Mantle", "DISABLE_PROMPT_CACHING"):
            self.assertIn(case, text)
        for gap in ("HIPAA", "Claude Desktop"):  # cases the router can't detect, so the user is told
            self.assertIn(gap, text)


class SkillGrants(unittest.TestCase):
    """A skill's allowed-tools run without a prompt for its turn, so each grant covers only the
    launcher commands that skill runs. Matching follows
    https://code.claude.com/docs/en/permissions.md#wildcard-patterns."""

    LAUNCHER = 'sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh"'
    PREFIX = LAUNCHER + ' --data-dir "${CLAUDE_PLUGIN_DATA}"'
    SKILLS = ("route", "models", "usage", "refresh")

    def rules(self, text: str) -> list[str]:
        block = re.search(r"^allowed-tools:(.*?)(?=^\S|^---)", text, re.M | re.S)
        return re.findall(r"Bash\(([^)]*)\)", block.group(1)) if block else []

    def allowed(self, rules: list[str], command: str) -> bool:
        for rule in rules:
            if rule.endswith(" *"):
                self.assertNotIn("*", rule[:-2], rule)
                if command == rule[:-2] or command.startswith(rule[:-1]):
                    return True
            elif "*" not in rule and command == rule:
                return True
        return False

    def test_each_skill_may_run_its_own_commands_and_nothing_that_writes_files(self):
        writes = [f"{self.PREFIX} upstream-check --baseline /tmp/x --write",
                  f"{self.PREFIX} upstream-check --baseline /tmp/x --json-out /tmp/y",
                  f"{self.LAUNCHER} upstream-check --baseline /tmp/x --write"]
        for name in self.SKILLS:
            text = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            rules = self.rules(text)
            runs = re.findall(r"`(%s[^`]*)`" % re.escape(self.LAUNCHER), text)
            self.assertTrue(runs, name)
            for command in runs:
                command = command.replace("<the brief>", "list the files").replace("$ARGUMENTS", "30 --json")
                self.assertTrue(self.allowed(rules, command), f"{name} can't run: {command}")
            for command in writes:
                self.assertFalse(self.allowed(rules, command), f"{name} pre-approves: {command}")
            if name != "models":
                self.assertFalse(self.allowed(rules, f"{self.PREFIX} models --reset"), name)
            if name != "refresh":
                self.assertFalse(self.allowed(rules, f"{self.PREFIX} ack"), name)


class Intake(Sandbox):
    """A teammate's report or another session's message reaching the main loop isn't the user's request."""

    def setUp(self):
        super().setUp()
        self.options(main_effort="true")  # off by default

    def delivered(self, tag: str = "teammate-message") -> str:
        return (f'Another Claude session sent a message:\n<{tag} teammate_id="auditor" color="blue">\n'
                f"{HARD}\n</{tag}>")

    def test_a_delivered_message_raises_no_effort(self):
        session.record("s1", "claude-opus-5-5")
        for tag in ("teammate-message", "cross-session-message"):
            self.assertFalse(intake.is_user_prompt(self.delivered(tag)), tag)
            self.assertIsNone(modpath.turn({"session_id": "s1", "text": self.delivered(tag)}), tag)
        self.assertEqual(modpath.turn({"session_id": "s1", "text": HARD}), {"level": "complex", "effort": "high"})
        self.assertTrue(intake.is_user_prompt("Another Claude session sent a message: is that how they phrase it?"))

    def test_a_delivered_message_leaves_the_users_request_alone(self):
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": LOOKUP})
        self.assertIsNone(hooks.user_prompt({"session_id": "s1", "prompt": self.delivered()}))
        self.assertEqual(request.load("s1").level, "lookup")

    def test_a_delivered_message_ends_the_floor_and_never_starts_a_request(self):
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": HARD, "prompt_id": "p1"})
        before = request.load("s1")
        hooks.user_prompt({"session_id": "s1", "prompt": self.delivered(), "prompt_id": "p2"})
        after = request.load("s1")
        self.assertEqual((after.floor, after.prompt_id, after.level), (None, "p1", before.level))
        hooks.user_prompt({"session_id": "s1", "prompt": HARD, "prompt_id": "p3"})
        self.assertEqual(request.load("s1").floor, before.level)

    KINDS = {"composer": True, "bridge": True, "sdk": True, "task-notification": False, "scheduled-trigger": True,
             "peer": False, "peer-send-message": False, "projects-relay": True, "channel": True, "coordinator": True,
             "observer": False, "observer-activity": False, "auto-continuation": True, "unclassified": True,
             "slack-ping": True, "plugin": True}  # PromptOrigin in the mod types pinned by tests/mod/typecheck.sh

    def test_every_origin_kind_is_classified(self):
        self.assertEqual(set(intake.ORIGINS), set(self.KINDS))
        for kind, person in self.KINDS.items():
            with self.subTest(kind=kind):
                self.assertEqual(intake.from_turn(HARD, None, kind).person, person)
        self.assertTrue(intake.from_turn(HARD, None, "a-kind-from-a-later-build").person)

    def test_the_framing_of_a_delivered_message(self):
        for text in FRAMING["delivered"]:
            seen = intake.from_prompt(text, self.project)
            self.assertEqual((seen.delivered, seen.person, seen.level), (True, False, None), text)
            self.assertFalse(intake.from_turn(text, None, None).person, text)
        for text in FRAMING["machine"]:
            with self.subTest(machine=text):
                self.assertEqual((intake.from_prompt(text, self.project).delivered, intake.is_user_prompt(text)),
                                 (False, False))
                self.assertFalse(intake.from_turn(text, None, "composer").person)
        for text in FRAMING["person"]:
            with self.subTest(person=text):
                self.assertTrue(intake.from_prompt(text, self.project).person)
                self.assertTrue(intake.from_turn(text, None, "composer").person)

    def test_a_long_run_after_a_bracket_is_read_quickly(self):
        for text in ("<" + "a" * 80_000, "<" + "a-" * 40_000, "<" + "a-b" * 27_000):
            started = time.monotonic()
            self.assertTrue(intake.is_user_prompt(text))
            self.assertLess(time.monotonic() - started, 0.5, text[:8])

    def test_a_command_is_this_turns_only_when_its_line_opens_the_turn(self):
        line = SlashSkills.LINE.format("hard", "")  # no arguments: the body is the task
        command = {"name": "hard", "args": "", "body": SlashSkills.HARD_BODY}
        seen = intake.from_turn(line, command, "composer")
        self.assertEqual((seen.person, seen.command, seen.level), (True, "hard", "hardest"))
        self.assertIsNone(intake.from_turn(FRAMING["delivered"][0], command, None).command)
        self.assertIsNone(intake.from_turn(SlashSkills.LINE.format("other", ""), command, None).command)
        self.assertFalse(intake.from_turn(line, command, "peer").person)


class Store(Sandbox):
    def test_keyed_files_drop_the_least_recently_written(self):
        with mock.patch.object(store, "KEEP_ENTRIES", 3):
            for i, key in enumerate(("zz", "aa", "mm", "bb")):
                with self.clock_at(1000.0 + i):
                    store.save_entry("sample", key, {"n": i})
        self.assertEqual(sorted(store.load_state_file(self.data / "sample.json")), ["aa", "bb", "mm"])

    def test_the_state_clock_can_be_replaced_and_restored(self):
        self.assertEqual(store.now(), self.clock)
        store.set_clock(None)
        self.assertAlmostEqual(store.now(), time.time(), delta=5)

    def test_ledger_survives_junk_lines(self):
        store.append_ledger({"event": "spawn"})
        with store.ledger_path().open("a") as fh:
            fh.write("not json\n[1]\n")
        self.assertEqual(len(ledger.read(7)), 1)

    def test_updates_from_processes_running_at_once_are_all_kept(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                "from modelrouter import store\n"
                "store.LOCK_WAIT_SECONDS = 60\n"  # a slow machine must wait, not skip the lock
                "def add(entry): entry['n'] = entry.get('n', 0) + 1\n"
                "for _ in range(20): store.mutate_entry('sample', 's1', add)\n")
        workers = [subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS)], env={**os.environ})
                   for _ in range(6)]
        for worker in workers:
            self.assertEqual(worker.wait(timeout=120), 0)
        self.assertEqual(store.load_entry("sample", "s1")["n"], 120)
        self.assertEqual(list(self.data.glob("*.lock")) + list(self.data.glob("*.tmp")), [])

    def test_two_processes_waiting_on_one_keyed_file_both_keep_their_update(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                "from modelrouter import store\n"
                "store.owner = sys.argv[2]\n"
                "store.LOCK_WAIT_SECONDS = 60\n"  # the second worker may start much later on a slow machine
                "print('ready', flush=True)\n"
                "store.mutate_entry('sample', 's1', lambda entry: entry.update({sys.argv[2]: 1}))\n")
        store.owner = "holder"
        with store.locked(store.data_dir() / "sample.json"):
            workers = [subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), name], stdout=subprocess.PIPE,
                                        text=True, env={**os.environ}) for name in ("a", "b")]
            for worker in workers:
                self.assertEqual(worker.stdout.readline().strip(), "ready")
            time.sleep(0.1)  # both are now waiting on the lock this test holds
        for worker in workers:
            worker.communicate(timeout=60)
            self.assertEqual(worker.returncode, 0)
        entry = store.load_entry("sample", "s1")
        self.assertEqual((entry.get("a"), entry.get("b")), (1, 1))
        counts = store.lock_counts()["sample.json"]
        self.assertEqual((counts["waits"], counts["other_session"], counts.get("skipped")), (2, 2, None))

    def test_a_lock_held_past_the_bound_skips_the_write(self):
        self.data.mkdir(exist_ok=True)
        store.save_entry("sample", "s1", {"n": 1})
        path = self.data / "sample.json"
        before = path.read_bytes()
        lock = self.data / "sample.json.lock"
        lock.write_text("other")  # held by someone else, and fresh
        (self.data / "ledger.jsonl.lock").write_text("")
        started = time.monotonic()
        with mock.patch.object(store, "LOCK_WAIT_SECONDS", 0.05):
            self.assertEqual(store.try_update(path, lambda data: data.update(b=2)), ({"s1": mock.ANY, "b": 2}, "busy"))
            store.mutate_entry("sample", "s1", lambda entry: entry.update(n=2))
            self.assertEqual(store.remove_state_file(path), "busy")
            store.append_ledger({"event": "spawn"})
            store.owner = "mine"
            store.update_entry("sample", "s1", n=3)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(store.ledger_path().exists())
        self.assertTrue(lock.exists())  # not ours to remove
        self.assertEqual(store.lock_counts()["sample.json"]["skipped"], 4)
        self.assertEqual(store.lock_counts()["sample.json"]["other_session"], 1)
        self.assertEqual(store.lock_counts()["ledger.jsonl"]["skipped"], 1)

    def test_a_stale_lock_is_broken(self):
        self.data.mkdir(exist_ok=True)
        lock = self.data / "state.json.lock"
        lock.write_text("")
        os.utime(lock, (time.time() - 60, time.time() - 60))
        store.update_dict(self.data / "state.json", lambda data: data.update(a=1))
        self.assertFalse(lock.exists())
        self.assertEqual(store.load_state_file(self.data / "state.json"), {"a": 1})
        self.assertEqual(store.lock_counts(), {"state.json": {"broken": 1, "at": self.clock}})

    def test_a_reset_while_another_process_holds_the_lock_says_so(self):
        self.data.mkdir(exist_ok=True)
        (self.data / "state.json.lock").write_text("")
        (self.data / "learned.json.lock").write_text("")
        with mock.patch.object(store, "LOCK_WAIT_SECONDS", 0.02):
            out = io.StringIO()
            with redirect_stdout(out):
                cli.main(["models", "--reset"])
        self.assertIn("another Model Router process held state.json", out.getvalue())
        self.assertIn("another Model Router process held learned.json", out.getvalue())

    def test_state_files_are_written_compact(self):
        store.save_entry("sample", "s1", {"a": {"model": "haiku"}})
        self.assertNotIn("\n", (self.data / "sample.json").read_text(encoding="utf-8"))

    def test_a_row_appended_while_the_ledger_is_trimmed_is_kept(self):
        import threading
        path = store.ledger_path()
        path.write_text("".join(json.dumps({"n": i, "pad": "x" * 20}) + "\n" for i in range(100)))
        real = store.write_atomic

        def slow_trim(target, text):
            racer = threading.Thread(target=store.append_ledger, args=({"event": "racer"},))
            racer.start()
            time.sleep(0.2)  # another hook appends between the trim's read and its replace
            real(target, text)
            self.racer = racer

        with mock.patch.object(store, "LEDGER_MAX_BYTES", path.stat().st_size), \
                mock.patch.object(store, "write_atomic", slow_trim):
            store.append_ledger({"event": "spawn"})
        self.racer.join()
        self.assertIn("racer", [row.get("event") for row in ledger.read()])

    def test_two_hooks_trimming_the_ledger_at_once_keep_half_of_it(self):
        path = store.ledger_path()
        path.write_text("".join(json.dumps({"n": i, "pad": "x" * 20}) + "\n" for i in range(100)))
        real = store.locked

        @contextlib.contextmanager
        def racing(lock_path):
            with real(lock_path):
                lines = lock_path.read_text(encoding="utf-8").splitlines(keepends=True)
                store.write_atomic(lock_path, "".join(lines[len(lines) // 2:]))  # the other hook trimmed first
                yield

        with mock.patch.object(store, "LEDGER_MAX_BYTES", path.stat().st_size), \
                mock.patch.object(store, "locked", racing):
            store.append_ledger({"event": "spawn"})
        self.assertEqual(len(ledger.read()), 51)


class Snapshot(Sandbox):
    def test_each_hook_call_reads_the_registry_and_options_once(self):
        os.environ["CLAUDE_CODE_USE_BEDROCK"] = "1"
        spawned = {"session_id": "s1", "cwd": str(self.project), "parent_model": "claude-opus-5-5", "kind": "subagent",
                   "tool_input": {"subagent_type": "general-purpose", "prompt": LOOKUP}}
        for name, run in (("mod-spawn", lambda: modpath.spawn(spawned)),
                          ("agent-failed", lambda: hooks.agent_failed(
                              {**spawned, "error": "API Error: 404 not_found_error: model: claude-haiku-5-5"})),
                          ("pre-agent", lambda: hooks.pre_agent(self.call("general-purpose", BUILD, session_id="s2"))),
                          ("user-prompt", lambda: hooks.user_prompt({"session_id": "s1", "prompt": HARD}))):
            with mock.patch.object(cfg, "load", wraps=cfg.load) as load, \
                    mock.patch.object(settings, "resolve", wraps=settings.resolve) as resolve:
                run()
            self.assertEqual((load.call_count, resolve.call_count), (1, 1), name)

    def test_an_override_file_is_read_once_per_call_under_a_cap_or_not(self):
        self.override({"models": {"saga": {"strength": 1.5}}}, user=True)
        self.override({"models": {"swift": {"strength": 1.6}}})
        for cap in (None, "sonnet"):
            if cap:
                store.update_state(lambda state: state.update(max_tier=cap))
            with mock.patch.object(store, "load_dict", wraps=store.load_dict) as read:
                self.out("models", "--json")
            files = [c.args[0].name for c in read.call_args_list if c.args[0].name == cfg.OVERRIDE_FILE]
            self.assertEqual(files, [cfg.OVERRIDE_FILE] * 2, cap)

    def test_a_cheap_hook_and_a_result_naming_no_model_load_neither_the_allowlist_nor_the_health_table(self):
        self.options(main_effort="true")
        usage = {"model": "claude-haiku-5-5", "input_tokens": 1, "output_tokens": 1}
        with mock.patch.object(allowlist, "read", side_effect=AssertionError("allowlist")), \
                mock.patch.object(health, "load", side_effect=AssertionError("health")):
            hooks.post_edit({"session_id": "s1", "tool_input": {"file_path": str(self.project / "a.py")}})
            hooks.stop({"session_id": "s1", "cwd": str(self.project)})
            modpath.turn({"session_id": "s1", "cwd": str(self.project), "text": LOOKUP})
            modpath.usage({"session_id": "s1", "cwd": str(self.project), "usage": usage})
            hooks.post_agent({**self.call(None, LOOKUP), "tool_response": {"status": "completed"}})

    def test_a_spawn_reads_the_allowlist_once(self):
        with mock.patch.object(allowlist, "read", wraps=allowlist.read) as read:
            modpath.spawn({"session_id": "s1", "cwd": str(self.project), "parent_model": "claude-opus-5-5",
                             "kind": "subagent", "tool_input": {"subagent_type": "general-purpose", "prompt": LOOKUP}})
        self.assertEqual(read.call_count, 1)

    def test_a_failed_agent_call_filters_the_usable_list_on_the_table_it_just_wrote(self):
        modpath.start({"session_id": "s1"})
        self.route_around("sonnet", "opus", "fable")
        health.failed("haiku", "404 model not found")
        out = hooks.agent_failed({**self.call("general-purpose", LOOKUP, model="haiku"),
                                  "error": "API Error: 404 not_found_error: model: haiku"})
        self.assertIn("looks unavailable", out["hookSpecificOutput"]["additionalContext"])

    def test_the_snapshot_picks_with_its_priority_usable_models_and_raises_read_once(self):
        store.save_json(learn._path(), {"lookup": {"bump": 1}})
        self.options(priority="quality")
        snap = settings.Snapshot({"session_id": "s1", "cwd": str(self.project)})
        with mock.patch.object(learn, "raises", wraps=learn.raises) as raises:
            picks = {level: snap.pick(level) for level in cfg.LEVELS}
            self.assertIs(snap.raises, snap.raises)
        self.assertEqual(raises.call_count, 1)
        self.assertEqual(picks["lookup"], ladder.pick(snap.registry, "lookup", "quality", snap.usable, {"lookup": 1}))
        self.assertEqual(picks["complex"], ladder.pick(snap.registry, "complex", "quality", snap.usable, {}))

    def test_a_field_is_read_on_first_use_and_then_kept(self):
        snap = settings.Snapshot({"session_id": "s1", "cwd": str(self.project)})
        with mock.patch.object(allowlist, "read", wraps=allowlist.read) as read:
            self.assertIs(snap.allowed, snap.allowed)
            self.assertEqual(snap.usable, snap.registry.available(snap.options.cap, allowed=snap.allowed))
        self.assertEqual(read.call_count, 1)


class Registry(Sandbox):
    def setUp(self):
        super().setUp()
        self.config = cfg.load()

    def test_family_maps_aliases_and_ids(self):
        f = lambda m: self.config.family(m)  # noqa: E731
        self.assertEqual(f("claude-opus-5-5"), "opus")
        self.assertEqual(f("sonnet[1m]"), "sonnet")
        self.assertEqual(f("best"), "opus")
        self.assertEqual(f("us.anthropic.claude-haiku-5-5-v1:0"), "haiku")
        for unknown in ("inherit", None, "gpt-9", 5, ["opus"]):
            self.assertIsNone(f(unknown))

    def test_select_by_priority(self):
        usable = self.config.available()
        pick = lambda needs, priority, names=usable: self.config.select(needs, priority, names)  # noqa: E731
        self.assertEqual([pick(n, "balanced") for n in (1, 2, 3, 4)], ["haiku", "sonnet", "opus", "fable"])
        self.assertEqual([pick(n, "quality") for n in (1, 2, 3, 4)], ["sonnet", "opus", "fable", "fable"])
        self.assertEqual([pick(n, "fast") for n in (1, 2, 3, 4)], ["haiku", "haiku", "sonnet", "opus"])
        self.assertEqual(pick(4, "balanced", ["haiku", "sonnet"]), "sonnet")
        self.assertIsNone(pick(2, "balanced", []))

    def test_select_uses_cost_and_speed_not_just_rank(self):
        config = dataclasses.replace(self.config, models={**self.config.models, "swift": Model("swift", 3, 5, 2.5)})
        usable = config.by_strength()
        self.assertEqual(config.select(3, "balanced", usable), "swift")  # cheaper than opus
        self.assertEqual(config.select(2, "fast", usable), "swift")  # the quickest that qualifies

    def test_cap_limits_available_models(self):
        self.assertEqual(self.config.available(cap="sonnet"), ["haiku", "sonnet"])
        self.assertEqual(self.config.available(cap="sonnet")[-1], "sonnet")
        self.assertEqual(self.config.available()[-1], "fable")

    def test_clamp_never_raises_above_what_was_asked(self):
        self.assertEqual(self.config.clamp("fable", ["haiku", "sonnet"]), "sonnet")
        self.assertEqual(self.config.clamp("opus", ["haiku", "opus"]), "opus")
        # Nothing at or below it is usable: the weakest usable is the least bad.
        self.assertEqual(self.config.clamp("haiku", ["sonnet", "opus"]), "sonnet")

    def test_project_can_add_a_model_but_not_rerank_or_slip_under_the_cap(self):
        self.override({"models": {"saga": {"strength": 0.5, "cost": 9}, "fable": {"strength": 0}}})
        config = cfg.load()
        self.assertEqual(config.models["fable"].strength, 4)
        self.assertEqual((config.models["saga"].project, config.models["fable"].trusted), (True, True))
        self.assertIn("saga", config.available())
        self.assertEqual(config.available(cap="sonnet"), ["haiku", "sonnet"])

    def test_the_users_own_file_is_trusted(self):
        self.override({"models": {"saga": {"strength": 1.5, "speed": 3, "cost": 1.5}, "opus": {"strength": 3.5}}},
                      user=True)
        self.override({"models": {"saga": {"strength": 9}}})  # a project can't re-rank the user's model
        config = cfg.load()
        self.assertEqual((config.models["saga"].strength, config.models["opus"].strength), (1.5, 3.5))
        self.assertTrue(config.models["saga"].trusted)
        self.assertEqual(config.available(cap="sonnet"), ["haiku", "saga", "sonnet"])

    def test_only_the_users_own_file_can_add_prices(self):
        self.override({"prices": {"Claude-Saga-1": {"input": 2, "output": 10, "cache_read": "x"},
                                  "claude-opus-5-5": {"output": 30}, "bad": 5}}, user=True)
        self.override({"prices": {"claude-haiku-5-5": {"input": 999}, "claude-proj-1": {"input": 1}}})
        config = cfg.load()
        self.assertEqual(config.price("claude-saga-1"), {"input": 2, "output": 10})
        self.assertEqual(config.price("claude-opus-5-5")["output"], 30)
        self.assertEqual(config.price("claude-opus-5-5")["input"],  # merged key by key
                         cfg.base_config()["prices"]["claude-opus-5-5"]["input"])
        self.assertNotIn("bad", config.prices)
        self.assertEqual(config.price("claude-haiku-5-5")["input"],
                         cfg.base_config()["prices"]["claude-haiku-5-5"]["input"])
        self.assertIsNone(config.price("claude-proj-1"))

    def test_malformed_override_files_are_ignored(self):
        self.override({"models": "nope", "levels": {"lookup": {"needs": "x", "effort": "ludicrous"},
                                                    "complex": {"effort": "max"}, "bogus": {"needs": 9}},
                       "agents": {"a": {"model": 5, "level": ["x"], "route": "no"}, "b": [1]},
                       "routing_mode": 5})
        config = cfg.load()
        self.assertEqual(config.levels[Level.lookup], LevelSpec(1, "medium"))
        self.assertEqual(config.levels[Level.complex].effort, "max")
        self.assertNotIn("bogus", config.levels)
        self.assertEqual({k: v for k, v in config.rules.items() if k in ("a", "b")}, {"a": Rule()})
        self.assertEqual(settings.resolve(config).mode, "auto")
        self.assertEqual(sorted(p.split(": ", 1)[1].split(" ")[0] for p in config.problems),
                         ["agents.a.level", "agents.a.model", "agents.a.route", "agents.b", "levels.bogus",
                          "levels.lookup.effort", "levels.lookup.needs", "models", "routing_mode"])
        (self.project / ".claude" / "model-router.json").write_text("{not json")
        self.assertEqual(len(cfg.load().models), 4)

    def test_a_project_file_may_raise_a_level_but_never_lower_it(self):
        self.override({"levels": {"complex": {"needs": 2, "effort": "low"}, "hardest": {"needs": 4, "effort": "max"},
                                  "lookup": {"effort": None}, "standard": {"needs": 2, "effort": "medium"}}})
        registry_ = cfg.load()
        levels, base = registry_.levels, cfg.shipped().levels
        self.assertEqual(levels[Level.complex], base[Level.complex])  # both lowerings dropped, without an error
        self.assertEqual(levels[Level.hardest], LevelSpec(4, "max"))
        self.assertEqual(levels[Level.lookup], base[Level.lookup])
        self.assertEqual(levels[Level.standard], base[Level.standard])
        self.assertEqual(registry_.problems, ())
        self.assertEqual([(h.kind, h.name) for h in registry_.held],
                         [("level", "complex"), ("level", "complex"), ("level", "lookup")])
        d = self.decide("general-purpose", HARD)
        self.assertEqual((d["model"], d["effort"]), ("opus", "high"))

    def test_the_floor_a_project_file_cannot_pass_is_the_users_own_value(self):
        self.override({"levels": {"complex": {"effort": "xhigh", "needs": 4}}}, user=True)
        self.override({"levels": {"complex": {"effort": "high", "needs": 3}, "hardest": {"effort": "high"}}})
        levels = cfg.load().levels
        self.assertEqual(levels[Level.complex], LevelSpec(4, "xhigh"))
        self.assertEqual(levels[Level.hardest].effort, "xhigh")
        self.override({"levels": {"complex": {"effort": None}}}, user=True)
        self.override({"levels": {"complex": {"effort": "high"}}})
        self.assertEqual(cfg.load().levels[Level.complex].effort, "high")  # raises a level the user left open

    def test_non_finite_numbers_are_rejected_in_every_file(self):
        nan, inf = float("nan"), float("inf")
        spec = {"levels": {"complex": {"needs": nan}, "hardest": {"needs": inf}, "standard": {"needs": 10 ** 400}},
                "models": {"saga": {"strength": inf, "speed": nan, "cost": 2}}, "prices": {"claude-x-1": {"input": inf}}}
        for user in (True, False):
            with self.subTest(user=user):
                self.override(spec, user=user)
                config = cfg.load()
                self.assertEqual(config.levels, cfg.shipped().levels)
                self.assertNotIn("saga", config.models)
                self.assertNotIn("claude-x-1", config.prices)
                self.assertEqual(self.decide("general-purpose", HARD)["model"], "opus")
                for where in ("levels.complex.needs", "levels.hardest.needs", "levels.standard.needs",
                              "models.saga.strength", "models.saga.speed", "prices.claude-x-1.input"):
                    self.assertTrue(any(f"{where} is not a finite number" in p for p in config.problems), where)
                data = json.loads(self.out("models", "--json"))
                self.assertEqual(data["config_problems"], list(config.problems))
                self.assertIn("Left out of the override files", self.out("models"))

    def test_a_project_model_name_cannot_capture_a_trusted_models_ids(self):
        self.override({"models": {"claude": {"strength": 0.5}, "opus-5": {"strength": 9}, "saga": {"strength": 2}}})
        config = cfg.load()
        self.assertEqual(config.family("claude-opus-5-5"), "opus")
        self.assertEqual([m for m in ("claude", "opus-5", "saga") if m in config.models], ["claude", "saga"])
        self.assertEqual([h.name for h in config.held], ["opus-5"])

    def test_needs_never_fall_as_the_level_rises(self):
        shipped = [cfg.shipped().levels[level].needs for level in cfg.LEVELS]
        self.assertEqual(shipped, [1, 2, 3, 3, 4])
        self.override({"levels": {"lookup": {"needs": 3}}})  # a raise lifts every level above it
        levels = cfg.load().levels
        self.assertEqual([levels[level].needs for level in cfg.LEVELS], [3, 3, 3, 3, 4])
        self.assertEqual(self.decide("general-purpose", BUILD)["model"], "opus")  # standard needs 3 now
        self.override({"levels": {"hardest": {"needs": 1}}}, user=True)  # a level can't need less than the one below
        self.override({"levels": {}})
        self.assertEqual(cfg.load().levels[Level.hardest].needs, 3)

    def test_an_alias_that_names_no_model_is_dropped_and_listed(self):
        base = {**cfg.base_config(), "aliases": {"best": "opus", "top": "ghost"}}
        reg = cfg.build(base)
        self.assertEqual((reg.family("best"), reg.family("top")), ("opus", None))
        self.assertEqual(reg.problems, ("aliases.top names no model, dropped",))

    def test_a_rule_a_project_file_cannot_apply_is_held_back_and_listed(self):
        self.override({"agents": {"quiet": {"route": False, "level": "complex"}}, "models": {"opus-x": {"strength": 9}}})
        reg = cfg.load()
        self.assertEqual([(h.kind, h.name) for h in reg.held], [("model", "opus-x"), ("rule", "quiet")])
        self.assertEqual(reg.rules["quiet"], Rule(floor=Level.complex))
        data = json.loads(self.out("models", "--json"))
        self.assertEqual([(h["kind"], h["name"]) for h in data["held_back"]], [("model", "opus-x"), ("rule", "quiet")])
        self.assertIn("- rule quiet: route false is ignored", self.out("models"))
        self.assertNotIn("config_problems", data)


class Allowlist(Sandbox):
    """availableModels and deniedModels, from the settings layers the mod sends or the files."""

    def setUp(self):
        super().setUp()
        self.config = cfg.load()

    def permitted(self, **layers):
        return allowlist.from_layers(layers).permitted(self.config)

    def test_a_managed_list_applies_alone_else_the_other_sources_are_concatenated(self):
        self.assertEqual(self.permitted(user={"availableModels": ["opus"]}, local={"availableModels": ["haiku"]},
                                        flag={"availableModels": ["claude-sonnet-5"]}), {"haiku", "sonnet", "opus"})
        self.assertEqual(self.permitted(policy={"availableModels": ["sonnet"]}, user={"availableModels": ["opus"]}),
                         {"sonnet"})
        self.assertEqual(self.permitted(policy={"availableModels": []}), set())

    def test_denied_models_count_only_in_policy_and_block_a_family_only_by_name(self):
        self.assertEqual(self.permitted(policy={"deniedModels": ["Opus"]}), {"haiku", "sonnet", "fable"})
        self.assertEqual(len(self.permitted(policy={"deniedModels": ["claude-opus-5-5", "sonnet[1m]", "best",
                                                                     "opusplan", "default"]})), 4)
        self.assertEqual(len(self.permitted(user={"deniedModels": ["opus"]}, project={"deniedModels": ["haiku"]})), 4)

    def test_a_family_deny_also_blocks_a_project_model_that_resolves_to_that_family(self):
        self.override({"models": {"best": {"strength": 3}}})
        config = cfg.load()
        self.assertEqual(config.family("best"), "opus")
        denied = allowlist.from_layers({"policy": {"deniedModels": ["opus"]}}).permitted(config)
        self.assertEqual(denied, {"haiku", "sonnet", "fable"})
        kept = allowlist.from_layers({"policy": {"deniedModels": ["claude-opus-5-5"]}}).permitted(config)
        self.assertEqual(kept, {"haiku", "sonnet", "fable", "opus", "best"})

    def test_entries_that_are_not_strings_match_nothing(self):
        self.assertEqual(self.permitted(policy={"availableModels": ["sonnet", {"id": "claude-opus-5-5"}],
                                                "deniedModels": [{"id": "sonnet"}]}), {"sonnet"})

    def test_junk_engine_layers_count_as_unset(self):
        sent = {"policy": {"deniedModels": [1, None]}, "user": "x", "project": {"availableModels": "opus"},
                "bogus": {"a": 1}}
        self.assertEqual(allowlist.read("s1", self.project, sent).permitted(self.config), set(self.config.models))
        self.assertIsNone(allowlist.remember("s1", "not an object"))

    def test_only_the_four_keys_are_stored(self):
        modpath.start({"session_id": "s1", "settings": {"policy": {"availableModels": ["opus"], "env": {"TOKEN": "x"}}}})
        self.assertNotIn("TOKEN", (self.data / "allowlist.json").read_text(encoding="utf-8"))

    def test_managed_drop_ins_merge_in_name_order(self):
        (self.home / "settings.json").write_text(json.dumps({"availableModels": ["haiku"]}))
        drop_ins = self.home / "managed-settings.d"
        drop_ins.mkdir()
        (drop_ins / "20-models.json").write_text(json.dumps({"availableModels": ["sonnet", "opus"],
                                                              "deniedModels": ["opus"]}))
        (drop_ins / ".30-hidden.json").write_text(json.dumps({"availableModels": ["fable"]}))
        (drop_ins / "40-notes.txt").write_text(json.dumps({"availableModels": ["fable"]}))
        self.assertEqual(allowlist.read(None, self.project).permitted(self.config), {"sonnet"})
        self.managed.write_text(json.dumps({"availableModels": ["fable"]}))
        (drop_ins / "10-more.json").write_text(json.dumps({"availableModels": ["sonnet"]}))
        self.assertEqual(allowlist.read(None, self.project).permitted(self.config), {"sonnet", "fable"})
        for path in (self.managed, drop_ins / "10-more.json", drop_ins / "20-models.json"):
            path.unlink()
        (drop_ins / "50-empty.json").write_text(json.dumps({"availableModels": []}))
        self.assertEqual(allowlist.read(None, self.project).permitted(self.config), set())
        (drop_ins / "60-a.json").write_text(json.dumps({"availableModelsMatch": "exact"}))
        self.assertTrue(allowlist.read(None, self.project).exact)
        (drop_ins / "70-b.json").write_text(json.dumps({"availableModelsMatch": "prefix"}))
        self.assertFalse(allowlist.read(None, self.project).exact)

    def test_the_local_settings_file_counts(self):
        (self.project / ".claude").mkdir()
        (self.project / ".claude" / "settings.local.json").write_text(json.dumps({"availableModels": ["haiku"]}))
        self.assertEqual(allowlist.read(None, self.project).permitted(self.config), {"haiku"})

    @unittest.skipIf(os.name == "nt", "on Windows the local file stays beside settings.json")
    def test_the_local_file_is_read_at_the_repository_root(self):
        (self.project / ".git").mkdir()
        sub = self.project / "pkg" / ".claude"
        sub.mkdir(parents=True)
        (self.project / ".claude").mkdir()
        (self.project / ".claude" / "settings.local.json").write_text(json.dumps({"availableModels": ["opus"]}))
        (self.project / ".claude" / "settings.json").write_text(json.dumps({"availableModels": ["fable"]}))
        (sub / "settings.local.json").write_text(json.dumps({"availableModels": ["haiku"]}))  # the root's wins
        (sub / "settings.json").write_text(json.dumps({"availableModels": ["sonnet"]}))  # read where the session is
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.project / "pkg")  # the session started in pkg
        self.assertEqual(allowlist.read(None, self.project / "pkg").permitted(self.config), {"sonnet", "opus"})

    @unittest.skipIf(os.name == "nt", "on Windows the local file stays beside settings.json")
    def test_a_worktree_reads_the_local_file_at_the_main_checkout(self):
        (self.project / ".git" / "worktrees" / "w").mkdir(parents=True)
        (self.project / ".git" / "worktrees" / "w" / "commondir").write_text("../..\n")
        tree = Path(self.tmp.name) / "w"
        tree.mkdir()
        (tree / ".git").write_text(f"gitdir: {self.project / '.git' / 'worktrees' / 'w'}\n")
        (self.project / ".claude").mkdir()
        (self.project / ".claude" / "settings.local.json").write_text(json.dumps({"availableModels": ["opus"]}))
        self.assertEqual(allowlist.read(None, tree).permitted(self.config), {"opus"})

    @unittest.skipIf(os.name == "nt", "on Windows the local file stays beside settings.json")
    def test_the_local_file_stays_beside_settings_json_when_the_root_cannot_be_trusted(self):
        (self.project / ".git").mkdir()
        (self.project / ".claude").mkdir()
        (self.project / ".claude" / "settings.local.json").write_text(json.dumps({"availableModels": ["opus"]}))
        sub = self.project / "pkg"
        (sub / ".claude").mkdir(parents=True)
        (sub / ".claude" / "settings.local.json").write_text(json.dumps({"availableModels": ["haiku"]}))
        self.assertEqual(allowlist.read(None, sub).permitted(self.config), {"opus"})
        with mock.patch.object(os, "getuid", return_value=os.getuid() + 1):
            self.assertEqual(allowlist.read(None, sub).permitted(self.config), {"haiku"})  # not owned by this user
        with mock.patch.object(Path, "home", return_value=self.project):
            self.assertEqual(allowlist.read(None, sub).permitted(self.config), {"haiku"})  # a root that is the home folder

    def test_the_windows_managed_folder_ignores_ProgramFiles(self):
        with mock.patch.dict(os.environ, {"ProgramFiles": r"D:\Planted"}), mock.patch.object(allowlist, "MANAGED_ROOT", None):
            self.assertEqual(allowlist.managed_root("win32"), Path(r"C:\Program Files\ClaudeCode"))
            self.assertEqual(allowlist.managed_root("darwin"), Path("/Library/Application Support/ClaudeCode"))
            self.assertEqual(allowlist.managed_root("linux"), Path("/etc/claude-code"))

    def test_a_blanked_engine_policy_never_hides_the_managed_files(self):
        self.managed.write_text(json.dumps({"deniedModels": ["opus"], "availableModels": ["sonnet", "opus", "haiku"]}))
        modpath.start({"session_id": "s1", "settings": {"policy": {}, "user": {}}})  # another mod answered {}
        self.assertEqual(allowlist.read("s1", self.project).permitted(self.config), {"sonnet", "haiku"})
        modpath.start({"session_id": "s1"})  # a later read failed: the stored layers stand, the files still apply
        self.assertEqual(allowlist.read("s1", self.project).permitted(self.config), {"sonnet", "haiku"})
        self.assertEqual(allowlist.read(None, self.project, {"policy": {}}).permitted(self.config), {"sonnet", "haiku"})

    def test_the_engine_policy_and_the_managed_files_both_have_to_permit(self):
        self.managed.write_text(json.dumps({"availableModels": ["opus", "haiku"], "deniedModels": ["haiku"]}))
        sent = {"policy": {"availableModels": ["claude-opus-5-5", "sonnet"], "deniedModels": ["fable"]}}
        self.assertEqual(allowlist.read(None, self.project, sent).permitted(self.config), {"opus"})
        self.assertFalse(allowlist.read(None, self.project, {"policy": {"availableModels": ["haiku"]}}).permitted(self.config))
        self.assertFalse(allowlist.read(None, self.project, {"policy": {"availableModels": ["haiku"]}}).permits_id(self.config, "opus", "claude-opus-5-5"))
        self.assertFalse(allowlist.read(None, self.project, {"policy": {}}).exact)
        self.managed.write_text(json.dumps({"availableModelsMatch": "exact"}))
        self.assertTrue(allowlist.read(None, self.project, {"policy": {"availableModelsMatch": "prefix"}}).exact)
        self.managed.write_text(json.dumps({"modelOverrides": {"claude-opus-5-5": "arn:x"}}))
        self.assertTrue(allowlist.read(None, self.project, {"policy": {}}).overrides)

    def test_deny_entries_match_after_trimming_and_by_family_name(self):
        every = {"haiku", "sonnet", "opus", "fable"}
        for entry in ("opus ", " opus", "claude-opus", "OPUS\t"):
            self.assertEqual(self.permitted(policy={"deniedModels": [entry]}), every - {"opus"}, repr(entry))
        for entry in ("claude-opus-5-5", "sonnet[1m]", "claude-opus-5"):
            self.assertEqual(self.permitted(policy={"deniedModels": [entry]}), every, repr(entry))
        self.assertFalse(allowlist.from_layers({"policy": {"deniedModels": ["claude-opus"]}}).permits_id(self.config, "opus", "claude-opus-5-5"))
        self.assertFalse(allowlist.from_layers({"policy": {"deniedModels": [" opus "]}}).permits_id(self.config, "opus", "claude-opus-5-5"))
        self.assertTrue(allowlist.from_layers({"policy": {"availableModels": [" opus "]}}).permits_id(self.config, "opus", "claude-opus-5-5"))

    def test_a_specific_entry_turns_off_its_familys_wildcard(self):
        # model-config.md, Merge behavior: with ["sonnet", "claude-sonnet-4-5"] only what claude-sonnet-4-5 matches.
        def permits(entries, name, model_id, source="policy"):
            return allowlist.from_layers({source: {"availableModels": entries}}).permits_id(self.config, name, model_id)

        pinned = ["sonnet", "claude-sonnet-4-5"]
        self.assertFalse(permits(pinned, "sonnet", "claude-sonnet-5-5"))
        self.assertFalse(permits(pinned, "sonnet", "claude-sonnet-5-5", source="user"))
        self.assertTrue(permits(pinned, "sonnet", "claude-sonnet-4-5-20250929"))
        self.assertTrue(permits(["sonnet"], "sonnet", "claude-sonnet-5-5"))
        self.assertTrue(permits(["sonnet", "sonnet[1m]", "best"], "sonnet", "claude-sonnet-5-5"))
        self.assertTrue(permits(["sonnet", "claude-opus-4-6"], "sonnet", "claude-sonnet-5-5"))
        self.assertFalse(permits(["opus", "claude-opus-4-6"], "opus", "claude-opus-5-5"))
        self.assertFalse(permits(["sonnet", "anthropic.claude-sonnet-5-5-v1:0"], "sonnet", "claude-sonnet-5-5"))
        # An alias that only contains a family's name names no version.
        for alias in ("opusplan", "default", "best"):
            self.assertTrue(permits(["opus", alias], "opus", "claude-opus-5-5"), alias)
        self.assertFalse(permits(["opus", "us.anthropic.claude-opus"], "opus", "claude-opus-5-5"))
        # The family keeps a permitted version, which its alias moves to, so it stays routable.
        self.assertEqual(self.permitted(policy={"availableModels": pinned}), {"sonnet"})
        managed = allowlist.from_layers({}, floor={"availableModels": pinned})
        self.assertFalse(managed.permits_id(self.config, "sonnet", "claude-sonnet-5-5"))

    def test_max_effort_level_caps_each_family_at_the_lowest_source(self):
        # settings-reference.md, maxEffortLevel: the lowest of any scope applies; a modelSettings entry replaces
        # that source's top-level cap for the model; "max" sets none.
        def caps(floor=None, **layers):
            found = allowlist.from_layers(layers, floor=floor)
            return {n: found.effort_cap(self.config, n) for n in ("haiku", "sonnet", "opus", "fable")}

        self.assertEqual(caps(), dict.fromkeys(("haiku", "sonnet", "opus", "fable")))
        self.assertEqual(caps(user={"maxEffortLevel": "high"}, project={"maxEffortLevel": "medium"}),
                         dict.fromkeys(("haiku", "sonnet", "opus", "fable"), "medium"))
        own = {"maxEffortLevel": "low", "modelSettings": {"claude-opus-5-5": {"maxEffortLevel": "max"},
                                                          "sonnet": {"maxEffortLevel": "medium"}, "x": "junk"}}
        self.assertEqual(caps(user=own), {"haiku": "low", "sonnet": "medium", "opus": None, "fable": "low"})
        self.assertEqual(caps(user=own, policy={"maxEffortLevel": "high"})["opus"], "high")
        self.assertEqual(caps(floor={"maxEffortLevel": "medium"}, user={"maxEffortLevel": "max"})["fable"], "medium")
        self.assertEqual(caps(user={"maxEffortLevel": "turbo", "modelSettings": ["opus"]})["opus"], None)
        (self.home / "settings.json").write_text(json.dumps({"maxEffortLevel": "medium"}))
        self.assertEqual(allowlist.read(None, self.project).effort_cap(self.config, "opus"), "medium")
        drop_ins = self.home / "managed-settings.d"
        drop_ins.mkdir()
        (drop_ins / "10-a.json").write_text(json.dumps({"modelSettings": {"haiku": {"maxEffortLevel": "low"}}}))
        (drop_ins / "20-b.json").write_text(json.dumps({"modelSettings": {"opus": {"maxEffortLevel": "low"}}}))
        read = allowlist.read(None, self.project)
        self.assertEqual([read.effort_cap(self.config, n) for n in ("haiku", "opus", "sonnet")], ["low", "low", "medium"])

    def test_a_managed_file_that_is_there_but_unreadable_permits_no_models(self):
        self.assertEqual(allowlist.read(None, self.project).permitted(self.config), set(self.config.models))  # absent
        drop_ins = self.home / "managed-settings.d"
        drop_ins.mkdir()
        (drop_ins / "50-bad.json").write_text("{not json")
        self.assertEqual(allowlist.read(None, self.project).permitted(self.config), set())
        (drop_ins / "50-bad.json").write_text("[]")
        self.assertEqual(allowlist.read(None, self.project, {"policy": {}}).permitted(self.config), set())  # the floor holds
        (drop_ins / "50-bad.json").write_text("   {\"availableModels\": ")  # content that stops short
        self.assertEqual(allowlist.read(None, self.project).permitted(self.config), set())

    def test_an_empty_or_blank_managed_file_and_a_folder_in_the_drop_ins_are_no_policy(self):
        everything = set(self.config.models)
        drop_ins = self.home / "managed-settings.d"
        drop_ins.mkdir()
        for text in ("", " \n\t "):
            self.managed.write_text(text)
            (drop_ins / "50-blank.json").write_text(text)
            self.assertEqual(allowlist.read(None, self.project).permitted(self.config), everything, repr(text))
        (drop_ins / "60-folder.json").mkdir()
        self.assertEqual(allowlist.read(None, self.project).permitted(self.config), everything)
        (drop_ins / "70-real.json").write_text(json.dumps({"deniedModels": ["opus"]}))
        self.assertEqual(allowlist.read(None, self.project).permitted(self.config), everything - {"opus"})

    def test_a_deny_list_over_the_cap_leaves_no_model_open_while_an_allow_list_is_only_cut(self):
        for denied in (["opus"] + ["x"] * allowlist.MAX_ENTRIES, ["opus", "y" * (allowlist.MAX_CHARS + 1)]):
            self.assertEqual(self.permitted(policy={"deniedModels": denied}), set())
        self.assertEqual(self.permitted(policy={"deniedModels": ["x"] * allowlist.MAX_ENTRIES}),
                         set(self.config.models))  # exactly at the cap is fine
        self.assertEqual(self.permitted(user={"deniedModels": ["x"] * 300}), set(self.config.models))  # not policy: ignored
        self.assertEqual(self.permitted(user={"availableModels": ["z" * 300, "haiku"] + ["q"] * 300}), {"haiku"})
        self.managed.write_text(json.dumps({"deniedModels": ["opus"] + ["x"] * allowlist.MAX_ENTRIES}))
        self.assertEqual(allowlist.read(None, self.project).permitted(self.config), set())
        self.assertEqual(allowlist.read(None, self.project, {"policy": {}}).permitted(self.config), set())
        self.assertEqual(allowlist.read("s1", self.project, {"policy": {"deniedModels": ["x"] * 300}}).permitted(self.config), set())

    def test_a_failed_policy_read_never_leaves_the_users_lists_standing(self):
        self.managed.write_text(json.dumps({"availableModels": ["sonnet"]}))
        (self.home / "settings.json").write_text(json.dumps({"availableModels": ["opus"]}))
        modpath.start({"session_id": "s1", "settings": {"user": {"availableModels": ["opus"]}}})  # policy omitted
        found = allowlist.read("s1", self.project)
        self.assertEqual((found.permitted(self.config), found.source), ({"sonnet"}, "engine"))
        modpath.start({"session_id": "s2", "settings": {"policy": {"availableModels": ["haiku"]}}})  # the others omitted
        self.assertEqual(allowlist.read("s2", self.project).permitted(self.config), set())  # haiku and sonnet share nothing

    def test_a_turn_remembers_the_layers_too(self):
        modpath.turn({"session_id": "s2", "text": LOOKUP, "settings": {"policy": {"availableModels": ["opus"]}}})
        self.assertEqual(allowlist.read("s2", self.project).permitted(self.config), {"opus"})

    def test_lists_are_capped_and_overrides_are_stored_as_a_flag(self):
        sent = {"user": {"availableModels": ["x" * 500] + ["opus"] * 300, "modelOverrides": {"a": {"b": [1] * 50}}},
                "project": {"modelOverrides": {}}}
        layers = allowlist.remember("s1", sent)
        self.assertEqual(layers["user"]["availableModels"], ["opus"] * allowlist.MAX_ENTRIES)
        self.assertEqual((layers["user"]["modelOverrides"], layers["project"]["modelOverrides"]), (True, False))
        self.assertNotIn('"a"', (self.data / "allowlist.json").read_text(encoding="utf-8"))

    def test_a_failing_store_does_not_stop_remembering_the_layers_for_this_call(self):
        sent = {"policy": {"availableModels": ["opus"]}}
        with mock.patch.object(store, "save_entry", side_effect=ValueError("bad")):
            layers = allowlist.remember("s1", sent)
        self.assertEqual(layers["policy"]["availableModels"], ["opus"])

    def test_reading_the_sent_layers_stores_nothing_until_the_mod_hook_remembers_them(self):
        sent = {"policy": {"availableModels": ["opus"]}}
        found = allowlist.read("s1", self.project, sent)
        self.assertEqual(found.permitted(self.config), {"opus"})
        self.assertFalse((self.data / "allowlist.json").exists())
        modpath.spawn({"session_id": "s1", "cwd": str(self.project), "settings": sent, "kind": "subagent",
                         "tool_input": {"subagent_type": "general-purpose", "prompt": LOOKUP}})
        self.assertEqual(allowlist.read("s1", self.project).permitted(self.config), {"opus"})

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_a_settings_file_that_is_not_a_small_regular_file_is_ignored(self):
        os.symlink("/dev/zero", self.home / "settings.json")
        (self.project / ".claude").mkdir()
        (self.project / ".claude" / "settings.json").write_text(json.dumps({"availableModels": ["haiku"], "pad": "x" * allowlist.MAX_BYTES}))
        self.assertEqual(allowlist.read(None, self.project).permitted(self.config), set(self.config.models))
        real = self.project / "real.json"
        real.write_text(json.dumps({"availableModels": ["haiku"]}))
        (self.home / "settings.json").unlink()
        os.symlink(real, self.home / "settings.json")  # a symlink to a regular file is how dotfile managers work
        self.assertEqual(allowlist.read(None, self.project).permitted(self.config), {"haiku"})

    @unittest.skipIf(os.name == "nt", "on Windows the local file stays beside settings.json")
    def test_a_crafted_git_file_does_not_move_the_local_file(self):
        other = Path(self.tmp.name) / "other"
        (other / ".git").mkdir(parents=True)
        (other / ".claude").mkdir()
        (other / ".claude" / "settings.local.json").write_text(json.dumps({"availableModels": ["opus"]}))
        (other / "stuff" / "w").mkdir(parents=True)
        (other / "stuff" / "w" / "commondir").write_text("../../.git\n")  # not under a worktrees folder
        tree = Path(self.tmp.name) / "tree"
        tree.mkdir()
        (tree / ".git").write_text(f"gitdir: {other / 'stuff' / 'w'}\n")
        self.assertEqual(allowlist.read(None, tree).permitted(self.config), set(self.config.models))
        (other / "elsewhere" / ".git").mkdir(parents=True)
        (other / "elsewhere" / ".claude").mkdir()
        (other / "elsewhere" / ".claude" / "settings.local.json").write_text(json.dumps({"availableModels": ["opus"]}))
        (other / ".git" / "worktrees" / "w").mkdir(parents=True)
        (other / ".git" / "worktrees" / "w" / "commondir").write_text("../../../elsewhere/.git\n")  # not the checkout's own .git
        (tree / ".git").write_text(f"gitdir: {other / '.git' / 'worktrees' / 'w'}\n")
        self.assertEqual(allowlist.read(None, tree).permitted(self.config), set(self.config.models))

    def test_nothing_left_prints_no_none_in_the_reports(self):
        (self.home / "settings.json").write_text(json.dumps({"availableModels": ["some-other-model"]}))
        snap = settings.Snapshot()
        self.assertNotIn("None", report.policy(snap))
        self.assertIn("left as it is", report.policy(snap))
        text = report.explain(snap, LOOKUP)
        self.assertNotIn("None", text)
        self.assertIn("left as it is", text)
        self.options(routing_mode="advise")
        self.assertIsNone(hooks.user_prompt({"session_id": "s1", "prompt": HARD}))

    def test_a_later_payload_without_policy_keeps_the_stored_policy(self):
        policy = {"availableModels": ["sonnet"]}  # server-managed: no managed file exists
        modpath.start({"session_id": "s1", "settings": {"policy": policy, "user": {"availableModels": ["opus"]}}})
        later = {"user": {"availableModels": ["opus"]}, "project": {}, "local": {}, "flag": {}}  # the policy read failed
        modpath.turn({"session_id": "s1", "text": LOOKUP, "settings": later})
        self.assertEqual(allowlist.read("s1", self.project).permitted(self.config), {"sonnet"})
        modpath.spawn({"session_id": "s1", "cwd": str(self.project), "parent_model": "claude-opus-5-5",
                         "tool_input": {"prompt": LOOKUP}, "settings": later})
        self.assertEqual(store.load_entry("allowlist", "s1")["layers"]["policy"], policy)
        self.assertEqual(allowlist.read("s1", self.project).permitted(self.config), {"sonnet"})
        self.assertEqual(self.ledger()[-1]["model"], "sonnet")
        newer = {"policy": {"availableModels": ["haiku"]}}  # a policy that is sent again replaces the stored one
        self.assertEqual(allowlist.read("s1", self.project, newer).permitted(self.config), {"haiku"})

    def test_a_deeply_nested_settings_file_is_unreadable_not_a_crash(self):
        deep = "[" * 400000
        (self.home / "settings.json").write_text(deep)
        self.assertEqual(allowlist.read(None, self.project).permitted(self.config), set(self.config.models))
        self.managed.write_text(deep)
        self.assertEqual(allowlist.read(None, self.project).permitted(self.config), set())

    def test_a_malformed_policy_list_permits_nothing_while_a_soft_one_is_unset(self):
        everything = set(self.config.models)
        for key in ("deniedModels", "availableModels"):
            for junk in ("opus", None, 5, {"a": 1}):
                self.assertEqual(self.permitted(policy={key: junk}), set(), (key, junk))
                self.assertEqual(self.permitted(user={key: junk}, project={key: junk}), everything, (key, junk))
        self.managed.write_text(json.dumps({"deniedModels": "opus"}))
        self.assertEqual(allowlist.read(None, self.project).permitted(self.config), set())
        self.assertEqual(allowlist.read(None, self.project, {"policy": {}}).permitted(self.config), set())

    def test_engine_layers_win_over_files_for_the_whole_session(self):
        (self.home / "settings.json").write_text(json.dumps({"availableModels": ["haiku"]}))
        modpath.start({"session_id": "s1", "settings": {"policy": {"availableModels": ["sonnet"]}}})
        self.assertEqual(allowlist.read("s1", self.project).permitted(self.config), {"sonnet"})
        self.assertEqual(self.decide("general-purpose", LOOKUP)["model"], "sonnet")  # a later hook of the session

    def test_a_failed_engine_read_falls_back_to_files(self):
        (self.home / "settings.json").write_text(json.dumps({"availableModels": ["haiku"]}))
        modpath.start({"session_id": "s1"})
        self.assertEqual(allowlist.read("s1", self.project).source, "files")
        modpath.start({"session_id": "s1", "settings": {"policy": {"availableModels": ["opus"]}}})
        modpath.spawn({"session_id": "s1", "cwd": str(self.project), "parent_model": "claude-opus-5-5",
                         "tool_input": {"prompt": LOOKUP}})  # this read failed: the latest set stands
        self.assertEqual(allowlist.read("s1", self.project).permitted(self.config), {"opus"})

    def test_nothing_left_leaves_the_call_as_it_is(self):
        (self.home / "settings.json").write_text(json.dumps({"availableModels": ["some-other-model"]}))
        self.assertEqual(self.config.available(allowed=allowlist.read(None, self.project)), [])
        self.session_on("claude-opus-5-5")
        self.assertIsNone(self.pre("general-purpose", LOOKUP))
        row = self.ledger()[-1]
        self.assertEqual((row["action"], row["model"]), ("keep", "opus"))
        self.assertIn("availableModels", row["reason"])


class Options(Sandbox):
    def mode(self) -> str:
        return settings.resolve(cfg.load()).mode

    def test_defaults(self):
        self.assertEqual(settings.resolve(cfg.load()), settings.Settings())

    def test_the_mantle_switch_and_each_prompt_caching_switch_are_read(self):
        env = {"CLAUDE_CODE_USE_MANTLE": "1", "DISABLE_PROMPT_CACHING": "true", "DISABLE_PROMPT_CACHING_OPUS": "1",
               "DISABLE_PROMPT_CACHING_NEW_FAMILY": "yes", "DISABLE_PROMPT_CACHING_SONNET": "0", "DISABLE_PROMPT_CACHINGX": "1"}
        with mock.patch.dict(os.environ, env):
            options = settings.resolve()
        self.assertTrue(options.mantle)
        self.assertEqual(options.cache_off, ("", "new_family", "opus"))

    def test_mode_defaults_to_auto(self):
        self.assertEqual(self.mode(), "auto")
        for value, expected in (("advise", "advise"), ("off", "off"), ("AUTO ", "auto"), ("nonsense", "auto")):
            self.options(routing_mode=value)
            self.assertEqual(self.mode(), expected)

    def test_files_can_weaken_but_not_strengthen_the_mode(self):
        self.override({"routing_mode": "off"})
        self.assertEqual(self.mode(), "off")
        self.override({"routing_mode": "auto"})
        self.override({"routing_mode": "advise"}, user=True)
        self.assertEqual(self.mode(), "advise")
        self.options(routing_mode="off")
        self.assertEqual(self.mode(), "off")

    def test_environment_facts(self):
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="opus", CLAUDE_CODE_SUBAGENT_MODEL_FORCE="yes",
                     CLAUDE_CODE_USE_VERTEX="1", max_tier="nonsense", main_effort="false")
        os.environ.update(ANTHROPIC_API_KEY="sk-test", ANTHROPIC_BASE_URL="https://gateway.example")
        s = settings.resolve(cfg.load())
        self.assertEqual((s.subagent_model, s.force, s.cloud, s.cap, s.main_effort, s.verify_gate),
                         ("opus", True, "vertex", None, False, True))
        self.assertEqual((s.api_key, s.base_url), ("sk-test", True))
        self.assertNotIn("sk-test", repr(s))

    def test_skill_commands_read_options_recorded_at_session_start(self):
        values = {"max_tier": "haiku", "priority": "quality", "agent_pins": "override", "routing_mode": "advise",
                  "main_effort": "false", "update_check": "off", "verify_gate": "false",
                  "strong_agent_gate": "false"}
        self.options(**values)
        hooks.session_start({"session_id": "s1"})
        for name in values:
            del os.environ[f"CLAUDE_PLUGIN_OPTION_{name.upper()}"]
        config = cfg.load()
        self.assertEqual(settings.resolve(config), settings.Settings())
        self.assertEqual(settings.resolve(config, recorded=True), settings.Settings(
            mode="advise", routing_mode="advise", priority="quality", cap="haiku", pins="override",
            main_effort=False, update_check=False, verify_gate=False, strong_agent_gate=False))
        self.assertIn("Cap: **haiku**", self.out("policy"))

    def test_policy_shows_the_routing_mode_the_user_set(self):
        self.options(routing_mode="off")
        hooks.session_start({"session_id": "s1"})
        del os.environ["CLAUDE_PLUGIN_OPTION_ROUTING_MODE"]
        self.assertIn("Routing: **off**", self.out("policy"))


class ConfigSchema(Sandbox):
    """config/models.json and model-router.json overrides carry a `schema` version; see
    docs/ARCHITECTURE.md "Compatibility"."""

    def test_the_shipped_config_has_the_current_schema_and_validates(self):
        base = cfg.base_config()
        self.assertEqual(base["schema"], cfg.CONFIG_SCHEMA)
        shipped = cfg.shipped()
        self.assertEqual((shipped.problems, shipped.held), ((), ()))
        for name, spec in base["models"].items():
            model = shipped.models[name]
            self.assertEqual((model.strength, model.speed, model.cost), (spec["strength"], spec["speed"], spec["cost"]), name)
        for name, spec in base["levels"].items():
            self.assertEqual(shipped.levels[name], LevelSpec(spec["needs"], spec["effort"]), name)
        self.assertEqual(shipped.prices.keys(), base["prices"].keys())
        self.assertEqual(shipped.aliases, base["aliases"])

    def test_an_override_without_a_schema_still_loads(self):
        self.override({"levels": {"complex": {"effort": "xhigh"}}}, user=True)
        self.override({"agents": {"my-agent": {"level": "lookup"}}})
        config = cfg.load()
        self.assertEqual((config.levels[Level.complex].effort, config.rules["my-agent"].floor),
                         ("xhigh", Level.lookup))
        self.assertEqual(config.newer, ())

    def test_an_override_from_a_newer_release_is_read_as_far_as_it_is_understood_and_named(self):
        self.override({"schema": cfg.CONFIG_SCHEMA + 1, "routing_mode": "off",
                       "levels": {"complex": {"effort": "xhigh"}}, "a_future_key": {"x": 1}}, user=True)
        self.override({"schema": cfg.CONFIG_SCHEMA + 1, "agents": {"my-agent": {"level": "lookup"}}})
        config = cfg.load()
        self.assertEqual((config.routing_mode, config.levels[Level.complex].effort), ("off", "xhigh"))
        self.assertEqual(config.rules["my-agent"].floor, Level.lookup)
        newer = [str(self.home / "model-router.json"), str(self.project / ".claude" / "model-router.json")]
        self.assertEqual([str(p) for p in config.newer], newer)
        self.assertEqual(json.loads(self.out("models", "--json"))["newer_config"], newer)
        self.assertIn("newer Model Router", self.out("models"))


class AgentDiscovery(Sandbox):
    def test_project_user_and_builtin_agents(self):
        self.agent("mine", "sonnet")
        self.agent("theirs", None, self.home / "agents", extra="tools: Read, Grep\neffort: high\n")
        self.assertEqual(agents.find("mine", self.project)["model"], "sonnet")
        found = agents.find("theirs", self.project)
        self.assertEqual((found["source"], found["model"], found["effort"]), ("user", None, "high"))
        self.assertEqual(agents.find("Explore", self.project)["source"], "builtin")
        self.assertIsNone(agents.find("nobody", self.project))

    def test_inherit_counts_as_no_model_and_project_shadows_builtin(self):
        self.agent("Explore", "inherit")
        found = agents.find("Explore", self.project)
        self.assertEqual((found["source"], found["model"]), ("project", None))

    def test_own_crew(self):
        self.assertIsNone(agents.find("model-router:architect", self.project)["model"])
        self.assertEqual(agents.find("model-router:scout", self.project)["model"], "haiku")
        self.assertIsNone(agents.find("model-router:nope", self.project))

    def test_other_plugin_by_install_record(self):
        install = self.home / "elsewhere" / "acme"
        self.agent("helper", None, install / "agents")
        self.agent("deep", "opus", install / "extra" / "agents")
        (self.home / "plugins").mkdir()
        (self.home / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {"acme@market": [{"installPath": str(install)}]}}))
        self.assertIsNone(agents.find("acme:helper", self.project)["model"])
        self.assertEqual(agents.find("acme:deep", self.project)["model"], "opus")
        self.assertIsNone(agents.find("other:helper", self.project))
        self.agent("vendored", "opus", install / "node_modules" / "pkg" / "agents")
        self.agent("tracked", "opus", install / ".git" / "agents")
        self.assertIsNone(agents.find("acme:vendored", self.project))  # dependency trees aren't walked
        self.assertIsNone(agents.find("acme:tracked", self.project))
        self.agent("kept", "opus", install / "data" / "agents")
        self.assertEqual(agents.find("acme:kept", self.project)["model"], "opus")
        (install / "docs").mkdir()
        for n in range(2000):
            (install / "docs" / f"{n}.md").write_text("")
        self.agent("late", "opus", install / "zz" / "agents")
        self.assertIsNone(agents.find("acme:late", self.project))  # the walk stops at 2000 .md files

    def test_other_plugin_by_folder_name(self):
        self.agent("helper", "haiku", self.home / "plugins" / "cache" / "market" / "acme" / "1.0" / "agents")
        self.assertEqual(agents.find("acme:helper", self.project)["model"], "haiku")

    def test_quoted_model(self):
        self.agent("quoted", '"opus"  # the strong one')
        self.assertEqual(agents.find("quoted", self.project)["model"], "opus")

    def test_a_file_saved_with_a_byte_order_mark(self):
        folder = self.project / ".claude" / "agents"
        folder.mkdir(parents=True)
        (folder / "bom.md").write_text("\ufeff---\nname: bom\nmodel: opus\n---\nbody\n", encoding="utf-8")
        self.assertEqual(agents.find("bom", self.project)["model"], "opus")


class Profile(unittest.TestCase):
    def level(self, prompt, agent="general-purpose"):
        return profile.profile_call({"prompt": prompt}, agent)["level"]

    def test_call_levels(self):
        self.assertEqual(self.level(LOOKUP), "lookup")
        self.assertEqual(self.level("run the test suite and report failures"), "lookup")
        self.assertEqual(self.level(BUILD), "standard")
        self.assertEqual(self.level("investigate why the checkout test is flaky"), "complex")
        self.assertEqual(self.level(HARD), "complex")
        self.assertEqual(self.level("review the auth module"), "complex")
        self.assertEqual(self.level("investigate why auth is flaky and redesign it across every service"), "hardest")

    def test_fixing_or_hunting_a_defect_is_complex_or_above(self):
        for brief in ("Fix the off-by-one in page_slice.", "Do not touch the tests, fix the parser.",
                      "investigate why the checkout test is flaky", "Debug why the job fails.",
                      "Figure out why it times out and fix it."):
            self.assertEqual(self.level(brief), "complex", brief)
        self.assertIn("fixes or hunts a defect",
                      profile.profile_call({"prompt": "Fix the off-by-one in page_slice."}, "general-purpose")["signals"])

    def test_words_that_only_look_like_a_fix_stay_put(self):
        for brief in ("Find the commit that landed the fix for the CSV export and give its hash.",
                      "Add a prefix to each key.", "Add debug logging to the retry loop.",
                      "Decide whether to log at debug or info.", "Rename the helper. Fixes #123"):
            self.assertNotEqual(self.level(brief), "complex", brief)
        for brief in ("Report which commit fixed the flaky test.", "Find where the test fixtures are loaded.",
                      "List the failing tests. Do not fix anything."):
            self.assertEqual(self.level(brief), "lookup", brief)

    def test_a_debug_compound_is_not_a_fix_even_when_the_description_leads(self):
        for description, prompt in (("Debug logging for the retry loop", "Add debug logging to the retry loop."),
                                    ("Debug mode flag", "Add a debug mode flag to the CLI."),
                                    ("Debug output", "Implement debug output for the exporter.")):
            result = profile.profile_call({"description": description, "prompt": prompt}, "general-purpose")
            self.assertEqual(result["level"], "standard", description)
            self.assertNotIn("fixes or hunts a defect", result["signals"], description)

    def test_finding_why_or_finding_a_defect_hunts_one(self):
        for brief in ("Find why the checkout total is wrong",
                      "Find the race condition between the api and worker services that drops jobs",
                      "Find why the build is slow.", "Find the bug in parse_date.", "Find the leak in the worker.",
                      "Find the regression.", "Find out why the nightly export fails."):
            self.assertEqual(self.level(brief), "complex", brief)
            self.assertEqual(profile.profile_prompt(brief)["level"], "complex", brief)
        for brief in ("Find where the auth middleware is defined", "Find all callers of parse_date",
                      "Find the file that defines the User model", "Find the files that import auth",
                      "Find the regression tests for the parser.", "Find the bug report template.",
                      "Do not find the bug, just list the failing tests."):
            self.assertEqual(self.level(brief), "lookup", brief)
        for brief in ("Rename the helper. Avoid finding the bug in the tests.",
                      "Rename the helper. Do not find why the tests fail."):
            self.assertEqual(self.level(brief), "standard", brief)
            self.assertNotEqual(profile.profile_prompt(brief)["level"], "complex", brief)

    def test_a_fix_the_brief_rules_out_does_not_floor_it(self):
        for brief in ("Rename the helper. Avoid fixing the unrelated tests.",
                      "Rename the helper. You must not fix the tests.",
                      "Rename the helper and try not to fix the tests.",
                      "Rename the helper. Out of scope: fixing the unrelated tests.",
                      "Rename the helper. Avoid investigating the flaky tests."):
            self.assertEqual(self.level(brief), "standard", brief)

    def test_ruling_out_a_change_keeps_the_task_after_it(self):
        for brief in ("Avoid breaking the CSV format and add tests for the parser.",
                      "You must not rename the public symbols and write tests for the loader.",
                      "Be careful not to touch the config and create a migration guide."):
            self.assertEqual(self.level(brief), "standard", brief)

    def test_the_standard_model_text_does_not_call_fixes_standard(self):
        models = json.loads((ROOT / "plugins/model-router/config/models.json").read_text())["models"]
        self.assertNotIn("ixes", models["sonnet"]["use_for"])
        self.assertTrue(models["opus"]["use_for"].startswith("Defect fixes, migrations and performance work"))

    def test_one_reading_serves_a_call_and_a_request(self):
        brief = "Fix the auth bug across every service. Do not change the public API."
        reading = profile.read(brief)
        self.assertEqual(reading.signals, ("risk", "scope"))
        self.assertEqual(profile.level(reading, "call", profile.role("general-purpose")),
                         profile.profile_call({"prompt": brief}, "general-purpose"))
        wording = profile.read(brief, limits=False)
        self.assertEqual({**profile.level(wording, "request"), "changes": "change" in wording.acts},
                         profile.profile_prompt(brief))

    def test_a_request_with_signals_and_no_verb_scores_but_a_call_that_asks_for_nothing_is_a_lookup(self):
        self.assertEqual(profile.profile_prompt("the payments service")["level"], "standard")
        self.assertEqual(self.level("the payments service"), "lookup")

    def test_the_two_kinds_have_their_own_length_rules(self):
        long = "notes " * 300
        self.assertEqual(profile.profile_prompt(long)["signals"], ["scope"])
        self.assertEqual(self.level(long), "lookup")
        self.assertEqual(self.level("list the files. " + "x " * 2100), "standard")

    def test_a_search_or_check_agent_stays_at_standard_for_a_fix_brief(self):
        brief = {"prompt": "Fix the off-by-one in page_slice."}
        self.assertEqual(profile.profile_call(brief, "model-router:verifier")["level"], "standard")
        self.assertEqual(profile.profile_call(brief, "model-router:architect")["level"], "complex")

    def test_the_request_prompt_is_floored_by_a_fix_like_a_call(self):
        fixed = profile.profile_prompt("Fix the off-by-one in page_slice.")
        self.assertEqual(fixed["level"], "complex")
        self.assertIn("fixes or hunts a defect", fixed["signals"])
        self.assertEqual(profile.profile_prompt("Figure out why it times out and fix it.")["level"], "complex")
        for brief in ("Rename the helper. Avoid fixing the unrelated tests.", "Do not fix the tests. Rename the helper.",
                      "Find the commit that landed the fix for the CSV export.", "Add debug logging to the retry loop.",
                      "Analyze the sales numbers in q3.csv.", "Reproduce the export from last week's report."):
            self.assertNotEqual(profile.profile_prompt(brief)["level"], "complex", brief)
        self.assertEqual(profile.profile_prompt("Reproduce the crash on upload.")["level"], "complex")

    def test_reading_about_something_sensitive_or_broad_is_still_a_lookup(self):
        self.assertEqual(self.level("read the auth module and list its functions"), "lookup")
        self.assertEqual(self.level("search across the codebase for usages of foo()"), "lookup")
        self.assertEqual(self.level("find the author of this file"), "lookup")
        self.assertEqual(self.level("list the files under src. Do not change any files."), "lookup")
        self.assertEqual(self.level("explain the architecture of the payment system"), "standard")

    def test_words_that_only_look_like_changes(self):
        self.assertEqual(self.level("find the port number the server listens on"), "lookup")
        self.assertEqual(self.level("which plugins support hooks?"), "lookup")
        self.assertEqual(self.level("read the update-check option"), "lookup")
        self.assertEqual(self.level("list the routes. Make sure to include the line numbers"), "lookup")
        # The limit names what the fix must work around, so its risk counts.
        self.assertEqual(self.level("Fix the retry bug. Do not change the public API."), "complex")

    def test_a_limit_ends_with_its_clause_and_keeps_the_task_after_it(self):
        self.assertEqual(self.level("Without changing the public API, fix the race condition in the payments "
                                    "service and add a regression test."), "complex")
        self.assertEqual(self.level("Never guess: debug why the auth token refresh fails intermittently across "
                                    "services, find the root cause and fix it."), "hardest")
        self.assertEqual(self.level("Do not stop until the database migration across all services is fixed "
                                    "and verified."), "standard")
        self.assertEqual(self.level("No need to ask questions, just implement the OAuth2 login flow and secure "
                                    "the session cookies."), "complex")

    def test_a_task_joined_to_a_limit_by_and_keeps_what_it_is_about(self):
        for brief, signals in (
                ("Do not touch tests and implement the payment retry.", ["risk"]),
                ("Don't break the public API and fix the authentication bug in the login service.", ["risk"]),
                ("Never log secrets and refactor the credentials loader across all services.", ["risk", "scope"]),
                ("Do not use any new dependencies and migrate the database schema to the new billing tables.",
                 ["risk"])):
            result = profile.profile_call({"prompt": brief}, "general-purpose")
            self.assertEqual((result["level"], result["signals"]), ("complex", signals), brief)
        # A risk named only inside the limit still says what the work touches.
        self.assertEqual(self.level("Rename the session helper without touching the auth tokens."), "complex")
        self.assertEqual(self.level("Find the config loader. Do not touch the database."), "lookup")
        # A limit's scope and ambiguity words don't count, so guardrails can't push a small edit to hardest.
        self.assertEqual(self.level("Fix the typo in docs/setup.md. Do not touch the database schema, do not make "
                                    "changes across other modules, and do not try to figure out the flaky CI job."),
                         "complex")
        # "And" ends a limit only before a verb that takes an object.
        for brief in ("Find X. Do not add and remove files.", "List the routes; don't review and fix anything.",
                      "Explain Y. Never plan and design new modules.",
                      "List the CI jobs. Do not edit the Dockerfile and build scripts.",
                      "Find the deploy script. Do not touch the production config and build scripts.",
                      "Show the migration files. Do not touch the schema and update scripts."):
            self.assertEqual(self.level(brief), "lookup", brief)

    def test_and_or_then_before_a_task_verb_ends_a_limit_without_an_article(self):
        for brief in ("Do not touch tests and implement payment retries.",
                      "Don't break the API and fix auth token refresh in the login service.",
                      "Never log secrets and refactor credentials loading.",
                      "Do not touch tests then implement the payment retry.",
                      "Don't touch the UI and also fix the race condition in the payments worker.",
                      "Never edit generated code and instead fix the auth bug in the source.",
                      "Do not touch tests and just fix the auth bug.",
                      "Do not touch tests and please fix the auth bug.",
                      "Do not touch tests and debug why payments fail intermittently."):
            self.assertEqual(self.level(brief), "complex", brief)
        # A verb that is often a noun ends the limit only with an object after it.
        for brief in ("List the CI jobs. Do not edit the Dockerfile and build scripts.",
                      "Find the deploy script. Do not touch the production config and build scripts.",
                      "Show the migration files. Do not touch the schema and update scripts."):
            self.assertEqual(self.level(brief), "lookup", brief)
        # So does a task verb in a compound noun such as "debug logs" or "audit trails".
        for brief in ("List the endpoints. Do not touch the auth module and debug logs.",
                      "Find the config. Do not delete the settings and audit logs.",
                      "Show the release notes. Do not edit the changelog and upgrade notes.",
                      "Find the tests. Do not edit the auth fixtures and debug output.",
                      "Explain the logger. Do not touch production and audit trails."):
            self.assertEqual(self.level(brief), "lookup", brief)
        # Scripts, builds, records, paths and tools are what these verbs take as an object, not compounds.
        for brief in ("Do not touch tests and fix scripts that rotate the production credentials.",
                      "Never log secrets and migrate records in the billing database.",
                      "Do not change the UI and fix builds that leak secrets.",
                      "Do not touch tests and refactor tools that handle payments.",
                      "Do not touch tests and fix paths in the auth middleware."):
            result = profile.profile_call({"prompt": brief}, "general-purpose")
            self.assertEqual((result["level"], result["signals"]), ("complex", ["risk"]), brief)

    def test_joins_inside_a_limit_stay_fast(self):
        started = time.monotonic()
        self.level("do not touch" + " tests and fix" * 20_000)
        self.level("do not add" + " and remove" * 20_000 + " files")
        self.level("never x" + " and then just" * 20_000 + " fix it")
        self.level("do not touch tests" + " " * 64_000 + "and fix it")
        self.level("add" + "\n" * 64_000)
        self.level("add" + "\n \t" * 32_000 + "x")
        self.level("add across" + "\n" * 64_000)
        self.level("add latency" + "\n" * 64_000)
        self.assertLess(time.monotonic() - started, LINEAR_SECONDS)

    def test_a_hyphenated_word_before_and_still_ends_a_limit(self):
        # "edit-mode" and "review-ready" are nouns, not the limit verbs edit and review.
        for brief in ("Do not touch the edit-mode and rename the helper.", "Do not touch the review-ready and implement the parser."):
            self.assertEqual(self.level(brief), "standard", brief)

    def test_a_long_hyphen_or_apostrophe_run_after_a_limit_stays_fast(self):
        for run in ("a-" * 32_000, "a'" * 32_000):  # 64 KB, such as a pasted token or a kebab-case list
            started = time.monotonic()
            self.level("do not " + run)
            self.assertLess(time.monotonic() - started, LINEAR_SECONDS, run[:4])

    def test_a_limit_over_a_list_of_single_verbs_stays_a_limit(self):
        for brief in ("Find where the retry logic lives. Do not add, remove or rename files.",
                      "Find where the retry logic lives. Do not edit, delete or create any files.",
                      "Explain how sessions are stored. Don't write, change or delete anything.",
                      "List the routes; do not change, fix or refactor anything.",
                      "Explain the cache. Never, ever change files.",
                      "Find where the retry logic lives. Do not add, remove, or rename files.",
                      "Find where the retry logic lives. Do not edit files, create branches or update the database."):
            self.assertEqual(self.level(brief), "lookup", brief)

    def test_a_comma_before_the_task_ends_the_limit(self):
        self.assertEqual(self.level("No need to ask, just implement the OAuth2 login flow and secure the session "
                                    "cookies."), "complex")
        self.assertEqual(self.level("Never guess, debug why the auth token refresh fails intermittently across "
                                    "services."), "hardest")
        self.assertEqual(self.level("The sync job runs without retries, add backoff to the payments client."),
                         "complex")

    def test_a_task_after_a_limit_and_a_comma_is_not_a_list_item(self):
        for brief in ("Do not ask questions, implement the OAuth2 login flow.",
                      "Do not change the tests, fix the auth bug in the login handler.",
                      "Don't touch the public API, fix the race condition in the payments service and add a "
                      "regression test.",
                      "Do not commit, write the migration for the payments table.",
                      "Do not change the tests, fix the auth bug and add a regression test.",
                      "Do not ask questions, migrate the database, then run the tests.",
                      "Don't touch the tests, fix the auth bug, then report back.",
                      "Do not ask questions, implement OAuth2 login, and secure the session cookies.",
                      "Do not ask questions, fix the auth bug or remove the check.",
                      "Never guess, fix the flaky auth test or delete it."):
            self.assertEqual(self.level(brief), "complex", brief)
        for brief in ("Do not modify tests, rename the parser, update the docs, and run the suite.",
                      "Do not touch the API, rename it or roll it back."):
            self.assertEqual(self.level(brief), "standard", brief)

    def test_a_limit_keeps_an_aside_and_list_verbs_outside_the_change_words(self):
        for brief in ("List the tables. Do not, under any circumstances, write to the database.",
                      "Find the config loader. Never, for any reason, change files.",
                      "Find the retry logic. Do not delete, modify or rename any files.",
                      "Do not touch, edit or delete files.",
                      "Find the schema. Do not edit files, create branches or update the database, or delete "
                      "anything."):
            self.assertEqual(self.level(brief), "lookup", brief)

    def test_a_long_list_after_a_limit_stays_fast(self):
        started = time.monotonic()
        self.level("do not add" + ", add x y" * 20_000 + " or add it")
        self.level("do not add" + ", add x y" * 20_000 + ", then stop")
        self.level("never x" + ", add never x" * 4_000 + ", then stop")
        self.level("never, add" + ", add never" * 4_000 + ", then stop")
        self.assertLess(time.monotonic() - started, LINEAR_SECONDS)

    def test_a_huge_whitespace_run_after_a_limit_stays_fast(self):
        started = time.monotonic()
        self.level("do not" + " " * 64_000 + "x")
        self.assertLess(time.monotonic() - started, LINEAR_SECONDS)

    def test_a_brief_in_another_language_starts_at_standard(self):
        for brief in ("Corrige le bug de sécurité dans le module de paiement.",
                      "Corrige le bug dans le module de paiement et ajoute un test pour la validation.",
                      "Diseña una migración de OAuth2 para todos los servicios y encuentra la causa raíz del fallo.",
                      "Behebe den Fehler in der Zahlungsfunktion und füge einen Test für die Validierung hinzu.",
                      "Correggi il bug nella funzione di pagamento e aggiungi un test per la validazione.",
                      "Corrija o erro na função de pagamento e adicione um teste para a validação.",
                      "Los de fout in de betaalmodule op en voeg een test toe voor de validatie.",
                      "すべてのサービスでOAuth2への移行を設計し、断続的な障害の根本原因を見つけてください。",
                      "Найди, где определена функция retry."):
            self.assertEqual(self.level(brief), "standard", brief)
            self.assertNotEqual(profile.profile_prompt(brief)["level"], "lookup", brief)
        for brief in ("grep TODO src/*.py", "list the files under src", "ls -la src/", "cat README.md",
                      "List i18n files: locales/de.json, locales/en.json, locales/es.json",
                      "grep -rn 'des_encrypt' lib/des/ lib/des.c"):
            self.assertEqual(self.level(brief), "lookup", brief)
            self.assertEqual(profile.profile_prompt(brief)["level"], "lookup", brief)

    def test_a_judgment_verb_counts_at_the_start_of_the_prompt_too(self):
        for call in ({"prompt": "Design a plugin loading strategy for the CLI."},
                     {"description": "Review CLI flags", "prompt": "Review the proposal for the new CLI flags."}):
            self.assertEqual(profile.profile_call(call, "general-purpose")["level"], "complex", call)
        for brief in ("Explain the design of the cache layer.", "What does the design doc say about retries?",
                      "Review the README for typos."):
            self.assertEqual(self.level(brief), "standard", brief)
        # Bare "research" is how people say "look into"; it isn't research-grade wording.
        self.assertEqual(self.level("Research the trade-offs of switching the database driver and recommend one."),
                         "complex")

    def test_make_sure_keeps_the_task_it_introduces(self):
        self.assertEqual(self.level("Make sure to fix the auth bug in the login handler."), "complex")
        self.assertEqual(self.level("Make sure the payments database migration is safe across all services "
                                    "and keep backward compatibility."), "standard")

    def test_thorough_or_long_briefs_are_not_lookups(self):
        self.assertEqual(self.level("very thorough: how does the request pipeline work"), "standard")
        self.assertEqual(self.level("note " * 1000), "standard")

    def test_agent_name_sets_a_floor_or_a_ceiling(self):
        self.assertEqual(self.level("look at the diff", "acme:code-reviewer"), "complex")
        self.assertEqual(self.level("look at this", "security-auditor"), "complex")
        self.assertEqual(self.level(HARD, "Explore"), "standard")
        self.assertEqual(self.level(HARD, "acme:test-runner"), "standard")
        self.assertEqual(self.level("look at this", "Plan"), "lookup")
        self.assertEqual(self.level(HARD, "helper"), "complex")

    def test_prompt_levels(self):
        self.assertEqual(profile.profile_prompt("where is MAX_RETRIES defined?")["level"], "lookup")
        self.assertEqual(profile.profile_prompt("add a retry to the fetch helper")["level"], "standard")
        self.assertEqual(profile.profile_prompt(
            "migrate auth to oauth across every service and figure out the best approach")["level"], "hardest")
        self.assertIn("scope", profile.profile_prompt("x " * 900)["signals"])


class RoutingEvals(Sandbox):
    """Each `claude plugin eval` case routes as its expected_outcome says, with no model call, so a
    routing regression fails CI instead of waiting for a paid eval run. A case hands a subagent an exact
    brief, and the router decides that call as it would in a session on the case's own model."""

    def test_each_eval_case_routes_as_expected(self):
        cases = sorted((PLUGIN / "evals").glob("*/prompt.md"))
        self.assertGreaterEqual(len(cases), 2)
        for path in cases:
            name = path.parent.name
            fields = frontmatter(path)
            want = re.fullmatch(r"['\"]?level (\w+), model (\w+), effort (\w+)['\"]?", fields.get("expected_outcome", ""))
            self.assertTrue(want, f"{name}: expected_outcome must read 'level L, model M, effort E'")
            quoted = re.search(r'this exact prompt: "([^"]+)"', path.read_text(encoding="utf-8").split("\n---\n", 1)[1])
            self.assertTrue(quoted, f"{name}: the case must quote the brief it hands a subagent")
            self.session_on(fields["model"], session_id=name)
            hook_input = {"session_id": name, "cwd": str(self.project),
                          "tool_input": {"subagent_type": "general-purpose", "prompt": quoted[1]}}
            d = spawn.decide(hook_input, settings.Snapshot(hook_input)).decision
            self.assertEqual((d["level"], d["model"], d["effort"]), want.groups(), name)


class Router(Sandbox):
    def setUp(self):
        super().setUp()
        self.session_on("claude-opus-5-5")

    def test_the_cheap_role_agents_keep_their_pin_and_the_rest_are_routed(self):
        self.session_on("claude-fable-5-2")
        for brief in (LOOKUP, HARDEST):
            d = self.decide("model-router:scout", brief)
            self.assertEqual((d["model"] or "haiku", d["effort"] or "medium"), ("haiku", "medium"), brief)
        self.assertEqual(self.decide("model-router:architect", HARDEST)["model"], "opus")
        self.assertEqual(self.decide("model-router:deep-solver", LOOKUP)["model"], "opus")  # routed, never Fable by pin

    def test_the_shipped_ladder_picks_at_balanced_priority(self):
        config = cfg.shipped()
        every = ["haiku", "sonnet", "opus", "fable"]
        picks = [tuple(ladder.pick(config, level, "balanced", every, {})) for level in cfg.LEVELS]
        self.assertEqual(picks, [("haiku", "medium"), ("sonnet", "medium"), ("opus", "high"),
                                 ("opus", "xhigh"), ("fable", "high")])

    def test_a_pick_weaker_than_the_levels_need_gets_one_more_effort_step(self):
        config = cfg.shipped()
        pick = lambda level, usable: tuple(ladder.pick(config, level, "balanced", usable, {}))  # noqa: E731
        self.assertEqual(pick("complex", ["haiku", "sonnet"]), ("sonnet", "xhigh"))
        self.assertEqual(pick("hardest", ["haiku", "sonnet"]), ("sonnet", "xhigh"))
        self.assertEqual(pick("frontier", ["haiku", "sonnet", "opus"]), ("opus", "xhigh"))
        self.assertEqual(pick("complex", ["haiku"]), ("haiku", "xhigh"))
        self.assertEqual(ladder.pick(config, "complex", "balanced", [], {}), (None, "high"))

    def test_a_pick_at_or_above_the_levels_need_gets_no_extra_step(self):
        config = cfg.shipped()
        pick = lambda level, usable, priority="balanced": tuple(ladder.pick(config, level, priority, usable, {}))  # noqa: E731
        self.assertEqual(pick("lookup", ["sonnet"]), ("sonnet", "medium"))  # stronger than needed
        self.assertEqual(pick("standard", ["sonnet", "opus"]), ("sonnet", "medium"))
        self.assertEqual(pick("complex", ["opus", "fable"]), ("opus", "high"))
        self.assertEqual(pick("complex", ["opus", "fable"], "quality"), ("fable", "high"))
        self.assertEqual(pick("complex", ["sonnet", "opus"], "fast"), ("sonnet", None))  # fast steps down on purpose

    def test_the_extra_effort_step_never_goes_past_xhigh(self):
        config = cfg.shipped()
        for effort, want in (("medium", "high"), ("high", "xhigh"), ("xhigh", "xhigh"), ("max", "max")):
            levels = {**config.levels, Level.complex: LevelSpec(3, effort)}
            pick = ladder.pick(dataclasses.replace(config, levels=levels), "complex", "balanced", ["sonnet"], {})
            self.assertEqual(pick, ("sonnet", want), effort)

    def test_complex_without_opus_runs_on_sonnet_at_xhigh_and_a_redo_adds_effort(self):
        self.options(max_tier="sonnet")
        d = self.decide("general-purpose", HARD)
        self.assertEqual((d["level"], d["model"], d["effort"]), ("complex", "sonnet", "xhigh"))
        self.pre("general-purpose", HARD)
        self.done("general-purpose", HARD, "claude-sonnet-5-5", model="sonnet", effort="xhigh")
        again = self.decide("general-purpose", HARD)
        self.assertEqual((again["level"], again["model"], again["effort"]), ("hardest", "sonnet", "max"))

    def test_complex_with_opus_and_fable_routed_around_runs_on_sonnet_at_xhigh_and_a_redo_adds_effort(self):
        self.route_around("opus", "fable")
        d = self.decide("general-purpose", HARD)
        self.assertEqual((d["level"], d["model"], d["effort"]), ("complex", "sonnet", "xhigh"))
        self.pre("general-purpose", HARD)
        self.done("general-purpose", HARD, "claude-sonnet-5-5", model="sonnet", effort="xhigh")
        again = self.decide("general-purpose", HARD)
        self.assertEqual((again["model"], again["effort"]), ("sonnet", "max"))

    def test_complex_with_only_opus_routed_around_moves_up_to_fable_at_high(self):
        self.route_around("opus")
        d = self.decide("general-purpose", HARD)
        self.assertEqual((d["model"], d["effort"]), ("fable", "high"))

    def test_a_pin_or_rule_the_cap_lowers_keeps_the_levels_own_effort(self):
        self.options(max_tier="sonnet")
        self.agent("pinned", "opus")
        self.agent("ruled")
        self.override({"agents": {"ruled": {"model": "opus"}}})
        for name, source in (("pinned", "pin"), ("ruled", "rule")):
            d = self.decide(name, HARD)
            self.assertEqual((d["source"], d["model"], d["effort"]), (source, "sonnet", "high"), name)
        self.assertEqual(self.decide("general-purpose", HARD)["effort"], "xhigh")

    def test_quality_priority_and_a_learned_raise_take_complex_to_fable(self):
        self.options(priority="quality")
        d = self.decide("general-purpose", HARD)
        self.assertEqual((d["level"], d["model"], d["effort"]), ("complex", "fable", "high"))
        os.environ.pop("CLAUDE_PLUGIN_OPTION_PRIORITY")
        for _ in range(learn.VOTES_NEEDED):
            self.spawned(source="caller", level="complex", pick="opus", model="fable")
        d = self.decide("general-purpose", HARD)
        self.assertEqual((d["model"], d["effort"]), ("fable", "high"))

    def test_agents_without_a_model_get_the_one_their_subtask_needs(self):
        self.agent("helper")
        for agent in ("helper", "general-purpose", None, "Explore"):
            d = self.decide(agent, LOOKUP)
            self.assertEqual((d["action"], d["model"], d["effort"], d["source"]),
                             ("rewrite", "haiku", "medium", "router"), agent)
        d = self.decide("helper", BUILD)
        self.assertEqual((d["model"], d["effort"]), ("sonnet", "medium"))
        d = self.decide("helper", HARD)
        self.assertEqual((d["model"], d["effort"]), ("opus", "high"))

    def test_a_rewrite_changes_two_fields_and_approves_nothing(self):
        out = self.pre("general-purpose", LOOKUP, description="d", run_in_background=True)
        self.assertEqual(out["updatedInput"], {"prompt": LOOKUP, "subagent_type": "general-purpose",
                                               "description": "d", "run_in_background": True,
                                               "model": "haiku", "effort": "medium"})
        self.assertNotIn("permissionDecision", out)
        self.assertIn("haiku at medium effort", out["additionalContext"])
        self.assertIn('keeps its `description` with "Redo:" in front', out["additionalContext"])
        self.assertEqual(self.ledger()[-1]["via"], "fallback")

    def test_no_output_when_the_model_would_be_the_same(self):
        self.session_on("claude-sonnet-5-5")
        self.override({"levels": {"standard": {"effort": None}}}, user=True)
        self.assertIsNone(self.pre("general-purpose", BUILD))
        self.assertEqual(self.ledger()[-1]["action"], "keep")

    def test_builtin_explore_is_always_written_out(self):
        # Its default model has differed between Claude Code versions.
        self.session_on("claude-sonnet-5-5")
        d = self.decide("Explore", "very thorough: how does the request pipeline work")
        self.assertEqual((d["action"], d["model"], d["was"]), ("rewrite", "sonnet", None))

    def test_inside_a_subagent_the_pick_is_always_written_out(self):
        self.session_on("claude-sonnet-5-5")
        hook_input = {**self.call("general-purpose", BUILD), "agent_id": "agent-1", "agent_type": "helper"}
        snap = settings.Snapshot(hook_input)
        d = router.decide(spawn.facts(hook_input, snap), snap.registry, settings.Settings())
        self.assertEqual((d["action"], d["model"], d["was"]), ("rewrite", "sonnet", None))

    def test_decide_needs_only_its_facts(self):
        config = cfg.load()
        usable = config.available()

        def decide(**facts) -> dict:
            facts = {"agent": "helper", "tool_input": {"prompt": LOOKUP}, "usable": usable, **facts}
            return router.decide(router.Facts(**facts), config, settings.Settings())

        self.assertEqual(decide()["action"], "keep")  # no definition found: left alone
        d = decide(definition={"source": "project", "model": None}, parent="opus")
        self.assertEqual((d["action"], d["model"], d["effort"], d["was"]), ("rewrite", "haiku", "medium", "opus"))
        d = decide(definition={"source": "project", "model": "opus"})
        self.assertEqual((d["model"], d["source"], d["effort"]), ("opus", "pin", "medium"))
        d = decide(definition={"source": "project", "model": None}, earlier={"level": "lookup", "model": "haiku"})
        self.assertEqual((d["level"], d["model"], d["escalated_from"]), ("standard", "sonnet", "lookup"))
        d = decide(definition={"source": "project", "model": None}, raises={"lookup": 1})
        self.assertEqual(d["model"], "sonnet")  # the learned raise arrives as a fact
        with mock.patch.object(learn, "stats", side_effect=AssertionError("decide read learned.json")):
            decide(definition={"source": "project", "model": None})

    def test_fork_is_never_touched(self):
        self.assertEqual(self.decide("fork", LOOKUP)["action"], "keep")

    def test_a_model_named_on_the_call_is_kept(self):
        d = self.decide("general-purpose", LOOKUP, model="opus")
        self.assertEqual((d["action"], d["model"], d["source"]), ("keep", "opus", "caller"))

    def test_a_project_file_may_raise_a_level_but_not_past_the_cap(self):
        self.assertEqual(self.decide("general-purpose", LOOKUP)["model"], "haiku")
        self.override({"levels": {"lookup": {"needs": 3, "effort": "high"}}})
        d = self.decide("general-purpose", LOOKUP)
        self.assertEqual((d["model"], d["effort"]), ("opus", "high"))
        self.options(max_tier="sonnet")
        d = self.decide("general-purpose", LOOKUP)
        self.assertEqual((d["model"], d["effort"]), ("sonnet", "xhigh"))  # the cap bounds the model; the missing strength costs one more effort step

    def test_a_model_named_on_the_call_is_lowered_to_the_cap(self):
        self.options(max_tier="sonnet")
        d = self.decide("general-purpose", LOOKUP, model="fable")
        self.assertEqual((d["action"], d["model"], d["effort"]), ("rewrite", "sonnet", None))

    def test_agents_that_name_a_model_keep_it_within_the_cap(self):
        self.agent("pinned", "opus")
        d = self.decide("pinned", LOOKUP)  # the pin holds the model; effort still follows the subtask
        self.assertEqual((d["action"], d["model"], d["source"], d["effort"]), ("rewrite", "opus", "pin", "medium"))
        self.agent("deep", "fable", extra="effort: xhigh\n")
        d = self.decide("deep", LOOKUP)
        self.assertEqual((d["model"], d["effort"]), ("fable", None))  # its own effort: xhigh
        self.options(max_tier="sonnet")
        d = self.decide("pinned", LOOKUP)
        self.assertEqual((d["action"], d["model"], d["source"], d["effort"]), ("rewrite", "sonnet", "pin", "medium"))
        self.assertEqual(self.decide("deep", HARD)["model"], "sonnet")

    def test_a_hand_off_to_the_strong_role_agents_runs_at_least_at_the_requests_level(self):
        self.session_on("claude-sonnet-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": (
            "Our order service double-charges customers under concurrent retries across the payments API and "
            "the worker queue. Find the root cause and design a fix that keeps backward compatibility.")})
        self.assertEqual(request.load("s1").level, "hardest")
        plan = "Plan a fix for the double-charge bug described above."
        self.assertEqual(self.decide("model-router:scout", plan.replace("Plan a fix for", "List the files of"))["model"],
                         "haiku")  # the floor is only for the strong role agents
        d = self.decide("model-router:architect", plan)
        self.assertEqual((d["level"], d["model"], d["effort"]), ("hardest", "opus", "xhigh"))
        d = self.decide("model-router:reviewer", "Review the diff saved at the path above for the double-charge fix.")
        self.assertEqual((d["level"], d["model"], d["effort"]), ("hardest", "opus", "xhigh"))
        self.pre("model-router:architect", plan)
        self.done("model-router:architect", plan, "claude-opus-5-5", model="opus", effort="xhigh")
        d = self.decide("model-router:architect", plan)
        self.assertEqual((d["level"], d["model"], d["escalated_from"]), ("frontier", "fable", "hardest"))
        check = "Check this proposed fix for the duplicate charge problem and improve it: add a lock."
        self.assertEqual(self.decide("model-router:architect", check, model="sonnet")["model"], "sonnet")
        request.begin("s1", prompt_id=None, level="frontier", need=None, session_tier=None, named=None)
        self.assertEqual(self.decide("model-router:deep-solver", check)["level"], "frontier")
        self.options(max_tier="sonnet")
        self.assertEqual(self.decide("model-router:architect", check)["model"], "sonnet")

    def test_a_hand_off_while_handling_another_sessions_message_routes_on_its_brief(self):
        self.session_on("claude-sonnet-5-5")
        hard = ("Our order service double-charges customers under concurrent retries across the payments API and "
                "the worker queue. Find the root cause and design a fix that keeps backward compatibility.")
        rename = "Review this one-line rename of a config field."
        for message in ("Another Claude session sent a message:\n<cross-session-message from=\"peer\">"
                        "Rename the field</cross-session-message>",
                        "Another Claude session sent a message:\n<teammate-message teammate_id=\"dev\">"
                        "Rename the field</teammate-message>"):
            hooks.user_prompt({"session_id": "s1", "prompt": hard})
            hooks.user_prompt({"session_id": "s1", "prompt": message})
            d = self.decide("model-router:architect", rename)
            self.assertEqual((d["level"], d["model"]), ("complex", "opus"), message)
            self.assertEqual(request.load("s1").level, "hardest")  # the Stop check still sees it

    def test_a_task_notification_keeps_the_requests_floor(self):
        self.session_on("claude-sonnet-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": (
            "Our order service double-charges customers under concurrent retries across the payments API and "
            "the worker queue. Find the root cause and design a fix that keeps backward compatibility.")})
        hooks.user_prompt({"session_id": "s1", "prompt": "<task-notification>done</task-notification>"})
        d = self.decide("model-router:architect", "Review this one-line rename of a config field.")
        self.assertEqual((d["level"], d["model"]), ("hardest", "opus"))

    def test_with_no_request_level_the_brief_decides_the_hand_off(self):
        self.session_on("claude-sonnet-5-5")
        d = self.decide("model-router:architect", "Plan a fix for the double-charge bug described above.")
        self.assertEqual((d["level"], d["model"]), ("complex", "opus"))

    def test_a_strong_agent_is_named_without_a_model_so_its_brief_routes_and_its_redo_climbs(self):
        config = cfg.load()
        self.assertEqual(gates.strong_agent(config, "sonnet", "complex"), "model-router:architect")
        self.assertEqual(gates.strong_agent(config, "fable", "frontier"), "model-router:deep-solver")
        self.assertEqual(gates.strong_agent(dataclasses.replace(config, crew={}), "opus", "complex"), 'a subagent with model: "opus"')
        self.session_on("claude-haiku-5-5")
        brief = "Design the session store migration across the API and worker services."
        named = self.decide("model-router:architect", brief, model="sonnet", effort="high")  # the old hint
        self.assertEqual((named["source"], named["model"]), ("caller", "sonnet"))
        d = self.decide("model-router:architect", brief)
        self.assertEqual((d["source"], d["level"], d["model"]), ("router", "hardest", "opus"))
        self.pre("model-router:architect", brief)
        self.done("model-router:architect", brief, "claude-opus-5-5", model="opus", effort="xhigh")
        d = self.decide("model-router:architect", brief)
        self.assertEqual((d["level"], d["model"], d["escalated_from"]), ("frontier", "fable", "hardest"))

    def test_the_deep_solver_starts_at_hardest_and_can_go_higher(self):
        d = self.decide("model-router:deep-solver", LOOKUP)  # its name floors the level; the ladder sets the model
        self.assertEqual((d["source"], d["level"], d["model"], d["effort"]), ("router", "hardest", "opus", "xhigh"))
        frontier = ("Investigate the unknown root cause of intermittent data loss in the payments database across "
                    "every service and design a fix; there is no known approach.")
        d = self.decide("model-router:deep-solver", frontier)
        self.assertEqual((d["level"], d["model"]), ("frontier", "fable"))
        self.assertNotIn("model-router:deep-solver", cfg.base_config().get("agents") or {})

    def test_the_role_agents_are_routed_by_their_brief(self):
        d = self.decide("model-router:implementer", LOOKUP)
        self.assertEqual((d["action"], d["source"], d["model"], d["effort"]), ("rewrite", "router", "haiku", "medium"))
        for agent in ("scout", "verifier", "docs-researcher"):  # pinned to Haiku, their own effort kept
            d = self.decide(f"model-router:{agent}", HARDEST)
            self.assertEqual((d["action"], d["source"], d["model"]), ("keep", "pin", "haiku"), agent)
        for agent in ("architect", "reviewer"):  # their names set a complex floor
            d = self.decide(f"model-router:{agent}", LOOKUP)
            self.assertEqual((d["source"], d["level"]), ("router", "complex"), agent)
        self.assertEqual(self.decide("model-router:implementer", HARD)["source"], "router")

    def test_pins_can_be_overridden(self):
        self.agent("pinned", "opus")
        self.options(agent_pins="override")
        d = self.decide("pinned", LOOKUP)
        self.assertEqual((d["action"], d["model"], d["source"]), ("rewrite", "haiku", "router"))
        self.assertEqual(self.decide("mystery:agent", LOOKUP)["model"], "haiku")

    def test_unknown_agents_and_unknown_models_are_left_alone(self):
        self.agent("exotic", "some-custom-model-id")
        for agent in ("mystery:agent", "exotic"):
            d = self.decide(agent, LOOKUP)
            self.assertEqual((d["action"], d["was"]), ("keep", None))

    def test_an_agents_own_effort_and_a_callers_effort_are_kept(self):
        self.agent("careful", None, extra="effort: max\n")
        self.assertIsNone(self.decide("careful", LOOKUP)["effort"])
        out = self.pre("general-purpose", LOOKUP, effort="high")
        self.assertEqual(out["updatedInput"]["effort"], "high")
        self.options(CLAUDE_CODE_EFFORT_LEVEL="medium")
        self.assertIsNone(self.decide("general-purpose", "list the tests")["effort"])

    def test_a_forced_subagent_model_still_gets_its_effort(self):
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="claude-opus-5-5[1m]", CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1")
        out = self.pre("general-purpose", LOOKUP)
        self.assertEqual(out["updatedInput"], {"prompt": LOOKUP, "subagent_type": "general-purpose", "effort": "medium"})
        self.assertIn("effort to medium", out["additionalContext"])
        self.assertEqual(self.decide("general-purpose", HARD)["effort"], "high")
        self.options(priority="fast")
        self.assertIsNone(self.decide("general-purpose", HARD)["effort"])
        self.assertEqual(self.decide("general-purpose", LOOKUP)["effort"], None)
        self.options(CLAUDE_CODE_SUBAGENT_EFFORT="medium")
        self.assertEqual(self.decide("general-purpose", LOOKUP)["action"], "keep")

    def test_the_subagent_effort_variable_does_not_reach_a_teammate(self):
        self.options(CLAUDE_CODE_SUBAGENT_EFFORT="low")
        self.assertEqual(self.decide("general-purpose", HARD)["action"], "keep")
        # The fallback knows a teammate by its named Agent call with agent teams on, the mod by the spawn's kind.
        self.options(CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS="1")
        out = self.pre("general-purpose", HARD, name="auditor")
        self.assertEqual((out["updatedInput"]["effort"], out["updatedInput"]["name"]), ("high", "auditor"))
        for kept in ({"isolation": "worktree"}, {"effort": "low"}):
            self.assertEqual(self.decide("general-purpose", HARD, name="auditor", **kept)["action"], "keep", kept)
        mod = lambda kind: modpath.spawn({"session_id": kind, "cwd": str(self.project), "parent_model": "claude-opus-5-5",  # noqa: E731
                                            "kind": kind, "tool_input": {"subagent_type": "general-purpose", "prompt": HARD}})
        self.assertEqual(mod("teammate")["effort"], "high")
        self.assertIsNone(mod("subagent"))

    def test_an_agent_on_the_sessions_own_model_runs_at_least_at_the_sessions_effort(self):
        # Claude Code runs a subagent on the main conversation's model at the session's level (sub-agents docs),
        # such as xhigh under --effort ultracode, so the router never sets one below it there.
        snap = settings.Snapshot({"session_id": "s1", "cwd": str(self.project)})

        def run(prompt, session_effort, earlier=None):
            facts = router.Facts(agent="general-purpose", tool_input={"subagent_type": "general-purpose", "prompt": prompt},
                                 usable=snap.usable, definition=agents.find("general-purpose", self.project),
                                 parent="opus", session_effort=session_effort, earlier=earlier)
            return router.decide(facts, snap.registry, snap.options)

        hard = run(HARD, "xhigh")
        self.assertEqual((hard["model"], hard["effort"]), ("opus", "xhigh"))
        self.assertIn("at least the session's xhigh", hard["reason"])
        self.assertEqual(run(HARD, None)["effort"], "high")
        self.assertEqual(run(HARD, "low")["effort"], "high")  # it only raises
        self.assertEqual(run(LOOKUP, "xhigh")["effort"], "medium")  # on another model the session's level doesn't apply
        redo = run(HARD, "xhigh", earlier={"level": "complex", "model": "opus", "effort": "xhigh"})
        self.assertNotEqual((redo["model"], redo["effort"]), ("opus", "xhigh"))  # a redo still asks for more

    def test_a_pinned_model_on_the_sessions_own_model_gets_the_sessions_effort_too(self):
        def run(**facts):
            snap = settings.Snapshot({"session_id": "s1", "cwd": str(self.project)})
            found = router.Facts(agent="general-purpose", tool_input={"subagent_type": "general-purpose", "prompt": LOOKUP},
                                 usable=snap.usable, definition=agents.find("general-purpose", self.project),
                                 parent="opus", session_effort="xhigh", **facts)
            return router.decide(found, snap.registry, snap.options)

        self.options(CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL="opus")
        pinned = run(workflow=True)
        self.assertEqual((pinned["model"], pinned["was"], pinned["effort"]), (None, "opus", "xhigh"))
        self.assertIn("at least the session's xhigh", pinned["reason"])
        self.options(CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL="haiku")
        self.assertEqual(run(workflow=True)["effort"], "medium")  # a pin on another model keeps the level's effort
        self.options(CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL="", CLAUDE_CODE_SUBAGENT_MODEL="opus",
                     CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1")
        forced = run()
        self.assertEqual((forced["was"], forced["effort"]), ("opus", "xhigh"))

    def test_the_sessions_effort_counts_only_for_a_call_from_the_main_conversation(self):
        snap = settings.Snapshot({"session_id": "s1", "cwd": str(self.project)})
        tool = {"subagent_type": "general-purpose", "prompt": HARD}

        def effort(**extra):
            return spawn.facts({"session_id": "s1", "tool_input": tool, **extra}, snap).session_effort

        self.assertEqual(effort(effort={"level": "xhigh"}), "xhigh")  # the hook input's level, on the fallback
        self.assertEqual(effort(session_effort="max"), "max")  # the main loop's, from the mod
        self.assertIsNone(effort(agent_id="a1", effort={"level": "xhigh"}))  # a nested call takes its parent's
        self.assertIsNone(effort(effort={"level": "auto"}))
        self.assertIsNone(effort())

    def test_the_users_environment_settings_win(self):
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="sonnet")
        self.assertEqual(self.decide("general-purpose", LOOKUP)["action"], "keep")
        self.assertEqual(self.decide("Explore", LOOKUP)["action"], "keep")
        self.options(CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1")
        self.agent("pinned", "opus")
        self.assertEqual(self.decide("pinned", LOOKUP, model="fable")["action"], "keep")

    def test_modes(self):
        self.options(routing_mode="advise")
        d = self.decide("general-purpose", LOOKUP)
        self.assertEqual((d["action"], d["pick"]), ("keep", "haiku"))
        self.options(routing_mode="off")
        self.assertIsNone(self.pre("general-purpose", LOOKUP))
        self.assertEqual(self.ledger()[-1]["event"], "spawn")  # the ledger still records

    def test_priority_shifts_the_pick_and_fast_drops_extra_thinking(self):
        self.options(priority="quality")
        self.assertEqual(self.decide("general-purpose", LOOKUP)["model"], "sonnet")
        self.options(priority="fast")
        self.assertEqual(self.decide("general-purpose", BUILD)["model"], "haiku")
        d = self.decide("general-purpose", HARD)
        self.assertEqual((d["model"], d["effort"]), ("sonnet", None))
        self.assertEqual(self.decide("general-purpose", LOOKUP)["effort"], None)

    def test_an_unavailable_model_is_routed_around(self):
        self.route_around("sonnet")
        self.session_on("claude-sonnet-5-5")
        self.assertEqual(self.decide("general-purpose", HARD)["model"], "opus")  # the next one strong enough
        self.route_around("fable", "opus")
        self.agent("pinned", "opus")
        self.assertEqual(self.decide("pinned", LOOKUP)["model"], "haiku")  # a named model is only ever lowered

    def test_a_project_rule_model_applies_only_when_it_is_not_weaker_than_the_pick(self):
        self.agent("worker")
        self.override({"agents": {"worker": {"model": "haiku"}}})
        held = self.decide("worker", HARD)  # the pick is opus
        self.assertEqual((held["source"], held["model"], held["effort"]), ("router", "opus", "high"))
        self.assertIn("a project rule names haiku for worker, weaker than the pick opus", held["held"])
        self.assertIn(held["held"], held["reason"])
        same = self.decide("worker", LOOKUP)  # the pick is haiku: not weaker
        self.assertEqual((same["source"], same["model"], same["held"]), ("rule", "haiku", None))
        self.override({"agents": {"worker": {"model": "fable"}}})
        raised = self.decide("worker", LOOKUP)
        self.assertEqual((raised["source"], raised["model"], raised["held"]), ("rule", "fable", None))

    def test_a_project_rule_model_never_lowers_what_the_call_would_run_on_without_it(self):
        self.override({"agents": {"deep": {"model": "haiku"}, "ghost": {"model": "haiku"}}})
        self.agent("deep", "opus")  # the user's own agent pins opus; the brief is a lookup, so the pick is haiku
        pinned = self.decide("deep", LOOKUP)
        self.assertEqual((pinned["source"], pinned["model"]), ("pin", "opus"))
        self.assertIn("weaker than the agent's own model opus", pinned["held"])
        self.agent("plain")
        self.override({"agents": {"deep": {"model": "haiku"}, "ghost": {"model": "haiku"}, "plain": {"model": "haiku"}}})
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="opus")
        env = self.decide("plain", LOOKUP)
        self.assertEqual((env["action"], env["source"], env["model"]), ("keep", None, None))
        self.assertIn("weaker than the CLAUDE_CODE_SUBAGENT_MODEL default opus", env["held"])
        self.assertIn(env["held"], env["reason"])
        os.environ.pop("CLAUDE_CODE_SUBAGENT_MODEL")
        ghost = self.decide("ghost", LOOKUP)  # no definition: what it would run on isn't known
        self.assertEqual((ghost["action"], ghost["source"], ghost["model"]), ("keep", None, None))
        self.assertIn("the model the call would run on isn't known", ghost["held"])
        self.override({"agents": {"deep": {"model": "fable"}, "plain": {"model": "sonnet"}}})
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="sonnet")
        stronger = self.decide("deep", LOOKUP)  # fable is above the pin: a raise applies
        self.assertEqual((stronger["source"], stronger["model"], stronger["held"]), ("rule", "fable", None))
        equal = self.decide("plain", LOOKUP)
        self.assertEqual((equal["source"], equal["model"], equal["held"]), ("rule", "sonnet", None))
        self.override({"agents": {"deep": {"model": "haiku"}}}, user=True)  # the user's own rule still lowers
        self.assertEqual(self.decide("deep", LOOKUP)["model"], "haiku")

    def test_a_project_rule_model_is_not_held_when_it_would_not_have_been_used(self):
        self.agent("worker", "opus")
        self.override({"agents": {"worker": {"model": "haiku"}}})
        named = self.decide("worker", LOOKUP, model="sonnet")  # the call names its own model
        self.assertEqual((named["source"], named["model"], named["held"]), ("caller", "sonnet", None))
        self.options(CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1")
        self.assertIsNone(self.decide("worker", LOOKUP)["held"])

    def test_the_users_own_rule_keeps_full_control(self):
        self.agent("worker")
        self.override({"agents": {"worker": {"model": "haiku", "level": "lookup"}}}, user=True)
        d = self.decide("worker", HARD)
        self.assertEqual((d["source"], d["level"], d["model"], d["held"]), ("rule", "lookup", "haiku", None))
        self.override({"agents": {"worker": {"model": "sonnet"}}})  # a project can't take it back or lower it
        self.assertEqual(self.decide("worker", HARD)["model"], "haiku")

    def test_a_project_rule_cannot_put_an_agent_on_a_model_the_project_added(self):
        self.agent("worker")
        self.override({"models": {"gpt-mini": {"strength": 9, "cost": 0}},
                       "agents": {"worker": {"model": "gpt-mini"}, "general-purpose": {"model": "gpt-mini"}}})
        for agent in ("worker", "general-purpose"):
            for brief in (LOOKUP, HARD):
                with self.subTest(agent=agent, brief=brief):
                    d = self.decide(agent, brief)
                    self.assertNotEqual(d["model"], "gpt-mini")
                    self.assertIn(f"a project rule names gpt-mini for {agent}, a model only that file vouches for",
                                  d["held"])
        self.assertEqual(self.decide("worker", HARD, model="gpt-mini")["model"], "gpt-mini")  # a call may name it
        self.override({"agents": {"worker": {"model": "gpt-mini"}}}, user=True)  # and so may the user's own rule
        mine = self.decide("worker", HARD)
        self.assertEqual((mine["source"], mine["model"], mine["held"]), ("rule", "gpt-mini", None))

    def test_a_project_rule_level_is_a_floor(self):
        self.agent("worker")
        self.override({"agents": {"worker": {"level": "complex"}}})
        d = self.decide("worker", LOOKUP)
        self.assertEqual((d["level"], d["model"]), ("complex", "opus"))
        self.override({"agents": {"worker": {"level": "lookup"}}})
        d = self.decide("worker", HARD)  # it never lowers what the brief needs
        self.assertEqual((d["level"], d["model"]), ("complex", "opus"))

    def test_a_project_rule_cannot_switch_routing_off_but_the_users_can(self):
        self.agent("worker")
        self.override({"agents": {"worker": {"route": False}}})
        d = self.decide("worker", LOOKUP)
        self.assertEqual((d["action"], d["model"]), ("rewrite", "haiku"))
        self.override({"agents": {"worker": {"route": False}}}, user=True)
        self.assertEqual(self.decide("worker", LOOKUP)["action"], "keep")

    def test_a_spawn_that_held_a_project_rule_says_so_in_the_ledger_and_in_models(self):
        self.agent("worker")
        self.override({"agents": {"worker": {"model": "haiku"}}})
        self.session_on("claude-sonnet-5-5")
        self.pre("worker", HARD)
        row = self.ledger()[-1]
        self.assertIn("weaker than the pick opus", row["held"])
        found = json.loads(self.out("models", "--json"))["held_back"]
        self.assertEqual(found, [{"kind": "rule", "name": "worker", "reason": row["held"]}])
        self.pre("general-purpose", LOOKUP)
        self.assertNotIn("held", self.ledger()[-1])

    def test_a_redo_the_effort_cap_holds_at_the_same_model_and_effort_goes_one_model_up(self):
        (self.home / "settings.json").write_text(json.dumps({"maxEffortLevel": "high"}))
        first = self.decide("general-purpose", HARD)
        self.assertEqual((first["model"], first["effort"]), ("opus", "high"))
        self.pre("general-purpose", HARD)
        self.done("general-purpose", HARD, "claude-opus-5-5", model="opus", effort="high")
        again = self.decide("general-purpose", HARD)
        self.assertEqual((again["level"], again["model"], again["effort"]), ("hardest", "fable", "high"))
        self.assertIn("maxEffortLevel holds opus at high, the same as the run that came back before, so it goes to fable",
                      again["reason"])
        self.pre("general-purpose", HARD)
        self.assertIn("so it goes to fable", self.ledger()[-1]["reason"])

    def test_the_redo_stays_when_no_stronger_model_is_usable_and_a_pin_keeps_its_model(self):
        (self.home / "settings.json").write_text(json.dumps({"maxEffortLevel": "high"}))
        self.options(max_tier="opus")
        self.pre("general-purpose", HARD)
        self.done("general-purpose", HARD, "claude-opus-5-5", model="opus", effort="high")
        again = self.decide("general-purpose", HARD)
        self.assertEqual((again["model"], again["effort"]), ("opus", "high"))
        self.assertNotIn("so it goes to", again["reason"])
        del os.environ["CLAUDE_PLUGIN_OPTION_MAX_TIER"]
        self.agent("pinned", "opus")
        self.pre("pinned", HARD)
        self.done("pinned", HARD, "claude-opus-5-5", model="opus", effort="high")
        self.assertEqual(self.decide("pinned", HARD)["model"], "opus")

    def test_rules_in_override_files(self):
        self.override({"agents": {"Explore": {"route": False}}}, user=True)
        self.override({"agents": {"general-purpose": {"model": "sonnet"}, "Plan": {"level": "frontier"}}})
        self.assertEqual(self.decide("general-purpose", LOOKUP)["model"], "sonnet")
        self.assertEqual(self.decide("Explore", LOOKUP)["action"], "keep")
        self.assertEqual(self.decide("Plan", LOOKUP)["model"], "fable")

    def test_session_model_unknown_still_routes(self):
        d = self.decide("general-purpose", BUILD, session_id="fresh")
        self.assertEqual((d["action"], d["model"], d["was"]), ("rewrite", "sonnet", None))

    def test_session_model_comes_from_the_main_thread_of_the_transcript(self):
        path = Path(self.tmp.name) / "t.jsonl"
        rows = [{"type": "assistant", "message": {"role": "assistant", "model": "claude-sonnet-5-5"}},
                {"type": "assistant", "isSidechain": True, "message": {"role": "assistant", "model": "claude-haiku-5-5"}}]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        hook_input = {**self.call("general-purpose", BUILD), "transcript_path": str(path)}
        snap = settings.Snapshot(hook_input)
        d = router.decide(spawn.facts(hook_input, snap), snap.registry, settings.Settings())
        self.assertEqual((d["model"], d["was"]), ("sonnet", "sonnet"))  # not the recorded opus, not the subagent's haiku

    def test_hooks_never_break_on_garbage(self):
        payloads = ("", "not json", "[]", json.dumps({"tool_input": "x"}),
                    json.dumps({"tool_input": {"model": 5, "subagent_type": ["a"], "prompt": {"a": 1}}}),
                    json.dumps({"session_id": 7, "cwd": 3, "prompt": 9, "transcript_path": [], "model": {}}))
        for command in hooks.HANDLERS:
            if command == "refresh-cache":
                continue  # needs the network
            for payload in payloads:
                res = subprocess.run([sys.executable, str(SCRIPT), command], input=payload, capture_output=True,
                                     text=True, env={**os.environ})
                self.assertEqual((res.returncode, res.stderr), (0, ""), (command, payload))

    def test_a_call_naming_a_model_outside_the_registry_is_left_alone(self):
        for model in ("inherit", "gateway/model-x"):
            d = self.decide("general-purpose", LOOKUP, model=model)
            self.assertEqual((d["action"], d["model"]), ("keep", None), model)
            self.assertIn("outside the registry", d["reason"])


class RepeatGuard(Sandbox):
    """A rewrite that didn't run is never repeated, so a refused rewrite can't loop."""

    def setUp(self):
        super().setUp()
        self.session_on("claude-opus-5-5")

    def test_the_same_call_again_without_a_result_passes_through(self):
        self.assertIsNotNone(self.pre("general-purpose", LOOKUP))
        self.next_message()
        self.assertIsNone(self.pre("general-purpose", LOOKUP))
        self.assertIn("passed through", self.ledger()[-1]["reason"])
        self.assertIsNotNone(self.pre("general-purpose", "list the tests"))  # a different call is routed

    def test_a_passed_through_call_is_routed_and_counted_with_no_source(self):
        key = calls.identify(self.call("general-purpose", LOOKUP)["tool_input"])[1]
        self.pre("general-purpose", LOOKUP)
        self.assertEqual(calls.route_of("s1", key).source, "router")
        self.next_message()
        self.pre("general-purpose", LOOKUP)
        route = calls.route_of("s1", key)
        self.assertEqual((route.source, route.counted), (None, None))

    def test_after_a_result_the_same_call_is_routed_again(self):
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", model="haiku", effort="low")
        self.assertIsNotNone(self.pre("general-purpose", LOOKUP))

    def test_a_call_redone_after_its_result_runs_one_level_up(self):
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", model="haiku", effort="low")
        again = self.decide("general-purpose", LOOKUP)
        self.assertEqual((again["level"], again["model"], again["effort"]), ("standard", "sonnet", "medium"))
        self.assertIn("one level above the lookup run", again["reason"])
        self.pre("general-purpose", LOOKUP)
        self.assertEqual(learn.stats()["lookup"]["votes"], 1)

    def test_a_run_an_api_error_cut_off_is_retried_at_its_level(self):
        # Sent again after an overload, the call is a retry, not a redo: the level didn't fail on quality.
        self.pre("general-purpose", LOOKUP)
        cut = {"resolvedModel": "claude-haiku-5-5", "status": "completed", "totalTokens": 40,
               "content": [{"type": "text", "text": "API Error: 529 {\"type\":\"overloaded_error\"}"}]}
        hooks.post_agent({**self.call("general-purpose", LOOKUP, model="haiku", effort="low"), "tool_response": cut})
        again = self.decide("general-purpose", LOOKUP)
        self.assertEqual((again["level"], again["model"], again["escalated_from"]), ("lookup", "haiku", None))
        self.assertEqual([r["status"] for r in ledger.read() if r.get("event") == "result"], ["cut_off"])
        answer = dict(cut, content=[{"type": "text", "text": "The API Error: 529 handler retries twice."}])
        hooks.post_agent({**self.call("general-purpose", LOOKUP, model="haiku", effort="low"), "tool_response": answer})
        self.assertEqual(self.decide("general-purpose", LOOKUP)["escalated_from"], "lookup")  # a real answer finishes it

    def test_a_standard_redo_moves_to_opus_at_high(self):
        self.pre("general-purpose", BUILD)
        self.done("general-purpose", BUILD, "claude-sonnet-5-5", model="sonnet", effort="medium")
        again = self.decide("general-purpose", BUILD)
        self.assertEqual((again["level"], again["model"], again["effort"], again["escalated_from"]),
                         ("complex", "opus", "high", "standard"))

    def test_a_complex_redo_stays_on_opus_at_xhigh(self):
        self.pre("general-purpose", HARD)
        self.done("general-purpose", HARD, "claude-opus-5-5", model="opus", effort="high")
        again = self.decide("general-purpose", HARD)
        self.assertEqual((again["level"], again["model"], again["effort"], again["escalated_from"]),
                         ("hardest", "opus", "xhigh", "complex"))

    def test_redo_counts_only_within_the_same_request(self):
        hooks.user_prompt({"session_id": "s1", "prompt": "check the repo", "prompt_id": "p1"})
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", model="haiku", effort="low")
        hooks.user_prompt({"session_id": "s1", "prompt": "check the repo again", "prompt_id": "p2"})
        self.assertEqual(self.decide("general-purpose", LOOKUP)["level"], "lookup")  # a loop, not a redo
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", model="haiku", effort="low")
        self.assertEqual(self.decide("general-purpose", LOOKUP)["level"], "standard")

    def test_a_rewritten_brief_marked_redo_runs_one_level_above_the_run_it_redoes(self):
        self.pre("general-purpose", BUILD, description="Parser change")
        self.done("general-purpose", BUILD, "claude-sonnet-5-5", model="sonnet", effort="medium", description="Parser change")
        rewritten = "implement the parser change, this time keeping quoted fields whole"
        unmarked = self.decide("general-purpose", rewritten, description="Parser change")
        self.assertEqual((unmarked["level"], unmarked["escalated_from"]), ("standard", None))  # a new call
        again = self.decide("general-purpose", rewritten, description="Redo: parser change")
        self.assertEqual((again["level"], again["model"], again["effort"], again["escalated_from"]),
                         ("complex", "opus", "high", "standard"))
        self.assertIn("one level above the standard run", again["reason"])
        self.pre("general-purpose", rewritten, description="redo: parser change")
        self.assertEqual(learn.stats()["standard"]["votes"], 1)

    def test_a_marked_redo_climbs_from_a_run_of_an_earlier_request(self):
        hooks.user_prompt({"session_id": "s1", "prompt": "fix the auth migration", "prompt_id": "p1"})
        self.pre("general-purpose", HARD, description="Second try")
        self.done("general-purpose", HARD, "claude-opus-5-5", model="opus", effort="high", description="Second try")
        hooks.user_prompt({"session_id": "s1", "prompt": "that dropped rows, try again", "prompt_id": "p2"})
        again = self.decide("general-purpose", BUILD, description="Redo: second try")
        self.assertEqual((again["level"], again["effort"], again["escalated_from"]), ("hardest", "xhigh", "complex"))
        with self.clock_at(self.clock + calls.REDO_WINDOW + 1):
            late = self.decide("general-purpose", BUILD, description="Redo: second try")
        self.assertEqual((late["level"], late["escalated_from"]), ("complex", "standard"))  # too old to climb from

    def test_a_marked_redo_with_no_run_on_record_goes_one_level_above_its_brief(self):
        again = self.decide("general-purpose", BUILD, description="Redo: parser change")
        self.assertEqual((again["level"], again["model"], again["escalated_from"]), ("complex", "opus", "standard"))
        self.assertIn("marked a redo", again["reason"])

    def test_a_background_run_is_climbed_from_only_after_it_stops(self):
        self.pre("general-purpose", HARD, description="Second try")
        self.launched("general-purpose", HARD, "claude-opus-5-5", "a1", model="opus", effort="high", description="Second try")
        running = self.decide("general-purpose", BUILD, description="Redo: second try")
        self.assertEqual((running["level"], running["escalated_from"]), ("complex", "standard"))
        self.stopped("a1")
        again = self.decide("general-purpose", BUILD, description="Redo: second try")
        self.assertEqual((again["level"], again["escalated_from"]), ("hardest", "complex"))

    def test_only_a_description_that_starts_with_the_mark_is_a_redo(self):
        for description in ("Redo the header layout", "Parser redo: second try", None):
            extra = {"description": description} if description else {}
            d = self.decide("general-purpose", BUILD, **extra)
            self.assertEqual((d["level"], d["escalated_from"]), ("standard", None), description)
        named = self.decide("general-purpose", BUILD, description="Redo: parser change", model="haiku")
        self.assertEqual((named["source"], named["model"], named["escalated_from"]), ("caller", "haiku", None))

    def test_a_marked_redo_climbs_from_the_run_with_its_description_not_the_agents_latest(self):
        self.pre("general-purpose", HARD, description="Second try")
        self.done("general-purpose", HARD, "claude-opus-5-5", model="opus", effort="high", description="Second try")
        self.pre("general-purpose", LOOKUP, description="Other job")  # a later, lighter run of the same agent
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", model="haiku", effort="medium", description="Other job")
        again = self.decide("general-purpose", BUILD, description="Redo:  second TRY")
        self.assertEqual((again["level"], again["effort"], again["escalated_from"]), ("hardest", "xhigh", "complex"))

    def test_a_marked_redo_with_no_run_under_its_description_ignores_the_agents_other_runs(self):
        self.pre("general-purpose", HARDEST, description="Other job")
        self.done("general-purpose", HARDEST, "claude-opus-5-5", model="opus", effort="xhigh", description="Other job")
        again = self.decide("general-purpose", BUILD, description="Redo: second try")
        self.assertEqual((again["level"], again["model"], again["escalated_from"]), ("complex", "opus", "standard"))

    def test_a_marked_redo_with_no_run_on_record_never_repeats_the_plain_call(self):
        # One level up can land on the model and effort the plain call already gets: under the session's floor,
        # a cap, a pin, the fast priority, or at the top of the ladder.
        frontier = "research a novel fix for the auth migration across every service, root cause unknown"
        self.agent("worker", model="sonnet")
        cases = {"ultracode": ("general-purpose", HARD, {"effort": {"level": "xhigh"}}, {}),
                 "an opus cap": ("general-purpose", HARDEST, {}, {"max_tier": "opus"}),
                 "the top of the ladder": ("general-purpose", frontier, {}, {}),
                 "a pinned agent": ("worker", HARDEST, {}, {}),
                 "the fast priority": ("general-purpose", LOOKUP, {}, {"priority": "fast"})}
        for name, (agent, prompt, hook, options) in cases.items():
            with self.subTest(name), mock.patch.dict(os.environ):
                self.options(**options)

                def run(description: str) -> tuple:
                    hook_input = {**self.call(agent, prompt, description=description), **hook}
                    snap = settings.Snapshot(hook_input)
                    d = router.decide(spawn.facts(hook_input, snap), snap.registry, snap.options)
                    return d["model"] or d["was"], d["effort"]

                plain, marked = run("Second try"), run("Redo: second try")
                self.assertNotEqual(marked, plain)
                if marked[0] == plain[0] and plain[1] and marked[1]:
                    self.assertGreater(cfg.EFFORTS.index(marked[1]), cfg.EFFORTS.index(plain[1]))

    def test_a_marked_redo_with_no_run_on_record_casts_no_vote(self):
        self.pre("general-purpose", BUILD, description="Redo: second try")
        self.assertEqual(learn.stats().get("standard", {}).get("votes", 0), 0)

    def test_a_redo_of_a_frontier_run_stays_on_the_top_rung(self):
        for expected in ("standard", "complex", "hardest", "frontier"):
            d = self.decide("general-purpose", BUILD)
            self.assertEqual(d["level"], expected)
            self.pre("general-purpose", BUILD)
            self.done("general-purpose", BUILD, "claude-opus-5-5", model=d["model"], effort=d["effort"])
        top = self.decide("general-purpose", BUILD)
        self.assertEqual((top["level"], top["model"]), ("frontier", "fable"))
        self.assertGreater(cfg.EFFORTS.index(top["effort"]), cfg.EFFORTS.index("high"))

    def test_a_redo_on_a_forced_model_asks_for_more_effort(self):
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="opus", CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1")
        for prompt in (LOOKUP, HARDEST):
            first = self.decide("general-purpose", prompt)
            self.pre("general-purpose", prompt)
            self.done("general-purpose", prompt, "claude-opus-5-5", effort=first["effort"])
            again = self.decide("general-purpose", prompt)
            self.assertGreater(cfg.EFFORTS.index(again["effort"]), cfg.EFFORTS.index(first["effort"]), prompt)

    def test_a_fast_redo_does_not_repeat_the_run_it_replaces(self):
        self.options(priority="fast")
        first = self.decide("general-purpose", LOOKUP)
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", model="haiku")
        again = self.decide("general-purpose", LOOKUP)
        self.assertNotEqual((again["model"], again["effort"]), (first["model"], first["effort"]))

    def test_a_redo_held_on_the_same_model_by_the_cap_gets_more_effort(self):
        self.options(max_tier="opus")
        hard = "investigate why auth is flaky and redesign it across every service"
        first = self.decide("general-purpose", hard)
        self.assertEqual((first["level"], first["model"], first["effort"]), ("hardest", "opus", "xhigh"))
        self.pre("general-purpose", hard)
        self.done("general-purpose", hard, "claude-opus-5-5", model="opus", effort="xhigh")
        again = self.decide("general-purpose", hard)
        self.assertEqual((again["level"], again["model"], again["effort"]), ("frontier", "opus", "max"))

    def test_redo_escalation_stops_at_the_top_and_skips_named_models(self):
        hard = "research a novel fix for the auth migration across every service, root cause unknown"
        self.assertEqual(self.decide("general-purpose", hard)["level"], "frontier")
        self.pre("general-purpose", hard)
        self.done("general-purpose", hard, "claude-fable-5-2", model="fable", effort="high")
        self.assertEqual(self.decide("general-purpose", hard)["level"], "frontier")
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", model="haiku", effort="low")
        named = self.decide("general-purpose", LOOKUP, model="haiku")
        self.assertEqual((named["source"], named["escalated_from"]), ("caller", None))

    def test_the_ledger_names_the_level_a_redo_climbed_from(self):
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", model="haiku", effort="low")
        self.pre("general-purpose", LOOKUP)
        spawns = [r for r in self.ledger() if r.get("event") == "spawn"]
        self.assertEqual([(r["level"], r["escalated_from"]) for r in spawns], [("lookup", None), ("standard", "lookup")])

    def test_an_error_also_settles_the_call(self):
        self.pre("general-purpose", LOOKUP)
        hooks.agent_failed({**self.call("general-purpose", LOOKUP, model="haiku"), "error": "timed out"})
        self.assertIsNotNone(self.pre("general-purpose", LOOKUP))

    def test_a_background_launch_settles_the_call_but_is_not_its_result(self):
        self.pre("general-purpose", LOOKUP)
        self.launched("general-purpose", LOOKUP, "claude-haiku-5-5", "a1", model="haiku", effort="low")
        again = self.decide("general-purpose", LOOKUP)  # the same brief, while the first still runs
        self.assertEqual((again["action"], again["level"], again["escalated_from"]), ("rewrite", "lookup", None))
        self.stopped("a1")
        again = self.decide("general-purpose", LOOKUP)
        self.assertEqual((again["level"], again["model"], again["escalated_from"]), ("standard", "sonnet", "lookup"))

    def test_claude_reissuing_the_rewritten_call_is_fine(self):
        for prompt in ("list the files under a", "list the files under b"):
            self.pre("general-purpose", prompt)
            self.assertIsNone(self.pre("general-purpose", prompt, model="haiku", effort="low"))
        self.assertEqual(self.ledger()[-1]["source"], "caller")
        self.assertTrue(health.load().healthy("haiku"))
        self.assertEqual(learn.stats()["lookup"]["votes"], 0)

    def test_a_model_that_never_runs_is_routed_around_after_two_refused_rewrites(self):
        for prompt in ("list the files under a", "list the files under b"):
            self.pre("general-purpose", prompt)
            self.next_message()
            self.pre("general-purpose", prompt)
        self.assertFalse(health.load().healthy("haiku"))
        self.assertEqual(self.decide("general-purpose", "list the files under c")["model"], "sonnet")
        self.assertIn(health.REFUSED, self.out("models"))

    def test_a_refused_effort_only_rewrite_under_force_passes_through(self):
        self.options(CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1", max_tier="sonnet")
        for session_id in ("s1", "s2"):  # s2's own model is unknown
            self.assertEqual(self.pre("general-purpose", LOOKUP, session_id=session_id)["updatedInput"]["effort"], "medium")
            for _ in range(2):
                self.next_message()
                self.assertIsNone(self.pre("general-purpose", LOOKUP, session_id=session_id))
            self.assertIn("passed through", self.ledger()[-1]["reason"])
        self.assertTrue(health.load().healthy("opus"))  # the rewrite never changed the model
        self.assertEqual(learn.stats()["lookup"]["calls"], 2)  # a pass-through isn't a routed call

    def test_a_model_known_to_work_is_not_blamed(self):
        health.ran("haiku", "claude-haiku-5-5", "haiku", forced=False)
        for prompt in ("list the files under a", "list the files under b"):
            self.pre("general-purpose", prompt)
            self.next_message()
            self.pre("general-purpose", prompt)
        self.assertTrue(health.load().healthy("haiku"))

    def test_the_cap_holds_even_when_a_lowering_rewrite_did_not_take(self):
        self.options(max_tier="sonnet")
        first = self.pre("general-purpose", LOOKUP, model="fable")
        self.assertEqual(first["updatedInput"]["model"], "sonnet")
        self.next_message()
        again = self.pre("general-purpose", LOOKUP, model="fable")
        self.assertEqual(again["permissionDecision"], "deny")
        self.assertIn('model: "sonnet"', again["permissionDecisionReason"])
        self.assertIsNone(self.pre("general-purpose", LOOKUP, model="sonnet"))

    def test_a_refused_rewrite_is_denied_when_the_untouched_call_would_break_the_cap(self):
        # The session runs on opus, so the call left untouched would inherit opus.
        self.options(max_tier="sonnet")
        self.assertEqual(self.pre("general-purpose", LOOKUP)["updatedInput"]["model"], "haiku")
        self.next_message()
        again = self.pre("general-purpose", LOOKUP)
        self.assertEqual(again["permissionDecision"], "deny")
        self.assertIn('model: "haiku"', again["permissionDecisionReason"])

    def test_pending_calls_survive_a_new_user_prompt(self):
        self.pre("general-purpose", LOOKUP)
        hooks.user_prompt({"session_id": "s1", "prompt": "try that again"})
        self.next_message()
        self.assertIsNone(self.pre("general-purpose", LOOKUP))

    def test_copies_of_one_call_issued_together_are_all_routed(self):
        for _ in range(3):
            self.assertEqual(self.pre("general-purpose", LOOKUP)["updatedInput"]["model"], "haiku")
        self.assertTrue(health.load().healthy("haiku"))

    def test_a_redo_at_the_top_of_the_ladder_gets_more_effort(self):
        hard = "research a novel fix for the auth migration across every service, root cause unknown"
        self.pre("general-purpose", hard)
        self.done("general-purpose", hard, "claude-fable-5-2", model="fable", effort="high")
        again = self.decide("general-purpose", hard)
        self.assertEqual((again["action"], again["level"], again["model"], again["effort"], again["escalated_from"]),
                         ("rewrite", "frontier", "fable", "xhigh", None))

    def test_a_redo_keeps_the_effort_the_call_names(self):
        self.pre("general-purpose", HARD)
        self.done("general-purpose", HARD, "claude-opus-5-5", model="opus", effort="high")
        for named in ("max", "low"):
            with self.subTest(effort=named):
                again = self.decide("general-purpose", HARD, effort=named)
                self.assertEqual((again["escalated_from"], again["model"], again["effort"]), ("complex", "opus", None))

    def test_a_redo_that_would_repeat_the_run_at_the_environments_effort_goes_one_model_up(self):
        for sid, name in (("e1", "CLAUDE_CODE_SUBAGENT_EFFORT"), ("e2", "CLAUDE_CODE_EFFORT_LEVEL")):
            with self.subTest(env=name):
                self.options(**{name: "high"})
                self.session_on("claude-sonnet-5-5", sid)
                first = self.decide("general-purpose", HARD, session_id=sid)
                self.assertEqual((first["level"], first["model"], first["effort"]), ("complex", "opus", None))
                self.pre("general-purpose", HARD, session_id=sid)
                self.done("general-purpose", HARD, "claude-opus-5-5", model="opus", session_id=sid)
                again = self.decide("general-purpose", HARD, session_id=sid)  # hardest is opus too: the same run
                self.assertEqual((again["escalated_from"], again["model"], again["effort"]), ("complex", "fable", None))
                self.assertIn("so it goes to fable", again["reason"])
                os.environ.pop(name)


class Calls(Sandbox):
    def route(self, tool_input: dict, model: str = "haiku", spawn_id: str | None = None, **fields) -> calls.Route | None:
        """A spawn of this call was routed, as `spawn.record` writes it; returns the earlier route."""
        fields = {"level": cfg.Level.lookup, "effort": "low", "source": "router", "path": "mod", "sent": None, **fields}
        return calls.routed("s1", calls.identify(tool_input)[1], calls.Route(model=model, **fields), spawn_id)

    def came_back(self, tool_input: dict, kind: str = "result", **fields) -> calls.Returned:
        agent, key = calls.identify(tool_input)
        return calls.returned("s1", calls.Outcome(kind, key=key, agent=agent, **fields))

    def test_a_call_is_known_by_its_agent_and_prompt(self):
        agent, key = calls.identify({"prompt": "x"})
        self.assertEqual(agent, "general-purpose")
        self.assertEqual(key, calls.identify({"subagent_type": "general-purpose", "prompt": "x", "model": "opus"})[1])
        self.assertNotEqual(key, calls.identify({"subagent_type": "Explore", "prompt": "x"})[1])
        self.assertEqual(calls.identify({"subagent_type": ["a"], "prompt": "x"})[0], "general-purpose")

    def test_models_used_reads_only_a_list_of_model_ids(self):
        self.assertEqual(calls.models_used(SWAP["swapped"]), ["claude-sonnet-5-5", "claude-sonnet-5"])
        for junk in (["a-1", "a-1"], ["claude-sonnet-5-5"], "claude-sonnet-5", [1, None],
                     ["claude-sonnet-5-5", "has spaces"], ["a-1", "a-1", "x" * 101], {}, None, "text", []):
            self.assertEqual(calls.models_used({"modelsUsed": junk}), [], junk)
        self.assertEqual(calls.models_used(SWAP["plain"]), [])
        self.assertEqual(calls.models_used({"modelsUsed": ["a", 3, "b"]}), ["a", "b"])
        many = [f"m{i}" for i in range(7)]
        self.assertEqual(calls.models_used({"modelsUsed": many}), many[:5])
        self.assertEqual(calls.models_used({"modelsUsed": ["a", "a", "a", "a", "a", "b"]}), [])

    def test_only_runs_at_their_level_are_redone(self):
        for source, rewrote in (("caller", False), (None, False), ("caller", True), ("router", False), ("pin", False),
                                ("rule", False)):
            call = {"prompt": f"list {source} {rewrote}"}
            self.route(call, source=source, rewrote=rewrote)
            self.assertTrue(self.came_back(call).finished, source)  # every foreground result finishes its call
            self.assertEqual(calls.finished_run("s1", calls.identify(call)[1]) is not None,
                             source in ("router", "pin", "rule") or rewrote, (source, rewrote))

    def test_a_finished_run_counts_in_its_own_request_or_window(self):
        call = {"prompt": LOOKUP}
        key = calls.identify(call)[1]
        self.route(call)
        self.came_back(call)
        self.assertIsNotNone(calls.finished_run("s1", key))
        with self.clock_at(self.clock + calls.REDO_WINDOW + 1):
            self.assertIsNone(calls.finished_run("s1", key))
        request.begin("s1", prompt_id="p1", level="lookup", need="haiku", session_tier=None, named=None)
        self.assertIsNone(calls.finished_run("s1", key))

    def test_each_outcome_kind_says_whether_it_finished_the_call(self):
        call = {"prompt": LOOKUP}
        key = calls.identify(call)[1]
        finished = lambda: calls.finished_run("s1", key) is not None  # noqa: E731
        self.route(call)
        for kind, fields, says in (
                ("failure", {"error": "timed out"}, False),
                ("result", {"launched": True, "agent_id": "a1"}, False),
                ("result", {"models_used": ("claude-haiku-5-5", "claude-haiku-5")}, False),
                ("stop", {"agent_id": "a9"}, False)):  # an agent this session never launched
            self.assertEqual((self.came_back(call, kind, **fields).finished, finished()), (says, False), (kind, fields))
        self.assertEqual((self.came_back(call, "stop", agent_id="a1", answered=False).finished, finished()), (False, False))
        self.came_back(call, launched=True, agent_id="a1")
        self.assertEqual((self.came_back(call, "stop", agent_id="a1").finished, finished()), (True, True))

    def test_a_swap_takes_back_the_count_its_own_route_made(self):
        call = {"prompt": LOOKUP}
        self.route(call, counted=cfg.Level.lookup)
        self.spawned()
        self.assertEqual(learn.stats()["lookup"]["calls"], 1)
        got = self.came_back(call, models_used=("claude-haiku-5-5", "claude-haiku-5"))
        self.assertEqual((got.note, got.finished, learn.stats()["lookup"]["calls"]), (calls.SWAPPED, False, 0))
        self.route(call, source="caller")  # a later run of the same call that counted nothing
        self.came_back(call, models_used=("claude-haiku-5-5", "claude-haiku-5"))
        self.assertEqual(learn.stats()["lookup"]["calls"], 0)

    def test_a_route_is_kept_per_spawn_and_the_earlier_one_is_this_requests(self):
        call = {"prompt": LOOKUP}
        self.assertIsNone(self.route(call, spawn_id="t1", sent="claude-haiku-5-5"))
        self.assertEqual(self.route(call, spawn_id="t2").model, "haiku")  # t1 stood earlier in this request
        self.assertEqual(calls.route_of("s1", calls.identify(call)[1], "t1").sent, "claude-haiku-5-5")
        self.assertIsNone(calls.route_of("s1", calls.identify(call)[1], "t2").sent)
        self.assertIsNotNone(calls.route_of("s1", calls.identify(call)[1], "no-such-id"))  # the call's latest
        request.begin("s1", prompt_id="p2", level="lookup", need="haiku", session_tier=None, named=None)
        self.assertIsNone(self.route(call, spawn_id="t3", prompt_id="p2"))
        self.assertIsNone(self.route({"prompt": "other"}, source="caller"))
        self.assertIsNone(self.route({"prompt": "other"}))  # a caller's run is no run to be redone

    def test_a_sessions_calls_are_dropped_a_day_after_it_last_wrote_them(self):
        # Every spawn rewrites these files whole under a lock it waits 1 s for, so old sessions cost lost writes.
        def run(session: str) -> None:
            for key, agent_id in (("k1", None), ("k2", "a1")):
                calls.routed(session, key, calls.Route(model="haiku", sent=None, level=cfg.Level.lookup, effort="low",
                                                       source="router", path="mod"))
                calls.returned(session, calls.Outcome("result", key=key, launched=bool(agent_id), agent_id=agent_id))

        def sessions() -> dict:
            return {name: sorted(store.load_state_file(store._keyed(name))) for name in ("routes", "launched", "done")}

        run("old")
        self.advance(calls.KEPT_FOR)
        run("recent")
        self.assertEqual(sessions(), dict.fromkeys(("routes", "launched", "done"), ["old", "recent"]))
        self.advance(1)
        run("new")
        self.assertEqual(sessions(), dict.fromkeys(("routes", "launched", "done"), ["new", "recent"]))
        store.save_entry("sessions", "kept", {"model": "opus"})
        self.advance(calls.KEPT_FOR + 1)
        store.save_entry("sessions", "next", {"model": "opus"})
        self.assertEqual(sorted(store.load_state_file(store._keyed("sessions"))), ["kept", "next"])  # other files keep theirs

    def test_a_route_with_junk_in_it_reads_as_none(self):
        key = calls.identify({"prompt": LOOKUP})[1]
        store.save_entry("routes", "s1", {key: {"call": key, "model": 7, "level": "nope", "at": "later"}, "x": 3})
        route = calls.route_of("s1", key)
        self.assertEqual((route.model, route.level, route.at), (None, None, 0.0))
        self.assertIsNone(calls.route_of("s1", "unknown"))

    def test_a_stored_time_that_is_not_a_number_sorts_as_oldest(self):
        key = calls.identify({"prompt": LOOKUP})[1]
        for odd in ("x", None, True, float("nan"), 10 ** 400):
            store.save_entry("routes", "s1", {"a": {"call": key, "model": "opus", "at": odd},
                                              "b": {"call": key, "model": "haiku", "at": 5}})
            self.assertEqual(calls.route_of("s1", key, "no-such-id").model, "haiku", odd)
            records = {"a": {"at": odd}, "b": {"at": 5}, "c": {"at": 6}}
            store.keep(records, "d", {}, kept=3)
            self.assertEqual(sorted(records), ["b", "c", "d"], odd)
            store.update_dict(store._keyed("routes"), lambda data: data.update(junk={"_at": odd}))
            for number in range(store.KEEP_ENTRIES):
                store.mutate_entry("routes", f"other{number}", lambda entry: None)
            self.assertNotIn("junk", store.load_state_file(store._keyed("routes")), odd)


class Fallback(Sandbox):
    def pending(self, key: str, at: float) -> None:
        with self.clock_at(at):
            fallback.remember("s1", key, {"model": "haiku", "was": None, "level": "lookup", "effort": None})

    def test_a_rewrite_waits_for_its_result_and_only_a_result_finishes_the_run(self):
        self.session_on("claude-opus-5-5")
        key = calls.identify({"prompt": LOOKUP})[1]
        self.pre("general-purpose", LOOKUP)
        self.assertIsNone(fallback.refused("s1", key))  # a copy issued alongside it
        self.next_message()
        self.assertEqual(fallback.refused("s1", key)["model"], "haiku")
        hooks.agent_failed({**self.call("general-purpose", LOOKUP), "error": "timed out"})  # accepted, nothing finished
        self.assertEqual((fallback.refused("s1", key), calls.finished_run("s1", key)), (None, None))
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5")
        self.assertEqual(calls.finished_run("s1", key)["level"], "lookup")

    def test_a_spawn_decides_without_writing_and_records_the_guarded_decision(self):
        hook_input = self.call("general-purpose", LOOKUP)
        spawned = spawn.decide(hook_input, settings.Snapshot(hook_input))
        self.assertEqual((self.ledger(), calls.strongest(cfg.load(), "s1")), ([], None))
        spawned.decision.update(action="keep", model=None, reason="held")
        spawn.record(spawned, "fallback")
        self.assertEqual([(r["action"], r["reason"]) for r in self.ledger()], [("keep", "held")])

    def test_waiting_rewrites_keep_the_most_recent(self):
        for i in range(store.KEPT_RECORDS + 2):
            fallback.remember("s1", calls.identify({"prompt": f"list {i}"})[1], self.decide("general-purpose", f"list {i}"))
            self.advance(0.001)
        self.next_message()
        self.assertIsNone(fallback.refused("s1", calls.identify({"prompt": "list 0"})[1]))
        self.assertIsNone(fallback.refused("s1", calls.identify({"prompt": "list 1"})[1]))
        self.assertIsNotNone(fallback.refused("s1", calls.identify({"prompt": "list 2"})[1]))

    def test_a_rewrite_stamped_in_the_future_counts_as_refused(self):
        self.pending("k", time.time() + 3600)
        self.assertEqual(fallback.refused("s1", "k")["model"], "haiku")  # passed on untouched, never looped
        self.pending("k", time.time() - 1)
        self.assertIsNone(fallback.refused("s1", "k"))  # a copy issued alongside it

    def test_a_copy_after_a_small_step_back_is_still_a_copy(self):
        # A fan-out's copies, with the clock stepped back a few seconds between their hooks (an NTP correction).
        written = self.clock
        self.pending("k", written)
        for back in (0.5, 3, fallback.REFUSED_AFTER - 0.5):
            with self.clock_at(written - back):
                self.assertIsNone(fallback.refused("s1", "k"), back)
        with self.clock_at(written + 1):
            self.assertIsNone(fallback.refused("s1", "k"))


class Request(Sandbox):
    def setUp(self):
        super().setUp()
        self.config = cfg.load()

    def start(self, **fields):
        request.begin("s1", **{"prompt_id": "p1", "level": "hardest", "need": "opus",
                               "session_tier": "sonnet", "named": None, **fields})

    def route(self, model: str, prompt_id: str | None = "p1") -> None:
        calls.routed("s1", model, calls.Route(model=model, sent=None, level=Level.lookup, effort="low",
                                              source="router", path="mod", prompt_id=prompt_id))

    def test_the_strongest_spawn_is_read_from_the_routes_and_a_new_request_clears_it(self):
        self.start()
        for model in ("haiku", "fable", "opus"):
            self.route(model)
        self.assertEqual(calls.strongest(self.config, "s1"), "fable")
        self.start(prompt_id="p2", level="lookup")
        self.assertIsNone(calls.strongest(self.config, "s1"))
        self.route("sonnet", "p2")
        self.assertEqual(calls.strongest(self.config, "s1"), "sonnet")

    def test_a_request_with_no_id_counts_the_spawns_written_since_it_started(self):
        self.start(prompt_id=None)
        self.route("opus", None)
        self.assertEqual(calls.strongest(self.config, "s1"), "opus")
        self.advance(1)
        self.start(prompt_id=None, level="lookup")
        self.assertIsNone(calls.strongest(self.config, "s1"))
        self.advance(1)
        self.route("haiku", None)
        self.assertEqual(calls.strongest(self.config, "s1"), "haiku")

    def test_the_floor_is_the_requests_level_until_a_delivered_message(self):
        self.start()
        self.assertEqual(request.load("s1").floor, "hardest")
        request.deliver("s1")
        self.assertEqual((request.load("s1").floor, request.load("s1").level), (None, "hardest"))
        self.start()
        self.assertEqual(request.load("s1").floor, "hardest")

    def test_the_escalation_is_asked_once_per_request(self):
        self.start()
        request.mark_escalation("s1", "haiku")
        self.assertEqual((request.load("s1").escalation_asked, request.load("s1").session_tier), (True, "haiku"))
        self.start()
        self.assertFalse(request.load("s1").escalation_asked)

    def test_a_session_without_a_request_reads_empty(self):
        self.assertEqual(request.load("nobody"), request.Request())
        store.save_entry("prompts", "s1", {"level": 5, "below": "yes", "calls": []})  # junk no module writes
        self.assertEqual(request.load("s1"), request.Request())


class Mod(Sandbox):
    """The calls hooks/router.ts makes for agent.spawn, turn.start and turn.complete."""

    def spawn(self, agent: str, prompt: str, parent: str = "claude-opus-5-5", **extra) -> dict | None:
        tool_input = {"subagent_type": agent, "prompt": prompt, "description": "d", **extra}
        return modpath.spawn({"session_id": "s1", "parent_model": parent,
                                "kind": "subagent", "tool_input": tool_input})

    def test_spawns_are_routed_from_the_parents_exact_model(self):
        self.assertEqual(self.spawn("general-purpose", LOOKUP), {"model": "haiku", "effort": "medium",
                                                                 "reason": "lookup subtask"})
        self.assertEqual(self.spawn("general-purpose", HARD)["model"], "opus")
        self.assertIsNone(self.spawn("general-purpose", BUILD, model="sonnet", effort="high"))
        self.assertEqual(self.ledger()[-1]["via"], "mod")

    def test_the_settings_hook_stands_down_once_the_mod_is_running(self):
        self.session_on("claude-opus-5-5")
        self.assertIsNotNone(self.pre("general-purpose", LOOKUP))
        modpath.start({"session_id": "s1"})
        count = len(self.ledger())
        self.assertIsNone(self.pre("general-purpose", "list the tests"))
        self.assertEqual(len(self.ledger()), count)

    def test_a_spawn_redone_after_its_result_runs_one_level_up(self):
        self.spawn("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5")
        self.assertEqual(self.spawn("general-purpose", LOOKUP)["model"], "sonnet")

    def test_a_workflow_stage_is_never_redone(self):
        stage = lambda **tool: modpath.spawn({  # noqa: E731
            "session_id": "s1", "cwd": str(self.project), "parent_model": "claude-opus-5-5", "kind": "workflow",
            "tool_input": {"subagent_type": "general-purpose", "prompt": BUILD, **tool}})
        plain = stage(description="Build step")
        self.assertEqual(plain["model"], "sonnet")
        self.assertEqual(stage(description="Redo: build step"), plain)  # a mark on a stage changes nothing
        self.spawn("general-purpose", BUILD, description="Build step")
        self.done("general-purpose", BUILD, "claude-sonnet-5-5", description="Build step")
        self.assertEqual(stage(description="Build step"), plain)  # a subagent's finished run is no run of the stage
        self.assertEqual(stage(description="Redo: build step"), plain)
        self.assertEqual(learn.stats().get("standard", {}).get("votes", 0), 0)

    def test_copies_of_one_brief_launched_in_the_background_all_run_at_its_level(self):
        for agent_id in ("a1", "a2", "a3"):
            self.assertEqual(self.spawn("general-purpose", LOOKUP)["model"], "haiku", agent_id)
            self.launched("general-purpose", LOOKUP, "claude-haiku-5-5", agent_id)
        self.assertEqual(learn.stats()["lookup"]["votes"], 0)

    def test_a_background_spawn_is_redone_one_level_up_only_after_it_stops(self):
        self.spawn("general-purpose", LOOKUP)
        self.launched("general-purpose", LOOKUP, "claude-haiku-5-5", "a1")
        self.stopped("a9")  # another agent, one this session never launched in the background
        self.assertEqual(self.spawn("general-purpose", LOOKUP)["model"], "haiku")
        self.stopped("a1")
        self.assertEqual(self.spawn("general-purpose", LOOKUP)["model"], "sonnet")
        self.stopped("a1")  # a second stop for the same agent changes nothing
        self.assertEqual(store.load_entry("launched", "s1").get("a1"), None)

    def test_a_background_agent_that_stops_without_an_answer_is_not_a_result(self):
        self.spawn("general-purpose", LOOKUP)
        self.launched("general-purpose", LOOKUP, "claude-haiku-5-5", "a1")
        self.stopped("a1", answer=" ")
        self.assertEqual(self.spawn("general-purpose", LOOKUP)["model"], "haiku")
        self.launched("general-purpose", LOOKUP, "claude-haiku-5-5", "a2")
        self.stopped("a2", answer=None)  # a Claude Code that doesn't send the answer
        self.assertEqual(self.spawn("general-purpose", LOOKUP)["model"], "sonnet")

    def test_a_background_agent_that_handed_back_its_report_is_a_result(self):
        self.spawn("general-purpose", LOOKUP)
        self.launched("general-purpose", LOOKUP, "claude-haiku-5-5", "a1")
        self.stopped("a1", answer="", transcript=self.handback("src/a.py and src/b.py"))
        self.assertEqual(self.spawn("general-purpose", LOOKUP)["model"], "sonnet")

    def test_a_handback_with_no_message_is_no_answer(self):
        self.spawn("general-purpose", LOOKUP)
        for agent_id, transcript in (("a1", self.handback(" ")), ("a2", str(Path(self.tmp.name) / "missing.jsonl"))):
            self.launched("general-purpose", LOOKUP, "claude-haiku-5-5", agent_id)
            self.stopped(agent_id, answer="", transcript=transcript)
            self.assertEqual(self.spawn("general-purpose", LOOKUP)["model"], "haiku", agent_id)

    def test_a_lagging_handback_transcript_is_no_answer(self):
        lines = (FIXTURES / "subagent-handback.jsonl").read_text().splitlines(keepends=True)
        lagging = {"only-the-blank-row": lines[1], "truncated-last-line": lines[0][:60]}
        self.spawn("general-purpose", LOOKUP)
        for agent_id, text in lagging.items():
            path = Path(self.tmp.name) / f"{agent_id}.jsonl"
            path.write_text(text)
            self.launched("general-purpose", LOOKUP, "claude-haiku-5-5", agent_id)
            self.stopped(agent_id, answer="", transcript=str(path))
            self.assertEqual(self.spawn("general-purpose", LOOKUP)["model"], "haiku", agent_id)

    def test_a_transcript_read_that_raises_still_counts_the_stop(self):
        self.spawn("general-purpose", LOOKUP)
        self.launched("general-purpose", LOOKUP, "claude-haiku-5-5", "a1")
        with mock.patch.object(session, "handed_back", side_effect=ValueError("boom")):
            self.stopped("a1", answer="", transcript=self.handback("src/a.py"))
        self.assertEqual(self.spawn("general-purpose", LOOKUP)["model"], "haiku")

    def test_turns_and_spawns_keep_the_mod_marked_after_a_clear(self):
        self.assertFalse(modpath.active("s2"))
        modpath.turn({"session_id": "s2", "text": LOOKUP})
        self.assertTrue(modpath.active("s2"))
        modpath.spawn({"session_id": "s3", "parent_model": "claude-opus-5-5", "tool_input": {"prompt": LOOKUP}})
        self.assertTrue(modpath.active("s3"))

    def test_a_pinned_agent_redone_gets_more_effort(self):
        self.agent("pinned", "opus")
        self.assertEqual(self.spawn("pinned", LOOKUP)["effort"], "medium")
        self.done("pinned", LOOKUP, "claude-opus-5-5")
        pick = self.spawn("pinned", LOOKUP)
        self.assertEqual((pick["model"], pick["effort"]), ("opus", "high"))

    def test_workflow_stages_and_teammate_roles_without_a_file_are_routed(self):
        for kind, agent in (("workflow", "workflow-subagent"), ("teammate", "helper-two")):
            pick = modpath.spawn({"session_id": "s1", "cwd": str(self.project), "parent_model": "claude-opus-5-5",
                                    "kind": kind, "tool_input": {"subagent_type": agent, "prompt": LOOKUP}})
            self.assertEqual((pick["model"], pick["effort"]), ("haiku", "medium"), kind)
        self.assertIsNone(self.spawn("no-such-agent", LOOKUP))

    def test_forced_subagent_model_gets_effort_only(self):
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="opus", CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1")
        pick = self.spawn("general-purpose", LOOKUP)
        self.assertEqual((pick["model"], pick["effort"]), (None, "medium"))

    def test_turn_effort_stays_off_without_a_first_party_credential(self):
        self.options(main_effort="true")  # off by default
        session.record("s1", "claude-opus-5-5")
        turn = lambda **extra: modpath.turn({"session_id": "s1", "text": HARD, **extra})  # noqa: E731
        self.assertIsNone(turn(first_party=False))  # a gateway: an effort change re-reads the context
        self.assertEqual(turn(first_party=True), {"level": "complex", "effort": "high"})
        self.assertEqual(turn(), {"level": "complex", "effort": "high"})  # not known: raised as before

    def test_prompt_caching_off_for_the_sessions_family_keeps_its_effort(self):
        self.options(main_effort="true")
        turn = lambda: modpath.turn({"session_id": "s1", "text": HARD})  # noqa: E731
        with mock.patch.dict(os.environ, {"DISABLE_PROMPT_CACHING_OPUS": "1"}):
            session.record("s1", "claude-opus-5-5")
            self.assertIsNone(turn())
            session.record("s1", "claude-sonnet-5-5")
            self.assertEqual(turn(), {"level": "complex", "effort": "high"})

    def test_turn_effort_is_off_by_default(self):
        session.record("s1", "claude-opus-5-5")
        self.assertIsNone(modpath.turn({"session_id": "s1", "text": HARD}))
        self.assertFalse(settings.resolve().main_effort)

    def test_turn_effort_rises_for_hard_requests_only(self):
        self.options(main_effort="true")  # off by default
        session.record("s1", "claude-opus-5-5")
        turn = lambda text: modpath.turn({"session_id": "s1", "text": text})  # noqa: E731
        self.assertEqual(turn(HARD), {"level": "complex", "effort": "high"})
        self.assertIsNone(turn(LOOKUP))
        self.assertIsNone(turn("<task-notification>done</task-notification>"))
        self.assertEqual(turn('<pasted_content id="x">\nlog\n</pasted_content id="x">\n' + HARD),
                         {"level": "complex", "effort": "high"})
        for name, value in (("CLAUDE_CODE_EFFORT_LEVEL", "low"), ("CLAUDE_CODE_USE_BEDROCK", "1"),
                            ("main_effort", "false")):
            with mock.patch.dict(os.environ):
                self.options(**{name: value})
                self.assertIsNone(turn(HARD), name)

    def test_a_max_effort_level_holds_every_effort_the_router_sets(self):
        self.options(main_effort="true")
        session.record("s1", "claude-opus-5-5")
        capped = {"user": {"maxEffortLevel": "medium"}}
        spawned = modpath.spawn({"session_id": "s1", "cwd": str(self.project), "parent_model": "claude-opus-5-5",
                                   "kind": "subagent", "settings": capped,
                                   "tool_input": {"subagent_type": "general-purpose", "prompt": HARD}})
        self.assertEqual((spawned["model"], spawned["effort"]), ("opus", "medium"))
        self.assertIn("effort held to medium by maxEffortLevel", spawned["reason"])
        self.assertIn("effort held to medium by maxEffortLevel", self.ledger()[-1]["reason"])
        self.assertEqual(modpath.turn({"session_id": "s1", "text": HARD, "settings": capped}),
                         {"level": "complex", "effort": "medium"})
        self.assertEqual(self.ledger()[-1]["reason"], "effort held to medium by maxEffortLevel")
        self.assertEqual(self.spawn("general-purpose", LOOKUP)["effort"], "medium")  # at the cap: no reason
        self.assertNotIn("maxEffortLevel", self.ledger()[-1]["reason"])
        # Without the mod, the settings files say it.
        (self.home / "settings.json").write_text(json.dumps({"maxEffortLevel": "low"}))
        out = self.pre("general-purpose", HARD, session_id="s2")
        self.assertEqual(out["updatedInput"]["effort"], "low")
        self.assertIn("effort held to low by maxEffortLevel", out["additionalContext"])
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="opus", CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1")
        self.assertEqual(self.pre("general-purpose", HARD, session_id="s3")["updatedInput"]["effort"], "low")
        route = self.out("explain", HARD)
        self.assertIn("effort: low.", route)
        self.assertIn("effort held to low by maxEffortLevel", route.split("Why:", 1)[1].splitlines()[0])
        self.assertIn("| complex | opus | low (effort held to low by maxEffortLevel) |", self.out("policy"))

    def test_turn_effort_is_left_alone_where_an_effort_change_drops_the_cache(self):
        self.options(main_effort="true")  # off by default
        turn = lambda sid="s1": modpath.turn({"session_id": sid, "text": HARD})  # noqa: E731
        session.record("s1", "claude-opus-5-5")
        self.assertEqual(turn(), {"level": "complex", "effort": "high"})
        for name in ("ANTHROPIC_BASE_URL", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "CLAUDE_CODE_USE_MANTLE",
                     "DISABLE_PROMPT_CACHING"):
            with mock.patch.dict(os.environ, {name: "1"}):
                self.assertIsNone(turn(), name)
        session.record("s1", "claude-opus-5")
        self.assertIsNone(turn())
        for model in ("opus", "opus[1m]", None):  # no version to judge, as after /clear: today's behavior
            sid = "s1" if model else "no-model"
            if model:
                session.record(sid, model)
            self.assertEqual(turn(sid), {"level": "complex", "effort": "high"}, model)
            with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://gateway"}):
                self.assertIsNone(turn(sid), model)
        live = lambda model: modpath.turn({"session_id": "s1", "text": HARD, "model": model})  # noqa: E731
        self.assertEqual(live("claude-opus-5-5"), {"level": "complex", "effort": "high"})
        session.record("s1", "claude-opus-5-5")
        self.assertIsNone(live("claude-opus-5"))  # the mod's live model beats a stale record
        self.assertEqual(live("Opus"), {"level": "complex", "effort": "high"})  # not an ID: the record decides

    def test_usage_is_priced_from_the_registry(self):
        modpath.usage({"session_id": "s1", "agent_id": "a1", "usage": {
            "model": "claude-opus-5-5", "input_tokens": 1_000_000, "output_tokens": 100_000,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}})
        row = self.ledger()[-1]
        self.assertEqual((row["event"], row["cost_usd"]), ("usage", 6.0))
        self.assertIsNone(cfg.load().cost("claude-unknown-9", {"input_tokens": 1}))
        # Opus 5.5 cache writes: $5/MTok for five minutes, $8/MTok for one hour.
        modpath.usage({"session_id": "s1", "agent_id": "a1", "usage": {
            "model": "claude-opus-5-5", "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 3_000_000, "ephemeral_5m_input_tokens": 1_000_000,
            "ephemeral_1h_input_tokens": 1_000_000}})
        self.assertEqual(self.ledger()[-1]["cost_usd"], 18.0)  # the unnamed million counts as five minutes
        modpath.usage({"session_id": "s1", "agent_id": "a1", "usage": {
            "model": "claude-opus-5-5", "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 1_000_000}})
        self.assertEqual(self.ledger()[-1]["cost_usd"], 5.0)  # no split reported
        config = cfg.load()
        self.assertEqual(config.price("claude-opus-4-7")["input"], 5)  # one entry covers Opus 4.x
        self.assertEqual(config.price("claude-opus-4-1-20250805"),  # except 4.1, at its own list price
                         {"input": 15, "output": 75, "cache_read": 1.5, "cache_write": 18.75, "cache_write_1h": 30})
        for opus_4 in ("claude-opus-4-0", "claude-opus-4-20250514"):  # Opus 4 lists at the same price as 4.1
            self.assertEqual(config.price(opus_4), config.price("claude-opus-4-1"), opus_4)
        self.assertEqual(config.price("claude-haiku-4-5-20251001")["input"], 1)
        self.assertEqual(config.price("claude-opus-5-5[1m]")["input"], 4)
        self.assertIsNone(config.price("claude-opus-5-6"))  # a new version is never priced as its neighbour

    def test_results_are_checked_against_the_model_the_mod_routed_to(self):
        self.options(agent_pins="override")
        self.agent("rev", "opus")
        for prompt in ("list the files under a", "list the files under b"):
            self.assertEqual(self.spawn("rev", prompt)["model"], "haiku")
            self.done("rev", prompt, "claude-haiku-5-5")
        self.assertTrue(health.load().healthy("opus"))
        self.assertEqual(self.ledger()[-1]["asked"], "haiku")
        hooks.agent_failed({**self.call("rev", "list the files under a"),
                            "error": "API Error: 404 not_found_error: model: claude-haiku-5-5"})
        seen = health.load()
        self.assertEqual([seen.healthy("haiku"), seen.healthy("opus")], [True, True])
        hooks.agent_failed({**self.call("rev", "list the files under a"),
                            "error": "API Error: 404 not_found_error: model: claude-haiku-5-5"})
        seen = health.load()
        self.assertEqual([seen.healthy("haiku"), seen.healthy("opus")], [False, True])

    def test_a_pick_that_never_arrived_is_no_substitution(self):
        # The mod's answer timed out, or a later mod replaced it: the call ran on what it would have had untouched.
        parent = "claude-sonnet-5-5"
        for number in range(3):
            brief = f"{HARD} {number}"
            self.assertEqual(self.spawn("general-purpose", brief, parent=parent)["model"], "opus")
            self.done("general-purpose", brief, parent)
        self.assertIsNone(store.load_state().get("health"))
        self.assertEqual(calls.route_of("s1", calls.identify({"subagent_type": "general-purpose", "prompt": f"{HARD} 0"})[1]).was, "sonnet")
        for number in range(2):  # a model that is neither the pick nor the parent is one
            brief = f"{HARD} other {number}"
            self.spawn("general-purpose", brief, parent=parent)
            self.done("general-purpose", brief, "claude-haiku-5-5")
        self.assertEqual(health.load().why_routed_around("opus"), "ran as claude-haiku-5-5 instead")

    def test_a_fallback_rewrite_that_ran_on_the_untouched_model_is_a_substitution(self):
        # On the fallback the rewrite is applied before the call starts, so a run on the parent's model didn't take it.
        self.session_on("claude-sonnet-5-5")
        for number in range(2):
            brief = f"{HARD} {number}"
            self.assertEqual(self.pre("general-purpose", brief)["updatedInput"]["model"], "opus")
            self.done("general-purpose", brief, "claude-sonnet-5-5", model="opus", effort="high")
        self.assertEqual(health.load().why_routed_around("opus"), "ran as claude-sonnet-5-5 instead")

    def test_a_result_reads_no_agent_files(self):
        self.agent("rev", "opus")
        self.spawn("rev", LOOKUP)
        with mock.patch.object(agents, "find", side_effect=AssertionError("agents.find on a result")):
            self.done("rev", LOOKUP, "claude-opus-5-5")
            for _ in range(2):
                hooks.agent_failed({**self.call("rev", LOOKUP), "error": "API Error: 404 not_found_error: model"})
        seen = health.load()
        self.assertEqual([seen.healthy("opus"), seen.healthy("haiku")], [False, True])


class Health(Sandbox):
    def setUp(self):
        super().setUp()
        self.now = self.clock

    def at(self, days):
        return self.clock_at(self.now + days * 86400)

    def test_a_swapped_run_is_no_evidence(self):
        health.ran("fable", "claude-opus-4-8", "opus", False, swapped=True)
        self.assertEqual(store.load_state().get("health"), None)
        health.ran("fable", "claude-opus-4-8", "opus", False)
        self.assertEqual(store.load_state()["health"]["fable"]["substituted"], 1)

    def test_only_a_model_error_counts_and_an_interrupt_never_does(self):
        for text in ERRORS["model_error"]:
            self.assertTrue(health.is_model_error(text), text)
            self.assertFalse(health.is_model_error(text, interrupted=True), text)
        for text in ERRORS["other"]:
            self.assertFalse(health.is_model_error(text), text)

    def test_two_model_errors_route_a_model_around_and_outages_grow(self):
        with self.at(0):
            self.assertEqual(health.failed("fable", "404 model not found"), 1)
            self.assertTrue(health.load().healthy("fable"))
            health.failed("fable", "404 model not found")
            self.assertEqual(health.load().why_routed_around("fable"), "404 model not found")
        with self.at(0.9):
            self.assertFalse(health.load().healthy("fable"))
        with self.at(1.1):
            self.assertTrue(health.load().healthy("fable"))
            health.failed("fable", "404 model not found")  # after an outage one failure is enough
        with self.at(1.1 + 2.9):
            self.assertFalse(health.load().healthy("fable"))
        with self.at(1.1 + 3.1):
            self.assertTrue(health.load().healthy("fable"))

    def test_parallel_failures_during_an_outage_never_stack_it(self):
        with self.at(0):
            for _ in range(6):
                health.failed("fable", "404 model not found")
            for _ in range(3):
                health.ran("opus", "claude-sonnet-5-5", "sonnet", forced=False)
        with self.at(1.1):
            self.assertEqual([health.load().healthy("fable"), health.load().healthy("opus")], [True, True])
        self.assertEqual(store.load_state()["health"]["fable"]["outages"], 1)

    def test_a_run_as_asked_clears_trouble_and_names_the_model(self):
        health.failed("opus", "model not found")
        health.failed("opus", "model not found")
        health.ran("opus", "claude-opus-5-5", "opus", forced=False)
        seen = health.load()
        self.assertEqual((seen.healthy("opus"), seen.why_routed_around("opus"), seen.last_ran_as("opus")),
                         (True, None, "claude-opus-5-5"))
        self.assertEqual(seen.seen_running(), ["claude-opus-5-5"])

    def test_two_substitutions_route_a_model_around(self):
        health.ran("fable", "claude-opus-5-5", "opus", forced=False)
        self.assertTrue(health.load().healthy("fable"))
        health.ran("fable", "claude-opus-5-5", "opus", forced=False)
        self.assertEqual(health.load().why_routed_around("fable"), "ran as claude-opus-5-5 instead")

    def test_a_run_on_the_untouched_model_is_no_substitution(self):
        for _ in range(2):
            health.ran("fable", "claude-opus-5-5", "opus", forced=False, untouched="opus")
        self.assertEqual(store.load_state().get("health"), None)
        health.ran("fable", "claude-opus-5-5", "opus", forced=False, untouched="sonnet")
        self.assertEqual(store.load_state()["health"]["fable"]["substituted"], 1)

    def test_a_forced_run_or_an_unknown_family_is_no_evidence(self):
        for _ in range(2):
            health.ran("fable", "claude-opus-5-5", "opus", forced=True)
            health.ran("fable", "claude-x", None, forced=False)
            health.ran(None, "claude-opus-5-5", "opus", forced=False)
        self.assertEqual(store.load_state().get("health"), None)

    def test_a_newest_id_is_routed_around_after_one_failure(self):
        health.failed("claude-haiku-5-5", "404 not_found_error", newest=True)
        self.assertFalse(health.load().healthy("claude-haiku-5-5"))

    def test_a_refused_rewrite_counts_only_against_a_model_never_seen_running(self):
        health.refused("haiku")
        health.refused("haiku")
        self.assertEqual(health.load().why_routed_around("haiku"), health.REFUSED)
        health.ran("sonnet", "claude-sonnet-5-5", "sonnet", forced=False)
        health.refused("sonnet")
        health.refused("sonnet")
        self.assertTrue(health.load().healthy("sonnet"))

    def test_an_outage_lasts_no_longer_than_its_days_after_the_clock_moves_back(self):
        with self.at(19):  # recorded before a 19-day jump back
            health.failed("opus", "model not found")
            health.failed("opus", "model not found")
        with self.at(0):
            seen = health.load()
            self.assertFalse(seen.healthy("opus"))
            seen.settle()
        with self.at(health.OUTAGE_DAYS[0] + 0.01):
            self.assertTrue(health.load().healthy("opus"))

    def test_an_ordinary_read_writes_nothing(self):
        health.failed("fable", "model not found")
        health.failed("fable", "model not found")
        health.ran("sonnet", "claude-sonnet-5-5", "sonnet", forced=False)
        before = store.state_path().read_bytes()
        seen = health.load()
        self.assertEqual([seen.healthy("fable"), seen.healthy("sonnet"), seen.healthy("none")], [False, True, True])
        self.assertEqual(store.state_path().read_bytes(), before)

    def test_reading_a_too_long_outage_writes_nothing_and_settling_checks_the_record_again(self):
        with self.at(19):
            health.failed("opus", "model not found")
            health.failed("opus", "model not found")
        long_outage = store.state_path().read_bytes()
        with self.at(0):
            seen = health.load()
            self.assertFalse(seen.healthy("opus"))
            self.assertEqual(store.state_path().read_bytes(), long_outage)
            # Another writer clears the record after this one read it.
            health.ran("opus", "claude-opus-5-5", "opus", forced=False)
            cleared = store.state_path().read_bytes()
            seen.settle()
        self.assertEqual(store.state_path().read_bytes(), cleared)  # the cleared record wins over the stale read

    def test_a_junk_record_reads_as_healthy(self):
        store.update_state(lambda s: s.update(health={"opus": {"unavailable_until": "soon", "outages": -3},
                                                      "sonnet": "x", "haiku": {"last_model": 5},
                                                      "fable": {"unavailable_until": float("nan"), "outages": float("inf")},
                                                      "saga": {"unavailable_until": float("inf"), "outages": float("nan")},
                                                      "huge": {"unavailable_until": 10 ** 400, "outages": 10 ** 400}}))
        seen = health.load()
        self.assertEqual([seen.healthy(n) for n in ("opus", "sonnet", "haiku", "fable", "saga", "huge")], [True] * 6)
        self.assertEqual(seen.seen_running(), [])

    def test_the_record_keeps_its_shape(self):
        health.failed("opus", "model not found")
        health.failed("opus", "model not found")
        health.ran("fable", "claude-opus-5-5", "opus", forced=False)
        record = store.load_state()["health"]
        self.assertEqual(set(record["opus"]), {"fail", "last_error", "outages", "unavailable_until"})
        self.assertEqual(set(record["fable"]), {"substituted", "substituted_by"})

    def test_forget_says_whether_it_removed_a_record(self):
        self.assertEqual(health.forget(), (False, "saved"))
        health.failed("opus", "model not found")
        self.assertEqual(health.forget(), (True, "saved"))
        self.assertNotIn("health", store.load_state())


class NewestOnProviders(Sandbox):
    """Where a provider's alias resolves to an older model (the alias table in
    https://code.claude.com/docs/en/model-config.md), the mod sends the family's newest full ID."""

    LAGS = {"bedrock": ("sonnet", "haiku"), "vertex": ("sonnet", "haiku"), "foundry": ("opus", "sonnet", "haiku"),
            "anthropic_aws": ("sonnet", "haiku"), "anthropic_google_cloud": ("haiku",)}
    NEWEST = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}

    def spawn(self, prompt: str, agent: str = "general-purpose", tool_use_id: str | None = None,
              **extra) -> tuple[dict | None, dict]:
        hook_input = {"session_id": "s1", "parent_model": "claude-opus-5-5",
                      "kind": "subagent", **({"tool_use_id": tool_use_id} if tool_use_id else {}),
                      "tool_input": {"subagent_type": agent, "prompt": prompt, "description": "d", **extra}}
        return modpath.spawn(hook_input), hook_input

    def model(self, prompt: str, **extra) -> str | None:
        out = self.spawn(prompt, **extra)[0]
        return out["model"] if out else None

    def test_the_provider_comes_from_the_documented_switches(self):
        for flags, want in (((), None), (("bedrock",), "bedrock"), (("vertex",), "vertex"), (("foundry",), "foundry"),
                            (("anthropic_aws",), "anthropic_aws"),
                            (("anthropic_google_cloud",), "anthropic_google_cloud"),
                            (("bedrock", "anthropic_aws"), "bedrock"), (("foundry", "anthropic_aws"), "foundry"),
                            (("anthropic_google_cloud", "vertex"), "anthropic_google_cloud"),
                            (("mantle",), None), (("bedrock", "mantle"), None), (("bedrock", "vertex"), None)):
            with mock.patch.dict(os.environ, {f"CLAUDE_CODE_USE_{f.upper()}": "1" for f in flags}):
                self.assertEqual(settings.resolve().provider, want, flags)

    def test_each_lagging_alias_gets_its_familys_newest_id(self):
        for provider, lagging in self.LAGS.items():
            with mock.patch.dict(os.environ, {f"CLAUDE_CODE_USE_{provider.upper()}": "1"}):
                for family, prompt in (("opus", HARDEST), ("sonnet", BUILD), ("haiku", LOOKUP)):
                    want = self.NEWEST[family] if family in lagging else family
                    self.assertEqual(self.model(prompt), want, (provider, family))
        self.assertEqual((self.model(LOOKUP), self.model(BUILD)), ("haiku", "sonnet"))  # the Anthropic API

    def test_the_shipped_ids_belong_to_their_families_and_are_priced(self):
        config = cfg.shipped()
        self.assertEqual({p: tuple(f) for p, f in config.older_aliases.items()}, self.LAGS)
        for family, model_id in config.newest.items():
            self.assertEqual((config.family(model_id), model_id), (family, self.NEWEST[family]))
            self.assertIsNotNone(config.price(model_id))

    def test_a_pin_an_allowlist_a_denial_or_overrides_keep_the_alias(self):
        os.environ["CLAUDE_CODE_USE_BEDROCK"] = "1"
        os.environ["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        self.assertEqual((self.model(LOOKUP), self.model(BUILD)), ("haiku", "claude-sonnet-5-5"))
        del os.environ["ANTHROPIC_DEFAULT_HAIKU_MODEL"]
        for managed, want in (({"availableModels": ["haiku", "sonnet", "opus"]}, "claude-haiku-5-5"),
                              ({"availableModels": ["claude-haiku-5", "sonnet"]}, "claude-haiku-5-5"),
                              ({"availableModels": ["claude-haiku-5", "sonnet"], "availableModelsMatch": "exact"}, "haiku"),
                              ({"availableModels": ["claude-haiku-4-5", "sonnet", "opus"]}, "haiku"),
                              ({"deniedModels": ["claude-haiku-5-5"]}, "haiku")):
            self.managed.write_text(json.dumps(managed))
            self.assertEqual(self.model(LOOKUP), want, managed)
        self.managed.unlink()
        (self.home / "settings.json").write_text(json.dumps({"modelOverrides": {"claude-haiku-4-5": "arn:aws:x"}}))
        self.assertEqual(self.model(LOOKUP), "haiku")

    def test_a_version_the_caller_or_the_agent_names_is_left_alone(self):
        os.environ["CLAUDE_CODE_USE_BEDROCK"] = "1"
        self.assertNotEqual(self.model(LOOKUP, model="claude-haiku-4-5"), "claude-haiku-5-5")
        self.agent("old-haiku", "claude-haiku-4-5")
        out = self.spawn(LOOKUP, agent="old-haiku")[0]
        self.assertNotEqual(out and out["model"], "claude-haiku-5-5")
        self.agent("any-haiku", "haiku")
        self.assertEqual(self.spawn(LOOKUP, agent="any-haiku")[0]["model"], "claude-haiku-5-5")

    def test_an_id_the_provider_rejects_falls_back_to_the_alias(self):
        os.environ["CLAUDE_CODE_USE_FOUNDRY"] = "1"
        out, hook_input = self.spawn(LOOKUP)
        self.assertEqual(out["model"], "claude-haiku-5-5")
        failed = hooks.agent_failed({**hook_input, "error": "API Error: 404 not_found_error: model: claude-haiku-5-5"})
        self.assertIn("claude-haiku-5-5", failed["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.ledger()[-1]["model"], "claude-haiku-5-5")  # the model_error row names the ID
        self.assertEqual(self.model(LOOKUP), "haiku")
        self.assertTrue(health.load().healthy("haiku"))

    def test_parallel_failures_of_a_sent_id_never_strike_the_alias(self):
        os.environ["CLAUDE_CODE_USE_FOUNDRY"] = "1"
        refused = "API Error: 404 not_found_error: model: claude-haiku-5-5"
        copies = [self.spawn(LOOKUP, tool_use_id=f"t{i}") for i in range(3)]
        self.assertEqual({out["model"] for out, _ in copies}, {"claude-haiku-5-5"})
        hooks.agent_failed({**copies[0][1], "error": refused})
        # The call comes again, now on the alias, before its siblings' failures arrive.
        self.assertEqual(self.spawn(LOOKUP, tool_use_id="t3")[0], {"model": "haiku", "effort": "medium", "reason": "lookup subtask"})
        for _, hook_input in copies[1:]:
            hooks.agent_failed({**hook_input, "error": refused})
        self.assertEqual([r["model"] for r in self.ledger() if r["event"] == "model_error"], ["claude-haiku-5-5"] * 3)
        self.assertEqual([health.load().healthy("haiku"), health.load().healthy("claude-haiku-5-5")], [True, False])
        self.assertEqual(self.model(LOOKUP), "haiku")

    def test_a_failure_with_no_spawn_id_reads_the_calls_latest_route(self):
        os.environ["CLAUDE_CODE_USE_FOUNDRY"] = "1"
        copies = [self.spawn(LOOKUP) for _ in range(2)]
        for _, hook_input in copies:
            hooks.agent_failed({**hook_input, "error": "API Error: 404 not_found_error: model: claude-haiku-5-5"})
        self.assertEqual([health.load().healthy("haiku"), health.load().healthy("claude-haiku-5-5")], [True, False])

    def test_the_agent_call_fallback_sends_only_aliases(self):
        os.environ["CLAUDE_CODE_USE_BEDROCK"] = "1"
        hook_input = {"session_id": "s9", "cwd": str(self.project),
                      "tool_input": {"subagent_type": "general-purpose", "prompt": LOOKUP}}
        self.assertEqual(hooks.pre_agent(hook_input)["hookSpecificOutput"]["updatedInput"]["model"], "haiku")


class TurnEffortTable(Sandbox):
    """modpath.turn_effort: the main loop's effort, and the reason for every case it is left alone."""

    OPUS = "claude-opus-5-5"

    def decide(self, **facts) -> tuple:
        found = modpath.turn_effort(modpath.TurnFacts(**{"level": Level.complex, "model": self.OPUS, **facts}), cfg.shipped())
        return found.effort, found.reason

    def test_a_turn_nothing_stops_is_raised_by_its_level(self):
        for level, want in ((Level.complex, "high"), (Level.hardest, "xhigh"), (Level.frontier, "xhigh")):
            self.assertEqual(self.decide(level=level), (want, None), level)
        for level in (Level.lookup, Level.standard):
            self.assertEqual(self.decide(level=level), (None, f"no main-loop effort is set for {level}"), level)
        self.assertEqual(self.decide(level=None), (None, "the turn isn't the person's request"))

    def test_each_reason_an_effort_change_is_skipped_has_its_own_words(self):
        cases = [
            ({"mode": "advise"}, "routing is advise"),
            ({"mode": "off"}, "routing is off"),
            ({"main_effort": False}, "the main_effort option is off"),
            ({"effort_level": True}, "CLAUDE_CODE_EFFORT_LEVEL sets the effort"),
            ({"first_party": False}, "no first-party credential"),
            ({"cloud": "bedrock"}, "drops the prompt cache on bedrock"),
            ({"cloud": "vertex"}, "drops the prompt cache on vertex"),
            ({"cloud": "foundry"}, "the docs don't list Foundry"),
            ({"mantle": True}, "Mantle endpoint"),
            ({"gateway": True}, "ANTHROPIC_BASE_URL sets a gateway"),
            ({"no_betas": True}, "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"),
            ({"cache_off": ("",)}, "prompt caching is turned off (DISABLE_PROMPT_CACHING)"),
            ({"cache_off": ("opus",)}, "prompt caching is turned off for opus"),
            ({"model": "claude-opus-5"}, "drops the prompt cache on opus 5"),
            ({"model": "claude-opus-5-6"}, "drops the prompt cache on opus 5.6"),
            ({"model": "claude-fable-5"}, "drops the prompt cache on fable 5"),
            ({"model": "claude-sonnet-5"}, "drops the prompt cache on sonnet 5"),
            ({"model": "claude-opus-4-7"}, "drops the prompt cache on opus 4.7"),
        ]
        for facts, words in cases:
            effort, reason = self.decide(**facts)
            self.assertIsNone(effort, facts)
            self.assertIn(words, reason, facts)

    def test_what_keeps_the_cache_is_raised(self):
        for facts in ({"first_party": True}, {"first_party": None}, {"cache_off": ("sonnet",)},
                      {"model": "claude-opus-5-5[1m]"}, {"model": "claude-opus-5-5[1M]"}, {"model": "claude-fable-5-1"},
                      {"model": "claude-fable-5-2"}, {"model": "claude-fable-6"},
                      {"model": "claude-sonnet-5-5-20260101"}, {"model": "claude-haiku-5-5"},
                      {"model": "opus"}, {"model": None}):  # no version to judge, as after /clear
            self.assertEqual(self.decide(**facts), ("high", None), facts)

    def test_the_first_reason_found_is_the_one_given(self):
        self.assertEqual(self.decide(main_effort=False, cloud="bedrock", effort_level=True)[1],
                         "the main_effort option is off")
        self.assertEqual(self.decide(first_party=False, cloud="bedrock")[1],
                         "the session holds no first-party credential (a gateway or a third-party provider)")
        self.assertIn("drops the prompt cache on bedrock", self.decide(cloud="bedrock", gateway=True, no_betas=True)[1])

    def test_a_max_effort_level_holds_the_effort_and_says_so(self):
        self.assertEqual(self.decide(effort_cap="medium"), ("medium", "effort held to medium by maxEffortLevel"))
        self.assertEqual(self.decide(level=Level.hardest, effort_cap="high"), ("high", "effort held to high by maxEffortLevel"))
        self.assertEqual(self.decide(effort_cap="xhigh"), ("high", None))

    def test_a_complex_turn_left_alone_says_why_in_the_ledger(self):
        self.options(main_effort="true")
        session.record("s1", self.OPUS)
        turn = lambda text="fix the auth migration across every service", **extra: modpath.turn(  # noqa: E731
            {"session_id": "s1", "text": text, **extra})
        with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://gateway"}):
            self.assertIsNone(turn())
        row = [r for r in self.ledger() if r["event"] == "turn"][-1]
        self.assertEqual((row["level"], row["effort"]), ("complex", None))
        self.assertIn("ANTHROPIC_BASE_URL sets a gateway", row["reason"])
        self.assertEqual(turn(), {"level": "complex", "effort": "high"})
        self.assertEqual([r["effort"] for r in self.ledger() if r["event"] == "turn"], [None, "high"])
        rows = len(self.ledger())
        with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://gateway"}):
            turn("list the files under src")  # below complex: most turns, so no row
            turn("<task-notification>done</task-notification>")  # not the person's
        self.options(routing_mode="off")
        turn()
        self.assertEqual(len(self.ledger()), rows)

    def test_a_complex_turn_with_the_option_off_is_logged_as_skipped(self):
        modpath.turn({"session_id": "s1", "text": "fix the auth migration across every service"})
        row = self.ledger()[-1]
        self.assertEqual((row["event"], row["effort"], row["reason"]), ("turn", None, "the main_effort option is off"))


class ModPayloads(Sandbox):
    """tests/fixtures/mod-payloads.json holds what hooks/router.ts sends; tests/mod checks the other side."""

    def setUp(self):
        super().setUp()
        self.payloads = json.loads((FIXTURES / "mod-payloads.json").read_text(encoding="utf-8"))
        self.options(main_effort="true")  # off by default

    def test_each_command_has_a_handler(self):
        self.assertEqual(set(self.payloads), {name for name in hooks.HANDLERS if name.startswith("mod-")})

    def test_start_marks_the_session(self):
        modpath.start(self.payloads["mod-start"])
        self.assertTrue(modpath.active("s1"))

    def test_spawn_reads_every_field(self):
        payload = self.payloads["mod-spawn"]
        with mock.patch.object(router, "decide", wraps=router.decide) as decide:
            modpath.spawn(payload)
        found = decide.call_args.args[0]
        self.assertEqual((found.agent, found.parent, found.tool_input), ("general-purpose", "opus", payload["tool_input"]))
        with mock.patch.object(router, "decide", wraps=router.decide) as decide:
            modpath.spawn({**payload, "parent_model": None})
        self.assertIsNone(decide.call_args.args[0].parent)  # parent_agent_id: inside a subagent, so unknown
        row = self.ledger()[-1]
        self.assertEqual((row["event"], row["via"], row["kind"], row["agent"], row["model"]),
                         ("spawn", "mod", "subagent", "general-purpose", "haiku"))
        self.assertTrue(modpath.active("s1"))

    def test_siblings_keep_their_sent_ids_when_the_same_call_is_issued_again(self):
        os.environ["CLAUDE_CODE_USE_FOUNDRY"] = "1"
        refused = "API Error: 404 not_found_error: model: claude-haiku-5-5"
        base = {**self.payloads["mod-spawn"], "parent_agent_id": None,
                "tool_input": {"subagent_type": "general-purpose", "prompt": LOOKUP}}
        copies = [{**base, "tool_use_id": f"tu{i}"} for i in (1, 2, 3)]
        self.assertEqual([modpath.spawn(c)["model"] for c in copies[:2]], ["claude-haiku-5-5"] * 2)
        self.assertEqual([calls.route_of("s1", "none", f"tu{i}").sent for i in (1, 2)], ["claude-haiku-5-5"] * 2)
        hooks.agent_failed({**copies[0], "error": refused})
        self.assertEqual(modpath.spawn(copies[2])["model"], "haiku")  # the ID is routed around now
        hooks.agent_failed({**copies[1], "error": refused})
        self.assertEqual([r["model"] for r in self.ledger() if r["event"] == "model_error"], ["claude-haiku-5-5"] * 2)
        self.assertTrue(health.load().healthy("haiku"))

    def test_turn_picks_an_effort(self):
        session.record("s1", "claude-opus-5-5")
        self.assertEqual(modpath.turn(self.payloads["mod-turn"]), {"level": "complex", "effort": "high"})

    def test_usage_is_logged_for_its_agent(self):
        modpath.usage(self.payloads["mod-usage"])
        row = self.ledger()[-1]
        self.assertEqual((row["event"], row["agent_id"], row["cost_usd"]), ("usage", "a1", 6.0))


class Ledger(Sandbox):
    def test_a_result_row_carries_models_used_only_after_a_swap(self):
        ledger.result("s1", "general-purpose", "sonnet", "claude-sonnet-5-5", "completed", 1200,
                      ["claude-sonnet-5-5", "claude-sonnet-5"])
        ledger.result("s1", "general-purpose", "sonnet", "claude-sonnet-5-5", "completed", 1200)
        swapped, plain = ledger.read()
        self.assertEqual(list(swapped)[-2:], ["total_tokens", "models_used"])
        self.assertEqual(swapped["models_used"], ["claude-sonnet-5-5", "claude-sonnet-5"])
        self.assertNotIn("models_used", plain)

    def test_each_row_kind_keeps_its_shape(self):
        decision = self.decide("general-purpose", LOOKUP)
        ledger.spawn("s1", "mod", decision, "d" * 200, kind="subagent")
        ledger.result("s1", "general-purpose", "haiku", "claude-haiku-5-5", "completed", 1200)
        ledger.model_error("s1", "deep", "fable")
        ledger.turn("s1", "complex", "high", command="hard")
        ledger.turn("s1", "complex", "high")
        ledger.usage(cfg.load(), "s1", "a1", {"model": "claude-opus-5-5", "input_tokens": 1_000_000,
                     "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}, None)
        rows = ledger.read()
        self.assertEqual([list(r) for r in rows], [
            ["ts", "event", "via", "session", "agent", "action", "level", "signals", "model", "was", "effort", "source",
             "escalated_from", "reason", "description", "kind"],
            ["ts", "event", "session", "agent", "asked", "resolved_model", "status", "total_tokens"],
            ["ts", "event", "session", "agent", "model"],
            ["ts", "event", "session", "level", "effort", "command"],
            ["ts", "event", "session", "level", "effort"],
            ["ts", "event", "session", "agent_id", "model", "input_tokens", "output_tokens", "cache_read_input_tokens",
             "cache_creation_input_tokens", "cost_usd"]])
        self.assertEqual((len(rows[0]["description"]), rows[-1]["cost_usd"]), (120, 4.0))

    def test_a_fallback_spawn_row_has_no_kind(self):
        ledger.spawn("s1", "fallback", self.decide("general-purpose", LOOKUP), None)
        self.assertNotIn("kind", ledger.read()[0])

    def test_rows_are_read_from_an_explicit_path(self):
        other = Path(self.tmp.name) / "elsewhere.jsonl"
        other.write_text(json.dumps({"ts": "2099-01-01T00:00:00+00:00", "event": "spawn"}) + "\nnot json\n")
        self.assertEqual(ledger.read(path=other), [{"ts": "2099-01-01T00:00:00+00:00", "event": "spawn"}])
        self.assertEqual(ledger.read(), [])

    def test_a_malformed_usage_writes_no_row(self):
        for usage in ({"input_tokens": 1}, "x", None, {"model": 5}):
            ledger.usage(cfg.load(), "s1", None, usage, None)
        self.assertEqual(ledger.read(), [])

    def test_a_turn_is_unpriced_if_any_request_is_unpriced(self):
        config = cfg.load()
        usage = {"model": "claude-opus-5-5", "input_tokens": 1_000_000}
        requests = [usage, {"model": "claude-unknown-9", "input_tokens": 5}, "junk"]
        self.assertIsNone(ledger.turn_cost(config, usage, requests))
        self.assertEqual(ledger.turn_cost(config, usage, requests[:1]), 4.0)
        self.assertEqual(ledger.turn_cost(config, usage, None), 4.0)


class LongPromptPricing(Sandbox):
    """Haiku 5.5 bills a request whose prompt is over 100,000 tokens at 5x, each request on its own:
    https://platform.claude.com/docs/en/about-claude/pricing.md#long-context-pricing"""

    @staticmethod
    def haiku(input_tokens=0, output_tokens=0, cache_read=0, cache_write=0) -> dict:
        return {"model": "claude-haiku-5-5", "input_tokens": input_tokens, "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read, "cache_creation_input_tokens": cache_write}

    def cost(self, usage: dict, requests: list | None = None):
        payload = {"session_id": "s1", "agent_id": "a1", "usage": usage}
        return ledger.turn_cost(cfg.load(), usage, requests)

    def test_a_request_over_100k_pays_the_long_prompt_rate(self):
        short = self.haiku(input_tokens=10_000, cache_write=20_000, output_tokens=1_000)  # 30K prompt
        long = self.haiku(input_tokens=5_000, cache_read=90_000, cache_write=10_000, output_tokens=2_000)  # 105K
        turn = self.haiku(input_tokens=15_000, cache_read=90_000, cache_write=30_000, output_tokens=3_000)
        # $0.004 at $0.10/$0.125/$0.50, plus $0.01825 at $0.50/$0.05/$0.625/$2.50.
        self.assertEqual(self.cost(turn, [short, long]), 0.02225)

    def test_requests_under_the_threshold_keep_the_base_rate_even_when_the_turn_is_over(self):
        self.assertEqual(self.cost(self.haiku(input_tokens=160_000),
                                   [self.haiku(input_tokens=100_000), self.haiku(input_tokens=60_000)]), 0.016)

    def test_a_turn_sent_without_its_requests_is_priced_at_the_base_rate(self):
        self.assertEqual(self.cost(self.haiku(input_tokens=160_000)), 0.016)

    def test_a_request_on_an_unpriced_model_leaves_the_turn_unpriced(self):
        stray = {**self.haiku(input_tokens=1), "model": "claude-unknown-9"}
        self.assertIsNone(self.cost(self.haiku(input_tokens=2), [self.haiku(input_tokens=1), stray]))


class Feedback(Sandbox):
    def setUp(self):
        super().setUp()
        self.session_on("claude-opus-5-5")

    def raise_lookup(self):
        for i in range(3):
            self.pre("general-purpose", f"list the files under dir{i}")
            self.pre("general-purpose", f"list the files under dir{i}", model="sonnet")

    def test_a_swapped_run_is_not_a_redo_and_does_not_count_for_its_level(self):
        key = calls.identify({"subagent_type": "general-purpose", "prompt": LOOKUP})[1]
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", models_used=SWAP["swapped"]["modelsUsed"])
        self.assertEqual(learn.stats()["lookup"]["calls"], 0)
        self.assertIsNone(calls.finished_run("s1", key))
        again = self.decide("general-purpose", LOOKUP)
        self.assertEqual((again["level"], again["escalated_from"]), ("lookup", None))
        self.pre("general-purpose", LOOKUP)
        self.assertEqual(learn.stats()["lookup"]["votes"], 0)

    def test_a_swapped_run_the_caller_chose_leaves_the_count_alone(self):
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5")
        self.assertEqual(learn.stats()["lookup"]["calls"], 1)
        self.pre("general-purpose", LOOKUP, model="sonnet")
        self.done("general-purpose", LOOKUP, "claude-sonnet-5-5", model="sonnet", models_used=SWAP["swapped"]["modelsUsed"])
        self.assertEqual(learn.stats()["lookup"]["calls"], 1)

    def test_a_swapped_run_with_no_count_to_take_back_leaves_zero(self):
        self.pre("general-purpose", LOOKUP)
        learn.reset()
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", models_used=SWAP["swapped"]["modelsUsed"])
        self.assertEqual(learn.stats().get("lookup", {}).get("calls", 0), 0)

    def test_a_plain_run_counts_for_its_level_and_finishes(self):
        key = calls.identify({"subagent_type": "general-purpose", "prompt": LOOKUP})[1]
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5")
        self.assertEqual(learn.stats()["lookup"]["calls"], 1)
        self.assertIsNotNone(calls.finished_run("s1", key))

    def test_redoing_routed_calls_on_a_stronger_model_raises_the_level(self):
        self.raise_lookup()
        self.assertEqual(learn.raises()["lookup"], 1)
        self.assertEqual(self.decide("general-purpose", LOOKUP)["model"], "sonnet")
        self.assertIn("| lookup | sonnet (raised by feedback) |", self.out("policy"))
        self.assertIn("cleared", self.out("models", "--reset"))
        self.assertEqual(self.decide("general-purpose", LOOKUP)["model"], "haiku")

    def test_a_complex_request_on_a_sonnet_session_is_told_to_hand_off_to_an_opus_agent(self):
        self.session_on("claude-sonnet-5-5")
        text = hooks.user_prompt({"session_id": "s1", "prompt": HARD})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("about opus-level reasoning, and this conversation runs on sonnet", text)
        self.assertIn("model-router:architect", text)
        self.assertIn("model-router:architect", hooks.stop({"session_id": "s1"})["reason"])

    def test_the_prompt_note_and_the_stop_gate_follow_a_raised_level(self):
        self.options(verify_gate="true")
        self.session_on("claude-sonnet-5-5")
        for _ in range(learn.VOTES_NEEDED):
            self.spawned(source="caller", level="complex", pick="opus", model="fable")
        hooks.user_prompt({"session_id": "s1", "prompt": HARD})  # opus would do, but complex is raised
        entry = request.load("s1")
        self.assertEqual((entry.level, entry.need, entry.below(cfg.load(), "claude-sonnet-5-5")), ("complex", "fable", True))
        self.assertIn("fable-level reasoning", hooks.stop({"session_id": "s1"})["reason"])
        text = hooks.user_prompt({"session_id": "s1", "prompt": HARD})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("about fable-level reasoning, and this conversation runs on sonnet", text)

    def test_a_few_overrides_among_many_calls_change_nothing(self):
        for i in range(40):
            self.pre("general-purpose", f"list the files under dir{i}")
        for i in range(6):
            self.pre("general-purpose", f"list other things {i}", model="opus")
        self.assertEqual(learn.raises()["lookup"], 0)

    def test_naming_a_weaker_or_equal_model_is_not_a_vote(self):
        for i in range(10):
            self.pre("general-purpose", f"implement change {i}", model="haiku")
            self.pre("general-purpose", f"implement other change {i}", model="sonnet")
        self.assertEqual(learn.stats(), {})

    def test_a_raise_is_undone_when_it_keeps_being_overruled_downwards(self):
        self.raise_lookup()
        for i in range(6):
            self.pre("general-purpose", f"list the things in {i}", model="haiku")
        self.assertEqual(learn.raises()["lookup"], 0)

    def test_each_kind_of_spawn_casts_its_own_votes(self):
        def votes(**facts) -> dict:
            learn.reset()
            self.spawned(**facts)
            return {k: v for k, v in (learn.stats().get("lookup") or {}).items() if v}

        self.assertEqual(votes(source="router"), {"calls": 1})
        self.assertEqual(votes(source="pin", model="opus"), {})
        self.assertEqual(votes(source="rule", model="opus"), {})
        self.assertEqual(votes(source="caller", model="sonnet"), {"votes": 1})
        self.assertEqual(votes(source="caller", model="sonnet", earlier="haiku"), {"votes": 2})
        self.assertEqual(votes(source="caller", model="sonnet", earlier="opus"), {"votes": 1})
        self.assertEqual(votes(source=None, model="opus"), {})
        self.assertEqual(votes(source="router", repeat_of="haiku"), {})  # passed through untouched
        learn.reset()
        self.spawned(source="router", level="standard", pick="sonnet", model="sonnet", escalated_from="lookup")
        self.assertEqual((learn.stats()["standard"]["calls"], learn.stats()["lookup"]["votes"]), (1, 1))

    def test_a_redo_of_a_pinned_or_ruled_agent_casts_no_vote(self):
        self.agent("pinned", "sonnet")
        self.override({"agents": {"ruled": {"model": "sonnet"}}}, user=True)
        for agent in ("pinned", "ruled"):
            self.pre(agent, LOOKUP)
            self.done(agent, LOOKUP, "claude-sonnet-5-5")
            again = self.decide(agent, LOOKUP)
            self.assertEqual((again["level"], again["escalated_from"]), ("standard", "lookup"), agent)
            self.pre(agent, LOOKUP)
        self.assertEqual(learn.stats(), {})
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5")
        self.pre("general-purpose", LOOKUP)
        self.assertEqual(learn.stats()["lookup"]["votes"], 1)  # the router's own redo does

    def test_a_workflow_stage_or_a_teammate_counts_for_no_level(self):
        mod = lambda kind, i: modpath.spawn({  # noqa: E731
            "session_id": "s1", "cwd": str(self.project), "parent_model": "claude-opus-5-5", "kind": kind,
            "tool_input": {"subagent_type": "workflow-subagent", "prompt": f"list the files under dir{i}"}})
        for i in range(100):
            self.assertEqual(mod("workflow", i)["model"], "haiku")
        mod("teammate", 0)
        self.assertEqual(learn.stats(), {})
        for i in range(learn.VOTES_NEEDED):
            self.spawned(source="caller", model="sonnet")
        self.assertEqual(learn.raises()["lookup"], 1)  # 6 votes still raise a level no workflow call inflated

    def test_feedback_never_goes_below_what_ships(self):
        for i in range(30):
            self.pre("general-purpose", f"implement change {i}", model="haiku")
        self.assertEqual(self.decide("general-purpose", BUILD)["model"], "sonnet")

    def test_votes_from_calls_issued_at_once_are_all_counted(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                "from modelrouter import config, learn, store\n"
                "store.LOCK_WAIT_SECONDS = 60\n"
                "decision = dict(source='router', level='lookup', pick='haiku', model='haiku', was=None,\n"
                "                escalated_from=None, repeat_of=None)\n"
                "for _ in range(10): learn.spawned(config.load(), decision, 'subagent', None, False)\n")
        workers = [subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS)], env={**os.environ})
                   for _ in range(5)]
        for worker in workers:
            self.assertEqual(worker.wait(timeout=120), 0)
        self.assertEqual(learn.stats()["lookup"]["calls"], 50)

    def test_a_model_the_user_named_casts_no_votes(self):
        hooks.user_prompt({"session_id": "s1", "prompt": "use opus to list the files in these six folders"})
        for i in range(learn.VOTES_NEEDED):
            self.pre("general-purpose", f"list the files under dir{i}", model="opus")
        self.assertEqual(learn.raises()["lookup"], 0)

    def test_a_spawn_identifies_its_call_once_and_reuses_the_raises(self):
        with mock.patch.object(calls, "identify", wraps=calls.identify) as identify, \
                mock.patch.object(learn, "raises", wraps=learn.raises) as raises:
            self.pre("general-purpose", BUILD, model="haiku")
        self.assertEqual(identify.call_count, 1)
        self.assertEqual(raises.call_count, 1)


class Hooks(Sandbox):
    def text(self, output: dict | None) -> str:
        return output["hookSpecificOutput"]["additionalContext"]

    def test_a_swapped_run_of_a_risk_brief_tells_claude(self):
        out = self.done("general-purpose", HARD, "claude-sonnet-5-5", models_used=SWAP["swapped"]["modelsUsed"])
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertEqual(self.text(out), "Model Router: this agent switched models mid-run "
                         "(claude-sonnet-5-5 to claude-sonnet-5), so its result needs a check before it is "
                         "relied on.")

    def test_a_swapped_run_of_a_plain_brief_says_nothing(self):
        self.assertIsNone(self.done("general-purpose", LOOKUP, "claude-haiku-5-5", models_used=["claude-haiku-5-5", "x-1"]))
        self.assertEqual(ledger.read()[-1]["models_used"], ["claude-haiku-5-5", "x-1"])

    def test_a_run_with_junk_models_used_is_recorded_as_any_other_run(self):
        key = calls.identify({"subagent_type": "general-purpose", "prompt": HARD})[1]
        self.pre("general-purpose", HARD)
        for junk in ([1, 2], "claude-sonnet-5", ["claude-sonnet-5-5"]):
            self.assertIsNone(self.done("general-purpose", HARD, "claude-sonnet-5-5", models_used=junk))
            self.assertNotIn("models_used", ledger.read()[-1])
        self.assertIsNotNone(calls.finished_run("s1", key))

    def test_a_background_launch_is_never_read_as_a_swap(self):
        self.pre("general-purpose", HARD)
        out = hooks.post_agent({**self.call("general-purpose", HARD), "tool_response": {
            "resolvedModel": "claude-sonnet-5-5", "status": "async_launched", "agentId": "a9",
            "modelsUsed": ["claude-sonnet-5-5", "claude-sonnet-5"]}})
        self.assertIsNone(out)
        self.assertNotIn("models_used", ledger.read()[-1])
        self.assertIn("a9", store.load_entry("launched", "s1"))

    def test_session_start_explains_automatic_routing(self):
        self.options(max_tier="opus")
        text = self.text(hooks.session_start({"session_id": "s1", "model": "claude-sonnet-5-5"}))
        self.assertIn("Model Router sets", text)
        self.assertNotIn("strongest model in use", text)  # changes nothing Claude does; /model-router:route says it
        self.assertNotIn("model-router:deep-solver", text)  # the Agent tool lists the role agents already
        self.assertIn("omit `effort` unless the user named one", text)
        self.assertIn("name `model` only when the user did or when what was read shows", text)  # any work, not only code
        self.assertIn('keeps its `description` with "Redo:" in front', text)
        self.assertNotIn("re-issued unchanged", text)  # Claude rewrites a brief it sends again
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", text)
        self.assertEqual(session.model("s1"), "claude-sonnet-5-5")
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="opus", CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1")
        text = self.text(hooks.session_start({"session_id": "s1"}))
        self.assertIn("pins every subagent to opus, so Model Router sets only their effort", text)

    def test_session_start_says_when_the_subagent_model_default_leaves_calls_alone(self):
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="sonnet")
        text = self.text(hooks.session_start({"session_id": "s1", "model": "claude-opus-5-5"}))
        self.assertIn("CLAUDE_CODE_SUBAGENT_MODEL sets the model of every agent without its own", text)
        self.options(agent_pins="override")  # then the router picks again, and the plain note is true
        text = self.text(hooks.session_start({"session_id": "s1", "model": "claude-opus-5-5"}))
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", text)

    def test_the_text_in_context_every_session_stays_within_budget(self):
        # The session-start note, the role agents' descriptions and the model-invoked skills' listings are in
        # the main loop's context in every session (docs/PERFORMANCE.md, "Context the plugin adds").
        note = self.text(hooks.session_start({"session_id": "s1", "model": "claude-sonnet-5-5"}))
        agents = [frontmatter(p)["description"] for p in (PLUGIN / "agents").glob("*.md")]
        listed = [frontmatter(p) for p in (PLUGIN / "skills").glob("*/SKILL.md")]
        skills = [f.get("description", "") + f.get("when_to_use", "") for f in listed
                  if f.get("disable-model-invocation") != "true"]
        self.assertLessEqual(len(note), 360, note)
        self.assertLessEqual(sum(map(len, agents)), 930)
        self.assertLessEqual(sum(map(len, skills)), 200)

    def test_session_start_is_silent_when_off_and_nothing_is_pending(self):
        self.options(routing_mode="off")
        self.assertIsNone(hooks.session_start({"session_id": "s1"}))

    def test_session_start_mentions_pending_upstream_and_new_models(self):
        store.save_json(upstream.path(), {
            "reviewed": {"claude_code_version": "2.0.0", "pages": {}, "aliases": ["haiku"]},
            "cache": {"releases": [{"version": "2.1.0", "date": "", "link": ""}], "pages": {},
                      "aliases": ["haiku", "sonnet", "opus", "fable", "saga"]}})
        text = self.text(hooks.session_start({"session_id": "s1"}))
        self.assertIn("saga", text)
        self.assertIn("1 Claude Code release(s)", text)
        self.options(update_check="false")
        self.assertNotIn("saga", self.text(hooks.session_start({"session_id": "s1"})))

    def test_user_prompt_is_silent_unless_the_session_is_too_weak(self):
        self.session_on("claude-opus-5-5")
        self.assertIsNone(hooks.user_prompt({"session_id": "s1", "prompt": HARDEST}))
        self.session_on("claude-haiku-5-5")
        text = self.text(hooks.user_prompt({"session_id": "s1", "prompt": HARDEST}))
        # By name only: the router routes the hand-off's brief, and its redo can climb.
        self.assertIn("model-router:architect can give the plan", text)
        self.assertNotIn('model: "', text)
        self.assertTrue(request.load("s1").below(cfg.load(), "claude-haiku-5-5"))

    def test_machine_written_turns_dont_reset_the_request(self):
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": HARD})
        self.assertIsNone(hooks.user_prompt({"session_id": "s1", "prompt": "<task-notification>done</task-notification>"}))
        self.assertEqual(request.load("s1").level, "complex")

    def test_a_prompt_that_opens_with_pasted_text_is_the_users_request(self):
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": HARD})
        pasted = ('<pasted_content id="x">\nTraceback (most recent call last):\n  File "billing.py"\n'
                  '</pasted_content id="x">\n' + LOOKUP)
        hooks.user_prompt({"session_id": "s1", "prompt": pasted})
        self.assertEqual(request.load("s1").level, "standard")  # the pasted traceback counts too

    def test_a_model_the_user_may_be_asking_for_is_left_to_claude(self):
        self.session_on("claude-opus-5-5")
        text = self.text(hooks.user_prompt({"session_id": "s1", "prompt": "use haiku to refactor the auth module"}))
        self.assertIn('model: "haiku"', text)
        self.assertIn("If it asks for that model", text)
        self.assertEqual(self.decide("general-purpose", HARD)["model"], "opus")  # nothing is forced
        config = cfg.load()
        self.assertEqual(request.named_model(config, "Use Opus for the review"), "opus")
        self.assertEqual(request.named_model(config, "run this on claude fable please"), "fable")
        for prompt in ("don't use opus for this", "the haiku about autumn", "work with the opus agent"):
            self.assertIsNone(request.named_model(config, prompt), prompt)

    def test_advise_mode_gives_the_estimate(self):
        self.options(routing_mode="advise")
        self.assertIn("Routing estimate", self.text(hooks.user_prompt({"session_id": "s1", "prompt": HARD})))

    def test_advise_mode_never_tells_claude_to_pass_a_model(self):
        # A model passed on a call outranks the agent definition's own `model`, so it would undo a pin.
        self.options(routing_mode="advise")
        note = self.text(hooks.session_start({"session_id": "s1", "model": "claude-sonnet-5-5"}))
        estimate = self.text(hooks.user_prompt({"session_id": "s1", "prompt": HARD}))
        for text in (note, estimate):
            self.assertNotRegex(text, r"(?i)\bpass\b")
            self.assertNotIn('model: "', text)
        self.assertIn("Agents keep the model their definition sets", note)

    def test_the_notes_state_facts_and_give_no_commands(self):
        # Text framed as an out-of-band command can trip Claude's prompt-injection defenses (hooks.md, additionalContext).
        self.options(max_tier="opus")
        self.session_on("claude-haiku-5-5")
        notes = [self.text(hooks.session_start({"session_id": "s1", "model": "claude-haiku-5-5"})),
                 self.text(hooks.user_prompt({"session_id": "s1", "prompt": HARDEST})),
                 hooks.stop({"session_id": "s1"})["reason"],
                 self.text(hooks.user_prompt({"session_id": "s1", "prompt": "use haiku to refactor auth"}))]
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="opus", CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1")
        notes.append(self.text(hooks.session_start({"session_id": "s2"})))
        for text in notes:
            self.assertNotRegex(text, r"(?i)(?:^|[.;:] )(?:tell|pass|don't|do not|never|get|hand|run|give|make|say|mention)\b", text)

    def test_results_teach_the_registry_which_models_really_run(self):
        self.agent("pinned", "opus")
        for _ in range(2):
            self.done("x", "p", "claude-opus-5-5", model="fable")
        self.assertFalse(health.load().healthy("fable"))
        self.pre("pinned", "p")
        self.done("pinned", "p", "claude-opus-5-5")
        self.assertEqual(health.load().last_ran_as("opus"), "claude-opus-5-5")
        hooks.post_agent({"session_id": "s1", "tool_input": {"subagent_type": "x"}, "tool_response": "text"})
        self.assertEqual(self.ledger()[-1]["event"], "result")

    def test_model_errors_point_to_the_next_model_and_other_errors_dont_count(self):
        failed = {"session_id": "s1", "cwd": str(self.project),
                  "error": "API Error: 404 not_found_error: model: claude-fable-5-2",
                  "tool_input": {"subagent_type": "deep", "prompt": "x"}}
        self.agent("deep", "fable")
        self.pre("deep", "x")
        self.assertIn('model: "opus"', self.text(hooks.agent_failed(failed)))
        self.assertIn("route around", self.text(hooks.agent_failed(failed)))
        self.assertFalse(health.load().healthy("fable"))
        self.assertIsNone(hooks.agent_failed({**failed, "error": "Command timed out", "tool_input": {"model": "opus"}}))
        self.assertIsNone(hooks.agent_failed({**failed, "is_interrupt": True, "tool_input": {"model": "opus"}}))
        self.assertTrue(health.load().healthy("opus"))

    def test_model_switch_is_recorded(self):
        hooks.model_switch({"session_id": "s1", "to_model": "claude-fable-5-2"})
        self.assertEqual(session.model("s1"), "claude-fable-5-2")

    def test_forced_runs_are_not_substitutions(self):
        self.options(CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1", CLAUDE_CODE_SUBAGENT_MODEL="sonnet")
        for _ in range(2):
            self.done("general-purpose", LOOKUP, "claude-sonnet-5-5", model="opus")
        self.assertTrue(health.load().healthy("opus"))


class GatesDefault(Sandbox):
    """verify_gate (the test-run check) and strong_agent_gate (the stronger-agent check) are on."""

    def hard_on_weak_session(self) -> None:
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": HARDEST})

    def edited(self) -> None:
        self.session_on("claude-opus-5-5")
        hooks.post_edit({"session_id": "s1", "tool_input": {"file_path": "src/app.py"}})

    def test_the_defaults(self):
        s = settings.resolve()
        self.assertEqual((s.verify_gate, s.strong_agent_gate), (True, True))

    def test_the_stronger_agent_check_runs_by_default_and_can_be_turned_off(self):
        self.hard_on_weak_session()
        self.assertIn("model-router:architect", hooks.stop({"session_id": "s1"})["reason"])
        self.options(strong_agent_gate="false")
        self.hard_on_weak_session()
        self.assertIsNone(hooks.stop({"session_id": "s1"}))

    def test_the_stronger_agent_check_waits_for_a_running_workflow(self):
        self.hard_on_weak_session()
        self.assertIsNone(hooks.stop({"session_id": "s1", "background_tasks": [{"type": "workflow", "name": "plan"}]}))
        self.assertIn("model-router:architect", hooks.stop({"session_id": "s1"})["reason"])

    def test_the_test_run_check_runs_by_default_and_can_be_turned_off(self):
        self.edited()
        self.assertIn("model-router:verifier", hooks.stop({"session_id": "s1"})["reason"])
        self.options(verify_gate="false")
        self.assertIsNone(hooks.stop({"session_id": "s1"}))

    def test_every_falsy_form_turns_the_test_run_check_off(self):
        for form in ("false", "False", "0", "no", "off"):
            with self.subTest(form=form):
                self.options(verify_gate=form)
                self.edited()
                self.assertIsNone(hooks.stop({"session_id": "s1"}))

    def test_turning_one_check_off_leaves_the_other(self):
        self.options(verify_gate="true", strong_agent_gate="false")
        self.edited()
        self.assertIn("model-router:verifier", hooks.stop({"session_id": "s1"})["reason"])

    def test_the_architect_is_asked_for_a_plan_or_check_never_the_deliverable(self):
        # The architect has no Write tool: a task whose deliverable is a file fails there, or its redo climbs.
        self.session_on("claude-haiku-5-5")
        note = self.text(hooks.user_prompt({"session_id": "s1", "prompt": HARDEST}))
        reason = hooks.stop({"session_id": "s1"})["reason"]
        for text in (note, reason):
            self.assertIn("model-router:architect", text)
            self.assertIn("writes nothing", text)
        self.assertIn("the change stays here", note)
        self.assertIn("what it finds is applied here", reason)
        self.assertNotIn("improve", reason)
        frontier = ("Investigate the unknown root cause of intermittent data loss in the payments database across "
                    "every service and design a fix; there is no known approach.")
        note = self.text(hooks.user_prompt({"session_id": "s1", "prompt": frontier}))
        self.assertIn("model-router:deep-solver", note)
        self.assertNotIn("writes nothing", note)  # the deep-solver has every tool and can do the work

    def test_a_finished_change_goes_to_the_reviewer_with_a_diff_and_an_open_plan_to_the_architect(self):
        # The reviewer stops without a diff file, so it is named only once code changed in this request.
        self.session_on("claude-haiku-5-5")
        hooks.post_edit({"session_id": "s1", "tool_input": {"file_path": "src/app.py"}})
        hooks.user_prompt({"session_id": "s1", "prompt": HARDEST})
        plan = hooks.stop({"session_id": "s1"})["reason"]
        self.assertIn("model-router:architect", plan)
        self.assertNotIn("model-router:reviewer", plan)  # the edit came before this request
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": HARDEST})
        hooks.post_edit({"session_id": "s1", "tool_input": {"file_path": "src/app.py"}})
        reason = hooks.stop({"session_id": "s1"})["reason"]
        self.assertIn("model-router:reviewer", reason)
        self.assertIn("the path of the diff, saved to a file", reason)
        self.assertIn("writes nothing", reason)
        self.assertNotIn("model-router:architect", reason)

    def test_the_test_run_check_names_the_reviewer_with_a_saved_diff(self):
        self.edited()
        self.assertIn("model-router:reviewer, given the path of a saved diff", hooks.stop({"session_id": "s1"})["reason"])

    def test_the_hand_off_is_for_what_a_few_reads_cant_settle(self):
        # A task the code settles in a few reads is done directly; delegating it only adds a round trip.
        self.session_on("claude-haiku-5-5")
        note = self.text(hooks.user_prompt({"session_id": "s1", "prompt": HARDEST}))
        self.assertIn("can't be settled from the code in a few reads, model-router:architect can give the plan", note)
        self.assertIn("Otherwise this conversation works directly", note)
        self.assertNotRegex(note, r"(?i)cheap|cost")

    def test_the_prompt_note_promises_a_check_only_when_the_stronger_agent_check_is_on(self):
        self.session_on("claude-haiku-5-5")
        note = lambda: self.text(hooks.user_prompt({"session_id": "s1", "prompt": HARDEST}))  # noqa: E731
        self.assertIn("model-router:architect", note())
        self.assertIn("is held once", note())
        self.options(strong_agent_gate="false")
        self.assertNotIn("held once", note())

    def text(self, out):
        return out["hookSpecificOutput"]["additionalContext"]


class Gates(Sandbox):
    def setUp(self):
        super().setUp()
        self.options(verify_gate="true")

    def transcript(self, command: str, stamp: str = "2099-01-01T00:00:00Z") -> str:
        path = Path(self.tmp.name) / "t.jsonl"
        path.write_text(json.dumps({"timestamp": stamp, "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}) + "\n")
        return str(path)

    def edit(self, path="src/app.py"):
        hooks.post_edit({"session_id": "s1", "tool_input": {"file_path": path}})

    def test_asks_once_when_code_changed_and_nothing_checked_it(self):
        self.edit()
        self.assertIn("model-router:verifier", hooks.stop({"session_id": "s1"})["reason"])
        self.assertIsNone(hooks.stop({"session_id": "s1"}))

    def test_never_blocks_while_already_continuing_or_when_switched_off(self):
        self.edit()
        self.assertIsNone(hooks.stop({"session_id": "s1", "stop_hook_active": True}))
        self.options(verify_gate="false")
        self.assertIsNone(hooks.stop({"session_id": "s1"}))
        self.options(verify_gate="true", routing_mode="advise")
        self.assertIsNone(hooks.stop({"session_id": "s1"}))

    def test_edits_outside_the_project_dont_count(self):
        outside = Path(self.tmp.name) / "cache" / "findings.json"
        hooks.post_edit({"session_id": "s1", "cwd": str(self.project), "tool_input": {"file_path": str(outside)}})
        self.assertIsNone(hooks.stop({"session_id": "s1"}))
        sub = self.project / "pkg"
        sub.mkdir()
        hooks.post_edit({"session_id": "s1", "cwd": str(sub), "tool_input": {"file_path": "../../cache/findings.json"}})
        self.assertIsNone(hooks.stop({"session_id": "s1"}))
        hooks.post_edit({"session_id": "s1", "cwd": str(sub), "tool_input": {"file_path": "../src/app.py"}})
        self.assertIn("model-router:verifier", hooks.stop({"session_id": "s1"})["reason"])

    def test_a_symlinked_cwd_matches_the_real_path(self):
        link = Path(self.tmp.name) / "link"
        try:
            os.symlink(self.project, link, target_is_directory=True)
        except OSError:
            self.skipTest("no symlink privilege")
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(link)}):
            hooks.post_edit({"session_id": "s1", "cwd": str(link),
                             "tool_input": {"file_path": str(self.project / "src" / "app.py")}})
        self.assertIn("model-router:verifier", hooks.stop({"session_id": "s1"})["reason"])

    def test_an_edit_beside_a_moved_cwd_still_counts(self):
        sub = self.project / "pkg"
        sub.mkdir()
        hooks.post_edit({"session_id": "s1", "cwd": str(sub),
                         "tool_input": {"file_path": str(self.project / "lib" / "util.py")}})
        self.assertIn("model-router:verifier", hooks.stop({"session_id": "s1"})["reason"])

    def test_docs_only_edits_dont_count(self):
        self.edit("README.md")
        self.assertIsNone(hooks.stop({"session_id": "s1"}))

    def test_a_checker_agent_or_a_test_command_satisfies_it(self):
        self.edit()
        self.pre("model-router:verifier", "run the tests")
        self.assertIsNone(hooks.stop({"session_id": "s1"}))
        self.edit()
        self.assertIsNone(hooks.stop({"session_id": "s1", "background_tasks": [{"agent_type": "acme:reviewer"}]}))
        self.assertIsNone(hooks.stop({"session_id": "s1", "transcript_path": self.transcript("npm run test")}))

    def test_a_workflow_or_agent_still_running_holds_the_gates_and_keeps_the_ask(self):
        # A workflow runs in the background, so the main turn can end mid-run; its edits aren't done yet.
        self.edit()
        for task in ({"type": "workflow", "name": "audit"}, {"type": "subagent", "agent_type": "acme:implementer"}):
            self.assertIsNone(hooks.stop({"session_id": "s1", "background_tasks": [task]}))
        shell = {"type": "shell", "command": "npm run dev"}
        self.assertIn("model-router:verifier", hooks.stop({"session_id": "s1", "background_tasks": [shell]})["reason"])

    def test_an_old_test_command_doesnt_count(self):
        self.edit()
        out = hooks.stop({"session_id": "s1", "transcript_path": self.transcript("pytest", "2001-01-01T00:00:00Z")})
        self.assertIsNotNone(out)

    def test_a_test_command_in_the_same_message_as_the_edit_counts(self):
        self.edit()
        edited = store.load_entry("edits", "s1")["edited_at"]
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(edited - 2))
        self.assertIsNone(hooks.stop({"session_id": "s1", "transcript_path": self.transcript("pytest", stamp)}))

    def test_check_command_patterns(self):
        for command in ("pytest -q", "npm test", "pnpm run lint", "cargo test", "go vet ./...", "tsc --noEmit",
                        "python -m pytest", "python3 -m unittest discover -s tests", "npx jest", "cd x && pytest",
                        "uv run pytest", "poetry run mypy .", "make test", "CI=1 npm test", "env CI=1 pytest",
                        "time pytest", "timeout 600 pytest -x", "bundle exec rspec", "./node_modules/.bin/eslint .",
                        ".venv/bin/python -m pytest", "docker compose run web pytest", "docker exec -it app rspec",
                        "sh -c 'pytest -q'", "(cd api && rubocop)", "git add -A; pytest", "ruff check . | tail -5",
                        "./gradlew build", "cargo clippy --all-targets", "nice -n 19 cargo test",
                        "timeout 200 uv run --python 3.9 python -m unittest discover -s tests",
                        "xargs bundle exec rspec < f", 'PATH="$HOME/.cargo/bin:$PATH" cargo clippy --workspace',
                        "$P -m pytest -q", "sudo -E make check", "uv pip install -e . && pytest",
                        "rspec spec 2>&1 | grep -c failures"):
            self.assertTrue(gates.is_check(command), command)
        for command in ("ls -la", "git status", "cat test.txt", "pip install pytest", "npm install --save-dev jest eslint",
                        "which pytest", "grep -rn ruff pyproject.toml", "cat jest.config.js", "brew install golangci-lint",
                        "git commit -m 'Run mypy in CI'", "git commit -m 'Run make test in CI'",
                        "tail -50 rspec.log", "command -v pytest",
                        "uv tool install ruff", "yarn add -D vitest", "CI=1 sudo pip3 install tox"):
            self.assertFalse(gates.is_check(command), command)

    def test_installing_a_test_tool_after_an_edit_still_asks(self):
        self.edit()
        out = hooks.stop({"session_id": "s1", "transcript_path": self.transcript("pip install pytest")})
        self.assertIn("model-router:verifier", out["reason"])

    def test_hard_request_on_a_weak_session_needs_a_strong_agent(self):
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": HARDEST})
        self.assertIn("model-router:architect", hooks.stop({"session_id": "s1"})["reason"])
        self.assertIsNone(hooks.stop({"session_id": "s1"}))

    def test_a_strong_enough_agent_satisfies_escalation(self):
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": HARD})
        self.pre("general-purpose", HARD)  # routed to opus
        self.assertIsNone(hooks.stop({"session_id": "s1"}))

    def test_an_agent_on_a_model_the_project_added_never_satisfies_escalation(self):
        self.override({"models": {"gpt-mini": {"strength": 9, "cost": 0}}})
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": HARD})
        self.pre("general-purpose", HARD, model="gpt-mini")
        self.assertIn("model-router:", (hooks.stop({"session_id": "s1"}) or {}).get("reason", "no check asked"))
        self.assertIsNone(calls.strongest(cfg.load(), "s1"))

    def test_agents_started_together_cant_hide_the_strong_one(self):
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": HARDEST})
        self.pre("general-purpose", HARDEST)
        self.pre("general-purpose", LOOKUP)
        self.assertEqual(calls.strongest(cfg.load(), "s1"), "opus")

    def test_no_escalation_for_easy_tasks_or_strong_sessions(self):
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": "where is the config file?"})
        self.assertIsNone(hooks.stop({"session_id": "s1"}))
        self.session_on("claude-fable-5-2")
        hooks.user_prompt({"session_id": "s1", "prompt": HARD})
        self.assertIsNone(hooks.stop({"session_id": "s1"}))


class SubagentChecks(Sandbox):
    """A subagent's commands are in its own transcript (SubagentStop's agent_transcript_path)."""

    def setUp(self):
        super().setUp()
        self.options(verify_gate="true")

    def transcript(self, name: str, command: str) -> str:
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps({"timestamp": "2099-01-01T00:00:00Z", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}) + "\n")
        return str(path)

    def subagent_fixes_and_stops(self, command: str) -> dict:
        hooks.post_edit({"session_id": "s1", "agent_id": "a1", "agent_type": "model-router:implementer",
                         "tool_input": {"file_path": "pages.py"}})
        hooks.agent_stopped({"session_id": "s1", "agent_id": "a1", "agent_type": "model-router:implementer",
                             "agent_transcript_path": self.transcript("agent-a1.jsonl", command),
                             "last_assistant_message": "Fixed the start index."})
        return {"session_id": "s1", "transcript_path": self.transcript("main.jsonl", "ls")}

    def test_a_test_run_inside_the_subagent_that_edited_satisfies_the_stop_check(self):
        self.assertIsNone(hooks.stop(self.subagent_fixes_and_stops("python3 -m pytest test_pages.py | tail -15")))

    def test_a_subagent_that_ran_no_check_still_gets_one_asked_for(self):
        stopped = hooks.stop(self.subagent_fixes_and_stops("cat pages.py"))
        self.assertIn("model-router:verifier", json.dumps(stopped))


class Upstream(Sandbox):
    TABLE = ("| Model alias | Behavior |\n| --- | --- |\n| **`default`** | x |\n| **`best`** | x |\n"
             "| **`fable`** | x |\n| **`sonnet`** | x |\n| **`opus`** | x |\n| **`haiku`** | x |\n"
             "| **`sonnet[1m]`** | x |\n| **`opusplan`** | x |\n| **`saga`** | x |\n")

    def fake(self, url: str) -> str:
        if url.endswith(".xml"):
            return (FIXTURES / "changelog-rss.xml").read_text(encoding="utf-8")
        return self.TABLE if "model-config" in url else (FIXTURES / "llms.txt").read_text(encoding="utf-8")

    def test_parse_fixtures(self):
        releases = upstream.parse_rss((FIXTURES / "changelog-rss.xml").read_text(encoding="utf-8"))
        self.assertTrue(releases and releases[0]["version"])
        pages = upstream.parse_llms_index((FIXTURES / "llms.txt").read_text(encoding="utf-8"))
        self.assertTrue(any(url.endswith("hooks.md") for url in pages))
        life = upstream.parse_deprecations((FIXTURES / "model-deprecations.md").read_text(encoding="utf-8"))
        self.assertEqual(life["claude-opus-5-5"]["state"], "Active")
        self.assertEqual(life["claude-sonnet-4-5-20250929"],
                         {"state": "Deprecated", "deprecated": "September 30, 2026", "retires": "November 30, 2026",
                          "replacement": "claude-sonnet-5-5"})

    def test_retiring_models_are_flagged_when_new_or_in_use(self):
        old = {"lifecycle": {"claude-sonnet-4-5-20250929": {"state": "Active"}, "claude-opus-5-5": {"state": "Active"}}}
        new = {"lifecycle": {"claude-sonnet-4-5-20250929": {"state": "Deprecated", "retires": "November 30, 2026"},
                             "claude-opus-5-5": {"state": "Active"}}}
        changes = upstream.diff(old, new, in_use=["claude-sonnet-4-5-20250929", "claude-opus-5-5[1m]"])
        self.assertEqual([m["model"] for m in changes["newly_deprecated"]], ["claude-sonnet-4-5-20250929"])
        self.assertEqual([m["model"] for m in changes["deprecated_in_use"]], ["claude-sonnet-4-5-20250929"])
        # a retirement covers the undated ID too
        undated = upstream.diff(old, new, in_use=["claude-sonnet-4-5"])
        self.assertEqual([m["model"] for m in undated["deprecated_in_use"]], ["claude-sonnet-4-5-20250929"])
        self.assertTrue(upstream.has_pending(changes))
        self.assertIn("retires November 30, 2026", report.status(changes))
        self.assertEqual(upstream.diff({}, new)["newly_deprecated"], [])  # a first review floods nothing
        in_use_only = upstream.diff(new, new, in_use=["claude-sonnet-4-5-20250929"])
        self.assertFalse(upstream.has_pending(in_use_only))  # said at session start on its own line

    def test_the_pages_routing_rests_on_are_watched(self):
        # The effort order, the cache rules, fast mode, the advisor and headless runs all decide what the router may do.
        base = "https://code.claude.com/docs/en/"
        pages = ["env-vars", "prompt-caching", "fast-mode", "advisor", "headless", "best-practices", "workflows"]
        live = {"pages": {base + p + ".md": "b" for p in pages}}
        changes = upstream.diff({"pages": {base + p + ".md": "a" for p in pages}}, live)
        self.assertEqual(changes["watched_pages_touched"], sorted(base + p + ".md" for p in pages))

    def test_aliases_and_families(self):
        aliases = upstream.parse_model_aliases(self.TABLE)
        self.assertIn("sonnet[1m]", aliases)
        self.assertEqual(upstream.model_families(aliases), ["fable", "haiku", "opus", "saga", "sonnet"])

    def test_diff_flags_releases_watched_pages_and_model_changes(self):
        base = "https://code.claude.com/docs/en/"
        reviewed = {"claude_code_version": "2.1.100", "pages": {base + "hooks.md": "a", base + "voice.md": "a"},
                    "aliases": ["haiku", "sonnet", "opus", "fable"]}
        live = {"releases": [{"version": "2.1.101"}, {"version": "2.1.99"}],
                "pages": {base + "hooks.md": "b", base + "voice.md": "a", base + "plugins/new.md": "a"},
                "aliases": upstream.parse_model_aliases(self.TABLE)}
        changes = upstream.diff(reviewed, live)
        self.assertEqual([r["version"] for r in changes["new_releases"]], ["2.1.101"])
        self.assertEqual(changes["watched_pages_touched"], [base + "hooks.md", base + "plugins/new.md"])
        self.assertEqual(changes["new_model_families"], ["saga"])
        self.assertEqual(changes["removed_model_families"], [])
        self.assertTrue(upstream.has_pending(upstream.diff(reviewed, {"aliases": live["aliases"]})))
        self.assertEqual(upstream.diff({}, {"aliases": ["haiku", "sonnet", "opus"]})["removed_model_families"], ["fable"])
        self.assertEqual(upstream.diff({}, {"aliases": []})["removed_model_families"], [])
        self.assertGreater(upstream.version_key("2.1.330"), upstream.version_key("2.1.99"))

    def test_first_refresh_marks_the_present_as_reviewed_then_ack(self):
        with mock.patch.object(upstream, "http_get", side_effect=self.fake):
            data = upstream.refresh(settings.Settings(), force=True)
        self.assertFalse(upstream.has_pending(upstream.pending(data)))
        data["reviewed"]["claude_code_version"] = "2.0.0"
        store.save_json(upstream.path(), data)
        self.assertTrue(upstream.has_pending(upstream.pending()))
        self.assertIn("as reviewed", self.out("ack"))
        self.assertFalse(upstream.has_pending(upstream.pending()))

    def test_ack_says_so_when_the_review_mark_was_not_written(self):
        store.save_json(upstream.path(), {"schema": 99, "cache": {"claude_code_version": "2.1.330"}})
        self.assertEqual(self.out("ack"), "Review mark left as is: upstream.json was written by a newer Model Router release.\n")
        store.save_json(upstream.path(), {"cache": {"claude_code_version": "2.1.330"}})
        Path(f"{upstream.path()}.lock").write_text("other")  # held by someone else, and fresh
        with mock.patch.object(store, "LOCK_WAIT_SECONDS", 0.05):
            self.assertEqual(cli.main(["ack"]), 1)
        self.assertNotIn("reviewed", json.loads(upstream.path().read_text()))

    def test_cache_ttl_skips_the_network(self):
        store.save_json(upstream.path(), {"cache": {"fetched_at": 10**12}})
        with mock.patch.object(upstream, "http_get", side_effect=AssertionError("network used")):
            upstream.refresh(settings.Settings(), force=False)

    def test_models_api_is_read_only_with_a_direct_api_key(self):
        reply = json.dumps({"data": [
            {"id": "claude-opus-5-5", "line": "opus", "lifecycle": "active", "retires_at": None},
            {"id": "claude-sonnet-4-5-20250929", "line": "sonnet", "lifecycle": "deprecated",
             "retires_at": "2026-11-30T00:00:00Z"},
            {"id": "claude-mythos-5-2", "line": "mythos", "lifecycle": "active", "retires_at": None}]})
        calls = []

        def fake_get(url, headers=None):
            calls.append((url, headers))
            return reply

        keyed = settings.Settings(api_key="sk-test")
        with mock.patch.object(upstream, "http_get", side_effect=fake_get):
            self.assertEqual(upstream.api_models(settings.Settings()), {})  # no key, no request
            for between in ({"base_url": True}, {"cloud": "bedrock"}):  # either would get the key
                self.assertEqual(upstream.api_models(settings.Settings(api_key="sk-test", **between)), {})
            models = upstream.api_models(keyed)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0].startswith("https://api.anthropic.com/v1/models"))
        self.assertEqual(calls[0][1]["Authorization"], "Bearer sk-test")
        self.assertEqual(models["claude-sonnet-4-5-20250929"]["retires_at"], "2026-11-30")
        live = {"api_models": models, "lifecycle": {}}
        changes = upstream.diff({"lifecycle": {"claude-sonnet-4-5-20250929": {"state": "Active"}}}, live,
                                in_use=["claude-sonnet-4-5-20250929"])
        self.assertEqual(changes["new_model_families"], ["mythos"])
        self.assertEqual(changes["deprecated_in_use"][0]["retires"], "2026-11-30")
        self.assertEqual(upstream.diff(upstream.snapshot(live), live)["new_model_families"], [])

    def test_a_version_older_than_its_familys_newest_is_named_at_session_start(self):
        life = upstream.parse_deprecations((FIXTURES / "model-deprecations.md").read_text(encoding="utf-8"))
        store.save_json(upstream.path(), {"cache": {"lifecycle": life, "releases": []}, "reviewed": {}})
        self.assertEqual(upstream.version_of("claude-haiku-4-5-20251001[1m]"), ("haiku", (4, 5)))
        self.assertIsNone(upstream.version_of("claude-3-haiku-20240307"))
        newest = upstream.newest_versions()
        self.assertEqual((newest["opus"], newest["sonnet"], newest["fable"]),
                         ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2"))
        self.assertEqual(upstream.behind(["claude-opus-5[1m]", "claude-opus-5-5", "opus", None]),
                         [("claude-opus-5", "claude-opus-5-5")])
        def note(model: str) -> str:
            out = hooks.session_start({"session_id": "s1", "model": model}) or {}
            return (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
        self.assertIn("claude-opus-4-7 is running here and claude-opus-5-5 is the newest of its family",
                      note("claude-opus-4-7"))
        self.assertNotIn("newest of its family", note("opus"))

    def test_a_dated_or_bracketed_id_is_the_same_model_but_a_newer_version_is_not(self):
        self.assertFalse(upstream._same_model("claude-fable-5", "claude-fable-5-2"))
        self.assertTrue(upstream._same_model("claude-sonnet-4-5-20250929", "claude-sonnet-4-5"))
        self.assertTrue(upstream._same_model("claude-sonnet-4-5", "claude-sonnet-4-5-20250929[1m]"))

    def test_a_date_is_not_a_minor_version(self):
        self.assertEqual(upstream.version_of("claude-opus-4-20250514"), ("opus", (4,)))
        self.assertEqual(upstream.version_of("claude-opus-4-1-20250805"), ("opus", (4, 1)))

    def test_an_empty_feed_is_an_error_not_data(self):
        store.save_json(upstream.path(), {"cache": {"fetched_at": 1, "aliases": ["haiku"]}})
        out = Path(self.tmp.name) / "changes.json"
        for feed in ("rss.xml", "llms.txt"):
            def fake(url, headers=None, feed=feed):
                return "<html>Down for maintenance</html>" if url.endswith(feed) else self.fake(url)

            with mock.patch.object(upstream, "http_get", side_effect=fake):
                with self.assertRaises(ValueError):
                    upstream.refresh(settings.Settings(), force=True)
                with self.assertRaises(ValueError):
                    self.out("upstream-check", "--baseline", str(out.with_name("baseline.json")),
                             "--json-out", str(out))
            self.assertFalse(out.exists())
        with mock.patch.object(upstream, "http_get", side_effect=lambda url, headers=None: (
                "<rss><channel></channel></rss>" if url.endswith(".xml") else self.fake(url))):
            with self.assertRaises(ValueError):
                upstream.fetch(settings.Settings())
        self.assertEqual(upstream.load()["cache"]["aliases"], ["haiku"])

    def test_upstream_check_writes_whether_anything_is_pending(self):
        baseline, out = Path(self.tmp.name) / "baseline.json", Path(self.tmp.name) / "changes.json"
        with mock.patch.object(upstream, "http_get", side_effect=self.fake):
            self.out("upstream-check", "--baseline", str(baseline), "--write")
            self.out("upstream-check", "--baseline", str(baseline), "--json-out", str(out))
            self.assertIs(json.loads(out.read_text())["pending"], False)
            store.save_json(baseline, {**store.load_dict(baseline), "claude_code_version": "2.0.0"})
            self.out("upstream-check", "--baseline", str(baseline), "--json-out", str(out))
            self.assertIs(json.loads(out.read_text())["pending"], True)

    def test_the_api_key_never_follows_a_redirect(self):
        import http.server
        import threading
        import urllib.error
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", "/landed")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with self.assertRaises(urllib.error.HTTPError):
                upstream.http_get(base + "/start", {"Authorization": "Bearer sk-test"})
            self.assertEqual(seen, [("/start", "Bearer sk-test")])
            self.assertEqual(upstream.http_get(base + "/start"), "ok")  # docs pages may still move
        finally:
            server.shutdown()
            server.server_close()

    def dripping_server(self, bodies: dict, delay: float, raw: dict | None = None):
        """Serves each path's body one byte at a time, `delay` seconds apart. A `raw` path
        drips its bytes with no status line or headers of the server's own."""
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path not in (raw or {}):
                    self.send_response(200)
                    self.end_headers()
                try:
                    for byte in (raw or {}).get(self.path) or bodies.get(self.path, b"x" * 1000):
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(delay)
                except OSError:
                    pass  # the client gave up

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_a_request_that_keeps_dripping_stops_at_its_deadline(self):
        base = self.dripping_server({}, delay=0.05)
        started = time.monotonic()
        with mock.patch.object(upstream, "HTTP_TIMEOUT", 0.5), self.assertRaises(OSError):
            upstream.http_get(base + "/slow")
        self.assertLess(time.monotonic() - started, 2)

    def test_the_whole_fetch_stops_at_its_budget(self):
        # The left-over worker's one request fails at its own 5 s deadline, so it never moves on
        # to the real URLs once the patch is gone.
        base = self.dripping_server({}, delay=0.02)
        started = time.monotonic()
        with mock.patch.multiple(upstream, FETCH_BUDGET=0.7, HTTP_TIMEOUT=5, CHANGELOG_RSS=base + "/rss"), \
                self.assertRaises(OSError):
            upstream.fetch(settings.Settings())
        self.assertLess(time.monotonic() - started, 2)

    def test_the_budget_covers_a_dripped_status_line_too(self):
        # Headers that never end: the left-over worker fails when the server hangs up.
        base = self.dripping_server({}, delay=0.05, raw={"/rss": b"HTTP/1.0 200 OK\r\nX-Pad: " + b"x" * 200})
        started = time.monotonic()
        with mock.patch.multiple(upstream, FETCH_BUDGET=0.7, HTTP_TIMEOUT=5, CHANGELOG_RSS=base + "/rss"), \
                self.assertRaises(OSError):
            upstream.fetch(settings.Settings())
        self.assertLess(time.monotonic() - started, 2)

    def test_a_body_cut_short_is_a_failed_request(self):
        import http.client
        base = self.dripping_server({}, delay=0, raw={"/cut": b"HTTP/1.0 200 OK\r\nContent-Length: 100\r\n\r\n"
                                                              + b"x" * 10})
        with self.assertRaises(http.client.IncompleteRead):
            upstream.http_get(base + "/cut")

    def test_a_failed_fetch_leaves_the_cache_alone(self):
        store.save_json(upstream.path(), {"cache": {"fetched_at": 1, "aliases": ["haiku"]}})
        with mock.patch.object(upstream, "http_get", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                upstream.refresh(settings.Settings(), force=True)
        self.assertEqual(upstream.load()["cache"]["aliases"], ["haiku"])

    def test_refresh_offline_says_so_in_one_line_and_shows_the_last_check(self):
        import urllib.error
        store.save_json(upstream.path(), {"reviewed": {"claude_code_version": "2.1.330"},
                                          "cache": {"fetched_at": 1, "releases": [{"version": "2.1.331"}]}})
        offline = urllib.error.URLError("[Errno 8] nodename nor servname provided, or not known")
        with mock.patch.object(upstream, "http_get", side_effect=offline), \
                redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(cli.main(["refresh", "--force"]), 0)
        first, rest = buf.getvalue().split("\n", 1)
        self.assertEqual(first, "Couldn't reach the Claude Code docs (<urlopen error [Errno 8] nodename nor servname "
                                "provided, or not known>); showing the last check.")
        self.assertIn("Last reviewed Claude Code version: 2.1.330", rest)
        self.assertIn("- 2.1.331", rest)


class RefreshBackoff(Sandbox):
    """A failed daily fetch waits before the next try: an hour, doubling to a day."""

    def refresh(self, at: float, fails: bool):
        effect = OSError("offline") if fails else None
        result = {"releases": [], "pages": {}, "aliases": ["haiku"], "fetched_at": int(at)}
        with self.clock_at(at), \
                mock.patch.object(upstream, "fetch", side_effect=effect, return_value=result) as fetch:
            try:
                upstream.refresh(settings.Settings(), force=False)
            except OSError:
                pass
        return fetch.call_count

    def test_a_failure_waits_doubling_up_to_a_day_and_a_success_resets(self):
        t = 1_000_000.0
        self.assertEqual(self.refresh(t, fails=True), 1)
        self.assertEqual(self.refresh(t + 1800, fails=True), 0)  # inside the first hour
        self.assertEqual(self.refresh(t + 3601, fails=True), 1)  # second failure: two hours
        self.assertEqual(self.refresh(t + 3601 + 7000, fails=True), 0)
        self.assertEqual(self.refresh(t + 3601 + 7201, fails=False), 1)
        self.assertNotIn("failed", upstream.load())
        store.update_dict(upstream.path(), lambda data: data["cache"].update(fetched_at=0))
        self.assertEqual(self.refresh(t + 20_000, fails=True), 1)  # the window starts over at an hour
        self.assertEqual(self.refresh(t + 20_000 + 3601, fails=True), 1)
        store.update_dict(upstream.path(), lambda data: data["failed"].update(count=40))
        self.assertEqual(self.refresh(t + 20_000 + 3601 + 86_401, fails=True), 1)  # capped at a day

    def test_a_failure_keeps_the_old_cache_and_a_forced_refresh_still_runs(self):
        store.save_json(upstream.path(), {"cache": {"aliases": ["haiku"], "fetched_at": 0}})
        self.refresh(1_000_000.0, fails=True)
        self.assertEqual(upstream.load()["cache"]["aliases"], ["haiku"])
        with mock.patch.object(upstream, "fetch", side_effect=OSError("offline")) as fetch:
            with self.assertRaises(OSError):
                upstream.refresh(settings.Settings(), force=True)
        self.assertEqual(fetch.call_count, 1)

    def test_a_corrupt_record_never_blocks_a_fetch(self):
        for record in ("junk", {"at": "soon", "count": 1}, {"at": 10**15, "count": 1}, {"at": 999_000, "count": -3},
                       {"at": 1, "count": float("inf")}, {"at": float("-inf"), "count": 1},
                       {"at": float("nan"), "count": 2}):
            with self.subTest(record=record):
                store.save_json(upstream.path(), {"failed": record})
                self.assertEqual(self.refresh(1_000_000.0, fails=False), 1)


class Reports(Sandbox):
    def test_policy_shows_levels_cap_and_the_crew(self):
        self.assertIn("| complex | opus | high |", self.out("policy"))
        store.update_state(lambda state: state.update(max_tier="sonnet", priority="quality"))
        text = self.out("policy")
        self.assertIn("Cap: **sonnet**", text)
        self.assertIn("fable (not in use)", text)
        self.assertIn("| model-router:deep-solver | routed by brief | Research-grade, open-ended tasks with no known approach |",
                      text)
        self.assertIn("| model-router:scout | haiku | Read-only search and summary |", text)
        self.assertIn("| lookup | sonnet |", text)

    def test_models_report(self):
        health.ran("fable", "claude-opus-5-5", "opus", forced=False)
        health.ran("fable", "claude-opus-5-5", "opus", forced=False)
        health.ran("haiku", "claude-haiku-5-5", "haiku", forced=False)
        (self.home / "settings.json").write_text(json.dumps({"availableModels": ["haiku", "opus", "fable"]}))
        store.save_json(upstream.path(), {"cache": {"aliases": ["haiku", "saga"]}})
        text = self.out("models")
        self.assertIn("ran as claude-opus-5-5 instead", text)
        self.assertIn("| haiku | claude-haiku-5-5 | working |", text)
        self.assertIn("| sonnet | - | not permitted", text)
        self.assertIn("Not in the registry yet, so not routed to: saga", text)

    def test_explain(self):
        self.assertIn("**complex**", self.out("explain", *HARD.split()))
        self.agent("pinned", "opus")
        self.assertIn("names its own model", self.out("explain", "--agent", "pinned", "list", "files"))
        self.assertIn("wasn't found", self.out("explain", "--agent", "mystery:agent", "list", "files"))

    def test_explain_says_what_the_spawn_decision_says(self):
        snap = settings.Snapshot({"session_id": "s1", "cwd": str(self.project)})
        for agent, brief in (("general-purpose", HARD), ("general-purpose", LOOKUP), ("model-router:scout", HARDEST)):
            d = spawn.decide({"session_id": "s1", "tool_input": {"prompt": brief, "subagent_type": agent}}, snap).decision
            text = report.explain(snap, brief, agent)
            self.assertIn(f"Level: **{d['level']}**", text)
            self.assertIn(f"Model: **{d['model'] or 'left as it is'}**, effort: {d['effort'] or 'unchanged'}.", text)
            self.assertIn(f"Why: {d['reason']}.", text)

    def test_the_readme_banner_shows_each_brief_on_the_level_explain_gives(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import docs_assets
        snap = settings.Snapshot({"session_id": "s1", "cwd": str(self.project)})
        for brief, level in docs_assets.CALLS:
            _, model, effort = docs_assets.RUNG[level]
            text = report.explain(snap, brief)
            self.assertIn(f"Level: **{level}**", text, brief)
            self.assertIn(f"Model: **{model.lower()}**, effort: {effort}.", text, brief)

    def test_explain_reads_a_find_that_hunts_a_defect_as_complex_and_a_find_that_looks_up_as_lookup(self):
        snap = settings.Snapshot({"session_id": "s1", "cwd": str(self.project)})
        for brief, level, model, request in (
                ("Find why the checkout total is wrong", "complex", "opus", "complex"),
                ("Find the race condition between the api and worker services that drops jobs",
                 "complex", "opus", "complex"),
                ("Find where the auth middleware is defined", "lookup", "haiku", "standard"),
                ("Find all callers of parse_date", "lookup", "haiku", "lookup"),
                ("Find the file that defines the User model", "lookup", "haiku", "lookup")):
            d = spawn.decide({"session_id": "s1", "tool_input": {"prompt": brief}}, snap).decision
            self.assertEqual((d["level"], d["model"]), (level, model), brief)
            self.assertIn(f"As your own request it reads **{request}**", report.explain(snap, brief), brief)

    def test_explain_reports_the_rules_the_old_profile_and_ladder_copy_missed(self):
        self.override({"agents": {"quiet": {"route": False}}}, user=True)
        self.assertIn("leaves this agent alone", self.out("explain", "--agent", "quiet", *LOOKUP.split()))
        self.options(CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1", CLAUDE_CODE_SUBAGENT_MODEL="sonnet")
        self.assertIn("pinned by CLAUDE_CODE_SUBAGENT_MODEL_FORCE", self.out("explain", *LOOKUP.split()))
        for name in ("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "CLAUDE_CODE_SUBAGENT_MODEL"):
            del os.environ[name]
        request.begin("s1", prompt_id="p1", level="hardest", need="opus", session_tier=None, named=None)
        snap = settings.Snapshot({"session_id": "s1", "cwd": str(self.project)})
        text = report.explain(snap, LOOKUP, "model-router:architect")
        self.assertIn("Level: **hardest**", text)  # the request's floor reaches a strong role agent
        key = calls.identify({"prompt": LOOKUP})[1]
        calls.routed("s1", key, calls.Route(model="haiku", sent=None, level=cfg.Level.lookup, effort="medium",
                                            source="router", path="mod", prompt_id="p1"))
        calls.returned("s1", calls.Outcome("result", key=key))
        self.assertIn("one level above the lookup run", report.explain(snap, LOOKUP))

    def test_explain_writes_nothing(self):
        # Every lazy read has something to write: a record too long for the clock, layers the session never stored.
        with self.clock_at(self.clock + 19 * 86400):
            health.failed("fable", "model not found")
            health.failed("fable", "model not found")
        sent = {"policy": {"availableModels": ["haiku", "sonnet", "fable"]}}
        hook_input = {"session_id": "s1", "cwd": str(self.project), "settings": sent}
        self.session_on("claude-sonnet-5-5")

        def files() -> dict:
            return {str(p): p.read_bytes() for root in (self.data, self.home, self.project)
                    for p in root.rglob("*") if p.is_file()}

        before = files()
        text = report.explain(settings.Snapshot(hook_input), HARD)
        report.explain(settings.Snapshot(hook_input), LOOKUP, "model-router:architect")
        report.policy(settings.Snapshot(hook_input))
        self.assertEqual(files(), before)
        self.assertIn("models in use: haiku, sonnet.", text)  # fable is routed around, and the record stays as it was
        state = store.state_path().read_bytes()
        modpath.spawn({**hook_input, "kind": "subagent", "tool_input": {"subagent_type": "general-purpose",
                                                                          "prompt": LOOKUP}})
        self.assertNotEqual(files(), before)  # a real spawn does write
        self.assertNotEqual(store.state_path().read_bytes(), state)  # and settles the outage the reads found

    def test_usage_report(self):
        self.assertIn("No activity", self.out("usage"))
        self.session_on("claude-opus-5-5")
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", model="haiku")
        text = self.out("usage", "30")
        self.assertIn("| general-purpose | 1 | 1 | lookup x1 | claude-haiku-5-5 x1 |", text)
        self.assertIn("| claude-haiku-5-5 | 1 | 1,200 |", text)
        for agent_id, model in ((None, "claude-opus-5-5"), ("a1", "claude-haiku-5-5"), ("a2", "claude-new-9")):
            modpath.usage({"session_id": "s1", "agent_id": agent_id, "usage": {
                "model": model, "input_tokens": 0, "output_tokens": 1_000_000,
                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}})
        text = self.out("usage", "30")
        self.assertIn("Spend at list price: $20.50", text)
        self.assertIn("| main | claude-opus-5-5 | 1 | 1,000,000 | 20.00 |", text)
        self.assertIn("No price in the registry for: claude-new-9", text)

    def test_usage_of_zero_days_reads_the_default_window_it_names(self):
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=20)).isoformat(timespec="seconds")
        store.append_ledger({"ts": old, "event": "usage", "session": "s1", "agent_id": None, "model": "claude-opus-5-5",
                             "input_tokens": 0, "output_tokens": 1_000_000, "cost_usd": 20.0})
        self.assertIn("Spend at list price: $20.00", self.out("usage", "30"))
        self.assertEqual(self.out("usage", "0").strip(), "No activity recorded in the last 7 days.")
        data = json.loads(self.out("usage", "0", "--json"))
        self.assertEqual((data["days"], data["spend"]["total_usd"]), (7, 0))

    def test_data_dir_flag(self):
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        store.save_json(other / "state.json", {"max_tier": "haiku"})
        self.assertIn("Cap: **haiku**", self.out("--data-dir", str(other), "policy"))


class StateFiles(Sandbox):
    def learned(self) -> Path:
        return store.data_dir() / "learned.json"

    def count_call(self) -> None:
        def change(data: dict) -> None:
            info = data.setdefault("lookup", {})
            info["calls"] = info.get("calls", 0) + 1
        store.update_dict(self.learned(), change)

    def test_state_files_carry_a_schema_version_that_reads_never_see(self):
        store.update_state(lambda state: state.update(max_tier="opus"))
        request.begin("s1", prompt_id=None, level="lookup", need=None, session_tier=None, named=None)
        self.assertEqual(json.loads((self.data / "state.json").read_text())["schema"], store.SCHEMA)
        self.assertEqual(json.loads((self.data / "prompts.json").read_text())["schema"], store.SCHEMA)
        self.assertEqual(store.load_state(), {"max_tier": "opus"})
        self.assertEqual(sorted(store.load_state_file(self.data / "prompts.json")), ["s1"])

    def test_a_file_from_before_versioning_is_migrated_and_kept(self):
        self.learned().write_text(json.dumps({"lookup": {"calls": 4, "votes": 1, "bump": 1}}))
        self.assertEqual(learn.raises()["lookup"], 1)
        self.count_call()
        saved = json.loads(self.learned().read_text())
        self.assertEqual((saved["schema"], saved["lookup"]["calls"], saved["lookup"]["bump"]), (store.SCHEMA, 5, 1))

    def test_a_file_that_fails_to_parse_is_set_aside_before_it_is_replaced(self):
        self.learned().write_text('{"lookup": {"calls": 4,')  # half-written
        self.assertEqual(learn.raises()["lookup"], 0)  # routing goes on
        self.count_call()
        aside = list(self.data.glob("learned.json.corrupt-*"))
        self.assertEqual(len(aside), 1)
        self.assertRegex(aside[0].name, r"^learned\.json\.corrupt-\d{8}T\d{12}Z$")
        self.assertEqual(aside[0].read_text(), '{"lookup": {"calls": 4,')
        self.assertEqual(learn.stats()["lookup"]["calls"], 1)
        text = self.out("models")
        self.assertIn("Set aside because it could not be read", text)
        self.assertIn(str(aside[0]), text)

    def test_a_file_that_is_not_an_object_is_set_aside(self):
        store.state_path().write_text("[1, 2]")
        store.update_state(lambda state: state.update(max_tier="opus"))
        self.assertEqual(len(list(self.data.glob("state.json.corrupt-*"))), 1)
        self.assertEqual(store.load_state(), {"max_tier": "opus"})

    def test_a_file_from_a_newer_version_is_read_but_never_overwritten(self):
        newer = json.dumps({"schema": store.SCHEMA + 1, "lookup": {"calls": 9, "bump": 1}})
        self.learned().write_text(newer)
        self.assertEqual(learn.raises()["lookup"], 1)
        self.count_call()
        self.assertEqual(self.learned().read_text(), newer)
        self.assertEqual(list(self.data.glob("learned.json.corrupt-*")), [])
        self.assertIn(f"only read and never updated: {self.learned()}", self.out("models"))

    def test_a_bad_schema_value_counts_as_unreadable(self):
        for value in (True, -1, "1", 1.0):
            with self.subTest(value=value):
                self.learned().write_text(json.dumps({"schema": value, "lookup": {"calls": 4}}))
                self.count_call()
                self.assertEqual(learn.stats(), {"lookup": {"calls": 1}})
                for aside in self.data.glob("learned.json.corrupt-*"):
                    aside.unlink()

    def test_a_file_that_cant_be_set_aside_is_left_as_it_is(self):
        self.learned().write_text("{oops")
        with mock.patch.object(store.os, "replace", side_effect=PermissionError("in use")):
            self.count_call()
        self.assertEqual(self.learned().read_text(), "{oops")

    def test_every_older_version_has_a_migration_step(self):
        self.assertEqual(set(store.MIGRATIONS), set(range(store.SCHEMA)))

    def test_a_broken_settings_file_is_never_moved(self):
        (self.home / "model-router.json").write_text("{oops")
        cfg.load()
        store.update_state(lambda state: state.update(max_tier="opus"))
        self.assertEqual((self.home / "model-router.json").read_text(), "{oops")
        self.assertEqual(list(self.home.glob("*.corrupt-*")), [])


class ResetCommand(Sandbox):
    """`models --reset` says what it cleared and what it had to leave."""

    NEWER = {"schema": 99, "health": {"opus": {"fail": 2}}, "lookup": {"calls": 3, "bump": 1}}

    def setUp(self):
        super().setUp()
        store.data_dir()

    def reset(self) -> str:
        return self.out("models", "--reset").split("\n\n")[0]

    def test_clears_both_when_it_can(self):
        health.failed("opus", "404 model not found")
        store.save_json(self.data / "learned.json", {"lookup": {"calls": 3, "bump": 1}})
        self.assertEqual(self.reset(), "Learned availability cleared.\nFeedback cleared.")
        self.assertNotIn("health", store.load_state())
        self.assertFalse((self.data / "learned.json").exists())
        self.assertEqual(self.reset(), "No learned availability to clear.\nNo feedback to clear.")

    def test_leaves_files_a_newer_release_wrote(self):
        for name in ("state.json", "learned.json"):
            (self.data / name).write_text(json.dumps(self.NEWER))
        self.assertEqual(self.reset(),
                         "Learned availability left as is: state.json was written by a newer Model Router release.\n"
                         "Feedback left as is: learned.json was written by a newer Model Router release.")
        for name in ("state.json", "learned.json"):
            self.assertEqual(json.loads((self.data / name).read_text()), self.NEWER)

    def test_leaves_files_it_cannot_read(self):
        for name in ("state.json", "learned.json"):
            (self.data / name).mkdir()
        self.assertEqual(self.reset(),
                         "Learned availability left as is: state.json could not be read or written.\n"
                         "Feedback left as is: learned.json could not be read or removed.")
        self.assertTrue((self.data / "learned.json").is_dir())

    def test_update_reports_whether_it_wrote(self):
        path = self.data / "learned.json"
        self.assertEqual(store.try_update(path, lambda data: data.update(a=1)), ({"a": 1}, "saved"))
        path.write_text(json.dumps({"schema": 99, "a": 1}))
        self.assertEqual(store.try_update(path, lambda data: data.update(a=2)), ({"a": 2}, "newer"))
        self.assertEqual(json.loads(path.read_text()), {"schema": 99, "a": 1})

    def test_corrupt_files_are_set_aside_not_deleted(self):
        for name in ("state.json", "learned.json"):
            (self.data / name).write_text('{"half": ')
        self.assertEqual(self.reset(), "No learned availability to clear.\nFeedback cleared.")
        self.assertEqual(len(list(self.data.glob("state.json.corrupt-*"))), 1)
        self.assertEqual(len(list(self.data.glob("learned.json.corrupt-*"))), 1)
        self.assertFalse((self.data / "learned.json").exists())


class SlashSkills(Sandbox):
    """A skill typed as /name is profiled by what it asks for: its arguments and its body."""

    def setUp(self):
        super().setUp()
        self.options(main_effort="true")  # off by default

    HARD_BODY = ("Base directory for this skill: /p/.claude/skills/hard\n\nInvestigate the race condition in the "
                 "auth migration across every service, find the root cause and redesign the locking.")
    LOOKUP_BODY = "Base directory for this skill: /p/.claude/skills/where\n\nList the files under src."
    LINE = "<command-message>{0}</command-message>\n<command-name>/{0}</command-name>\n<command-args>{1}</command-args>"

    def turn(self, name: str, args: str, body: str | None, **extra) -> dict | None:
        command = {"name": name, "args": args, **({"body": body} if body is not None else {})}
        return modpath.turn({"session_id": "s1", "text": self.LINE.format(name, args), "command": command, **extra})

    def test_a_hard_skill_raises_the_turn_and_a_lookup_does_not(self):
        self.session_on("claude-opus-5-5")
        # No placeholder takes the arguments, so the engine appends them (as seen live on 2.1.330).
        appended = self.HARD_BODY + "\n\n\nARGUMENTS: please check src too"
        self.assertEqual(self.turn("hard", "please check src too", appended)["effort"], "xhigh")
        self.assertEqual(self.turn("hard", "", self.HARD_BODY)["effort"], "xhigh")
        self.assertIsNone(self.turn("where", "", self.LOOKUP_BODY))
        self.assertEqual(self.turn("plain", HARD, None), {"level": "complex", "effort": "high"})  # args alone

    def test_a_skill_that_takes_its_arguments_is_profiled_by_them(self):
        self.session_on("claude-opus-5-5")
        raw = (PLUGIN / "skills" / "ship" / "SKILL.md").read_text(encoding="utf-8")
        body = "Base directory for this skill: /p/skills/ship\n\n" + raw.split("---\n", 2)[2]
        typo = "rename the helper in README.md"
        self.assertIsNone(self.turn("model-router:ship", typo, body.replace("$ARGUMENTS", typo)))
        # A fix is complex whatever its size, so a typo fix raises the turn.
        fix = "fix the typo in README.md"
        self.assertEqual(self.turn("model-router:ship", fix, body.replace("$ARGUMENTS", fix)),
                         {"level": "complex", "effort": "high"})
        hard = body.replace("$ARGUMENTS", HARD)
        self.assertEqual(self.turn("model-router:ship", HARD, hard), {"level": "complex", "effort": "high"})
        self.assertEqual(self.turn("model-router:ship", "", body.replace("$ARGUMENTS", ""))["level"], "complex")
        # The settings hook reads the raw file, where the placeholder is still there.
        skills = self.project / ".claude" / "skills" / "build"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text(raw, encoding="utf-8")
        hooks.user_prompt({"session_id": "s1", "prompt": "/build " + typo, "cwd": str(self.project)})
        self.assertEqual(request.load("s1").level, "standard")
        named = self.project / ".claude" / "skills" / "named"
        named.mkdir()
        (named / "SKILL.md").write_text("---\nname: named\narguments: [task]\n---\nRedesign the auth schema across "
                                        "every service: $task\n", encoding="utf-8")
        hooks.user_prompt({"session_id": "s1", "prompt": "/named " + typo, "cwd": str(self.project)})
        self.assertEqual(request.load("s1").level, "standard")

    def test_a_typed_plugin_skill_is_profiled_by_its_arguments(self):
        # Its body isn't under .claude, and the skill's own name must not count as wording.
        # Read as typed, /acme:security-review profiled standard on its own name's "security".
        for typed, level in (("/acme:code-review", "lookup"), ("/acme:code-review 482", "lookup"),
                             ("/acme:security-review", "lookup"), ("/acme:security-review 482", "lookup"),
                             ("/acme:go fix the typo in README.md", "complex"),
                             (f"/acme:go {HARD}", "complex"), (f"/acme:go {HARDEST}", "hardest"),
                             ("/acme:go list the files under src", "lookup")):
            with self.subTest(typed=typed):
                got = intake.from_prompt(typed, self.project)
                self.assertEqual((got.person, got.level), (True, level))

    def test_a_typed_plugin_skill_is_read_from_its_plugin_root(self):
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": "/model-router:ship 1234", "cwd": str(self.project)})
        self.assertEqual(request.load("s1").level, "complex")  # ship's own body, which its arguments only name
        other = self.home / "plugins" / "cache" / "acme"
        (other / "skills" / "go").mkdir(parents=True)
        (other / "skills" / "go" / "SKILL.md").write_text(self.HARD_BODY.split("\n\n")[1] + " $ARGUMENTS\n")
        (other / "skills" / "dir" / "SKILL.md").mkdir(parents=True)  # not a regular file
        (self.home / "plugins" / "installed_plugins.json").write_text(
            json.dumps({"plugins": {"acme@market": [{"installPath": str(other)}]}}))
        for typed, level in (("/acme:go 482", "complex"), ("/acme:dir 482", "lookup"), ("/acme:gone 482", "lookup"),
                             ("/other:go 482", "lookup")):
            with self.subTest(typed=typed):
                self.assertEqual(intake.from_prompt(typed, self.project).level, level)

    def test_a_typed_skill_for_a_plugin_the_install_record_lacks_walks_nothing(self):
        # A --plugin-dir plugin is never in the record, so a walk could not find it and would only cost time.
        other = self.home / "plugins" / "cache" / "acme"
        (other / "skills" / "go").mkdir(parents=True)
        (other / "skills" / "go" / "SKILL.md").write_text(self.HARD_BODY.split("\n\n")[1] + " $ARGUMENTS\n")
        self.assertEqual(agents.installed_roots("acme"), [])
        with mock.patch.object(agents.os, "walk", side_effect=AssertionError("walked the plugins folder")):
            self.assertEqual(intake.from_prompt("/acme:go 482", self.project).level, "lookup")
            hooks.user_prompt({"session_id": "s1", "prompt": "/acme:go 482", "cwd": str(self.project)})
        (self.home / "plugins" / "installed_plugins.json").write_text(
            json.dumps({"plugins": {"acme@market": [{"installPath": str(other)}]}}))
        self.assertEqual(agents.installed_roots("acme"), [other])
        self.assertEqual(intake.from_prompt("/acme:go 482", self.project).level, "complex")

    def test_the_mod_raises_the_request_where_the_settings_hook_saw_less(self):
        self.options(main_effort="false")
        self.session_on("claude-haiku-5-5")
        # Not an installed plugin, so UserPromptSubmit sees only the arguments.
        self.assertIsNone(hooks.user_prompt({"session_id": "s1", "prompt": "/acme:hard 1234"}))
        self.assertEqual(request.load("s1").level, "lookup")
        appended = self.HARD_BODY + "\n\n\nARGUMENTS: 1234"  # no placeholder took them
        self.assertIsNone(self.turn("acme:hard", "1234", appended))  # main_effort is off: no effort
        entry = request.load("s1")
        self.assertEqual((entry.level, entry.need), ("hardest", "opus"))
        self.assertEqual(self.decide("model-router:architect", LOOKUP)["level"], "hardest")  # the floor
        self.assertIn("model-router:architect", hooks.stop({"session_id": "s1"})["reason"])

    def test_a_raise_the_busy_lock_dropped_is_not_reported_as_made(self):
        hook_input = {"session_id": "s1", "cwd": str(self.project)}
        snap = settings.Snapshot(hook_input)
        request.begin("s1", prompt_id="p1", level="lookup", need="haiku", session_tier=None, named=None)
        reading = intake.Intake(False, True, Level.complex, (), command="hard")
        (self.data / "prompts.json.lock").write_text("other")  # held by someone else, and fresh
        with mock.patch.object(store, "LOCK_WAIT_SECONDS", 0.05):
            self.assertFalse(request.raise_to("s1", reading, snap.registry, snap.usable, snap.raises))
        self.assertEqual(request.load("s1").level, "lookup")
        (self.data / "prompts.json.lock").unlink()
        self.assertTrue(request.raise_to("s1", reading, snap.registry, snap.usable, snap.raises))
        self.assertEqual(request.load("s1").level, "complex")

    def test_a_turn_only_raises_and_only_for_a_typed_command_of_the_person(self):
        self.session_on("claude-haiku-5-5")
        self.turn("acme:hard", "1234", self.HARD_BODY)
        self.assertIsNone(request.load("s1").level)  # a turn never starts a request
        hooks.user_prompt({"session_id": "s1", "prompt": "/acme:hard 1234"})
        for text, command in ((HARD, None), (f'Another Claude session sent a message:\n<teammate-message id="a">\n{HARD}\n</teammate-message>', None),
                               (self.LINE.format("hard", ""), {"name": "hard", "args": "", "body": self.HARD_BODY})):
            for origin in ("composer", "peer"):
                if command and origin == "composer":
                    continue
                modpath.turn({"session_id": "s1", "text": text, "command": command, "origin": origin})
                self.assertEqual(request.load("s1").level, "lookup", (text[:30], origin))
        self.options(routing_mode="off")
        self.turn("hard", "", self.HARD_BODY)
        self.assertEqual(request.load("s1").level, "lookup")
        self.options(routing_mode="auto")
        self.turn("hard", "", self.HARD_BODY)
        self.assertEqual(request.load("s1").level, "hardest")
        self.turn("where", "", self.LOOKUP_BODY)
        self.assertEqual(request.load("s1").level, "hardest")  # never lowered

    def test_an_argument_that_only_names_the_target_leaves_the_task_to_the_body(self):
        self.session_on("claude-opus-5-5")
        raw = ("---\nname: security-review\nargument-hint: <pr-number>\n---\nReview pull request #$ARGUMENTS for "
               "security vulnerabilities: auth bypasses, injection, secrets in code, across the API and worker "
               "services.\n")
        body = "Base directory for this skill: /p/.claude/skills/security-review\n\n" + raw.split("---\n", 2)[2]
        for target in ("482", "src/auth.py", "https://github.com/acme/api/pull/482", "PaymentService",
                       "src/a.py src/b.py src/c.py", "482 --base main", "src/auth.py src/session.py",
                       "docs/plan.md app/x.py", "auth module", "payments service", "audit", "the login page",
                       "PR 482 from Dana", "look at it", "the payments service", "the auth module docs"):
            with self.subTest(target=target):
                self.assertEqual(self.turn("security-review", target, body.replace("$ARGUMENTS", target))["effort"],
                                 "high")
                self.assertEqual(intake.profile_skill(target, raw, rendered=False)["level"], "complex")
        skills = self.project / ".claude" / "skills" / "security-review"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text(raw, encoding="utf-8")
        for typed in ("/security-review 482", "/security-review the login page"):
            hooks.user_prompt({"session_id": "s1", "prompt": typed, "cwd": str(self.project)})
            self.assertEqual(request.load("s1").level, "complex", typed)
        ship = (PLUGIN / "skills" / "ship" / "SKILL.md").read_text(encoding="utf-8").split("---\n", 2)[2]
        ship_body = lambda args: ship.replace("$ARGUMENTS", args)  # noqa: E731
        # A template body serves many targets, so its own wording decides at most complex: an issue number
        # through ship doesn't take its pipeline's gate text as the task.
        self.assertEqual(self.turn("model-router:ship", "1234", ship_body("1234")), {"level": "complex",
                                                                                  "effort": "high"})
        # A task in the arguments still decides, the m3-02 case.
        for args in ("rename the helper in README.md", "explain the retry flow"):
            with self.subTest(args=args):
                self.assertIsNone(self.turn("model-router:ship", args, ship_body(args)))
        for args in ("fix the typo in README.md", "fix it"):
            with self.subTest(args=args):
                self.assertEqual(self.turn("model-router:ship", args, ship_body(args)),
                                 {"level": "complex", "effort": "high"})
        # No listed verb: a description without an instruction leaves the task to ship's body, capped at complex.
        for args in ("the README typo near the top", "tidy the imports please"):
            with self.subTest(args=args):
                self.assertEqual(self.turn("model-router:ship", args, ship_body(args))["level"], "complex")
        for text, work in (("fix the typo", True), ("audit", False), ("src/a.py src/b.py", False),
                           ("tidy the imports please", False), ("PaymentService", False),
                           ("the payments service", False), ("please review PR 482", True)):
            self.assertEqual(profile.asks_for_work(text), work, text)

    def test_only_a_placeholder_that_receives_the_arguments_hands_them_the_task(self):
        hard = self.HARD_BODY.split("\n\n")[1]
        rendered = lambda body, args: intake.profile_skill(args, body, rendered=True)["level"]  # noqa: E731
        raw = lambda body, args: intake.profile_skill(args, body, rendered=False)["level"]  # noqa: E731
        # An earlier ARGUMENTS: line in the body is not the engine's trailer, and any trailer means no placeholder.
        self.assertEqual(rendered(hard + "\nARGUMENTS: none\nKeep notes.\n\n\nARGUMENTS: src", "src"), "hardest")
        self.assertEqual(rendered(hard + "\n\n\nARGUMENTS: src  extra", "src extra"), "hardest")
        self.assertEqual(rendered(hard + "\n\n\nARGUMENTS: line one\nline two", "line one\nline two"), "hardest")
        for body, args in ((hard + " Refunds over $500 need approval.", "src"),  # an amount, not a placeholder
                           (hard + " Costs $1.00 a run.", "src"),
                           (hard + " Compare with $2.", "src"),  # $2 receives nothing from one argument
                           (hard + " Target: \\$ARGUMENTS", "src"),  # escaped
                           ("---\narguments: [task]\n---\n" + hard, "src")):  # declared, never used
            self.assertEqual(raw(body, args), "hardest", body)
        task = "list the files under src"  # arguments that carry a task of their own
        self.assertEqual(raw(hard + " Target: $0", task), "lookup")
        self.assertEqual(raw(hard + " Target: $ARGUMENTS[0]", task), "lookup")
        self.assertEqual(raw("---\narguments:\n  - task\n---\n" + hard + " On $task.", task), "lookup")

    def test_a_long_ordinary_skill_body_is_not_a_hard_request(self):
        self.session_on("claude-opus-5-5")
        body = "Never commit secrets or credentials. " + "Write a clear commit message for the staged change. " * 40
        self.assertGreater(len(body), 1500)
        self.assertIsNone(self.turn("commit", "", body))
        self.assertIsNone(self.turn("commit", "", "Review the staged diff in production style.\n" + body))
        # The cut only works while the opening is too short for profile_prompt's own length rule.
        self.assertNotIn("scope", profile.profile_prompt("note " * (intake.SKILL_BODY_CHARS // 5))["signals"])

    def test_a_teammate_message_still_does_not_raise(self):
        self.session_on("claude-opus-5-5")
        self.assertIsNone(self.turn("hard", "", self.HARD_BODY, origin="peer"))
        delivered = "Another Claude session sent a message:\n<cross-session-message from=\"x\">\n" + HARD
        self.assertIsNone(modpath.turn({"session_id": "s1", "text": delivered}))
        # A command left over from a submit whose turn never started can't stand in for another turn's text.
        self.assertIsNone(modpath.turn({"session_id": "s1", "text": delivered,
                                          "command": {"name": "rename", "args": HARD}}))
        self.assertIsNone(modpath.turn({"session_id": "s1", "text": self.LINE.format("other", ""),
                                          "command": {"name": "rename", "args": HARD}}))

    def test_the_settings_hook_reads_a_project_or_user_skill_it_can_find(self):
        self.session_on("claude-haiku-5-5")
        skills = self.project / ".claude" / "skills" / "hard"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("---\nname: hard\ndescription: x\n---\n" + self.HARD_BODY.split("\n\n")[1])
        commands = self.home / "commands"
        commands.mkdir(parents=True)
        (commands / "where.md").write_text("List the files under src.")
        level = lambda prompt: (hooks.user_prompt({"session_id": "s1", "prompt": prompt, "cwd": str(self.project)}),  # noqa: E731
                                request.load("s1").level)[1]
        self.assertEqual(level("/hard please check src too"), "hardest")
        self.assertEqual(level("/where"), "lookup")
        self.assertEqual(level("/elsewhere:unknown " + LOOKUP), "lookup")  # not found: the typed line is profiled
        (skills / "SKILL.md").write_bytes("\ufeff---\r\nname: hard\r\ndescription: secrets schema production\r\n---\r\n"
                                          "List the files under src.\r\n".encode("utf-8"))
        self.assertEqual(level("/hard"), "lookup")  # a BOM and CRLF frontmatter is still left out
        if hasattr(os, "mkfifo"):
            fifo = self.project / ".claude" / "skills" / "pipe"
            fifo.mkdir()
            os.mkfifo(fifo / "SKILL.md")
            self.assertEqual(level("/pipe " + LOOKUP), "lookup")  # never opened, so the hook can't hang


@unittest.skipUnless(shutil.which("sh"), "needs sh")
class Launcher(Sandbox):
    def test_launcher_runs_hooks_and_commands(self):
        self.session_on("claude-opus-5-5")
        launcher = (SCRIPTS / "model-router.sh").as_posix()
        res = subprocess.run(["sh", launcher, "pre-agent"], input=json.dumps(self.call("general-purpose", LOOKUP)),
                             capture_output=True, text=True, env={**os.environ})
        self.assertEqual(json.loads(res.stdout)["hookSpecificOutput"]["updatedInput"]["model"], "haiku")
        res = subprocess.run(["sh", launcher, "policy"], capture_output=True, text=True, env={**os.environ})
        self.assertIn("Subtask level", res.stdout)

    def fake_pythons(self, python3: str, python: str) -> dict:
        """A PATH whose python3 and python are scripts that log each start and then run `real`
        (the interpreter running this test) or exit as a too-old one would."""
        folder = Path(self.tmp.name) / "bin"
        folder.mkdir()
        log = folder / "starts.log"
        real = Path(sys.executable).as_posix()
        bodies = {"real": f'exec "{real}" "$@"', "old": "exit 87",
                  "broken": "echo 'Python was not found' >&2; exit 49"}  # a Store stub or an unset shim
        for name, kind in (("python3", python3), ("python", python)):
            body = bodies[kind]
            script = folder / name
            script.write_bytes(f'#!/bin/sh\necho {name} >> "{log.as_posix()}"\n{body}\n'.encode())
            script.chmod(0o755)
        env = {**os.environ, "PATH": f"{folder}{os.pathsep}/usr/bin{os.pathsep}/bin"}
        return {"env": env, "log": log}

    def test_a_hook_starts_python_once(self):
        self.session_on("claude-opus-5-5")
        fake = self.fake_pythons("real", "real")
        res = subprocess.run(["sh", (SCRIPTS / "model-router.sh").as_posix(), "pre-agent"],
                             input=json.dumps(self.call("general-purpose", LOOKUP)), capture_output=True,
                             text=True, env=fake["env"])
        self.assertEqual(json.loads(res.stdout)["hookSpecificOutput"]["updatedInput"]["model"], "haiku")
        self.assertEqual(fake["log"].read_text().split(), ["python3"])

    def test_a_too_old_or_broken_python3_falls_through_to_python(self):
        self.session_on("claude-opus-5-5")
        for kind in ("old", "broken"):
            with self.subTest(python3=kind):
                fake = self.fake_pythons(kind, "real")
                res = subprocess.run(["sh", (SCRIPTS / "model-router.sh").as_posix(), "pre-agent"],
                                     input=json.dumps(self.call("general-purpose", LOOKUP)), capture_output=True,
                                     text=True, env=fake["env"])
                self.assertEqual(json.loads(res.stdout)["hookSpecificOutput"]["updatedInput"]["model"], "haiku")
                shutil.rmtree(fake["log"].parent)

    def test_with_no_new_enough_python_hooks_pass_and_commands_explain(self):
        launcher = (SCRIPTS / "model-router.sh").as_posix()
        for kind in ("old", "broken"):
            with self.subTest(pythons=kind):
                fake = self.fake_pythons(kind, kind)
                hook = subprocess.run(["sh", launcher, "pre-agent"], input="{}", capture_output=True, text=True,
                                      env=fake["env"])
                self.assertEqual((hook.returncode, hook.stdout), (0, ""))
                command = subprocess.run(["sh", launcher, "policy"], capture_output=True, text=True,
                                         env=fake["env"])
                self.assertEqual(command.returncode, 0)
                self.assertIn("needs Python 3.9 or later", command.stdout)
                shutil.rmtree(fake["log"].parent)

    def test_a_real_command_error_keeps_its_exit_code(self):
        fake = self.fake_pythons("real", "real")
        res = subprocess.run(["sh", (SCRIPTS / "model-router.sh").as_posix(), "--no-such-flag"],
                             capture_output=True, text=True, env=fake["env"])
        self.assertEqual(res.returncode, 2)  # argparse's code, not the fallback's 0
        self.assertEqual(fake["log"].read_text().split(), ["python3", "python3"])  # the run, then the probe

    def test_a_hook_command_never_exits_2(self):
        # Exit 2 blocks PreToolUse, UserPromptSubmit and Stop; argparse exits 2 on a bad flag.
        fake = self.fake_pythons("real", "real")
        for hook in ("pre-agent", "user-prompt", "stop", "mod-spawn"):
            with self.subTest(hook=hook):
                res = subprocess.run(["sh", (SCRIPTS / "model-router.sh").as_posix(), hook, "--no-such-flag"],
                                     input="{}", capture_output=True, text=True, env=fake["env"])
                self.assertEqual(res.returncode, 1, res.stderr)

    def test_a_command_whose_text_names_a_hook_keeps_its_exit_code(self):
        fake = self.fake_pythons("real", "real")
        launcher = (SCRIPTS / "model-router.sh").as_posix()
        for args in (["explain", "--no-such-flag", "stop"], ["--data-dir", "stop", "explain", "--no-such-flag"]):
            with self.subTest(args=args):
                res = subprocess.run(["sh", launcher, *args], capture_output=True, text=True, env=fake["env"])
                self.assertEqual(res.returncode, 2, res.stderr)
        res = subprocess.run(["sh", launcher, "--data-dir", "unused", "stop", "--no-such-flag"], input="{}",
                             capture_output=True, text=True, env=fake["env"])
        self.assertEqual(res.returncode, 1, res.stderr)

    def test_hooks_dont_load_the_network_or_xml_modules(self):
        probe = ("import sys; sys.path.insert(0, sys.argv[1]); import modelrouter.cli; "
                 "print(sorted(m for m in ('urllib.request', 'http.client', 'xml.etree.ElementTree') if m in sys.modules))")
        res = subprocess.run([sys.executable, "-c", probe, str(SCRIPTS)], capture_output=True, text=True)
        self.assertEqual(res.stdout.strip(), "[]", res.stderr)

    def test_the_entry_point_exits_87_on_an_old_python_before_importing_anything(self):
        source = (SCRIPTS / "model-router.py").read_text(encoding="utf-8")
        head = source.split("from pathlib")[0]
        self.assertIn("sys.version_info < (3, 9)", head)
        self.assertIn("87", head)
        # An old Python compiles the whole file before the check runs: any newer syntax would
        # exit 1, which the launcher reads as a real error rather than "try the next one".
        code = "\n".join(line.split("#")[0] for line in source.splitlines() if not line.lstrip().startswith(('"""', "#")))
        self.assertTrue(source.isascii())
        self.assertNotRegex(code, r"""\bf["']|:=|->|\w+\s*:\s*\w+\s*=""")


class ReadmeDiagrams(unittest.TestCase):
    SVG = "{http://www.w3.org/2000/svg}"
    NAMES = ("two-paths.svg", "ladder.svg", "redo-escalation.svg")

    def parse(self, name: str):
        import xml.etree.ElementTree as ET
        return ET.parse(ROOT / "docs" / "assets" / name).getroot()

    def test_each_diagram_is_valid_titled_svg_linked_from_the_architecture_page(self):
        page = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        for name in self.NAMES:
            root = self.parse(name)
            self.assertEqual(root.tag, f"{self.SVG}svg", name)
            for attr in ("viewBox", "width", "height"):
                self.assertTrue(root.get(attr), f"{name} has no {attr}")
            title = root.find(f"{self.SVG}title")
            self.assertTrue(title is not None and (title.text or "").strip(), f"{name} has no title")
            self.assertIn(f"](assets/{name})", page)
            source = (ROOT / "docs" / "assets" / name).read_text(encoding="utf-8")
            self.assertNotIn("var(", source, f"{name}: librsvg and some viewers render CSS variables black")
            self.assertIn("prefers-color-scheme: dark", source, name)

    def test_the_guide_carries_the_precedence_and_stop_check_diagrams(self):
        guide = (ROOT / "docs" / "GUIDE.md").read_text(encoding="utf-8")
        for name in ("precedence.svg", "stop-checks.svg"):
            self.assertIn(f"](assets/{name})", guide)

    def text(self, name: str) -> str:
        return " ".join((cell.text or "").strip() for cell in self.parse(name).iter(f"{self.SVG}text"))

    def test_the_two_paths_diagram_names_every_settings_hook(self):
        events = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        words = self.text("two-paths.svg").split()
        for event in events:
            self.assertIn(event, words)

    def test_the_redo_diagram_states_the_learning_thresholds(self):
        text = self.text("redo-escalation.svg")
        self.assertIn(f"{learn.VOTES_NEEDED} votes", text)
        self.assertIn(f"{round(learn.SHARE_NEEDED * 100)}%", text)

    def test_the_ladder_diagram_matches_the_registry(self):
        config = json.loads((PLUGIN / "config" / "models.json").read_text(encoding="utf-8"))
        by_strength = {m["strength"]: name for name, m in config["models"].items()}
        text = self.text("ladder.svg").lower()
        for level, spec in config["levels"].items():
            row = f"{level} {by_strength[spec['needs']]} {spec['effort']}"
            self.assertIn(row, text)

    def test_the_ladder_note_says_where_a_ruled_out_model_moves_the_pick(self):
        config = cfg.shipped()
        self.assertEqual(ladder.pick(config, "lookup", "balanced", ["sonnet", "opus", "fable"], {}).model, "sonnet")
        self.assertEqual(ladder.pick(config, "complex", "balanced", ["haiku", "sonnet"], {}).model, "sonnet")
        text = self.text("ladder.svg")
        self.assertIn("the pick moves to the nearest usable model above it, or to the strongest one left", text)
        self.assertNotIn("never raised", text)

    def test_the_redo_note_names_each_step_of_the_ladder(self):
        levels = json.loads((PLUGIN / "config" / "models.json").read_text(encoding="utf-8"))["levels"]
        steps = list(zip(levels, list(levels)[1:]))
        stronger = [a for a, b in steps if levels[b]["needs"] > levels[a]["needs"]]
        text = self.text("redo-escalation.svg").lower()
        self.assertIn(f"{', '.join(stronger[:-1])} and {stronger[-1]} move to a stronger model", text)
        for a, b in steps:
            if levels[b]["needs"] == levels[a]["needs"]:
                self.assertIn(f"{a} stays on", text)


class MachineReadable(Sandbox):
    """`--json` on usage and models: one object, a versioned schema, the same facts as the tables."""

    def json_out(self, *argv: str) -> dict:
        return json.loads(self.out(*argv, "--json"))

    def test_usage_json(self):
        self.assertEqual(self.json_out("usage"), {
            "schema": "model-router.usage/1", "days": 7, "agents": [], "models": [], "model_errors": 0,
            "spend": {"total_usd": 0, "rows": [], "unpriced": []}})
        self.assertIsInstance(self.json_out("usage")["spend"]["total_usd"], float)
        self.session_on("claude-opus-5-5")
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", model="haiku")
        for agent_id, model in ((None, "claude-opus-5-5"), ("a1", "claude-new-9")):
            modpath.usage({"session_id": "s1", "agent_id": agent_id, "usage": {
                "model": model, "input_tokens": 0, "output_tokens": 1_000_000,
                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}})
        data = self.json_out("usage", "30")
        self.assertEqual(data["days"], 30)
        self.assertEqual(data["agents"], [{"agent": "general-purpose", "calls": 1, "routed": 1,
                                           "levels": {"lookup": 1}, "ran_on": {"claude-haiku-5-5": 1}}])
        self.assertEqual(data["models"], [{"model": "claude-haiku-5-5", "runs": 1, "tokens": 1200, "run_tokens": None}])
        self.assertEqual(data["spend"], {"total_usd": 20.0, "unpriced": ["claude-new-9"], "rows": [
            {"loop": "main", "model": "claude-opus-5-5", "turns": 1, "output_tokens": 1_000_000, "usd": 20.0},
            {"loop": "subagent", "model": "claude-new-9", "turns": 1, "output_tokens": 1_000_000, "usd": 0}]})

    def test_usage_json_sums_an_agents_turns_beside_its_final_request(self):
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5")  # totalTokens 1200, the final request only
        for agent_id, tokens in (("a1", 3000), ("a1", 2000), (None, 9000)):
            modpath.usage({"session_id": "s1", "agent_id": agent_id, "usage": {
                "model": "claude-haiku-5-5", "input_tokens": tokens - 100, "output_tokens": 50,
                "cache_read_input_tokens": 40, "cache_creation_input_tokens": 10}})
        self.assertEqual(self.json_out("usage")["models"],
                         [{"model": "claude-haiku-5-5", "runs": 1, "tokens": 1200, "run_tokens": 5000}])
        self.assertIn("| claude-haiku-5-5 | 1 | 1,200 | 5,000 |", self.out("usage"))

    def test_usage_says_where_a_trimmed_ledger_starts(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        path = store.ledger_path()
        path.write_text("".join(json.dumps({"ts": (now - datetime.timedelta(days=d)).isoformat(timespec="seconds"),
                                            "event": "spawn", "agent": "old", "pad": "x" * 40}) + "\n"
                                for d in (8, 3)))
        self.assertNotIn("since", self.json_out("usage"))
        self.assertIn("Subagent usage, last 7 days\n", self.out("usage"))
        self.pre("general-purpose", LOOKUP)
        with mock.patch.object(store, "LEDGER_MAX_BYTES", path.stat().st_size):
            store.append_ledger({"event": "spawn", "agent": "general-purpose"})  # the trim drops both old rows
        self.assertNotIn("old", [r.get("agent") for r in ledger.read()])
        start = ledger.read()[0]["ts"]
        self.assertEqual(self.json_out("usage")["since"], start)
        self.assertIn(f"Subagent usage, last 7 days (the ledger starts {start[:10]})\n", self.out("usage"))

    def test_a_model_the_project_added_is_never_shown_as_the_strongest_in_use(self):
        self.override({"models": {"gpt-mini": {"strength": 9, "cost": 0}}})
        self.assertEqual(self.json_out("models")["strongest_in_use"], "fable")
        self.assertIn("Strongest model in use: **fable**", self.out("policy"))

    def test_models_json(self):
        health.ran("fable", "claude-opus-5-5", "opus", forced=False)
        health.ran("fable", "claude-opus-5-5", "opus", forced=False)
        health.ran("haiku", "claude-haiku-5-5", "haiku", forced=False)
        (self.home / "settings.json").write_text(json.dumps({"availableModels": ["haiku", "opus", "fable"]}))
        store.save_json(upstream.path(), {"cache": {"aliases": ["haiku", "saga"]}})
        data = self.json_out("models")
        self.assertEqual(data["schema"], "model-router.models/1")
        self.assertEqual((data["cap"], data["priority"], data["strongest_in_use"]), (None, "balanced", "opus"))
        rows = {m["model"]: m for m in data["models"]}
        self.assertEqual(list(rows), ["haiku", "sonnet", "opus", "fable"])
        self.assertEqual(rows["haiku"], {"model": "haiku", "last_ran_as": "claude-haiku-5-5",
                                         "status": "working", "detail": "working"})
        self.assertEqual((rows["sonnet"]["status"], rows["sonnet"]["last_ran_as"]), ("not_permitted", None))
        self.assertEqual(rows["fable"]["status"], "routed_around")
        self.assertIn("ran as claude-opus-5-5 instead", rows["fable"]["detail"])
        self.assertEqual(rows["opus"]["status"], "not_used")
        self.assertEqual((data["doc_aliases"], data["not_in_registry"]), (["haiku", "saga"], ["saga"]))
        self.assertEqual(data["levels"], [])
        store.save_json(store.data_dir() / "learned.json", {"complex": {"calls": 4, "votes": 1, "bump": 1}})
        self.assertEqual(self.json_out("models")["levels"],
                         [{"level": "complex", "routed_calls": 4, "votes_too_low": 1, "raised": True}])
        self.assertIn("| complex | 4 | 1 | yes |", self.out("models"))
        store.update_state(lambda state: state.update(max_tier="sonnet"))
        rows = {m["model"]: m for m in self.json_out("models")["models"]}
        self.assertEqual(rows["opus"]["status"], "above_cap")
        self.assertEqual(json.loads(self.out("models", "--reset", "--json"))["schema"], "model-router.models/1")
        store.save_json(upstream.path(), {"cache": {"aliases": "saga"}})  # a corrupt cache lists nothing
        self.assertEqual(self.json_out("models")["not_in_registry"], [])

    def test_a_workflow_model_pin_keeps_its_model_and_the_router_sets_only_effort(self):
        # The pin wins over the model a mod sets for a workflow agent, so the router keeps it and sets effort only.
        stage = {"session_id": "s1", "cwd": str(self.project), "parent_model": "claude-opus-5-5",
                 "kind": "workflow", "tool_input": {"subagent_type": "stage", "prompt": LOOKUP}}
        sub = dict(stage, kind="subagent", tool_input={"subagent_type": "general-purpose", "prompt": LOOKUP})
        self.assertEqual(modpath.spawn(stage)["model"], "haiku")
        routed = modpath.spawn(sub)
        self.assertIsNone(self.json_out("models")["workflow_model"])
        self.options(CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL="claude-opus-5-5")
        self.assertEqual(self.json_out("models")["workflow_model"], "claude-opus-5-5")
        self.assertIn("pins every workflow agent to claude-opus-5-5, so Model Router sets only their effort", self.out("models"))
        note = hooks.session_start({"session_id": "s1"})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL runs every workflow agent on claude-opus-5-5", note)
        self.assertIn("so the router sets only their effort", note)
        pick = modpath.spawn(stage)
        self.assertEqual((pick["model"], pick["effort"]), (None, "medium"))
        self.assertIn("CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL", pick["reason"])
        row = [r for r in ledger.read() if r.get("event") == "spawn"][-1]
        self.assertEqual((row["model"], row["was"], row["effort"]), ("opus", "opus", "medium"))
        self.assertEqual(modpath.spawn(sub), routed)
        named = dict(stage, tool_input=dict(stage["tool_input"], model="sonnet"))
        self.assertIsNone(modpath.spawn(named))  # a model the script names is kept, as before

    def test_usage_shows_the_model_a_workflow_agent_ran_on(self):
        # A workflow agent never reaches the Agent tool's PostToolUse, so only its usage rows say what it ran on.
        modpath.spawn({"session_id": "s1", "cwd": str(self.project), "parent_model": "claude-opus-5-5",
                       "kind": "workflow", "tool_input": {"subagent_type": "workflow-subagent", "prompt": LOOKUP}})
        used = {"model": "claude-haiku-5-5", "input_tokens": 10, "output_tokens": 5,
                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        for _ in range(2):  # two turns of one agent are one run
            modpath.usage({"session_id": "s1", "agent_id": "w1", "subagent_type": "workflow-subagent", "usage": used})
        modpath.usage({"session_id": "s1", "agent_id": "w2", "usage": used})  # a subagent: its result row counts
        agent = next(a for a in report.usage_data(1)["agents"] if a["agent"] == "workflow-subagent")
        self.assertEqual(agent["ran_on"], {"claude-haiku-5-5": 1})
        self.assertIn("claude-haiku-5-5 x1", report.usage(1))

    def test_a_workflow_stage_and_a_subagent_of_one_type_both_show_what_they_ran_on(self):
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5")
        used = {"model": "claude-sonnet-5-5", "input_tokens": 10, "output_tokens": 5,
                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        modpath.usage({"session_id": "s1", "agent_id": "w1", "subagent_type": "general-purpose", "usage": used})
        agent = next(a for a in report.usage_data(1)["agents"] if a["agent"] == "general-purpose")
        self.assertEqual(agent["ran_on"], {"claude-haiku-5-5": 1, "claude-sonnet-5-5": 1})

    def test_models_json_names_state_files_set_aside_or_left_read_only(self):
        data = self.json_out("models")
        self.assertEqual((data["set_aside"], data["newer_files"]), ([], []))
        learned = store.data_dir() / "learned.json"
        learned.write_text('{"lookup": {"calls": 4,')
        store.update_dict(learned, lambda data: None)
        newer = store.data_dir() / "prompts.json"
        newer.write_text(json.dumps({"schema": store.SCHEMA + 1}))
        data = self.json_out("models")
        self.assertEqual(data["set_aside"], [str(p) for p in store.set_aside()])
        self.assertRegex(data["set_aside"][0], r"learned\.json\.corrupt-")
        self.assertEqual(data["newer_files"], [str(newer)])

    def test_models_shows_lock_counts_per_state_file(self):
        self.assertNotIn("locks", self.json_out("models"))
        self.assertNotIn("Lock waits", self.out("models"))
        (store.data_dir() / "sample.json.lock").write_text("")
        with mock.patch.object(store, "LOCK_WAIT_SECONDS", 0.02):
            store.update_entry("sample", "s1", n=1)
        self.assertEqual(self.json_out("models")["locks"], [
            {"file": "sample.json", "waits": 0, "other_session": 0, "skipped": 1, "broken": 0, "last_at": self.clock}])
        self.assertIn("| sample.json | 0 | 0 | 1 | 0 |", self.out("models"))


class JsonOutputs(Sandbox):
    """The bytes of `models --json` and `usage --json` on a recorded setup. A change to how the registry is
    built or read may not move them (docs/ARCHITECTURE.md, Compatibility); a new key shows only when it has
    something to say."""

    DIR = FIXTURES / "json-outputs"

    def recorded(self) -> None:
        store.ledger_path().write_text(json.dumps({"ts": "2000-01-01T00:00:00+00:00", "event": "spawn"}) + "\n")  # no `since`
        self.override({"levels": {"complex": {"effort": "xhigh"}}, "agents": {"ruled": {"model": "sonnet"}},
                       "models": {"swift": {"strength": 2.5, "speed": 5, "cost": 2.5}},
                       "prices": {"claude-swift-1": {"input": 1, "output": 5}}}, user=True)
        self.override({"levels": {"standard": {"effort": "high"}}, "agents": {"slow": {"level": "complex"}},
                       "models": {"saga": {"strength": 1.5, "speed": 2, "cost": 1.2}}})
        health.ran("fable", "claude-opus-5-5", "opus", forced=False)
        health.ran("fable", "claude-opus-5-5", "opus", forced=False)
        health.ran("haiku", "claude-haiku-5-5", "haiku", forced=False)
        (self.home / "settings.json").write_text(json.dumps({"availableModels": ["haiku", "sonnet", "opus", "fable", "swift"]}))
        store.save_json(upstream.path(), {"cache": {"aliases": ["haiku", "saga", "swift"]}})
        store.save_json(learn._path(), {"complex": {"calls": 4, "votes": 1, "bump": 1}, "lookup": {"calls": 2}})
        self.session_on("claude-opus-5-5")
        for agent, brief, ran in (("general-purpose", LOOKUP, "claude-haiku-5-5"), ("ruled", BUILD, "claude-sonnet-5-5"),
                                  ("slow", LOOKUP, "claude-opus-5-5"), ("general-purpose", HARD, "claude-opus-5-5")):
            self.pre(agent, brief)
            self.done(agent, brief, ran)
        for agent_id, model in ((None, "claude-opus-5-5"), ("a1", "claude-haiku-5-5"), ("a1", "claude-new-9")):
            modpath.usage({"session_id": "s1", "agent_id": agent_id, "usage": {
                "model": model, "input_tokens": 1200, "output_tokens": 400_000,
                "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 500}})

    def test_the_models_and_usage_outputs_are_the_recorded_bytes(self):
        self.recorded()
        got = {"models": self.out("models", "--json"), "usage": self.out("usage", "--json")}
        store.update_state(lambda state: state.update(max_tier="sonnet"))
        got["models-capped"] = self.out("models", "--json")
        for name, text in got.items():
            self.assertEqual(text, (self.DIR / f"{name}.json").read_text(encoding="utf-8"), name)


@unittest.skipUnless(shutil.which("sh"), "needs sh")
class OverheadBench(unittest.TestCase):
    def test_the_overhead_bench_reports_every_hook_and_state_file(self):
        run = subprocess.run([sys.executable, str(ROOT / "bench" / "overhead.py"), "--runs", "1", "--calls", "40",
                              "--json"], capture_output=True, text=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        hooks_json = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        commands = {h["args"][-1] for groups in hooks_json.values() for group in groups for h in group["hooks"]}
        timed = {row["command"] for row in report["hooks"]}
        self.assertEqual(timed, commands - {"refresh-cache"})  # async, and it would use the network
        for row in [report["floor"], *report["hooks"], *report["mod"]]:
            for key in ("p50_ms", "p95_ms", "runs"):
                self.assertIn(key, row, row)
        for row in report["hooks"]:
            self.assertIn(row["phase"], ("cold", "warm"))
        files = {row["file"]: row for row in report["state"]}
        for name in ("ledger.jsonl", "learned.json", "state.json"):
            self.assertIn(name, files)
            self.assertIn("bytes_per_1000_calls", files[name])
            self.assertIs(files[name]["bounded"], True, name)
        locks = {row["path"]: row for row in report["locks"]}
        self.assertEqual({path: row["spawns"] > 0 for path, row in locks.items()}, {"fallback": True, "mod": True})
        self.assertLessEqual(locks["mod"]["per_spawn"], 3.0)  # the ledger, the route and the votes
        self.assertLessEqual(locks["fallback"]["per_spawn"], 4.0)  # and the rewrite waiting for its result
        for key in ("python", "platform", "claude_code"):
            self.assertIn(key, report["machine"])


class PriceTable(unittest.TestCase):
    """Prices from https://platform.claude.com/docs/en/about-claude/pricing.md (checked 2026-11-22)."""

    # The pricing page lists no rate for this one.
    UNLISTED = {"claude-mythos-preview"}

    def test_every_model_still_in_service_has_a_price(self):
        config = cfg.load()
        lifecycle = json.loads((ROOT / "compat" / "baseline.json").read_text(encoding="utf-8"))["lifecycle"]
        missing = [model for model, info in lifecycle.items()
                   if info.get("state") in ("Active", "Deprecated") and model not in self.UNLISTED
                   and config.price(model) is None]
        self.assertEqual(missing, [])

    def test_mythos_is_priced_like_its_fable_twin(self):
        config = cfg.load()
        for version, rates in (("5-2", (8, 40, 0.2, 10, 16)), ("5-1", (10, 50, 0.25, 12.5, 20)),
                               ("5", (10, 50, 1, 12.5, 20))):
            price = config.price(f"claude-mythos-{version}")
            self.assertEqual(tuple(price[k] for k in ("input", "output", "cache_read", "cache_write",
                                                      "cache_write_1h")), rates, version)


import random  # noqa: E402
import timeit  # noqa: E402


class SecurityClaims(Sandbox):
    """Each claim in docs/SECURITY.md's assessment, rerun on every change (S8.3)."""

    def test_a_repository_file_keeps_only_routing_mode_models_levels_and_agents(self):
        cleaned = cfg._clean({"routing_mode": "advise", "models": {"saga": {"strength": 1}},
                              "levels": {"lookup": {"effort": "low"}}, "agents": {"x": {"level": "lookup"}},
                              "prices": {"claude-saga-1": {"input": 1}}, "newest": {"opus": "claude-x"},
                              "older_aliases": {"bedrock": ["opus"]}, "crew": {"x": {}}, "main": {}}, cfg.PROJECT_FILE, [])
        self.assertEqual(set(cleaned), {"routing_mode", "models", "levels", "agents"})
        self.override({"prices": {"claude-opus-5-5": {"output": 0}}, "newest": {"opus": "claude-evil-1"}})
        config = cfg.load()
        self.assertEqual(config.newest, {k: v for k, v in cfg.base_config()["newest"].items() if k[0] != "$"})
        self.assertEqual(config.prices["claude-opus-5-5"], cfg.base_config()["prices"]["claude-opus-5-5"])

    def test_the_weakest_routing_mode_wins(self):
        for user, project, want in (("auto", "off", "off"), ("off", "auto", "off"), ("advise", "auto", "advise")):
            with self.subTest(user=user, project=project):
                self.override({"routing_mode": user}, user=True)
                self.override({"routing_mode": project})
                self.assertEqual(settings.resolve(cfg.load()).mode, want)

    def test_a_project_model_cannot_take_a_trusted_name_or_count_under_a_cap(self):
        self.override({"models": {"opus-fast": {"strength": 9}, "pus": {"strength": 9}, "saga": {"strength": 0.1}}})
        config = cfg.load()
        self.assertNotIn("opus-fast", config.models)
        self.assertNotIn("pus", config.models)
        self.assertIn("saga", config.available())
        self.assertNotIn("saga", config.available(cap="sonnet"))

    def test_a_project_model_is_never_the_routers_pick(self):
        shipped = cfg.load()
        self.override({"models": {"gpt-mini": {"strength": 9, "cost": 0, "speed": 9}, "saga": {"strength": 2.5}}})
        config = cfg.load()
        for priority in cfg.PRIORITIES:
            for level in Level:
                with self.subTest(priority=priority, level=level):
                    self.assertEqual(ladder.pick(config, level, priority, config.available(), {}),
                                     ladder.pick(shipped, level, priority, shipped.available(), {}))
        self.session_on("claude-opus-5-5")
        self.assertEqual(self.decide("general-purpose", HARD, model="gpt-mini")["model"], "gpt-mini")

    def test_a_named_model_that_cant_run_never_falls_to_a_project_model(self):
        self.override({"models": {"saga": {"strength": 2.5}, "tiny": {"strength": 1.5}}})
        self.session_on("claude-opus-5-5")
        for name in ("opus", "haiku", "opus", "haiku"):
            health.failed(name, "model not found")
        self.assertEqual(self.decide("general-purpose", HARD, model="opus")["model"], "sonnet")
        self.assertEqual(self.decide("general-purpose", LOOKUP, model="haiku")["model"], "sonnet")

    def test_a_redo_the_effort_cap_holds_never_moves_to_a_project_model(self):
        self.override({"models": {"saga": {"strength": 3.5}, "titan": {"strength": 5}}})
        self.session_on("claude-opus-5-5")
        (self.home / "settings.json").write_text(json.dumps({"maxEffortLevel": "high"}))
        frontier = "research a novel fix for the auth migration across every service, root cause unknown"
        for prompt, ran, redo in ((HARD, "opus", "fable"), (frontier, "fable", "fable")):
            with self.subTest(ran=ran):
                self.pre("general-purpose", prompt)
                self.done("general-purpose", prompt, f"claude-{ran}-5-{2 if ran == 'fable' else 5}", model=ran, effort="high")
                self.assertEqual(self.decide("general-purpose", prompt)["model"], redo)

    def test_it_writes_only_under_its_data_directory(self):
        outside = [self.home, self.project]
        before = {p: p.stat().st_mtime_ns for root in outside for p in root.rglob("*")}
        self.session_on("claude-opus-5-5")
        hooks.session_start({"session_id": "s1", "source": "startup", "model": "claude-opus-5-5",
                             "cwd": str(self.project)})
        hooks.user_prompt({"session_id": "s1", "prompt": HARD, "cwd": str(self.project)})
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5")
        for argv in (("policy",), ("models",), ("usage",), ("status",), ("explain", HARD)):
            self.out(*argv)
        after = {p: p.stat().st_mtime_ns for root in outside for p in root.rglob("*")}
        self.assertEqual(after, before)
        self.assertTrue(any(self.data.iterdir()))

    def test_it_never_approves_a_tool_call(self):
        self.session_on("claude-opus-5-5")
        outputs = [hooks.pre_agent(self.call(agent, brief, **extra)) for agent, brief, extra in (
            ("general-purpose", LOOKUP, {}), ("general-purpose", HARD, {}), (None, BUILD, {"model": "opus"}),
            ("Explore", LOOKUP, {"model": "fable"}))]
        for out in outputs:
            decision = ((out or {}).get("hookSpecificOutput") or {}).get("permissionDecision")
            self.assertIn(decision, (None, "deny"))

    def test_only_upstream_opens_connections(self):
        for path in (SCRIPTS / "modelrouter").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if path.name != "upstream.py":
                self.assertIsNone(re.search(r"^\s*(?:import|from)\s+(?:urllib|http|socket|ssl)\b", text, re.M), path)
        hosts = set(re.findall(r"https://([^/\"']+)/", (SCRIPTS / "modelrouter" / "upstream.py").read_text()))
        self.assertLessEqual(hosts, {"code.claude.com", "platform.claude.com", "api.anthropic.com"})

    def test_the_api_key_stays_out_of_repr_and_state(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-secret-123"
        options = settings.resolve(cfg.load())
        self.assertNotIn("sk-secret-123", repr(options))
        settings.record(options)
        hooks.session_start({"session_id": "s1", "source": "startup", "cwd": str(self.project)})
        for path in self.data.rglob("*"):
            if path.is_file():
                self.assertNotIn("sk-secret-123", path.read_text(encoding="utf-8", errors="replace"), path)

    def test_the_redirect_guard_and_skill_grants_have_their_own_tests(self):
        names = {name for cls in (Upstream, SkillGrants) for name in dir(cls) if name.startswith("test_")}
        self.assertIn("test_the_api_key_never_follows_a_redirect", names)
        self.assertIn("test_each_skill_may_run_its_own_commands_and_nothing_that_writes_files", names)


class ProfileProperties(unittest.TestCase):
    """Property tests of profile.py with a fixed seed (S9.8)."""

    VERBS = ["fix", "add", "list", "find", "review", "explain", "update", "investigate", "show", "write"]
    NOUNS = ["the parser", "the cache", "the readme", "src", "the config loader", "the retry logic", "a test",
             "the login page", "the docs", "the build"]
    SIGNALS = ["security", "the database schema", "across every service", "a race condition", "production",
               "the payments flow", "the root cause", "the public API"]
    LIMITS = ["Do not change any files.", "Don't touch the tests.", "Never edit the README.",
              "Without changing the public API.", "No need to ask questions."]
    AGENTS = ["general-purpose", "Explore", "acme:code-reviewer", "helper"]

    def briefs(self, count=300):
        rng = random.Random(7)
        for _ in range(count):
            words = [rng.choice(self.VERBS), rng.choice(self.NOUNS)]
            if rng.random() < 0.4:
                words += ["and", rng.choice(self.VERBS), rng.choice(self.NOUNS)]
            yield " ".join(words) + ".", rng.choice(self.AGENTS), rng

    def level(self, text, agent):
        return profile.profile_call({"prompt": text}, agent)["level"]

    def test_adding_a_signal_word_never_lowers_the_level(self):
        for brief, agent, rng in self.briefs():
            signal = rng.choice(self.SIGNALS)
            with self.subTest(brief=brief, signal=signal, agent=agent):
                self.assertGreaterEqual(self.level(f"{brief} It touches {signal}.", agent), self.level(brief, agent))

    def test_a_limit_never_lowers_what_the_brief_gets_without_it(self):
        for brief, agent, rng in self.briefs():
            limit = rng.choice(self.LIMITS)
            with self.subTest(brief=brief, limit=limit, agent=agent):
                self.assertGreaterEqual(self.level(f"{brief} {limit}", agent), self.level(brief, agent))

    def test_time_grows_linearly_with_length(self):
        rng = random.Random(11)
        words = self.VERBS + [w for n in self.NOUNS + self.SIGNALS for w in n.split()] + ["do", "not", ",", "or"]
        text = " ".join(rng.choice(words) for _ in range(4000))

        def took(t):
            return min(timeit.repeat(lambda: profile.profile_call({"prompt": t}, "x"), number=1, repeat=3))
        small, large = took(text), took(text * 8)
        self.assertLess(large, small * 8 * 4 + 0.05)  # linear, with room for noise; quadratic would be 64x


class SpawnPaths(Sandbox):
    """The mod path and the Agent-call fallback decide the same spawn the same way (S9.8, differential)."""

    def test_the_two_adapters_decide_one_call_the_same_way(self):
        cases = [("general-purpose", LOOKUP, {}), ("general-purpose", BUILD, {}), ("general-purpose", HARD, {}),
                 ("Explore", HARD, {}), (None, "investigate why the checkout test is flaky", {}),
                 ("general-purpose", LOOKUP, {"model": "opus"}), ("general-purpose", HARD, {"effort": "low"}),
                 ("general-purpose", LOOKUP, {"model": "sonnet", "effort": "high"}),
                 ("general-purpose", LOOKUP, {"name": "auditor"})]  # a named call: a teammate, which casts no votes
        self.options(CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS="1")
        for index, (agent, brief, extra) in enumerate(cases):
            with self.subTest(agent=agent, brief=brief, extra=extra):
                call = self.call(agent, brief, tool_use_id=f"t{index}", **extra)
                key = calls.identify(call["tool_input"])[1]
                seen = {}
                for path in ("fallback", "mod"):
                    session_id = f"{path}{index}"
                    self.session_on("claude-opus-5-5", session_id)
                    hook_input = {**call, "session_id": session_id}
                    if path == "fallback":
                        out = hooks.pre_agent(hook_input)
                        answer = ((out or {}).get("hookSpecificOutput") or {}).get("updatedInput") or {}
                    else:
                        kind = "teammate" if "name" in extra else "subagent"
                        answer = modpath.spawn({**hook_input, "parent_model": "claude-opus-5-5", "kind": kind}) or {}
                    # What runs: the fallback returns the whole call, the mod only what it changes.
                    ran = (answer.get("model") or extra.get("model"), answer.get("effort") or extra.get("effort"))
                    route = calls.route_of(session_id, key, f"t{index}")
                    row = next(r for r in self.ledger() if r.get("event") == "spawn" and r["session"] == session_id)
                    seen[path] = (ran, {k: v for k, v in dataclasses.asdict(route).items() if k not in ("at", "path")},
                                  {k: v for k, v in row.items() if k not in ("ts", "session", "via", "kind")})
                self.assertEqual(seen["fallback"], seen["mod"])

    def spawn_on(self, path: str, session: str, agent: str, brief: str) -> None:
        self.session_on("claude-opus-5-5", session)
        if path == "fallback":
            hooks.pre_agent(self.call(agent, brief, session_id=session, tool_use_id="t1"))
        else:
            modpath.spawn({"session_id": session, "cwd": str(self.project), "parent_model": "claude-opus-5-5",
                             "kind": "subagent", **({"tool_use_id": "t1"} if path == "mod with a spawn id" else {}),
                             "tool_input": {"subagent_type": agent, "prompt": brief}})

    def test_every_kind_of_outcome_leaves_the_same_redo_on_both_paths(self):
        agent, brief = "general-purpose", LOOKUP
        swapped = SWAP["swapped"]["modelsUsed"]
        outcomes = {
            "a result": (lambda s: self.done(agent, brief, "claude-haiku-5-5", session_id=s, tool_use_id="t1"), True),
            "a result on the rewritten call": (lambda s: self.done(agent, brief, "claude-haiku-5-5", session_id=s,
                                                                    model="haiku", effort="medium"), True),
            "a swapped result": (lambda s: self.done(agent, brief, "claude-haiku-5-5", swapped, session_id=s), False),
            "an error": (lambda s: hooks.agent_failed({**self.call(agent, brief, session_id=s), "error": "timed out"}), False),
            "a background launch": (lambda s: self.launched(agent, brief, "claude-haiku-5-5", "a1", session_id=s), False),
            "a stop with an answer": (lambda s: (self.launched(agent, brief, "claude-haiku-5-5", "a1", session_id=s),
                                                 hooks.agent_stopped({"session_id": s, "agent_id": "a1",
                                                                      "last_assistant_message": "src/a.py"})), True),
            "a stop with no answer": (lambda s: (self.launched(agent, brief, "claude-haiku-5-5", "a1", session_id=s),
                                                 hooks.agent_stopped({"session_id": s, "agent_id": "a1",
                                                                      "last_assistant_message": " "})), False),
        }
        for name, (came_back, redo) in outcomes.items():
            seen = {}
            for number, path in enumerate(("fallback", "mod", "mod with a spawn id")):
                with self.subTest(outcome=name, path=path):
                    session = f"{name}-{number}"
                    self.spawn_on(path, session, agent, brief)
                    came_back(session)
                    again = self.decide(agent, brief, session_id=session)
                    seen[path] = (again["level"], again["model"], again["effort"], again["escalated_from"])
            want = ("standard", "sonnet", "medium", "lookup") if redo else ("lookup", "haiku", "medium", None)
            self.assertEqual(seen, {path: want for path in ("fallback", "mod", "mod with a spawn id")}, name)

    def test_a_model_the_cap_lowered_is_redone_on_both_paths_and_one_the_caller_kept_is_not(self):
        self.options(max_tier="sonnet")
        for number, (path, model, redo) in enumerate((("fallback", "fable", True), ("mod", "fable", True),
                                                      ("fallback", "sonnet", False), ("mod", "sonnet", False))):
            session = f"s{number}"
            self.session_on("claude-opus-5-5", session)
            if path == "fallback":
                hooks.pre_agent(self.call("general-purpose", LOOKUP, session_id=session, model=model))
            else:
                modpath.spawn({"session_id": session, "cwd": str(self.project), "parent_model": "claude-opus-5-5",
                                 "kind": "subagent", "tool_input": {"subagent_type": "general-purpose",
                                                                    "prompt": LOOKUP, "model": model}})
            self.done("general-purpose", LOOKUP, "claude-sonnet-5-5", session_id=session)
            again = self.decide("general-purpose", LOOKUP, session_id=session)
            self.assertEqual((again["escalated_from"], again["level"]), ("lookup", "standard") if redo else (None, "lookup"),
                             (path, model))

    def locks_taken(self, act) -> list[str]:
        """The state files `act` took a lock on, in order."""
        taken, real = [], store.locked

        @contextlib.contextmanager
        def counting(path):
            taken.append(path.name)
            with real(path):
                yield

        with mock.patch.object(store, "locked", counting):
            act()
        return taken

    def spawn_again(self, path: str, session: str, brief: str) -> None:
        """One more spawn in a session `spawn_on` already set up."""
        if path == "fallback":
            hooks.pre_agent(self.call("general-purpose", brief, session_id=session))
        else:
            modpath.spawn({"session_id": session, "cwd": str(self.project), "parent_model": "claude-opus-5-5",
                             "kind": "subagent", "tool_input": {"subagent_type": "general-purpose", "prompt": brief}})

    def test_a_spawn_takes_one_lock_per_state_file_it_writes(self):
        # A spawn that is the request's strongest so far still writes its route and that fact in one go.
        for path, want in (("fallback", ["ledger.jsonl", "routes.json", "learned.json", "pending.json"]),
                           ("mod", ["ledger.jsonl", "routes.json", "learned.json"])):
            with self.subTest(path=path):
                session = f"first-{path}"
                hooks.user_prompt({"session_id": session, "prompt": HARD})
                self.spawn_on(path, session, "general-purpose", LOOKUP)
                taken = self.locks_taken(lambda: self.spawn_again(path, session, HARD))
                self.assertEqual(taken, want)

    def test_a_redo_takes_one_lock_on_the_learned_file_for_its_count_and_its_vote(self):
        for path in ("fallback", "mod"):
            with self.subTest(path=path):
                session = f"redo-{path}"
                self.spawn_on(path, session, "general-purpose", LOOKUP)
                self.done("general-purpose", LOOKUP, "claude-haiku-5-5", session_id=session)
                self.advance(fallback.REFUSED_AFTER + 1)
                before = learn.stats()
                taken = self.locks_taken(lambda: self.spawn_again(path, session, LOOKUP))
                self.assertEqual(taken.count("learned.json"), 1)
                after = learn.stats()
                self.assertEqual(after["standard"]["calls"], before.get("standard", {}).get("calls", 0) + 1)  # its count
                self.assertEqual(after["lookup"]["votes"], before["lookup"].get("votes", 0) + 1)  # its vote

    def test_a_route_that_cant_be_written_doesnt_cost_the_spawn_its_count(self):
        for path in ("fallback", "mod"):
            with self.subTest(path=path):
                before = learn.stats().get("lookup", {}).get("calls", 0)
                with mock.patch.object(calls, "routed", side_effect=OSError("disk full")):
                    self.spawn_on(path, f"s-{path}", "general-purpose", LOOKUP)
                self.assertEqual(learn.stats()["lookup"]["calls"], before + 1)

    def test_a_result_leaves_the_same_records_on_both_paths(self):
        for number, path in enumerate(("fallback", "mod")):
            self.spawn_on(path, f"s{number}", "general-purpose", LOOKUP)
            self.done("general-purpose", LOOKUP, "claude-haiku-5-5", session_id=f"s{number}")
        key = calls.identify({"subagent_type": "general-purpose", "prompt": LOOKUP})[1]
        done = [{k: v for k, v in store.load_entry("done", f"s{n}")[key].items() if k != "at"} for n in (0, 1)]
        self.assertEqual(done[0], done[1])
        routes = [{k: v for k, v in calls.route_of(f"s{n}", key).__dict__.items() if k not in ("at", "path")} for n in (0, 1)]
        self.assertEqual(routes[0], routes[1])
        self.assertEqual([calls.route_of(f"s{n}", key).path for n in (0, 1)], ["fallback", "mod"])


class EdgeCases(Sandbox):
    """Compaction, empty briefs and a spawn another mod changed (S12.7, S12.11, S12.14)."""

    def test_a_compaction_keeps_the_request_and_routed_calls_and_the_model(self):
        self.session_on("claude-sonnet-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": HARD, "cwd": str(self.project)})
        self.pre("general-purpose", LOOKUP)
        def snapshot() -> dict:
            return {"prompts": request.load("s1"), "routes": store.load_entry("routes", "s1"),
                    "pending": store.load_entry("pending", "s1"), "sessions": store.load_entry("sessions", "s1")}
        state = snapshot()
        self.assertTrue(all(state.values()), state)
        out = hooks.session_start({"session_id": "s1", "source": "compact", "cwd": str(self.project)})
        self.assertEqual(snapshot(), state)  # no model: kept as is
        self.assertIn("Model Router sets", json.dumps(out))  # compaction drops the old note, so it is sent again
        hooks.session_start({"session_id": "s1", "source": "compact", "model": "claude-opus-5-5",
                             "cwd": str(self.project)})
        self.assertEqual(session.model("s1"), "claude-opus-5-5")
        self.assertEqual(store.load_entry("pending", "s1"), state["pending"])

    def test_an_empty_or_blank_brief_is_a_lookup_and_fails_open(self):
        for prompt in ("", "   ", "\n\t \n"):
            with self.subTest(prompt=repr(prompt)):
                self.assertEqual(profile.profile_call({"prompt": prompt}, "general-purpose")["level"], "lookup")
                self.assertEqual(profile.profile_call({"prompt": prompt, "description": None}, "x")["level"],
                                 "lookup")
        self.session_on("claude-opus-5-5")
        for tool_input in ({"prompt": ""}, {"prompt": "   "}, {}, {"prompt": None}, {"description": ""}):
            with self.subTest(tool_input=tool_input):
                out = hooks.pre_agent({"session_id": "s1", "cwd": str(self.project), "tool_input": tool_input})
                decision = ((out or {}).get("hookSpecificOutput") or {}).get("permissionDecision")
                self.assertIsNone(decision)  # nothing to route on: the call goes through untouched
        self.assertEqual(self.out_hook("pre-agent", {"session_id": "s1", "tool_input": {"prompt": ""}})[0], 0)

    def out_hook(self, command, payload):
        res = subprocess.run([sys.executable, str(SCRIPT), command], input=json.dumps(payload), capture_output=True,
                             text=True, env={**os.environ})
        return res.returncode, res.stdout

    def test_a_result_on_another_model_is_logged_as_the_model_that_ran(self):
        self.session_on("claude-opus-5-5")
        # The fallback path routed a lookup to haiku; the provider ran it on sonnet: a substitution.
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-sonnet-5-5")
        rows = [r for r in self.ledger() if r.get("event") == "result"]
        self.assertEqual((rows[-1]["asked"], rows[-1]["resolved_model"]), ("haiku", "claude-sonnet-5-5"))
        self.assertTrue(health.load().healthy("haiku"))
        # A model that ran as asked clears it, so one stray change never takes a model out of rotation.
        self.pre("general-purpose", LOOKUP + " again")
        self.done("general-purpose", LOOKUP + " again", "claude-haiku-5-5")
        self.pre("general-purpose", LOOKUP + " third")
        self.done("general-purpose", LOOKUP + " third", "claude-sonnet-5-5")
        self.assertTrue(health.load().healthy("haiku"))  # a second substitution in a row would have routed it around
        # Under CLAUDE_CODE_SUBAGENT_MODEL_FORCE every agent runs on the forced model: never a substitution.
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="opus", CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1")
        self.pre("general-purpose", LOOKUP + " forced")
        for _ in range(2):
            self.done("general-purpose", LOOKUP + " forced", "claude-sonnet-5-5")
        self.assertTrue(health.load().healthy("haiku"))
        self.done("general-purpose", LOOKUP + " forced", "claude-opus-5-5")
        self.assertEqual([r for r in self.ledger() if r.get("event") == "result"][-1]["resolved_model"],
                         "claude-opus-5-5")


@unittest.skipUnless(os.name == "posix" and shutil.which("sh"), "needs a POSIX sh to isolate")
class NoPython(Sandbox):
    """With no python3 or python on PATH at all, hooks pass and commands explain (S12.23, S13.3)."""

    def test_every_hook_exits_0_and_a_command_explains(self):
        folder = Path(self.tmp.name) / "only-sh"
        folder.mkdir()
        sh = Path(shutil.which("sh")).resolve()
        (folder / "sh").symlink_to(sh)
        env = {"PATH": str(folder), "HOME": str(self.home), "CLAUDE_PLUGIN_DATA": str(self.data)}
        launcher = (SCRIPTS / "model-router.sh").as_posix()
        probe = subprocess.run([str(sh), "-c", "command -v python3 || command -v python || echo none"],
                               capture_output=True, text=True, env=env)
        self.assertEqual(probe.stdout.strip(), "none")
        for command in sorted(hooks.HANDLERS):
            with self.subTest(hook=command):
                res = subprocess.run([str(sh), launcher, command], input='{"session_id": "s1"}', capture_output=True,
                                     text=True, env=env)
                self.assertEqual((res.returncode, res.stdout), (0, ""), res.stderr)
        for command in ("policy", "models", "usage"):
            res = subprocess.run([str(sh), launcher, command], capture_output=True, text=True, env=env)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("needs Python 3.9 or later", res.stdout)


def _mod_types() -> Path | None:
    """The installed Claude Code's mod API types, when they can be found."""
    named = os.environ.get("MODEL_ROUTER_MOD_TYPES")
    if named:
        return Path(named) if Path(named).is_file() else None
    claude = shutil.which("claude")
    if not claude:
        return None
    try:
        version = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=20).stdout.split()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return None
    roots = {Path(tempfile.gettempdir()), Path("/tmp"), Path("/private/tmp")}
    uid = os.getuid() if hasattr(os, "getuid") else None
    for root in roots:
        for base in ([root / f"claude-{uid}"] if uid is not None else []) + [root]:
            found = sorted(base.glob(f"bundled-skills/{version}/*/plugin-authoring/types/claude-code.d.ts"))
            if found:
                return found[0]
    return None


class ModApiTypes(unittest.TestCase):
    """Every event and field hooks/router.ts uses exists in the installed Claude Code's mod types (S12.26)."""

    def type_fields(self, types: str, name: str, seen: set) -> set:
        if name in seen:
            return set()
        seen.add(name)
        match = re.search(rf"\b(?:export )?type {re.escape(name)}(?:<[^>]*>)? = ", types)
        if not match:
            return set()
        depth, i, body = 0, match.end(), []
        while i < len(types):
            ch = types[i]
            depth += ch in "{(<"
            depth -= ch in "})>"
            body.append(ch)
            if (ch == ";" and depth <= 0) or depth < 0:
                break
            i += 1
        text = "".join(body)
        fields = set(re.findall(r"^\s*(?:readonly\s+)?(\w+)\??:", text, re.M))
        for ref in set(re.findall(r"\b([A-Z]\w+)\b", text)) - {name}:
            fields |= self.type_fields(types, ref, seen)
        return fields

    def test_router_ts_uses_only_events_and_fields_the_types_declare(self):
        path = _mod_types()
        if path is None:
            self.skipTest("the installed Claude Code's mod types weren't found (set MODEL_ROUTER_MOD_TYPES)")
        types = re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(encoding="utf-8"), flags=re.S)  # docs hold braces
        router = (PLUGIN / "hooks" / "router.ts").read_text(encoding="utf-8")
        blocks = re.split(r"\n  on\('", router)[1:]
        self.assertGreaterEqual(len(blocks), 7)
        for block in blocks:
            event = block.split("'", 1)[0]
            with self.subTest(event=event):
                # The event maps name an input, a result and a stream chunk type; the input is the payload.
                names = re.findall(rf"'{re.escape(event)}': (\w+);", types)
                self.assertTrue(names, f"{event} is not an event in {path}")
                declared = next((n for n in names if n.endswith("Input")), names[0])
                if event == "tool.call":
                    continue  # its fields are the called tool's input, typed per tool
                fields = self.type_fields(types, declared, set())
                used = set(re.findall(r"\be\.(\w+)", block))
                missing = sorted(f for f in used if f not in fields)
                self.assertEqual(missing, [], f"{event} ({declared}) lacks {missing}")


class ClockAndCleanup(Sandbox):
    """A clock moved backwards, and copies of unreadable state files piling up."""

    def test_a_run_stamped_in_the_future_is_not_a_redo(self):
        route = calls.Route(model="haiku", sent=None, level=cfg.Level.lookup, effort="low", source="router", path="mod")
        calls.routed("s1", "k", route)
        with self.clock_at(self.clock + 3600):
            calls.returned("s1", calls.Outcome("result", key="k"))
        self.assertIsNone(calls.finished_run("s1", "k"))
        with self.clock_at(self.clock - 60):
            calls.returned("s1", calls.Outcome("result", key="k"))
        self.assertEqual(calls.finished_run("s1", "k")["level"], "lookup")

    def test_only_the_newest_copies_of_an_unreadable_file_are_kept(self):
        learned = store.data_dir() / "learned.json"
        for i in range(store.KEEP_SET_ASIDE + 3):
            learned.write_text('{"broken": ' + str(i))
            store.update_dict(learned, lambda data: data.update(ok=True))
        copies = sorted(store.data_dir().glob("learned.json.corrupt-*"))
        self.assertEqual(len(copies), store.KEEP_SET_ASIDE)
        self.assertEqual(copies[-1].read_text(), '{"broken": ' + str(store.KEEP_SET_ASIDE + 2))

    def test_set_asides_in_one_clock_tick_each_keep_their_update(self):
        # Windows' clock on Python 3.9 can hand two set-asides the same microsecond stamp.
        tick = datetime.datetime(2026, 11, 23, 12, 0, 0, 0, tzinfo=datetime.timezone.utc)
        frozen = types.SimpleNamespace(timezone=datetime.timezone,
                                       datetime=types.SimpleNamespace(now=lambda tz=None: tick))
        learned = store.data_dir() / "learned.json"
        with mock.patch.object(store, "_dt", frozen):
            for i in range(store.KEEP_SET_ASIDE + 3):
                learned.write_text('{"broken": ' + str(i))
                store.update_dict(learned, lambda data: data.update(ok=True))
                self.assertNotIn("broken", learned.read_text(), i)  # saved, not left corrupt
        copies = sorted(store.data_dir().glob("learned.json.corrupt-*"))
        self.assertEqual(len(copies), store.KEEP_SET_ASIDE)
        self.assertEqual([c.read_text() for c in copies],
                         ['{"broken": ' + str(i) for i in range(3, store.KEEP_SET_ASIDE + 3)])


class DesignDecisions(unittest.TestCase):
    """A design decision worked out against load and consistency limits needs the strongest coding level."""

    def test_a_design_against_load_and_consistency_limits_is_hardest(self):
        brief = ("Read SPEC.md and design how the catalog servers cache prices. This is a design decision with "
                 "data-consistency and performance trade-offs: honour the staleness limits and the database's "
                 "capacity. Write DESIGN.json with the strategy, ttl_seconds and max_staleness_seconds.")
        result = profile.profile_call({"description": "Design catalog price caching", "prompt": brief}, "claude")
        self.assertEqual((result["level"], sorted(result["signals"])), ("hardest", ["ambiguity", "load", "risk"]))
        # Loading a file isn't a load limit.
        self.assertEqual(profile.profile_call({"description": "Find the key load path",
                                               "prompt": "Find where the app loads its config."}, "Explore")["level"],
                         "lookup")

    def test_a_load_word_without_a_design_choice_adds_nothing(self):
        for prompt in ("Rename the flaky latency test in test_client.py.",
                       "Review this function for performance issues."):
            result = profile.profile_call({"prompt": prompt}, "general-purpose")
            self.assertEqual(result["level"], "standard", prompt)
            self.assertNotIn("load", result["signals"], prompt)

    def test_a_decision_already_made_asks_for_none(self):
        for prompt in ("As decided on Monday, rename the payments column in every service that reads it, across all "
                       "services.", "We decided on Postgres, so migrate the billing schema in all services to it."):
            result = profile.profile_call({"prompt": prompt}, "general-purpose")
            self.assertEqual(result["level"], "complex", prompt)
            self.assertNotIn("ambiguity", result["signals"], prompt)
        for prompt in ("Help me decide on a cache for the session store.",
                       "We need to decide whether to shard the orders table.",
                       "Choosing between Kafka and SQS for the event bus.", "Weighing the options for the job queue."):
            self.assertIn("ambiguity", profile.profile_call({"prompt": prompt}, "general-purpose")["signals"], prompt)

    def test_a_reported_plan_or_choice_lets_no_load_word_count(self):
        for prompt in ("As planned, rename the payments column in every service, across all services, to keep latency "
                       "low.", "We decided on Postgres to handle 10,000 writes per second; migrate the billing schema "
                       "in all services to it."):
            result = profile.profile_call({"prompt": prompt}, "general-purpose")
            self.assertEqual(result["level"], "complex", prompt)
            self.assertNotIn("load", result["signals"], prompt)

    def test_a_doc_or_file_name_is_not_a_signal(self):
        cases = {"Read the migration guides under docs/ and list every renamed option.": [],
                 "Using only docs/, find the version that changed the default and write RESEARCH.json.": [],
                 "Migrate the billing schema to Postgres.": ["risk"],
                 "Research which queue the team should adopt and write a short note.": ["ambiguity"]}
        for prompt, expected in cases.items():
            self.assertEqual(profile.profile_call({"prompt": prompt}, "general-purpose")["signals"], expected, prompt)

    def test_researching_docs_is_not_research_grade(self):
        brief = ("Research the fetchkit docs: read the changelogs and migration guides across the docs tree, then "
                 "write RESEARCH.json with the version where the retry default changed.")
        self.assertEqual(profile.profile_call({"prompt": brief}, "general-purpose")["level"], "complex")
        self.assertEqual(profile.profile_prompt(brief)["level"], "complex")
        grade = ("Research-grade problem: our CRDT editor diverges in rare three-way merges across clients; find the "
                 "root cause and design a fix for the data loss.")
        self.assertEqual(profile.profile_call({"prompt": grade}, "general-purpose")["level"], "frontier")

    def test_only_a_millisecond_budget_counts_as_load(self):
        for prompt in ("Plan the release notes for 2.0; the API answers in 120 ms.",
                       "Plan the sprint: rename the 300 ms login delay."):
            result = profile.profile_call({"prompt": prompt}, "general-purpose")
            self.assertEqual(result["level"], "standard", prompt)
            self.assertNotIn("load", result["signals"], prompt)
        for prompt in ("Choose a partitioning scheme for the events table so writes stay under 50 ms at 10,000 per "
                       "second.", "Design a scheme that keeps p99 under 200 ms.",
                       "Choose a caching approach with p99 200 ms for reads."):
            self.assertIn("load", profile.profile_call({"prompt": prompt}, "general-purpose")["signals"], prompt)

    def test_a_capacity_or_sizing_decision_keeps_its_load_signal(self):
        for prompt, level in (("Plan the capacity of the queue cluster for 10x traffic: how many brokers do we need?",
                               "complex"),
                              ("Size the connection pool so the billing service meets its latency target under "
                               "peak load.", "standard")):
            result = profile.profile_call({"prompt": prompt}, "general-purpose")
            self.assertEqual(result["level"], level, prompt)
            self.assertIn("load", result["signals"], prompt)


class Levels(unittest.TestCase):
    def test_levels_order_by_difficulty_not_alphabetically(self):
        names = ["lookup", "standard", "complex", "hardest", "frontier"]
        self.assertEqual(sorted(names), ["complex", "frontier", "hardest", "lookup", "standard"])  # what a bare str does
        self.assertEqual(sorted(reversed(cfg.LEVELS)), [Level(n) for n in names])
        self.assertIs(max(Level.lookup, Level.hardest), Level.hardest)
        self.assertIs(min(Level.frontier, Level.complex), Level.complex)
        self.assertTrue(Level.lookup < "standard" < Level.complex <= Level.complex < Level.hardest < Level.frontier)
        self.assertFalse(Level.hardest < Level.complex)
        self.assertIs(Level.complex.up(), Level.hardest)
        self.assertIsNone(Level.frontier.up())

    def test_a_level_is_its_bare_name_wherever_it_is_written(self):
        self.assertEqual(json.dumps({"level": Level.hardest}), '{"level": "hardest"}')
        self.assertEqual((f"{Level.complex}", str(Level.complex), "%s" % Level.complex), ("complex",) * 3)
        self.assertEqual(Level.complex, "complex")
        self.assertEqual({Level.complex: 1}["complex"], 1)
        self.assertEqual((Level.parse("nope"), Level.parse(5), Level.parse(None)), (None, None, None))
        self.assertIs(Level.parse("complex"), Level.complex)
        with self.assertRaises(TypeError):
            Level.complex < 3

    def test_only_config_knows_the_order_of_the_levels(self):
        found = []
        for folder, pattern in ((SCRIPTS / "modelrouter", "*.py"), (ROOT / "bench", "*.py"), (ROOT / "tools", "*.py"),
                                (ROOT / "tests", "*.py")):
            for path in folder.glob(pattern):
                if path.name != "config.py" and "LEVELS" + ".index" in path.read_text(encoding="utf-8"):
                    found.append(str(path.relative_to(ROOT)))
        self.assertEqual(found, [])


class Roles(unittest.TestCase):
    """An agent's name reads as a role by whole parts, from one table: "researcher" holds "search",
    "relocation" holds "locat". The levels each name gets are in tests/fixtures/agent-name-corpus.json."""

    def test_the_table_carries_what_each_of_our_agents_is(self):
        role = profile.role
        self.assertEqual(role("model-router:architect"), profile.Role("heavy", floor="complex", strong=True, writes=False))
        self.assertEqual(role("model-router:reviewer"),
                         profile.Role("heavy", floor="complex", strong=True, checker=True, writes=False))
        self.assertEqual(role("model-router:deep-solver"), profile.Role("heavy", floor="hardest", strong=True))
        self.assertEqual(role("model-router:verifier"), profile.Role("light", cap="standard", checker=True))
        self.assertEqual(role("model-router:implementer"), profile.Role())

    def test_another_plugins_agent_is_only_a_kind_never_strong_or_read_only(self):
        self.assertEqual(profile.role("acme:deep-solver"), profile.Role("heavy", floor="complex"))
        self.assertEqual(profile.role("acme:code-reviewer"), profile.Role("heavy", floor="complex", checker=True))
        self.assertEqual(profile.role("scout"), profile.Role("light", cap="standard"))

    def test_a_name_that_only_contains_a_role_word_has_no_role(self):
        for agent in ("researcher", "relocation-migrator", "dependency-resolver", "helper", "", None):
            self.assertEqual(profile.role(agent), profile.Role(), agent)

    def test_the_strong_roles_are_the_ones_that_get_the_requests_floor(self):
        names = {agent.split(":")[1] for agent, held in profile.ROLES.items() if held.strong}
        self.assertEqual(names, {profile.ARCHITECT, profile.REVIEWER, profile.DEEP_SOLVER})


if __name__ == "__main__":
    unittest.main()
