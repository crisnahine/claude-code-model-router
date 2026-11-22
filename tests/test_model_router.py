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
            if name.startswith(("CLAUDE_PLUGIN_OPTION_", "CLAUDE_CODE_SUBAGENT_", "CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_USE_", "ANTHROPIC_")):
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
        launcher = (SCRIPTS / "model-router.sh").read_text(encoding="utf-8")
        floor = "%s.%s" % re.search(r"sys\.version_info < \((\d+), (\d+)\)", launcher).groups()
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


class Store(Sandbox):
    def test_keyed_files_drop_the_least_recently_written(self):
        with mock.patch.object(store, "KEEP_ENTRIES", 3):
            for i, key in enumerate(("zz", "aa", "mm", "bb")):
                with mock.patch.object(store.time, "time", return_value=1000.0 + i):
                    store.save_entry("prompts", key, {"n": i})
        self.assertEqual(sorted(store.load_dict(self.data / "prompts.json")), ["aa", "bb", "mm"])

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
        self.assertEqual(store.load_dict(path), {"a": 1, "b": 2})
        self.assertTrue(lock.exists())  # not ours to remove

    def test_state_files_are_written_compact(self):
        store.save_entry("pending", "s1", {"a": {"model": "haiku"}})
        self.assertNotIn("\n", (self.data / "pending.json").read_text(encoding="utf-8"))

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
        self.assertEqual(self.level("Fix the retry bug. Do not change the public API."), "standard")

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

    def test_turn_effort_rises_for_hard_requests_only(self):
        turn = lambda text: hooks.mod_turn({"session_id": "s1", "text": text})  # noqa: E731
        self.assertEqual(turn(HARD), {"level": "complex", "effort": "high"})
        self.assertIsNone(turn(LOOKUP))
        self.assertIsNone(turn("<task-notification>done</task-notification>"))
        for name, value in (("CLAUDE_CODE_EFFORT_LEVEL", "low"), ("CLAUDE_CODE_USE_BEDROCK", "1"),
                            ("main_effort", "false")):
            with mock.patch.dict(os.environ):
                self.options(**{name: value})
                self.assertIsNone(turn(HARD), name)

    def test_usage_is_priced_from_the_registry(self):
        hooks.mod_usage({"session_id": "s1", "agent_id": "a1", "usage": {
            "model": "claude-opus-5-5", "input_tokens": 1_000_000, "output_tokens": 100_000,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}})
        row = self.ledger()[-1]
        self.assertEqual((row["event"], row["cost_usd"]), ("usage", 6.0))
        self.assertIsNone(registry.cost(cfg.load_config(), "claude-unknown-9", {"input_tokens": 1}))
        config = cfg.load_config()
        self.assertEqual(registry.price(config, "claude-opus-4-7")["input"], 5)  # one entry covers Opus 4.x
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
        self.assertEqual(hooks.mod_turn(self.payloads["mod-turn"]), {"level": "complex", "effort": "high"})

    def test_usage_is_logged_for_its_agent(self):
        hooks.mod_usage(self.payloads["mod-usage"])
        row = self.ledger()[-1]
        self.assertEqual((row["event"], row["agent_id"], row["cost_usd"]), ("usage", "a1", 6.0))


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
        self.assertEqual(learn.bump("lookup"), 1)
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
        self.assertEqual(learn.bump("lookup"), 0)

    def test_naming_a_weaker_or_equal_model_is_not_a_vote(self):
        for i in range(10):
            self.pre("general-purpose", f"implement change {i}", model="haiku")
            self.pre("general-purpose", f"implement other change {i}", model="sonnet")
        self.assertEqual(learn.stats(), {})

    def test_a_raise_is_undone_when_it_keeps_being_overruled_downwards(self):
        self.raise_lookup()
        for i in range(6):
            self.pre("general-purpose", f"list the things in {i}", model="haiku")
        self.assertEqual(learn.bump("lookup"), 0)

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
        self.assertEqual(learn.bump("lookup"), 0)

    def test_a_spawn_identifies_its_call_once_and_reuses_the_raises(self):
        with mock.patch.object(calls, "identify", wraps=calls.identify) as identify, \
                mock.patch.object(learn, "bump", side_effect=AssertionError("re-read the raises")):
            self.pre("general-purpose", BUILD, model="haiku")
        self.assertEqual(identify.call_count, 1)


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


if __name__ == "__main__":
    unittest.main()
