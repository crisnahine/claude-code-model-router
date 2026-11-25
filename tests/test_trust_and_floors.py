"""A project's file never makes a call run weaker, and the router's floors hold on every branch."""
from __future__ import annotations

import functools
import itertools
import unittest
from unittest import mock

from test_model_router import LOOKUP, Sandbox
from modelrouter import agents, calls, hooks, settings, spawn, store
from modelrouter import config as cfg
from modelrouter import mod as modpath
from modelrouter.config import EFFORTS, LEVELS

ARN = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/a1b2c3d4"
UNKNOWN = "unknown"  # an inherited effort the router can't see, so any effort it sets may be lower


def mod_input(project, agent: str, prompt: str = LOOKUP, parent: str | None = "opus", nested: bool = False,
              session_effort: str | None = None, kind: str = "subagent", **tool_input) -> dict:
    """What hooks/router.ts sends on agent.spawn."""
    hook_input = {"session_id": "s1", "cwd": str(project), "kind": kind,
                  "tool_input": {"subagent_type": agent, "prompt": prompt, **tool_input}}
    if parent:
        hook_input["parent_model"] = parent
    if nested:
        hook_input["parent_agent_id"] = "sub1"
    if session_effort:
        hook_input["session_effort"] = session_effort
    return hook_input


class ProjectNamesMatchOnlyExactly(Sandbox):
    def setUp(self):
        super().setUp()
        self.agent("bedrock-worker", ARN, folder=self.home / "agents")

    def spawn(self, agent: str = "bedrock-worker", parent: str = "claude-opus-5-5"):
        return modpath.spawn(mod_input(self.project, agent, parent=parent))

    def test_a_project_model_name_never_captures_an_id_outside_the_registry(self):
        for name in ("arn:aws:bedrock", "-"):
            self.override({"models": {name: {"strength": 0.1}}})
            self.assertIsNone(self.spawn(), name)
            self.options(max_tier="fable")
            self.assertIsNone(self.spawn(), name)
            self.options(max_tier="opus")
            self.assertEqual(self.spawn("general-purpose", parent=ARN)["model"], "haiku", name)  # its own level's
            self.assertIsNone(self.ledger()[-1]["was"], name)

    def test_a_project_model_still_runs_when_named_exactly(self):
        self.override({"models": {"house-model": {"strength": 2}}})
        answer = modpath.spawn(mod_input(self.project, "general-purpose", model="house-model"))
        self.assertEqual(answer["model"], "house-model")
        self.assertEqual(cfg.load().family("claude-opus-5-5"), "opus")


class ProjectFloorsOnAnUnknownModel(Sandbox):
    def spawn(self, agent: str, **extra):
        return modpath.spawn(mod_input(self.project, agent, **extra))

    def test_a_project_floor_only_raises_explore_and_plans_own_level(self):
        for agent in ("Explore", "Plan"):
            self.override({"agents": {agent: {"level": "lookup"}}})
            self.assertEqual((self.spawn(agent)["model"], self.spawn(agent)["effort"]), ("haiku", "medium"), agent)
            self.override({"agents": {agent: {"level": "standard"}}})
            self.assertEqual((self.spawn(agent)["model"], self.spawn(agent)["effort"]), ("haiku", "high"), agent)

    def test_a_project_floor_only_raises_a_nested_fallback_calls_own_level(self):
        self.override({"agents": {"general-purpose": {"level": "lookup"}}})
        hook_input = {"session_id": "s1", "cwd": str(self.project), "agent_id": "sub1",
                      "tool_input": {"subagent_type": "general-purpose", "prompt": LOOKUP}}
        snap = settings.Snapshot(hook_input)
        decision = spawn.decide(hook_input, snap).decision
        self.assertEqual((decision["level"], decision["model"], decision["source"]), ("standard", "haiku", "router"))

    def test_a_floor_that_can_only_raise_still_applies(self):
        self.override({"agents": {"Plan": {"level": "frontier"}}})
        self.assertEqual(self.spawn("Plan")["model"], "fable")
        self.override({"agents": {"Plan": {"level": "complex"}}}, user=True)
        self.override({})
        self.assertEqual(self.spawn("Plan")["model"], "sonnet")  # the user's own floor is trusted


class ProjectNeedsKeepANamedModelsRung(Sandbox):
    def test_a_needs_raise_never_moves_a_named_model_to_a_lower_effort(self):
        self.agent("deep", "opus", folder=self.home / "agents")
        for levels in ({"standard": {"needs": 3}}, {"complex": {"needs": 4}}):
            self.override({"levels": levels})
            named = modpath.spawn(mod_input(self.project, "general-purpose", parent="sonnet", model="opus"))
            pinned = modpath.spawn(mod_input(self.project, "deep", parent="sonnet"))
            for answer in (named, pinned):
                self.assertEqual(answer["model"], "opus", levels)
                self.assertGreaterEqual(EFFORTS.index(answer["effort"]), EFFORTS.index("high"), levels)

    def test_a_needs_raise_still_lifts_a_named_model_through_a_floor(self):
        self.override({"levels": {"standard": {"needs": 3}}, "agents": {"general-purpose": {"level": "standard"}}})
        answer = modpath.spawn(mod_input(self.project, "general-purpose", parent="sonnet", model="sonnet"))
        self.assertEqual(answer["model"], "opus")


class ProjectRuleOnTheSameModel(Sandbox):
    def test_a_project_model_the_call_runs_on_anyway_changes_nothing(self):
        alone = modpath.spawn(mod_input(self.project, "general-purpose", nested=True))
        self.override({"agents": {"general-purpose": {"model": alone["model"]}}})
        ruled = modpath.spawn(mod_input(self.project, "general-purpose", nested=True))
        self.assertEqual((ruled["model"], ruled["effort"]), (alone["model"], alone["effort"]))
        self.agent("exact", "claude-opus-5-5", folder=self.home / "agents")
        self.override({"agents": {"exact": {"model": "opus"}}})
        hook_input = mod_input(self.project, "exact", nested=True)
        decision = spawn.decide({**hook_input, "agent_id": "sub1"}, settings.Snapshot(hook_input)).decision
        self.assertEqual(decision["version"], "claude-opus-5-5")


class NestedRedo(Sandbox):
    PROMPT = "run the parser tests and report failures"

    def setUp(self):
        super().setUp()
        # An agent that inherits runs on its parent's model at its parent's effort.
        self.agent("heir", "inherit", folder=self.home / "agents")

    def spawn(self, description: str, tid: str):
        return hooks.HANDLERS["mod-spawn"]({**mod_input(self.project, "heir", self.PROMPT,
                                                        parent="claude-sonnet-5-5", nested=True,
                                                        session_effort="medium", description=description),
                                            "tool_use_id": tid})

    def test_a_redo_of_a_nested_run_at_its_parents_effort_moves_to_a_stronger_model(self):
        self.assertEqual(self.spawn("Parser tests", "toolu_1"), modpath.OK)
        hooks.post_agent({"session_id": "s1", "agent_id": "sub1", "cwd": str(self.project), "tool_use_id": "toolu_1",
                          "tool_input": {"subagent_type": "heir", "prompt": self.PROMPT,
                                         "description": "Parser tests"},
                          "tool_response": {"resolvedModel": "claude-sonnet-5-5", "status": "completed"}})
        self.assertEqual(self.spawn("Redo: parser tests", "toolu_2")["model"], "opus")

    def test_a_nested_redo_naming_the_same_model_climbs_unless_it_names_more_than_its_levels_effort(self):
        self.spawn("Parser tests", "toolu_1")
        hooks.post_agent({"session_id": "s1", "agent_id": "sub1", "cwd": str(self.project), "tool_use_id": "toolu_1",
                          "tool_input": {"subagent_type": "heir", "prompt": self.PROMPT,
                                         "description": "Parser tests"},
                          "tool_response": {"resolvedModel": "claude-sonnet-5-5", "status": "completed"}})
        def redo(effort: str):
            return hooks.HANDLERS["mod-spawn"]({**mod_input(self.project, "heir", self.PROMPT,
                                                            parent="claude-sonnet-5-5", nested=True, model="sonnet",
                                                            effort=effort, description="Redo: parser tests"),
                                                "tool_use_id": f"toolu_{effort}"})
        self.assertEqual(redo("medium")["model"], "opus")
        self.assertEqual(redo("max"), modpath.OK)  # the caller picked the effort lever, so it runs as named
        self.options(CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1", CLAUDE_CODE_SUBAGENT_MODEL="sonnet")
        self.assertEqual(redo("max"), modpath.OK)  # the pin's effort step never lowers it

    def test_a_nested_redo_with_nothing_on_record_moves_to_a_stronger_model(self):
        self.assertEqual(self.spawn("Redo: other tests", "toolu_3")["model"], "opus")
        self.assertFalse(self.ledger()[-1]["on_record"])

    def test_a_nested_redo_with_no_stronger_model_still_takes_an_effort_step(self):
        self.options(max_tier="sonnet")
        redo = self.spawn("Redo: other tests", "toolu_3")
        self.assertEqual((redo["model"], redo["effort"]), ("sonnet", "high"))

    def test_a_nested_redo_under_the_force_pin_still_takes_an_effort_step(self):
        self.options(CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1", CLAUDE_CODE_SUBAGENT_MODEL="sonnet")

        def named(description: str, tid: str):
            return hooks.HANDLERS["mod-spawn"]({**mod_input(self.project, "heir", self.PROMPT,
                                                            parent="claude-sonnet-5-5", nested=True, model="sonnet",
                                                            description=description), "tool_use_id": tid})
        self.assertEqual(named("Parser tests", "toolu_1"), modpath.OK)
        hooks.post_agent({"session_id": "s1", "agent_id": "sub1", "cwd": str(self.project), "tool_use_id": "toolu_1",
                          "tool_input": {"subagent_type": "heir", "prompt": self.PROMPT,
                                         "description": "Parser tests", "model": "sonnet"},
                          "tool_response": {"resolvedModel": "claude-sonnet-5-5", "status": "completed"}})
        for description in ("Redo: parser tests", "Redo: other tests"):  # on record, then nothing on record
            redo = named(description, "toolu_2")
            self.assertEqual((self.ledger()[-1]["action"], redo["effort"]), ("rewrite", "high"), description)


class ProjectFloorsSayWhyTheyDidNothing(Sandbox):
    def test_a_project_floor_raises_a_call_naming_no_model_so_nothing_is_held(self):
        # Such a call runs at its agent's own level, which the router knows, so a project's floor always applies.
        self.override({"agents": {"Plan": {"level": "complex"}}})
        self.assertEqual(modpath.spawn(mod_input(self.project, "Plan"))["model"], "sonnet")
        self.assertEqual((self.ledger()[-1]["source"], "held" in self.ledger()[-1]), ("rule", False))
        self.options(max_tier="sonnet")
        self.override({"agents": {"general-purpose": {"level": "hardest"}}})
        answer = modpath.spawn(mod_input(self.project, "general-purpose", parent="sonnet"))
        self.assertEqual((answer["model"], answer["effort"]), ("sonnet", "xhigh"))  # the cap holds the raise
        self.assertNotIn("held", self.ledger()[-1])
        self.override({"agents": {"general-purpose": {"level": "lookup"}}})
        modpath.spawn(mod_input(self.project, "general-purpose", parent="sonnet"))
        self.assertNotIn("held", self.ledger()[-1])  # it stands above the floor, so the floor holds


class WorkflowStagesWriteNoRoute(Sandbox):
    PROMPT = "audit the auth module for the token refresh bug"

    def spawn(self, kind: str, prompt: str, description: str, tid: str | None = None, **tool_input):
        hook_input = mod_input(self.project, "general-purpose", prompt, parent="claude-sonnet-5-5",
                               session_effort="medium", kind=kind, description=description, **tool_input)
        return hooks.HANDLERS["mod-spawn"]({**hook_input, **({"tool_use_id": tid} if tid else {})})

    def test_a_fan_out_never_pushes_a_running_calls_route_out(self):
        self.spawn("subagent", self.PROMPT, "Audit auth", "toolu_fg", model="opus", effort="high")
        for n in range(store.KEPT_RECORDS):
            self.advance(1)
            self.spawn("workflow", f"review file {n}", f"Review {n}")
        self.assertEqual(set(store.load_entry("routes", "s1")) - {"_at"}, {"toolu_fg"})
        hooks.post_agent({"session_id": "s1", "cwd": str(self.project), "tool_use_id": "toolu_fg",
                          "tool_input": {"subagent_type": "general-purpose", "prompt": self.PROMPT,
                                         "description": "Audit auth", "model": "opus", "effort": "high"},
                          "tool_response": {"resolvedModel": "claude-opus-5-5", "status": "completed"}})
        self.assertEqual(calls.last_run("s1", calls.task({"description": "Audit auth"}))["model"], "opus")
        self.assertEqual(self.spawn("subagent", self.PROMPT, "Redo: audit auth", "toolu_redo")["model"], "fable")


class ProjectFileNeverRunsACallWeaker(Sandbox):
    """Every way a project's file can touch a call, against the same call with no project file."""

    PARENTS = ("haiku", "opus", "fable")
    CONTEXTS = (("main", "low"), ("main", "max"), ("nested", None), ("fallback", None))
    AGENTS = ("general-purpose", "Explore", "deep", "bedrock-worker", "heir")
    MODELS = (None, "haiku", "sonnet", "opus")
    EFFORTS = (None, "low", "max")

    def setUp(self):
        super().setUp()
        self.agent("deep", "opus", folder=self.home / "agents")
        self.agent("bedrock-worker", ARN, folder=self.home / "agents")
        self.agent("heir", "inherit", folder=self.home / "agents")
        self.shipped = cfg.shipped()

    def files(self, agent: str) -> list[dict]:
        found = [{"models": {name: {"strength": s}}} for name, s in (("arn:aws:bedrock", 0.1), ("-", 9), ("house", 0.5))]
        found += [{"agents": {agent: {"level": str(level)}}} for level in LEVELS]
        found += [{"agents": {agent: {"model": model}}} for model in ("haiku", "sonnet", "opus", "fable", "house")]
        found += [{"models": {"house": {"strength": 0.5}}, "agents": {agent: {"model": "house"}}}]
        found += [{"levels": {str(level): {"needs": n}}} for level in LEVELS for n in (2, 3, 4, 5)]
        found += [{"levels": {str(level): {"effort": e}}} for level in LEVELS for e in EFFORTS]
        return found

    def calls(self):
        for parent, (context, session_effort), agent, model, effort in itertools.product(
                self.PARENTS, self.CONTEXTS, self.AGENTS, self.MODELS, self.EFFORTS):
            tool_input = {k: v for k, v in (("model", model), ("effort", effort)) if v}
            hook_input = {"session_id": "s1", "cwd": str(self.project),
                          "tool_input": {"subagent_type": agent, "prompt": LOOKUP, **tool_input}}
            if context != "fallback":
                hook_input.update(kind="subagent", parent_model=parent)
            if context != "main":
                hook_input["agent_id"] = "sub1"
            if session_effort:
                hook_input["session_effort"] = session_effort
            own = {"deep": "opus", "bedrock-worker": ARN}.get(agent)
            runs_on = model or own or parent
            inherited = effort or (session_effort if context == "main" and runs_on == parent else UNKNOWN)
            yield agent, hook_input, runs_on, inherited

    def ran(self, snap: settings.Snapshot, hook_input: dict, runs_on: str, inherited: str) -> tuple[str, str]:
        decision = spawn.decide(hook_input, snap).decision
        if decision["action"] != "rewrite":
            return runs_on, inherited
        return decision["model"] or runs_on, decision["effort"] or inherited

    def weaker(self, before: tuple[str, str], after: tuple[str, str]) -> bool:
        (m0, e0), (m1, e1) = before, after
        if m1 != m0:  # a stronger model is a step up whatever its effort, as a redo's climb reads it
            return m0 not in self.shipped.models or not self.shipped.stronger(m1, m0)
        if e0 == UNKNOWN:
            return e1 != UNKNOWN
        return e1 != e0 and (e1 == UNKNOWN or EFFORTS.index(e1) < EFFORTS.index(e0))

    def walk(self) -> list[str]:
        """The snapshot reads only the files, so one serves every call under one project file."""
        found, every = [], list(self.calls())
        self.override({})
        snap = settings.Snapshot(every[0][1])
        before = [self.ran(snap, hook_input, runs_on, inherited) for _, hook_input, runs_on, inherited in every]
        for agent in self.AGENTS:
            for file in self.files(agent):
                self.override(file)
                snap = settings.Snapshot(every[0][1])
                for (name, hook_input, runs_on, inherited), was in zip(every, before):
                    after = self.ran(snap, hook_input, runs_on, inherited) if name == agent else was
                    if self.weaker(was, after):
                        found.append(f"{file} on {hook_input}: {was} became {after}")
        return found

    # The agent files never change in the walk, and looking them up is most of its time.
    @mock.patch.object(agents, "find", functools.lru_cache(maxsize=None)(agents.find))
    def test_no_project_file_runs_any_call_weaker(self):
        self.assertEqual(self.walk()[:5], [])

    @mock.patch.object(agents, "find", functools.lru_cache(maxsize=None)(agents.find))
    def test_no_project_file_runs_any_call_weaker_under_a_cap(self):
        self.options(max_tier="opus")
        self.assertEqual(self.walk()[:5], [])


if __name__ == "__main__":
    unittest.main()
