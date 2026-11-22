"""Tests for the Model Router plugin runtime. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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

sys.path.insert(0, str(SCRIPTS))
from modelrouter import agents, calls, cli, gates, hooks, learn, profile, registry, report, router, session, settings, spawn, store, upstream  # noqa: E402
from modelrouter import config as cfg  # noqa: E402

LOOKUP = "list the files under src"
BUILD = "implement the parser change"
HARD = "fix the auth migration across every service"


def frontmatter(path: Path) -> dict:
    match = re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    assert match, f"{path} has no frontmatter"
    fields = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


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
            if name.startswith(("CLAUDE_PLUGIN_OPTION_", "CLAUDE_CODE_SUBAGENT_", "CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_USE_", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "ANTHROPIC_")):
                del os.environ[name]
        self.no_managed = mock.patch.object(
            registry, "_settings_files",
            lambda project: [self.home / "settings.json", project / ".claude" / "settings.json"])
        self.no_managed.start()
        self.managed = self.home / "managed-settings.json"
        self.managed_file = mock.patch.object(registry, "_managed_file", lambda: self.managed)
        self.managed_file.start()

    def tearDown(self):
        self.managed_file.stop()
        self.no_managed.stop()
        self.patch.stop()
        self.tmp.cleanup()

    # helpers ---------------------------------------------------------------
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
        config = cfg.load_config(hook_input)
        options = settings.resolve(config)
        return router.decide(spawn.facts(hook_input, config, options), config, options)

    def pre(self, agent: str | None, prompt: str, **extra) -> dict | None:
        out = hooks.pre_agent(self.call(agent, prompt, **extra))
        return out["hookSpecificOutput"] if out else None

    def done(self, agent: str | None, prompt: str, ran_as: str, **extra) -> None:
        """The agent call finished and Claude Code reports the model it ran on."""
        hooks.post_agent({**self.call(agent, prompt, **extra),
                          "tool_response": {"resolvedModel": ran_as, "status": "completed", "totalTokens": 1200}})

    def launched(self, agent: str | None, prompt: str, ran_as: str, agent_id: str, **extra) -> None:
        """The agent call returned at once: the agent runs on in the background."""
        hooks.post_agent({**self.call(agent, prompt, **extra), "tool_response": {
            "resolvedModel": ran_as, "status": "async_launched", "agentId": agent_id}})

    def stopped(self, agent_id: str, answer: str | None = "src/a.py and src/b.py") -> None:
        """A subagent finished, as SubagentStop reports it."""
        hook_input = {"session_id": "s1", "agent_id": agent_id, "agent_type": "general-purpose"}
        hooks.agent_stopped(hook_input if answer is None else {**hook_input, "last_assistant_message": answer})

    def ledger(self) -> list[dict]:
        return store.read_ledger()

    def next_message(self) -> None:
        """Claude's next message comes seconds after this one: waiting rewrites are older."""
        store.mutate_entry("pending", "s1", lambda pending: [
            v.update(at=v["at"] - calls.REFUSED_AFTER - 1) for v in pending.values() if isinstance(v, dict)])

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

    def test_crew_matches_agent_files(self):
        config = json.loads((PLUGIN / "config" / "models.json").read_text(encoding="utf-8"))
        files = {p.stem: frontmatter(p) for p in (PLUGIN / "agents").glob("*.md")}
        self.assertEqual(set(config["crew"]), set(files))
        for name, agent in config["crew"].items():
            self.assertEqual(files[name].get("name"), name)
            self.assertEqual(files[name].get("model"), agent["model"], f"model drift for {name}")
            self.assertIn(agent["model"], config["models"])

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
        for option in manifest["userConfig"].values():
            self.assertNotIn("options", option)  # not accepted by the oldest supported Claude Code

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
        skip = {".git", "fixtures", "__pycache__", "results"}
        for path in ROOT.rglob("*"):
            if path.is_file() and not skip & set(path.parts) and path.suffix != ".pyc":
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


class OptionText(unittest.TestCase):
    def test_main_effort_names_every_case_it_stays_off(self):
        manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        text = manifest["userConfig"]["main_effort"]["description"]
        for case in ("CLAUDE_CODE_EFFORT_LEVEL", "Bedrock", "Vertex", "Foundry", "ANTHROPIC_BASE_URL",
                     "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "Fable 5.1", "Opus 5.5", "Sonnet 5.5", "Haiku 5.5"):
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


class DeliveredMessages(Sandbox):
    """A teammate's report or another session's message reaching the main loop isn't the user's request."""

    def delivered(self, tag: str = "teammate-message") -> str:
        return (f'Another Claude session sent a message:\n<{tag} teammate_id="auditor" color="blue">\n'
                f"{HARD}\n</{tag}>")

    def test_a_delivered_message_raises_no_effort(self):
        session.record("s1", "claude-opus-5-5")
        for tag in ("teammate-message", "cross-session-message"):
            self.assertFalse(hooks.is_user_prompt(self.delivered(tag)), tag)
            self.assertIsNone(hooks.mod_turn({"session_id": "s1", "text": self.delivered(tag)}), tag)
        self.assertEqual(hooks.mod_turn({"session_id": "s1", "text": HARD}), {"level": "complex", "effort": "high"})
        self.assertTrue(hooks.is_user_prompt("Another Claude session sent a message: is that how they phrase it?"))

    def test_a_delivered_message_leaves_the_users_request_alone(self):
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": LOOKUP})
        self.assertIsNone(hooks.user_prompt({"session_id": "s1", "prompt": self.delivered()}))
        self.assertEqual(store.load_entry("prompts", "s1")["level"], "lookup")

    def test_a_machine_origin_from_the_mod_raises_no_effort(self):
        session.record("s1", "claude-opus-5-5")
        turn = lambda origin: hooks.mod_turn({"session_id": "s1", "text": HARD, "origin": origin})  # noqa: E731
        for origin in ("task-notification", "peer", "peer-send-message"):
            self.assertIsNone(turn(origin), origin)
        for origin in ("composer", "bridge", "sdk", "unclassified", "coordinator", "a-kind-from-a-later-build"):
            self.assertEqual(turn(origin), {"level": "complex", "effort": "high"}, origin)


class Store(Sandbox):
    def test_keyed_files_drop_the_least_recently_written(self):
        with mock.patch.object(store, "KEEP_ENTRIES", 3):
            for i, key in enumerate(("zz", "aa", "mm", "bb")):
                with mock.patch.object(store.time, "time", return_value=1000.0 + i):
                    store.save_entry("prompts", key, {"n": i})
        self.assertEqual(sorted(store.load_state_file(self.data / "prompts.json")), ["aa", "bb", "mm"])

    def test_ledger_survives_junk_lines(self):
        store.append_ledger({"event": "spawn"})
        with store.ledger_path().open("a") as fh:
            fh.write("not json\n[1]\n")
        self.assertEqual(len(store.read_ledger(7)), 1)

    def test_updates_from_processes_running_at_once_are_all_kept(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                "from modelrouter import store\n"
                "store.LOCK_WAIT_SECONDS = 60\n"  # a slow machine must wait, not skip the lock
                "def add(entry): entry['n'] = entry.get('n', 0) + 1\n"
                "for _ in range(20): store.mutate_entry('prompts', 's1', add)\n")
        workers = [subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS)], env={**os.environ})
                   for _ in range(6)]
        for worker in workers:
            self.assertEqual(worker.wait(timeout=120), 0)
        self.assertEqual(store.load_entry("prompts", "s1")["n"], 120)
        self.assertEqual(list(self.data.glob("*.lock")) + list(self.data.glob("*.tmp")), [])

    def test_a_stale_lock_is_cleared_and_a_held_one_never_hangs(self):
        path = self.data / "state.json"
        self.data.mkdir(exist_ok=True)
        lock = self.data / "state.json.lock"
        lock.write_text("")
        os.utime(lock, (time.time() - 60, time.time() - 60))
        store.update_dict(path, lambda data: data.update(a=1))
        self.assertFalse(lock.exists())
        lock.write_text("")  # held by someone else, and fresh
        started = time.monotonic()
        with mock.patch.object(store, "LOCK_WAIT_SECONDS", 0.05):
            store.update_dict(path, lambda data: data.update(b=2))
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(store.load_state_file(path), {"a": 1, "b": 2})
        self.assertTrue(lock.exists())  # not ours to remove

    def test_state_files_are_written_compact(self):
        store.save_entry("pending", "s1", {"a": {"model": "haiku"}})
        self.assertNotIn("\n", (self.data / "pending.json").read_text(encoding="utf-8"))

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
        self.assertIn("racer", [row.get("event") for row in store.read_ledger()])

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
        self.assertEqual(len(store.read_ledger()), 51)


class Registry(Sandbox):
    def setUp(self):
        super().setUp()
        self.config = cfg.load_config()

    def test_family_maps_aliases_and_ids(self):
        f = lambda m: registry.family(self.config, m)  # noqa: E731
        self.assertEqual(f("claude-opus-5-5"), "opus")
        self.assertEqual(f("sonnet[1m]"), "sonnet")
        self.assertEqual(f("best"), "opus")
        self.assertEqual(f("us.anthropic.claude-haiku-5-5-v1:0"), "haiku")
        for unknown in ("inherit", None, "gpt-9", 5, ["opus"]):
            self.assertIsNone(f(unknown))

    def test_select_by_priority(self):
        usable = registry.available(self.config)
        pick = lambda needs, priority, names=usable: registry.select(self.config, needs, priority, names)  # noqa: E731
        self.assertEqual([pick(n, "balanced") for n in (1, 2, 3, 4)], ["haiku", "sonnet", "opus", "fable"])
        self.assertEqual([pick(n, "quality") for n in (1, 2, 3, 4)], ["sonnet", "opus", "fable", "fable"])
        self.assertEqual([pick(n, "fast") for n in (1, 2, 3, 4)], ["haiku", "haiku", "sonnet", "opus"])
        self.assertEqual(pick(4, "balanced", ["haiku", "sonnet"]), "sonnet")
        self.assertIsNone(pick(2, "balanced", []))

    def test_select_uses_cost_and_speed_not_just_rank(self):
        config = dict(self.config)
        config["models"] = {**self.config["models"], "swift": {"strength": 3, "speed": 5, "cost": 2.5}}
        usable = registry.by_strength(config)
        self.assertEqual(registry.select(config, 3, "balanced", usable), "swift")  # cheaper than opus
        self.assertEqual(registry.select(config, 2, "fast", usable), "swift")  # the quickest that qualifies

    def test_cap_limits_available_models(self):
        self.assertEqual(registry.available(self.config, cap="sonnet"), ["haiku", "sonnet"])
        self.assertEqual(registry.ceiling(self.config, cap="sonnet"), "sonnet")
        self.assertEqual(registry.ceiling(self.config), "fable")

    def test_clamp_never_raises_above_what_was_asked(self):
        self.assertEqual(registry.clamp(self.config, "fable", ["haiku", "sonnet"]), "sonnet")
        self.assertEqual(registry.clamp(self.config, "opus", ["haiku", "opus"]), "opus")
        # Nothing at or below it is usable: the weakest usable is the least bad.
        self.assertEqual(registry.clamp(self.config, "haiku", ["sonnet", "opus"]), "sonnet")

    def test_settings_allowlist_is_discovered(self):
        (self.home / "settings.json").write_text(json.dumps({"availableModels": ["sonnet", "claude-haiku-5-5"]}))
        self.assertEqual(registry.available(self.config, self.project), ["haiku", "sonnet"])
        self.managed.write_text(json.dumps({"deniedModels": ["haiku"]}))
        self.assertEqual(registry.available(self.config, self.project), ["sonnet"])

    def test_allowlists_from_user_and_project_settings_are_concatenated(self):
        (self.home / "settings.json").write_text(json.dumps({"availableModels": ["opus"]}))
        (self.project / ".claude").mkdir()
        (self.project / ".claude" / "settings.json").write_text(json.dumps({"availableModels": ["claude-sonnet-5"]}))
        self.assertEqual(registry.available(self.config, self.project), ["sonnet", "opus"])

    def test_a_managed_allowlist_applies_alone(self):
        (self.home / "settings.json").write_text(json.dumps({"availableModels": ["opus"]}))
        self.managed.write_text(json.dumps({"availableModels": ["sonnet"]}))
        self.assertEqual(registry.available(self.config, self.project), ["sonnet"])

    def test_managed_drop_in_files_merge_with_the_managed_file(self):
        (self.home / "settings.json").write_text(json.dumps({"availableModels": ["haiku"]}))
        drop_ins = self.home / "managed-settings.d"
        drop_ins.mkdir()
        (drop_ins / "20-models.json").write_text(json.dumps({"availableModels": ["sonnet", "opus"],
                                                              "deniedModels": ["opus"]}))
        (drop_ins / ".30-hidden.json").write_text(json.dumps({"availableModels": ["fable"]}))
        (drop_ins / "40-notes.txt").write_text(json.dumps({"availableModels": ["fable"]}))
        self.assertEqual(registry.permitted(self.config, self.project), {"sonnet"})
        self.managed.write_text(json.dumps({"availableModels": ["fable"]}))
        (drop_ins / "10-more.json").write_text(json.dumps({"availableModels": ["sonnet"]}))
        self.assertEqual(registry.permitted(self.config, self.project), {"sonnet", "fable"})
        for path in (self.managed, drop_ins / "10-more.json", drop_ins / "20-models.json"):
            path.unlink()
        (drop_ins / "50-empty.json").write_text(json.dumps({"availableModels": []}))
        self.assertEqual(registry.permitted(self.config, self.project), set())  # an empty drop-in list still applies alone

    def test_an_empty_managed_allowlist_permits_nothing(self):
        self.managed.write_text(json.dumps({"availableModels": []}))
        self.assertEqual(registry.permitted(self.config, self.project), set())

    def test_denied_models_block_a_family_only_when_they_name_it(self):
        self.managed.write_text(json.dumps({"deniedModels": ["Opus"]}))
        self.assertEqual(registry.available(self.config, self.project), ["haiku", "sonnet", "fable"])
        # A denied version leaves the alias on the newest permitted one; best, opusplan and default are ignored.
        self.managed.write_text(json.dumps({"deniedModels": ["claude-opus-5-5", "sonnet[1m]", "best", "opusplan", "default"]}))
        self.assertEqual(len(registry.available(self.config, self.project)), 4)

    def test_settings_entries_that_are_not_strings_match_nothing(self):
        self.managed.write_text(json.dumps({"availableModels": ["sonnet", {"id": "claude-opus-5-5"}],
                                            "deniedModels": [{"id": "sonnet"}]}))
        self.assertEqual(registry.permitted(self.config, self.project), {"sonnet"})

    def test_denied_models_outside_managed_settings_are_ignored(self):
        (self.home / "settings.json").write_text(json.dumps({"deniedModels": ["opus"]}))
        (self.project / ".claude").mkdir()
        (self.project / ".claude" / "settings.json").write_text(json.dumps({"deniedModels": ["haiku"]}))
        self.assertEqual(registry.available(self.config, self.project), ["haiku", "sonnet", "opus", "fable"])

    def test_an_allowlist_that_excludes_everything_is_ignored(self):
        (self.home / "settings.json").write_text(json.dumps({"availableModels": ["some-other-model"]}))
        self.assertEqual(len(registry.available(self.config, self.project)), 4)

    def test_two_model_errors_route_around_a_model_and_outages_grow(self):
        self.assertEqual(registry.record_failure("fable", "404 model not found"), 1)
        self.assertEqual(registry.ceiling(self.config), "fable")
        registry.record_failure("fable", "404 model not found")
        self.assertEqual(registry.ceiling(self.config), "opus")
        first = registry.health()["fable"]["unavailable_until"] - time.time()
        self.assertTrue(0.9 * 86400 < first < 1.1 * 86400)
        registry.update_health("fable", unavailable_until=time.time() - 1)  # the day has passed
        self.assertEqual(registry.ceiling(self.config), "fable")
        registry.record_failure("fable", "404 model not found")  # one more failure is now enough
        second = registry.health()["fable"]["unavailable_until"] - time.time()
        self.assertTrue(2.9 * 86400 < second < 3.1 * 86400)

    def test_a_working_result_clears_trouble_and_records_the_real_model(self):
        registry.record_failure("opus", "model not found")
        registry.record_failure("opus", "model not found")
        registry.record_result("opus", "claude-opus-5-5", "opus")
        info = registry.health()["opus"]
        self.assertTrue(registry.healthy("opus"))
        self.assertEqual((info["last_model"], info["fail"], info["outages"]), ("claude-opus-5-5", 0, 0))

    def test_two_substitutions_route_around_a_model(self):
        registry.record_result("fable", "claude-opus-5-5", "opus")
        self.assertTrue(registry.healthy("fable"))
        registry.record_result("fable", "claude-opus-5-5", "opus")
        self.assertFalse(registry.healthy("fable"))

    def test_project_can_add_a_model_but_not_rerank_or_slip_under_the_cap(self):
        self.override({"models": {"saga": {"strength": 0.5, "cost": 9}, "fable": {"strength": 0}}})
        config = cfg.load_config()
        self.assertEqual(config["models"]["fable"]["strength"], 4)
        self.assertIn("saga", registry.available(config))
        self.assertEqual(registry.available(config, cap="sonnet"), ["haiku", "sonnet"])

    def test_the_users_own_file_is_trusted(self):
        self.override({"models": {"saga": {"strength": 1.5, "speed": 3, "cost": 1.5}, "opus": {"strength": 3.5}}},
                      user=True)
        self.override({"models": {"saga": {"strength": 9}}})  # a project can't re-rank the user's model
        config = cfg.load_config()
        self.assertEqual((config["models"]["saga"]["strength"], config["models"]["opus"]["strength"]), (1.5, 3.5))
        self.assertEqual(registry.available(config, cap="sonnet"), ["haiku", "saga", "sonnet"])

    def test_only_the_users_own_file_can_add_prices(self):
        self.override({"prices": {"Claude-Saga-1": {"input": 2, "output": 10, "cache_read": "x"},
                                  "claude-opus-5-5": {"output": 30}, "bad": 5}}, user=True)
        self.override({"prices": {"claude-haiku-5-5": {"input": 999}, "claude-proj-1": {"input": 1}}})
        config = cfg.load_config()
        self.assertEqual(registry.price(config, "claude-saga-1"), {"input": 2, "output": 10})
        self.assertEqual(registry.price(config, "claude-opus-5-5")["output"], 30)
        self.assertEqual(registry.price(config, "claude-opus-5-5")["input"],  # merged key by key
                         cfg.base_config()["prices"]["claude-opus-5-5"]["input"])
        self.assertNotIn("bad", config["prices"])
        self.assertEqual(registry.price(config, "claude-haiku-5-5")["input"],
                         cfg.base_config()["prices"]["claude-haiku-5-5"]["input"])
        self.assertIsNone(registry.price(config, "claude-proj-1"))

    def test_malformed_override_files_are_ignored(self):
        self.override({"models": "nope", "levels": {"lookup": {"needs": "x", "effort": "ludicrous"},
                                                    "complex": {"effort": "max"}, "bogus": {"needs": 9}},
                       "agents": {"a": {"model": 5, "level": ["x"], "route": "no"}, "b": [1]},
                       "routing_mode": 5})
        config = cfg.load_config()
        self.assertEqual(config["levels"]["lookup"], {"needs": 1, "effort": "low"})
        self.assertEqual(config["levels"]["complex"]["effort"], "max")
        self.assertNotIn("bogus", config["levels"])
        self.assertEqual(config["agents"], {"a": {}})
        self.assertEqual(settings.resolve(config).mode, "auto")
        (self.project / ".claude" / "model-router.json").write_text("{not json")
        self.assertEqual(len(cfg.load_config()["models"]), 4)

    def test_a_project_model_name_cannot_capture_a_trusted_models_ids(self):
        self.override({"models": {"claude": {"strength": 0.5}, "opus-5": {"strength": 9}, "saga": {"strength": 2}}})
        config = cfg.load_config()
        self.assertEqual(registry.family(config, "claude-opus-5-5"), "opus")
        self.assertEqual([m for m in ("claude", "opus-5", "saga") if m in config["models"]], ["claude", "saga"])


class Options(Sandbox):
    def mode(self) -> str:
        return settings.resolve(cfg.load_config()).mode

    def test_defaults(self):
        self.assertEqual(settings.resolve(cfg.load_config()), settings.Settings())

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
        s = settings.resolve(cfg.load_config())
        self.assertEqual((s.subagent_model, s.force, s.cloud, s.cap, s.main_effort, s.verify_gate),
                         ("opus", True, "vertex", None, False, True))
        self.assertEqual((s.api_key, s.base_url), ("sk-test", True))
        self.assertNotIn("sk-test", repr(s))

    def test_skill_commands_read_options_recorded_at_session_start(self):
        values = {"max_tier": "haiku", "priority": "quality", "agent_pins": "override", "routing_mode": "advise",
                  "main_effort": "false", "update_check": "off", "verify_gate": "0"}
        self.options(**values)
        hooks.session_start({"session_id": "s1"})
        for name in values:
            del os.environ[f"CLAUDE_PLUGIN_OPTION_{name.upper()}"]
        config = cfg.load_config()
        self.assertEqual(settings.resolve(config), settings.Settings())
        self.assertEqual(settings.resolve(config, recorded=True), settings.Settings(
            mode="advise", routing_mode="advise", priority="quality", cap="haiku", pins="override",
            main_effort=False, update_check=False, verify_gate=False))
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
        cleaned = cfg._clean(base)
        for name in base["models"]:
            self.assertEqual(set(cleaned["models"][name]), {"strength", "speed", "cost"}, name)
        for name, spec in base["levels"].items():
            self.assertEqual(cleaned["levels"][name], spec, name)
        self.assertEqual(cfg._prices(base["prices"]).keys(), base["prices"].keys())

    def test_an_override_without_a_schema_still_loads(self):
        self.override({"levels": {"complex": {"effort": "xhigh"}}}, user=True)
        self.override({"agents": {"my-agent": {"level": "lookup"}}})
        config = cfg.load_config()
        self.assertEqual((config["levels"]["complex"]["effort"], config["agents"]["my-agent"]["level"]),
                         ("xhigh", "lookup"))
        self.assertEqual(cfg.newer_overrides(), [])

    def test_an_override_from_a_newer_release_is_read_as_far_as_it_is_understood_and_named(self):
        self.override({"schema": cfg.CONFIG_SCHEMA + 1, "routing_mode": "off",
                       "levels": {"complex": {"effort": "xhigh"}}, "a_future_key": {"x": 1}}, user=True)
        self.override({"schema": cfg.CONFIG_SCHEMA + 1, "agents": {"my-agent": {"level": "lookup"}}})
        config = cfg.load_config()
        self.assertEqual((config["routing_mode"], config["levels"]["complex"]["effort"]), ("off", "xhigh"))
        self.assertEqual(config["agents"]["my-agent"]["level"], "lookup")
        newer = [str(self.home / "model-router.json"), str(self.project / ".claude" / "model-router.json")]
        self.assertEqual([str(p) for p in cfg.newer_overrides()], newer)
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
        self.assertEqual(agents.find("model-router:architect", self.project)["model"], "opus")
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
        self.assertEqual(self.level("investigate why the checkout test is flaky"), "standard")
        self.assertEqual(self.level(HARD), "complex")
        self.assertEqual(self.level("review the auth module"), "complex")
        self.assertEqual(self.level("investigate why auth is flaky and redesign it across every service"), "hardest")

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
        self.assertLess(time.monotonic() - started, 0.5)

    def test_a_long_hyphen_or_apostrophe_run_after_a_limit_stays_fast(self):
        for run in ("a-" * 32_000, "a'" * 32_000):  # 64 KB, such as a pasted token or a kebab-case list
            started = time.monotonic()
            self.level("do not " + run)
            self.assertLess(time.monotonic() - started, 0.5, run[:4])

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
        for brief in ("Do not modify tests, fix the parser, update the docs, and run the suite.",
                      "Do not touch the API, fix it or roll it back."):
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
        self.assertLess(time.monotonic() - started, 0.5)

    def test_a_huge_whitespace_run_after_a_limit_stays_fast(self):
        started = time.monotonic()
        self.level("do not" + " " * 64_000 + "x")
        self.assertLess(time.monotonic() - started, 0.5)

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
    routing regression fails CI instead of waiting for a paid eval run."""

    def test_each_eval_case_routes_as_expected(self):
        cases = sorted((PLUGIN / "evals").glob("*/prompt.md"))
        self.assertGreaterEqual(len(cases), 2)
        config = cfg.load_config()
        options = settings.resolve(config)
        for path in cases:
            name = path.parent.name
            want = re.fullmatch(r"['\"]?level (\w+), model (\w+), effort (\w+)['\"]?",
                                frontmatter(path).get("expected_outcome", ""))
            self.assertTrue(want, f"{name}: expected_outcome must read 'level L, model M, effort E'")
            brief = path.read_text(encoding="utf-8").split("\n---\n", 1)[1].strip()
            got = report.explain(config, options, brief)
            self.assertIn(f"Level: **{want[1]}**", got, name)
            self.assertIn(f"Model: **{want[2]}**, effort: {want[3]}.", got, name)


class Router(Sandbox):
    def setUp(self):
        super().setUp()
        self.session_on("claude-opus-5-5")

    def test_agents_without_a_model_get_the_one_their_subtask_needs(self):
        self.agent("helper")
        for agent in ("helper", "general-purpose", None, "Explore"):
            d = self.decide(agent, LOOKUP)
            self.assertEqual((d["action"], d["model"], d["effort"], d["source"]),
                             ("rewrite", "haiku", "low", "router"), agent)
        d = self.decide("helper", BUILD)
        self.assertEqual((d["model"], d["effort"]), ("sonnet", "high"))
        d = self.decide("helper", HARD)
        self.assertEqual((d["model"], d["effort"]), ("opus", "high"))

    def test_a_rewrite_changes_two_fields_and_approves_nothing(self):
        out = self.pre("general-purpose", LOOKUP, description="d", run_in_background=True)
        self.assertEqual(out["updatedInput"], {"prompt": LOOKUP, "subagent_type": "general-purpose",
                                               "description": "d", "run_in_background": True,
                                               "model": "haiku", "effort": "low"})
        self.assertNotIn("permissionDecision", out)
        self.assertIn("haiku at low effort", out["additionalContext"])
        self.assertEqual(self.ledger()[-1]["via"], "fallback")

    def test_no_output_when_the_model_would_be_the_same(self):
        self.session_on("claude-sonnet-5-5")
        self.override({"levels": {"standard": {"effort": None}}})
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
        config = cfg.load_config()
        d = router.decide(spawn.facts(hook_input, config, settings.Settings()), config, settings.Settings())
        self.assertEqual((d["action"], d["model"], d["was"]), ("rewrite", "sonnet", None))

    def test_decide_needs_only_its_facts(self):
        config = cfg.load_config()
        usable = registry.available(config)

        def decide(**facts) -> dict:
            facts = {"agent": "helper", "tool_input": {"prompt": LOOKUP}, "usable": usable, **facts}
            return router.decide(router.Facts(**facts), config, settings.Settings())

        self.assertEqual(decide()["action"], "keep")  # no definition found: left alone
        d = decide(definition={"source": "project", "model": None}, parent="opus")
        self.assertEqual((d["action"], d["model"], d["effort"], d["was"]), ("rewrite", "haiku", "low", "opus"))
        d = decide(definition={"source": "project", "model": "opus"})
        self.assertEqual((d["model"], d["source"], d["effort"]), ("opus", "pin", "low"))
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

    def test_a_model_named_on_the_call_is_lowered_to_the_cap(self):
        self.options(max_tier="sonnet")
        d = self.decide("general-purpose", LOOKUP, model="fable")
        self.assertEqual((d["action"], d["model"], d["effort"]), ("rewrite", "sonnet", None))

    def test_agents_that_name_a_model_keep_it_within_the_cap(self):
        self.agent("pinned", "opus")
        d = self.decide("pinned", LOOKUP)  # the pin holds the model; effort still follows the subtask
        self.assertEqual((d["action"], d["model"], d["source"], d["effort"]), ("rewrite", "opus", "pin", "low"))
        d = self.decide("model-router:deep-solver", LOOKUP)
        self.assertEqual((d["model"], d["effort"]), ("fable", None))  # its own effort: xhigh
        self.options(max_tier="sonnet")
        d = self.decide("pinned", LOOKUP)
        self.assertEqual((d["action"], d["model"], d["source"], d["effort"]), ("rewrite", "sonnet", "pin", "low"))
        self.assertEqual(self.decide("model-router:deep-solver", HARD)["model"], "sonnet")

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
        self.assertEqual(out["updatedInput"], {"prompt": LOOKUP, "subagent_type": "general-purpose", "effort": "low"})
        self.assertIn("effort to low", out["additionalContext"])
        self.assertEqual(self.decide("general-purpose", HARD)["effort"], "high")
        self.options(priority="fast")
        self.assertIsNone(self.decide("general-purpose", HARD)["effort"])
        self.assertEqual(self.decide("general-purpose", LOOKUP)["effort"], "low")
        self.options(CLAUDE_CODE_SUBAGENT_EFFORT="medium")
        self.assertEqual(self.decide("general-purpose", LOOKUP)["action"], "keep")

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
        self.assertEqual(self.decide("general-purpose", LOOKUP)["effort"], "low")

    def test_an_unavailable_model_is_routed_around(self):
        registry.update_health("opus", unavailable_until=time.time() + 999)
        self.session_on("claude-sonnet-5-5")
        self.assertEqual(self.decide("general-purpose", HARD)["model"], "fable")  # the next one strong enough
        registry.update_health("fable", unavailable_until=time.time() + 999)
        self.agent("pinned", "opus")
        self.assertEqual(self.decide("pinned", LOOKUP)["model"], "sonnet")  # a named model is only ever lowered

    def test_rules_in_override_files(self):
        self.override({"agents": {"general-purpose": {"model": "sonnet"}, "Explore": {"route": False},
                                  "Plan": {"level": "frontier"}}})
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
        config = cfg.load_config()
        d = router.decide(spawn.facts(hook_input, config, settings.Settings()), config, settings.Settings())
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

    def test_after_a_result_the_same_call_is_routed_again(self):
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", model="haiku", effort="low")
        self.assertIsNotNone(self.pre("general-purpose", LOOKUP))

    def test_a_call_redone_after_its_result_runs_one_level_up(self):
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", model="haiku", effort="low")
        again = self.decide("general-purpose", LOOKUP)
        self.assertEqual((again["level"], again["model"], again["effort"]), ("standard", "sonnet", "high"))
        self.assertIn("one level above the lookup run", again["reason"])
        self.pre("general-purpose", LOOKUP)
        self.assertEqual(learn.stats()["lookup"]["votes"], 1)

    def test_redo_counts_only_within_the_same_request(self):
        hooks.user_prompt({"session_id": "s1", "prompt": "check the repo", "prompt_id": "p1"})
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", model="haiku", effort="low")
        hooks.user_prompt({"session_id": "s1", "prompt": "check the repo again", "prompt_id": "p2"})
        self.assertEqual(self.decide("general-purpose", LOOKUP)["level"], "lookup")  # a loop, not a redo
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", model="haiku", effort="low")
        self.assertEqual(self.decide("general-purpose", LOOKUP)["level"], "standard")

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
        self.pre("general-purpose", LOOKUP)
        self.assertIsNone(self.pre("general-purpose", LOOKUP, model="haiku", effort="low"))
        self.assertEqual(self.ledger()[-1]["source"], "caller")
        self.assertEqual(registry.health(), {})
        self.assertEqual(learn.stats()["lookup"]["votes"], 0)

    def test_a_model_that_never_runs_is_routed_around_after_two_refused_rewrites(self):
        for prompt in ("list the files under a", "list the files under b"):
            self.pre("general-purpose", prompt)
            self.next_message()
            self.pre("general-purpose", prompt)
        self.assertFalse(registry.healthy("haiku"))
        self.assertEqual(self.decide("general-purpose", "list the files under c")["model"], "sonnet")
        self.assertIn("came back without having run", self.out("models"))

    def test_a_model_known_to_work_is_not_blamed(self):
        registry.record_result("haiku", "claude-haiku-5-5", "haiku")
        for prompt in ("list the files under a", "list the files under b"):
            self.pre("general-purpose", prompt)
            self.next_message()
            self.pre("general-purpose", prompt)
        self.assertTrue(registry.healthy("haiku"))

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
        self.assertEqual(registry.health(), {})

    def test_a_redo_at_the_top_of_the_ladder_gets_more_effort(self):
        hard = "research a novel fix for the auth migration across every service, root cause unknown"
        self.pre("general-purpose", hard)
        self.done("general-purpose", hard, "claude-fable-5-2", model="fable", effort="high")
        again = self.decide("general-purpose", hard)
        self.assertEqual((again["action"], again["level"], again["model"], again["effort"], again["escalated_from"]),
                         ("rewrite", "frontier", "fable", "xhigh", None))


class Calls(Sandbox):
    def setUp(self):
        super().setUp()
        self.config = cfg.load_config()

    def route(self, tool_input: dict, model: str = "haiku", **facts) -> str | None:
        facts = {"level": "lookup", "effort": "low", "source": "router", **facts}
        return calls.routed(self.config, "s1", calls.identify(tool_input)[1], model, **facts)

    def test_a_call_is_known_by_its_agent_and_prompt(self):
        agent, key = calls.identify({"prompt": "x"})
        self.assertEqual(agent, "general-purpose")
        self.assertEqual(key, calls.identify({"subagent_type": "general-purpose", "prompt": "x", "model": "opus"})[1])
        self.assertNotEqual(key, calls.identify({"subagent_type": "Explore", "prompt": "x"})[1])
        self.assertEqual(calls.identify({"subagent_type": ["a"], "prompt": "x"})[0], "general-purpose")

    def test_a_rewrite_waits_for_its_result_and_only_a_result_finishes_the_run(self):
        call = {"prompt": LOOKUP}
        key = calls.identify(call)[1]
        self.route(call, awaiting=True)
        self.assertIsNone(calls.refused_rewrite("s1", key))  # a copy issued alongside it
        self.next_message()
        self.assertEqual(calls.refused_rewrite("s1", key)["model"], "haiku")
        calls.settled("s1", call)  # an error: the rewrite was accepted, but nothing finished
        self.assertIsNone(calls.refused_rewrite("s1", key))
        self.assertIsNone(calls.finished_run("s1", key))
        calls.settled("s1", call, finished=True)
        self.assertEqual(calls.finished_run("s1", key)["level"], "lookup")

    def test_only_runs_at_their_level_are_redone(self):
        for source in ("caller", None):
            call = {"prompt": f"list {source}"}
            self.route(call, source=source)
            calls.settled("s1", call, finished=True)
            self.assertIsNone(calls.finished_run("s1", calls.identify(call)[1]), source)

    def test_a_finished_run_counts_in_its_own_request_or_window(self):
        call = {"prompt": LOOKUP}
        key = calls.identify(call)[1]
        self.route(call)
        calls.settled("s1", call, finished=True)
        self.assertIsNotNone(calls.finished_run("s1", key))
        with mock.patch.object(calls.time, "time", return_value=time.time() + calls.REDO_WINDOW + 1):
            self.assertIsNone(calls.finished_run("s1", key))
        hooks.user_prompt({"session_id": "s1", "prompt": "check the repo", "prompt_id": "p1"})
        self.assertIsNone(calls.finished_run("s1", key))

    def test_the_router_pick_before_and_the_strongest_spawn_are_per_request(self):
        call = {"prompt": LOOKUP}
        self.assertIsNone(self.route(call))
        self.assertEqual(self.route(call, "opus", source="caller"), "haiku")
        self.assertEqual(self.route({"prompt": "x"}, "fable", source="pin"), None)
        self.assertEqual(calls.strongest("s1"), "fable")
        hooks.user_prompt({"session_id": "s1", "prompt": "something else"})
        self.assertIsNone(calls.strongest("s1"))
        self.assertIsNone(self.route(call, "opus", source="caller"))

    def test_records_keep_the_most_recent_calls(self):
        for i in range(calls.KEPT + 2):
            self.route({"prompt": f"list {i}"}, awaiting=True)
        self.next_message()
        self.assertIsNone(calls.refused_rewrite("s1", calls.identify({"prompt": "list 0"})[1]))
        self.assertIsNone(calls.refused_rewrite("s1", calls.identify({"prompt": "list 1"})[1]))
        self.assertIsNotNone(calls.refused_rewrite("s1", calls.identify({"prompt": "list 2"})[1]))


class Mod(Sandbox):
    """The calls hooks/router.ts makes for agent.spawn, turn.start and turn.complete."""

    def spawn(self, agent: str, prompt: str, parent: str = "claude-opus-5-5", **extra) -> dict | None:
        tool_input = {"subagent_type": agent, "prompt": prompt, "description": "d", **extra}
        return hooks.mod_spawn({"session_id": "s1", "cwd": str(self.project), "parent_model": parent,
                                "kind": "subagent", "tool_input": tool_input})

    def test_spawns_are_routed_from_the_parents_exact_model(self):
        self.assertEqual(self.spawn("general-purpose", LOOKUP), {"model": "haiku", "effort": "low",
                                                                 "reason": "lookup subtask"})
        self.assertEqual(self.spawn("general-purpose", HARD)["model"], "opus")
        self.assertIsNone(self.spawn("general-purpose", BUILD, model="sonnet", effort="high"))
        self.assertEqual(self.ledger()[-1]["via"], "mod")

    def test_the_settings_hook_stands_down_once_the_mod_is_running(self):
        self.session_on("claude-opus-5-5")
        self.assertIsNotNone(self.pre("general-purpose", LOOKUP))
        hooks.mod_start({"session_id": "s1"})
        count = len(self.ledger())
        self.assertIsNone(self.pre("general-purpose", "list the tests"))
        self.assertEqual(len(self.ledger()), count)

    def test_a_spawn_redone_after_its_result_runs_one_level_up(self):
        self.spawn("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5")
        self.assertEqual(self.spawn("general-purpose", LOOKUP)["model"], "sonnet")

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

    def test_turns_and_spawns_keep_the_mod_marked_after_a_clear(self):
        self.assertFalse(hooks.mod_active("s2"))
        hooks.mod_turn({"session_id": "s2", "text": LOOKUP})
        self.assertTrue(hooks.mod_active("s2"))
        hooks.mod_spawn({"session_id": "s3", "parent_model": "claude-opus-5-5", "tool_input": {"prompt": LOOKUP}})
        self.assertTrue(hooks.mod_active("s3"))

    def test_a_pinned_agent_redone_gets_more_effort(self):
        self.agent("pinned", "opus")
        self.assertEqual(self.spawn("pinned", LOOKUP)["effort"], "low")
        self.done("pinned", LOOKUP, "claude-opus-5-5")
        pick = self.spawn("pinned", LOOKUP)
        self.assertEqual((pick["model"], pick["effort"]), ("opus", "high"))

    def test_workflow_stages_and_teammate_roles_without_a_file_are_routed(self):
        for kind, agent in (("workflow", "workflow-subagent"), ("teammate", "helper-two")):
            pick = hooks.mod_spawn({"session_id": "s1", "cwd": str(self.project), "parent_model": "claude-opus-5-5",
                                    "kind": kind, "tool_input": {"subagent_type": agent, "prompt": LOOKUP}})
            self.assertEqual((pick["model"], pick["effort"]), ("haiku", "low"), kind)
        self.assertIsNone(self.spawn("no-such-agent", LOOKUP))

    def test_forced_subagent_model_gets_effort_only(self):
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="opus", CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1")
        pick = self.spawn("general-purpose", LOOKUP)
        self.assertEqual((pick["model"], pick["effort"]), (None, "low"))

    def test_turn_effort_stays_off_without_a_first_party_credential(self):
        session.record("s1", "claude-opus-5-5")
        turn = lambda **extra: hooks.mod_turn({"session_id": "s1", "text": HARD, **extra})  # noqa: E731
        self.assertIsNone(turn(first_party=False))  # a gateway: an effort change re-reads the context
        self.assertEqual(turn(first_party=True), {"level": "complex", "effort": "high"})
        self.assertEqual(turn(), {"level": "complex", "effort": "high"})  # not known: raised as before

    def test_turn_effort_rises_for_hard_requests_only(self):
        session.record("s1", "claude-opus-5-5")
        turn = lambda text: hooks.mod_turn({"session_id": "s1", "text": text})  # noqa: E731
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

    def test_turn_effort_is_left_alone_where_an_effort_change_drops_the_cache(self):
        turn = lambda: hooks.mod_turn({"session_id": "s1", "text": HARD})  # noqa: E731
        for model in ("claude-opus-5-5[1m]", "claude-opus-5-5[1M]", "claude-fable-5-1", "claude-fable-5-2",
                      "claude-fable-6", "claude-sonnet-5-5-20260101", "claude-haiku-5-5"):
            session.record("s1", model)
            self.assertEqual(turn(), {"level": "complex", "effort": "high"}, model)
        for name in ("ANTHROPIC_BASE_URL", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"):
            with mock.patch.dict(os.environ, {name: "1"}):
                self.assertIsNone(turn(), name)
        for model in ("claude-opus-5", "claude-sonnet-5", "claude-fable-5", "claude-opus-4-7", "claude-opus-5-6"):
            session.record("s1", model)
            self.assertIsNone(turn(), model)
        for model in ("opus", "opus[1m]", None):  # no version to judge, as after /clear: today's behavior
            store.save_entry("sessions", "s1", {"model": model} if model else {})
            self.assertEqual(turn(), {"level": "complex", "effort": "high"}, model)
            with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://gateway"}):
                self.assertIsNone(turn(), model)
        live = lambda model: hooks.mod_turn({"session_id": "s1", "text": HARD, "model": model})  # noqa: E731
        self.assertEqual(live("claude-opus-5-5"), {"level": "complex", "effort": "high"})
        session.record("s1", "claude-opus-5-5")
        self.assertIsNone(live("claude-opus-5"))  # the mod's live model beats a stale record
        self.assertEqual(live("Opus"), {"level": "complex", "effort": "high"})  # not an ID: the record decides

    def test_usage_is_priced_from_the_registry(self):
        hooks.mod_usage({"session_id": "s1", "agent_id": "a1", "usage": {
            "model": "claude-opus-5-5", "input_tokens": 1_000_000, "output_tokens": 100_000,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}})
        row = self.ledger()[-1]
        self.assertEqual((row["event"], row["cost_usd"]), ("usage", 6.0))
        self.assertIsNone(registry.cost(cfg.load_config(), "claude-unknown-9", {"input_tokens": 1}))
        # Opus 5.5 cache writes: $5/MTok for five minutes, $8/MTok for one hour.
        hooks.mod_usage({"session_id": "s1", "agent_id": "a1", "usage": {
            "model": "claude-opus-5-5", "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 3_000_000, "ephemeral_5m_input_tokens": 1_000_000,
            "ephemeral_1h_input_tokens": 1_000_000}})
        self.assertEqual(self.ledger()[-1]["cost_usd"], 18.0)  # the unnamed million counts as five minutes
        hooks.mod_usage({"session_id": "s1", "agent_id": "a1", "usage": {
            "model": "claude-opus-5-5", "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 1_000_000}})
        self.assertEqual(self.ledger()[-1]["cost_usd"], 5.0)  # no split reported
        config = cfg.load_config()
        self.assertEqual(registry.price(config, "claude-opus-4-7")["input"], 5)  # one entry covers Opus 4.x
        self.assertEqual(registry.price(config, "claude-opus-4-1-20250805"),  # except 4.1, at its own list price
                         {"input": 15, "output": 75, "cache_read": 1.5, "cache_write": 18.75, "cache_write_1h": 30})
        for opus_4 in ("claude-opus-4-0", "claude-opus-4-20250514"):  # Opus 4 lists at the same price as 4.1
            self.assertEqual(registry.price(config, opus_4), registry.price(config, "claude-opus-4-1"), opus_4)
        self.assertEqual(registry.price(config, "claude-haiku-4-5-20251001")["input"], 1)
        self.assertEqual(registry.price(config, "claude-opus-5-5[1m]")["input"], 4)
        self.assertIsNone(registry.price(config, "claude-opus-5-6"))  # a new version is never priced as its neighbour

    def test_results_are_checked_against_the_model_the_mod_routed_to(self):
        self.options(agent_pins="override")
        self.agent("rev", "opus")
        for prompt in ("list the files under a", "list the files under b"):
            self.assertEqual(self.spawn("rev", prompt)["model"], "haiku")
            self.done("rev", prompt, "claude-haiku-5-5")
        self.assertTrue(registry.healthy("opus"))
        self.assertEqual(self.ledger()[-1]["asked"], "haiku")
        hooks.agent_failed({**self.call("rev", "list the files under a"),
                            "error": "API Error: 404 not_found_error: model: claude-haiku-5-5"})
        self.assertEqual((registry.health()["haiku"]["fail"], "opus" in registry.health()), (1, False))

    def test_a_result_reads_no_agent_files(self):
        self.agent("rev", "opus")
        self.spawn("rev", LOOKUP)
        with mock.patch.object(agents, "find", side_effect=AssertionError("agents.find on a result")):
            self.done("rev", LOOKUP, "claude-opus-5-5")
            hooks.agent_failed({**self.call("rev", LOOKUP), "error": "API Error: 404 not_found_error: model"})
        self.assertEqual(registry.health()["opus"]["fail"], 1)


class ModPayloads(Sandbox):
    """tests/fixtures/mod-payloads.json holds what hooks/router.ts sends; tests/mod checks the other side."""

    def setUp(self):
        super().setUp()
        self.payloads = json.loads((FIXTURES / "mod-payloads.json").read_text(encoding="utf-8"))

    def test_each_command_has_a_handler(self):
        self.assertEqual(set(self.payloads), {name for name in hooks.HANDLERS if name.startswith("mod-")})

    def test_start_marks_the_session(self):
        hooks.mod_start(self.payloads["mod-start"])
        self.assertTrue(hooks.mod_active("s1"))

    def test_spawn_reads_every_field(self):
        payload = {**self.payloads["mod-spawn"], "cwd": str(self.project)}
        with mock.patch.object(router, "decide", wraps=router.decide) as decide:
            hooks.mod_spawn(payload)
        found = decide.call_args.args[0]
        self.assertEqual((found.agent, found.parent, found.tool_input), ("general-purpose", "opus", payload["tool_input"]))
        with mock.patch.object(router, "decide", wraps=router.decide) as decide:
            hooks.mod_spawn({**payload, "parent_model": None})
        self.assertIsNone(decide.call_args.args[0].parent)  # parent_agent_id: inside a subagent, so unknown
        row = self.ledger()[-1]
        self.assertEqual((row["event"], row["via"], row["kind"], row["agent"], row["model"]),
                         ("spawn", "mod", "subagent", "general-purpose", "haiku"))
        self.assertTrue(hooks.mod_active("s1"))

    def test_turn_picks_an_effort(self):
        session.record("s1", "claude-opus-5-5")
        self.assertEqual(hooks.mod_turn(self.payloads["mod-turn"]), {"level": "complex", "effort": "high"})

    def test_usage_is_logged_for_its_agent(self):
        hooks.mod_usage(self.payloads["mod-usage"])
        row = self.ledger()[-1]
        self.assertEqual((row["event"], row["agent_id"], row["cost_usd"]), ("usage", "a1", 6.0))


class LongPromptPricing(Sandbox):
    """Haiku 5.5 bills a request whose prompt is over 100,000 tokens at 5x, each request on its own:
    https://platform.claude.com/docs/en/about-claude/pricing.md#long-context-pricing"""

    @staticmethod
    def haiku(input_tokens=0, output_tokens=0, cache_read=0, cache_write=0) -> dict:
        return {"model": "claude-haiku-5-5", "input_tokens": input_tokens, "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read, "cache_creation_input_tokens": cache_write}

    def cost(self, usage: dict, requests: list | None = None):
        payload = {"session_id": "s1", "agent_id": "a1", "usage": usage}
        hooks.mod_usage({**payload, "requests": requests} if requests is not None else payload)
        return self.ledger()[-1]["cost_usd"]

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

    def test_redoing_routed_calls_on_a_stronger_model_raises_the_level(self):
        self.raise_lookup()
        self.assertEqual(learn.raises()["lookup"], 1)
        self.assertEqual(self.decide("general-purpose", LOOKUP)["model"], "sonnet")
        self.assertIn("| lookup | sonnet (raised by feedback) |", self.out("policy"))
        self.assertIn("cleared", self.out("models", "--reset"))
        self.assertEqual(self.decide("general-purpose", LOOKUP)["model"], "haiku")

    def test_the_prompt_note_and_the_stop_gate_follow_a_raised_level(self):
        for _ in range(learn.VOTES_NEEDED):
            learn.decided(cfg.load_config(), source="caller", level="complex", pick="opus", runs_on="fable")
        hooks.user_prompt({"session_id": "s1", "prompt": HARD})  # opus would do, but complex is raised
        entry = store.load_entry("prompts", "s1")
        self.assertEqual((entry["level"], entry["need"], entry["below"]), ("complex", "fable", True))
        self.assertIn("fable-level reasoning", hooks.stop({"session_id": "s1"})["reason"])
        text = hooks.user_prompt({"session_id": "s1", "prompt": HARD})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("about fable-level reasoning, and this conversation runs on opus", text)

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
        config = cfg.load_config()

        def votes(**facts) -> dict:
            learn.reset()
            learn.decided(config, **{"level": "lookup", "pick": "haiku", "runs_on": "haiku", **facts})
            return {k: v for k, v in (learn.stats().get("lookup") or {}).items() if v}

        self.assertEqual(votes(source="router"), {"calls": 1})
        self.assertEqual(votes(source="pin", runs_on="opus"), {})
        self.assertEqual(votes(source="caller", runs_on="sonnet"), {"votes": 1})
        self.assertEqual(votes(source="caller", runs_on="sonnet", earlier="haiku"), {"votes": 2})
        self.assertEqual(votes(source="caller", runs_on="sonnet", earlier="opus"), {"votes": 1})
        self.assertEqual(votes(source=None, runs_on="opus"), {})
        learn.reset()
        learn.decided(config, source="router", level="standard", pick="sonnet", runs_on="sonnet",
                      escalated_from="lookup")
        self.assertEqual((learn.stats()["standard"]["calls"], learn.stats()["lookup"]["votes"]), (1, 1))

    def test_feedback_never_goes_below_what_ships(self):
        for i in range(30):
            self.pre("general-purpose", f"implement change {i}", model="haiku")
        self.assertEqual(self.decide("general-purpose", BUILD)["model"], "sonnet")

    def test_votes_from_calls_issued_at_once_are_all_counted(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                "from modelrouter import config, learn, store\n"
                "store.LOCK_WAIT_SECONDS = 60\n"
                "facts = dict(source='router', level='lookup', pick='haiku', runs_on='haiku')\n"
                "for _ in range(10): learn.decided(config.load_config(), **facts)\n")
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

    def test_session_start_explains_automatic_routing(self):
        self.options(max_tier="opus")
        text = self.text(hooks.session_start({"session_id": "s1", "model": "claude-sonnet-5-5"}))
        self.assertIn("Model Router is on", text)
        self.assertIn("strongest model in use: opus", text)
        self.assertIn("model-router:deep-solver", text)
        self.assertIn("re-issue the same call unchanged", text)
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", text)
        self.assertEqual(session.model("s1"), "claude-sonnet-5-5")
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="opus", CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1")
        text = self.text(hooks.session_start({"session_id": "s1"}))
        self.assertIn("pins every subagent to opus, so Model Router sets only their effort", text)

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
        self.assertIsNone(hooks.user_prompt({"session_id": "s1", "prompt": HARD}))
        self.session_on("claude-haiku-5-5")
        text = self.text(hooks.user_prompt({"session_id": "s1", "prompt": HARD}))
        self.assertIn("model-router:architect", text)
        self.assertTrue(store.load_entry("prompts", "s1")["below"])

    def test_machine_written_turns_dont_reset_the_request(self):
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": HARD})
        self.assertIsNone(hooks.user_prompt({"session_id": "s1", "prompt": "<task-notification>done</task-notification>"}))
        self.assertEqual(store.load_entry("prompts", "s1")["level"], "complex")

    def test_a_prompt_that_opens_with_pasted_text_is_the_users_request(self):
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": HARD})
        pasted = ('<pasted_content id="x">\nTraceback (most recent call last):\n  File "billing.py"\n'
                  '</pasted_content id="x">\n' + LOOKUP)
        hooks.user_prompt({"session_id": "s1", "prompt": pasted})
        self.assertEqual(store.load_entry("prompts", "s1")["level"], "standard")  # the pasted traceback counts too

    def test_a_model_the_user_may_be_asking_for_is_left_to_claude(self):
        self.session_on("claude-opus-5-5")
        text = self.text(hooks.user_prompt({"session_id": "s1", "prompt": "use haiku to refactor the auth module"}))
        self.assertIn('model: "haiku"', text)
        self.assertIn("If the user is asking", text)
        self.assertEqual(self.decide("general-purpose", HARD)["model"], "opus")  # nothing is forced
        config = cfg.load_config()
        self.assertEqual(hooks.named_model(config, "Use Opus for the review"), "opus")
        self.assertEqual(hooks.named_model(config, "run this on claude fable please"), "fable")
        for prompt in ("don't use opus for this", "the haiku about autumn", "work with the opus agent"):
            self.assertIsNone(hooks.named_model(config, prompt), prompt)

    def test_advise_mode_gives_the_estimate(self):
        self.options(routing_mode="advise")
        self.assertIn("Routing estimate", self.text(hooks.user_prompt({"session_id": "s1", "prompt": HARD})))

    def test_results_teach_the_registry_which_models_really_run(self):
        self.agent("pinned", "opus")
        for _ in range(2):
            self.done("x", "p", "claude-opus-5-5", model="fable")
        self.assertFalse(registry.healthy("fable"))
        self.pre("pinned", "p")
        self.done("pinned", "p", "claude-opus-5-5")
        self.assertEqual(registry.health()["opus"]["last_model"], "claude-opus-5-5")
        hooks.post_agent({"session_id": "s1", "tool_input": {"subagent_type": "x"}, "tool_response": "text"})
        self.assertEqual(self.ledger()[-1]["event"], "result")

    def test_model_errors_point_to_the_next_model_and_other_errors_dont_count(self):
        failed = {"session_id": "s1", "cwd": str(self.project),
                  "error": "API Error: 404 not_found_error: model: claude-fable-5-2",
                  "tool_input": {"subagent_type": "model-router:deep-solver", "prompt": "x"}}
        self.pre("model-router:deep-solver", "x")
        self.assertIn('model: "opus"', self.text(hooks.agent_failed(failed)))
        self.assertIn("route around", self.text(hooks.agent_failed(failed)))
        self.assertFalse(registry.healthy("fable"))
        self.assertIsNone(hooks.agent_failed({**failed, "error": "Command timed out", "tool_input": {"model": "opus"}}))
        self.assertIsNone(hooks.agent_failed({**failed, "is_interrupt": True, "tool_input": {"model": "opus"}}))
        self.assertTrue(registry.healthy("opus"))

    def test_model_switch_is_recorded(self):
        hooks.model_switch({"session_id": "s1", "to_model": "claude-fable-5-2"})
        self.assertEqual(session.model("s1"), "claude-fable-5-2")

    def test_forced_runs_are_not_substitutions(self):
        self.options(CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1", CLAUDE_CODE_SUBAGENT_MODEL="sonnet")
        for _ in range(2):
            self.done("general-purpose", LOOKUP, "claude-sonnet-5-5", model="opus")
        self.assertTrue(registry.healthy("opus"))


class Gates(Sandbox):
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
        for command in ("pytest -q", "npm test", "pnpm run lint", "cargo test", "go vet ./...", "tsc --noEmit"):
            self.assertTrue(gates.CHECK_COMMAND.search(command), command)
        for command in ("ls -la", "git status", "cat test.txt"):
            self.assertFalse(gates.CHECK_COMMAND.search(command), command)

    def test_hard_request_on_a_weak_session_needs_a_strong_agent(self):
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": HARD})
        self.assertIn("model-router:architect", hooks.stop({"session_id": "s1"})["reason"])
        self.assertIsNone(hooks.stop({"session_id": "s1"}))

    def test_a_strong_enough_agent_satisfies_escalation(self):
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": HARD})
        self.pre("general-purpose", HARD)  # routed to opus
        self.assertIsNone(hooks.stop({"session_id": "s1"}))

    def test_agents_started_together_cant_hide_the_strong_one(self):
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": HARD})
        self.pre("general-purpose", HARD)
        self.pre("general-purpose", LOOKUP)
        self.assertEqual(calls.strongest("s1"), "opus")

    def test_no_escalation_for_easy_tasks_or_strong_sessions(self):
        self.session_on("claude-haiku-5-5")
        hooks.user_prompt({"session_id": "s1", "prompt": "where is the config file?"})
        self.assertIsNone(hooks.stop({"session_id": "s1"}))
        self.session_on("claude-fable-5-2")
        hooks.user_prompt({"session_id": "s1", "prompt": HARD})
        self.assertIsNone(hooks.stop({"session_id": "s1"}))


class SubagentChecks(Sandbox):
    """A subagent's commands are in its own transcript (SubagentStop's agent_transcript_path)."""

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
        self.assertTrue(upstream.has_pending(changes))
        self.assertIn("retires November 30, 2026", report.status(changes))
        self.assertEqual(upstream.diff({}, new)["newly_deprecated"], [])  # a first review floods nothing
        in_use_only = upstream.diff(new, new, in_use=["claude-sonnet-4-5-20250929"])
        self.assertFalse(upstream.has_pending(in_use_only))  # said at session start on its own line

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


class Reports(Sandbox):
    def test_policy_shows_levels_cap_and_lowered_crew(self):
        self.assertIn("| complex | opus | high |", self.out("policy"))
        store.update_state(lambda state: state.update(max_tier="sonnet", priority="quality"))
        text = self.out("policy")
        self.assertIn("Cap: **sonnet**", text)
        self.assertIn("fable (not in use)", text)
        self.assertIn("| model-router:deep-solver | sonnet (lowered from fable) |", text)
        self.assertIn("| lookup | sonnet |", text)

    def test_models_report(self):
        registry.record_result("fable", "claude-opus-5-5", "opus")
        registry.record_result("fable", "claude-opus-5-5", "opus")
        registry.record_result("haiku", "claude-haiku-5-5", "haiku")
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

    def test_usage_report(self):
        self.assertIn("No activity", self.out("usage"))
        self.session_on("claude-opus-5-5")
        self.pre("general-purpose", LOOKUP)
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", model="haiku")
        text = self.out("usage", "30")
        self.assertIn("| general-purpose | 1 | 1 | lookup x1 | claude-haiku-5-5 x1 |", text)
        self.assertIn("| claude-haiku-5-5 | 1 | 1,200 |", text)
        for agent_id, model in ((None, "claude-opus-5-5"), ("a1", "claude-haiku-5-5"), ("a2", "claude-new-9")):
            hooks.mod_usage({"session_id": "s1", "agent_id": agent_id, "usage": {
                "model": model, "input_tokens": 0, "output_tokens": 1_000_000,
                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}})
        text = self.out("usage", "30")
        self.assertIn("Spend at list price: $20.50", text)
        self.assertIn("| main | claude-opus-5-5 | 1 | 1,000,000 | 20.00 |", text)
        self.assertIn("No price in the registry for: claude-new-9", text)

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
        store.save_entry("prompts", "s1", {"level": "lookup"})
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
        cfg.load_config()
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
        registry.record_failure("opus", "404 model not found")
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

    HARD_BODY = ("Base directory for this skill: /p/.claude/skills/hard\n\nInvestigate the race condition in the "
                 "auth migration across every service, find the root cause and redesign the locking.")
    LOOKUP_BODY = "Base directory for this skill: /p/.claude/skills/where\n\nList the files under src."
    LINE = "<command-message>{0}</command-message>\n<command-name>/{0}</command-name>\n<command-args>{1}</command-args>"

    def turn(self, name: str, args: str, body: str | None, **extra) -> dict | None:
        command = {"name": name, "args": args, **({"body": body} if body is not None else {})}
        return hooks.mod_turn({"session_id": "s1", "text": self.LINE.format(name, args), "command": command, **extra})

    def test_a_hard_skill_raises_the_turn_and_a_lookup_does_not(self):
        self.session_on("claude-opus-5-5")
        self.assertEqual(self.turn("hard", "please check src too", self.HARD_BODY)["effort"], "xhigh")
        self.assertIsNone(self.turn("where", "", self.LOOKUP_BODY))
        self.assertEqual(self.turn("plain", HARD, None), {"level": "complex", "effort": "high"})  # args alone

    def test_a_long_ordinary_skill_body_is_not_a_hard_request(self):
        self.session_on("claude-opus-5-5")
        body = "Never commit secrets or credentials. " + "Write a clear commit message for the staged change. " * 40
        self.assertGreater(len(body), 1500)
        self.assertIsNone(self.turn("commit", "", body))
        self.assertIsNone(self.turn("commit", "", "Review the staged diff in production style.\n" + body))
        # The cut only works while the opening is too short for profile_prompt's own length rule.
        self.assertNotIn("scope", profile.profile_prompt("note " * (hooks._SKILL_BODY_CHARS // 5))["signals"])

    def test_a_teammate_message_still_does_not_raise(self):
        self.session_on("claude-opus-5-5")
        self.assertIsNone(self.turn("hard", "", self.HARD_BODY, origin="peer"))
        delivered = "Another Claude session sent a message:\n<cross-session-message from=\"x\">\n" + HARD
        self.assertIsNone(hooks.mod_turn({"session_id": "s1", "text": delivered}))
        # A command left over from a submit whose turn never started can't stand in for another turn's text.
        self.assertIsNone(hooks.mod_turn({"session_id": "s1", "text": delivered,
                                          "command": {"name": "rename", "args": HARD}}))
        self.assertIsNone(hooks.mod_turn({"session_id": "s1", "text": self.LINE.format("other", ""),
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
                                store.load_entry("prompts", "s1")["level"])[1]
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

    def test_each_diagram_is_valid_titled_svg_linked_from_the_readme(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in self.NAMES:
            root = self.parse(name)
            self.assertEqual(root.tag, f"{self.SVG}svg", name)
            for attr in ("viewBox", "width", "height"):
                self.assertTrue(root.get(attr), f"{name} has no {attr}")
            title = root.find(f"{self.SVG}title")
            self.assertTrue(title is not None and (title.text or "").strip(), f"{name} has no title")
            self.assertIn(f"](docs/assets/{name})", readme)
            source = (ROOT / "docs" / "assets" / name).read_text(encoding="utf-8")
            self.assertNotIn("var(", source, f"{name}: librsvg and some viewers render CSS variables black")
            self.assertIn("prefers-color-scheme: dark", source, name)

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
        from modelrouter import ladder
        config = cfg.base_config()
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
            hooks.mod_usage({"session_id": "s1", "agent_id": agent_id, "usage": {
                "model": model, "input_tokens": 0, "output_tokens": 1_000_000,
                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}})
        data = self.json_out("usage", "30")
        self.assertEqual(data["days"], 30)
        self.assertEqual(data["agents"], [{"agent": "general-purpose", "calls": 1, "routed": 1,
                                           "levels": {"lookup": 1}, "ran_on": {"claude-haiku-5-5": 1}}])
        self.assertEqual(data["models"], [{"model": "claude-haiku-5-5", "runs": 1, "tokens": 1200}])
        self.assertEqual(data["spend"], {"total_usd": 20.0, "unpriced": ["claude-new-9"], "rows": [
            {"loop": "main", "model": "claude-opus-5-5", "turns": 1, "output_tokens": 1_000_000, "usd": 20.0},
            {"loop": "subagent", "model": "claude-new-9", "turns": 1, "output_tokens": 1_000_000, "usd": 0}]})

    def test_models_json(self):
        registry.record_result("fable", "claude-opus-5-5", "opus")
        registry.record_result("fable", "claude-opus-5-5", "opus")
        registry.record_result("haiku", "claude-haiku-5-5", "haiku")
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
        for key in ("python", "platform", "claude_code"):
            self.assertIn(key, report["machine"])


class PriceTable(unittest.TestCase):
    """Prices from https://platform.claude.com/docs/en/about-claude/pricing.md (checked 2026-11-22)."""

    # The pricing page lists no rate for this one.
    UNLISTED = {"claude-mythos-preview"}

    def test_every_model_still_in_service_has_a_price(self):
        config = cfg.load_config()
        lifecycle = json.loads((ROOT / "compat" / "baseline.json").read_text(encoding="utf-8"))["lifecycle"]
        missing = [model for model, info in lifecycle.items()
                   if info.get("state") in ("Active", "Deprecated") and model not in self.UNLISTED
                   and registry.price(config, model) is None]
        self.assertEqual(missing, [])

    def test_mythos_is_priced_like_its_fable_twin(self):
        config = cfg.load_config()
        for version, rates in (("5-2", (8, 40, 0.2, 10, 16)), ("5-1", (10, 50, 0.25, 12.5, 20)),
                               ("5", (10, 50, 1, 12.5, 20))):
            price = registry.price(config, f"claude-mythos-{version}")
            self.assertEqual(tuple(price[k] for k in ("input", "output", "cache_read", "cache_write",
                                                      "cache_write_1h")), rates, version)


if __name__ == "__main__":
    unittest.main()
