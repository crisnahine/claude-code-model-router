"""Tests for the mod's answers and for what counts as a model error."""
from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from test_model_router import LOOKUP, Sandbox, cli, health, hooks, modpath, spawn, store

BEDROCK_403 = ("Agent terminated early due to an API error: AWS authentication failed · enable the model for your "
               "account and region in the Amazon Bedrock console · API Error: 403 You don't have access to the "
               "model with the specified model ID.")


class RefusedNewestId(Sandbox):
    def spawn_and_fail(self, tool_use_id: str, error: str, **extra) -> dict | None:
        tool_input = {"subagent_type": "general-purpose", "prompt": "write the tests", "description": "write tests",
                      "model": "sonnet", "effort": "medium", **extra}
        pick = modpath.spawn({"session_id": "s1", "parent_model": "claude-opus-5-5", "kind": "subagent",
                              "tool_use_id": tool_use_id, "tool_input": tool_input})
        hooks.agent_failed({"session_id": "s1", "cwd": str(self.project), "tool_use_id": tool_use_id,
                            "tool_input": tool_input, "error": error})
        return pick

    def test_a_provider_403_on_the_newest_id_moves_the_next_call_to_the_alias(self):
        self.options(CLAUDE_CODE_USE_BEDROCK="1")
        self.assertEqual(self.spawn_and_fail("toolu_1", BEDROCK_403)["model"], "claude-sonnet-5-5")
        self.assertFalse(health.load().healthy("claude-sonnet-5-5"))
        self.assertIsNone(self.spawn_and_fail("toolu_2", BEDROCK_403))  # the alias, as the call named it
        self.assertTrue(health.load().healthy("sonnet"))  # a 403 never strikes the alias

    def test_a_403_on_an_alias_strikes_nothing(self):
        self.spawn_and_fail("toolu_1", BEDROCK_403)
        self.spawn_and_fail("toolu_2", BEDROCK_403)
        self.assertTrue(health.load().healthy("sonnet"))


class UnknownAgentType(Sandbox):
    TYPED = "Agent type 'not_found_error' not found. Available agents: general-purpose, Explore"
    LISTED = "Agent type 'model-router:archtect' not found. Available agents: general-purpose, Explore, model_not_found"

    def test_the_agent_list_error_is_no_model_error_whatever_names_it_echoes(self):
        for text in (self.TYPED, self.LISTED):
            self.assertFalse(health.is_model_error(text))
            self.assertFalse(health.is_refusal(text))

    def test_it_never_routes_the_named_model_around(self):
        for tool_use_id in ("toolu_1", "toolu_2"):
            call = {**self.call("not_found_error", "look into it", model="fable"), "tool_use_id": tool_use_id}
            hooks.pre_agent(call)
            hooks.agent_failed({**call, "error": self.TYPED})
        self.assertTrue(health.load().healthy("fable"))


class ExactModelKept(Sandbox):
    def setUp(self):
        super().setUp()
        self.session_on("claude-opus-5-5")

    def spawn(self, agent: str, session_id: str = "s1", **extra) -> dict | None:
        return modpath.spawn({"session_id": session_id, "cwd": str(self.project), "kind": "subagent",
                              "parent_model": "claude-opus-5-5", **extra,
                              "tool_input": {"subagent_type": agent, "prompt": "design the store"}})

    def test_setting_effort_keeps_an_exact_id_where_the_alias_runs_an_older_model(self):
        self.options(CLAUDE_CODE_USE_FOUNDRY="1")
        self.agent("designer", "claude-opus-5-5")
        self.assertEqual(self.spawn("designer")["model"], "claude-opus-5-5")
        updated = self.pre("designer", "design the store")["updatedInput"]
        self.assertNotIn("model", updated)  # the agent's own model applies
        self.assertTrue(updated["effort"])

    def test_setting_effort_keeps_a_1m_variant(self):
        self.agent("reader", "sonnet[1m]")
        self.assertEqual(self.spawn("reader")["model"], "sonnet[1m]")
        self.assertNotIn("model", self.pre("reader", "design the store")["updatedInput"])

    def test_an_exact_id_the_lists_deny_or_that_failed_here_runs_on_the_alias(self):
        self.options(CLAUDE_CODE_USE_FOUNDRY="1")
        self.agent("designer", "claude-opus-5-5")
        denied = {"policy": {"deniedModels": ["claude-opus-5-5"]}}
        self.assertEqual(self.spawn("designer", "s2", settings=denied)["model"], "opus")
        health.failed("claude-opus-5-5", "API Error: 404 not_found_error", newest=True)
        self.assertEqual(self.spawn("designer")["model"], "opus")
        self.assertEqual(self.pre("designer", "design the store")["updatedInput"]["model"], "opus")

    def test_modeloverrides_still_keep_an_exact_id_the_agent_names(self):
        # Claude Code maps a named ID through modelOverrides whether or not the router keeps it.
        arn = "arn:aws:bedrock:us-east-2:123456789012:application-inference-profile/sonnet-55"
        cases = [({"CLAUDE_CODE_USE_BEDROCK": "1"}, "claude-sonnet-5-5", {"claude-sonnet-5-5": arn}),
                 ({"CLAUDE_CODE_USE_FOUNDRY": "1"}, "claude-opus-5-5", {"claude-opus-5-5": "opus-55-prod"}),
                 ({"CLAUDE_CODE_USE_FOUNDRY": "1"}, "claude-opus-5-5", {"claude-haiku-4-5": "my-haiku"})]
        for n, (env, pinned, overrides) in enumerate(cases):
            with self.subTest(pinned=pinned, overrides=overrides), mock.patch.dict(os.environ, env):
                self.agent("pinned", pinned)
                answer = self.spawn("pinned", f"o{n}", settings={"user": {"modelOverrides": overrides}})
                self.assertEqual(answer["model"], pinned)
                self.assertNotIn("model", self.pre("pinned", "design the store", session_id=f"o{n}")["updatedInput"])

    def test_a_redo_on_the_same_family_keeps_the_exact_model(self):
        self.agent("reader", "sonnet[1m]", extra="effort: medium\n")  # below high, so the redo stays on the family
        self.spawn("reader", tool_use_id="x1")
        hooks.post_agent({**self.call("reader", "design the store"), "tool_use_id": "x1",
                          "tool_response": {"resolvedModel": "claude-sonnet-5-5[1m]", "status": "completed",
                                            "totalTokens": 10}})
        redo = modpath.spawn({"session_id": "s1", "cwd": str(self.project), "kind": "subagent",
                              "parent_model": "claude-opus-5-5", "tool_use_id": "x2",
                              "tool_input": {"subagent_type": "reader", "prompt": "design the store",
                                             "description": "Redo: design"}})
        self.assertEqual(redo["model"], "sonnet[1m]")
        self.assertEqual(redo["effort"], "high")


class SpawnAnswer(Sandbox):
    def answer(self, hook_input: dict) -> str:
        with mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(json.dumps(hook_input).encode()))), \
                redirect_stdout(io.StringIO()) as out:
            cli.main(["mod-spawn"])
        return out.getvalue().strip()

    def test_a_spawn_left_as_it_is_answers_ok_so_silence_means_the_runtime_failed(self):
        kept = {"session_id": "s1", "parent_model": "claude-opus-5-5", "kind": "subagent",
                "tool_input": {"subagent_type": "general-purpose", "prompt": "p", "model": "sonnet", "effort": "high"}}
        self.assertEqual(json.loads(self.answer(kept)), {"ok": True})
        with mock.patch.object(modpath, "spawn", side_effect=RuntimeError("boom")), redirect_stderr(io.StringIO()):
            self.assertEqual(self.answer(kept), "")


class MovedByAnotherMod(Sandbox):
    """A later mod that moves a spawn to another family says nothing about the model this mod sent."""

    def finish(self, tool_use_id: str, moved: bool, error: str | None = None) -> None:
        tool_input = {"subagent_type": "general-purpose", "prompt": f"list the files under {tool_use_id}", "model": "haiku"}
        modpath.spawn({"session_id": "s1", "parent_model": "claude-sonnet-5-5", "kind": "subagent",
                       "tool_use_id": tool_use_id, "tool_input": tool_input})
        mark = self.data / modpath.MARKS / f"{tool_use_id}.moved"
        if moved:  # what hooks/router.ts writes when core was asked for another family than it sent
            mark.parent.mkdir(parents=True, exist_ok=True)
            mark.write_text("")
        call = {"session_id": "s1", "cwd": str(self.project), "tool_use_id": tool_use_id, "tool_input": tool_input}
        if error:
            hooks.agent_failed({**call, "error": error})
        else:
            hooks.post_agent({**call, "tool_response": {"resolvedModel": "claude-opus-5-5", "status": "completed"}})
        self.assertFalse(mark.exists())  # a mark answers its one call

    def test_a_run_another_mod_moved_strikes_no_model(self):
        for n in range(2):
            self.finish(f"toolu_m{n}", moved=True)
            self.finish(f"toolu_e{n}", moved=True, error="API Error: 404 not_found_error: model: claude-opus-5-5")
        self.assertIsNone(store.load_state().get("health"))
        for n in range(2):
            self.finish(f"toolu_p{n}", moved=False)
        self.assertEqual(health.load().why_routed_around("haiku"), "ran as claude-opus-5-5 instead")

    def test_only_a_plain_tool_use_id_is_looked_up(self):
        for bad in ("../x", "a/b", "", None, 5, "x" * 300):
            self.assertFalse(modpath.moved(bad), bad)


class RedoWithNothingHigher(Sandbox):
    """A redo of a run already at the strongest usable model and its top effort runs the same again, and Claude is told."""

    SAME = ("This redo ran on haiku at max, the strongest model and the most effort usable here, the same as the run "
            "it redoes.")

    def setUp(self):
        super().setUp()
        self.session_on("claude-opus-5-5")

    def run_once(self, description: str, ran_as: str = "claude-haiku-5-5", **extra) -> dict | None:
        out = self.pre("general-purpose", LOOKUP, description=description, **extra)
        self.done("general-purpose", LOOKUP, ran_as, description=description, **extra)
        return out

    def test_the_decision_says_so_only_when_no_step_is_left(self):
        self.options(max_tier="haiku")
        self.run_once("List src", model="haiku")
        for effort in ("high", "xhigh", "max"):
            climbed = self.decide("general-purpose", LOOKUP, description="Redo: list src")
            self.assertEqual((climbed["model"], climbed["effort"], climbed["no_step"]), ("haiku", effort, False))
            self.assertIsNone(spawn.redo_note(climbed))
            self.assertNotIn("the strongest model and the most effort usable here", self.run_once("Redo: list src")["additionalContext"])
        same = self.decide("general-purpose", LOOKUP, description="Redo: list src")
        self.assertEqual((same["model"], same["effort"], same["no_step"]), ("haiku", "max", True))
        self.assertEqual(spawn.redo_note(same), self.SAME)
        self.assertFalse(self.decide("general-purpose", LOOKUP, description="List src")["no_step"])

    def test_the_fallback_tells_claude_beside_its_rewrite(self):
        self.options(max_tier="haiku")
        self.run_once("List src")
        for _ in range(3):
            self.run_once("Redo: list src")
        out = self.run_once("Redo: list src")
        self.assertEqual(out["updatedInput"]["effort"], "max")
        self.assertTrue(out["additionalContext"].startswith("Model Router set this agent's model to haiku"))
        self.assertTrue(out["additionalContext"].endswith(" " + self.SAME))

    def test_a_redo_left_as_it_is_still_tells_claude(self):
        # The environment sets the effort and the call names the model, so the climb has nothing to change.
        self.options(max_tier="haiku", CLAUDE_CODE_EFFORT_LEVEL="high")
        self.assertIsNone(self.run_once("List src", model="haiku"))
        out = self.run_once("Redo: list src", model="haiku")
        self.assertEqual(out, {"hookEventName": "PreToolUse", "additionalContext":
                               "This redo ran on haiku, the strongest model and the most effort usable here, the same as the run it redoes."})

    def test_a_copy_the_repeat_guard_passes_through_says_nothing(self):
        self.run_once("Draft it", ran_as="claude-fable-5-5", model="fable", effort="max")
        self.assertIn("the strongest model and the most effort usable here", self.pre("general-purpose", LOOKUP, description="Redo: draft it",
                                                   model="fable", effort="max")["additionalContext"])
        self.next_message()
        self.assertIsNone(self.pre("general-purpose", LOOKUP, description="Redo: draft it", model="fable", effort="max"))

    def test_the_mod_hands_the_note_to_its_agent_call(self):
        self.options(max_tier="haiku")
        self.run_once("List src")
        redo = {"session_id": "s1", "parent_model": "claude-opus-5-5", "kind": "subagent",
                "tool_input": {"subagent_type": "general-purpose", "prompt": LOOKUP, "description": "Redo: list src"}}
        self.assertNotIn("note", modpath.spawn(redo))
        for _ in range(3):
            self.run_once("Redo: list src")
        self.assertEqual(modpath.spawn(redo), {"model": "haiku", "effort": "max", "reason": self.ledger()[-1]["reason"],
                                               "note": self.SAME})


class ModStartRules(Sandbox):
    """The mod puts the session note's table in the Agent tool's description, from the runtime's answer."""

    def test_mod_start_answers_the_session_note_while_routing(self):
        for env in ({}, {"max_tier": "sonnet"}, {"CLAUDE_CODE_EFFORT_LEVEL": "high"}):
            with self.subTest(env=env):
                self.options(**env)
                note = hooks.session_start({"session_id": "s1"})["hookSpecificOutput"]["additionalContext"]
                answer = hooks.HANDLERS["mod-start"]({"session_id": "s1"})
                self.assertEqual({k: answer[k] for k in ("ok", "rules")}, {"ok": True, "rules": note})
        self.options(routing_mode="off")
        self.assertEqual(hooks.HANDLERS["mod-start"]({"session_id": "s1"}), {"ok": True})

    def test_mod_start_answers_the_same_table_for_a_workflow_scripts_stages(self):
        # A stage gets its model and effort from the table or from its agent's tools, so the Workflow tool's text carries both.
        text = hooks.HANDLERS["mod-start"]({"session_id": "s1"})["workflow_rules"]
        self.assertTrue(text.startswith("Model Router keeps the `model` and `effort` a workflow script names on an "
                                        "`agent()` call, within the user's cap. Here every `agent()` call in a script "
                                        "names both, by the work:\n- haiku at medium: finding, reading and listing\n"))
        self.assertIn("- sonnet at high: a judgment call with no check to run (a design or a review)\nA stage that names neither runs on its agent's own model, "
                      "else at the level its tools put it.", text)
        self.assertLessEqual(len(text), 540)  # it sits in the Workflow tool's description all session
        self.options(CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL="haiku")
        self.assertIn("A stage that names neither runs on haiku.", hooks.HANDLERS["mod-start"]({"session_id": "s1"})["workflow_rules"])
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="opus", CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1")
        self.assertNotIn("workflow_rules", hooks.HANDLERS["mod-start"]({"session_id": "s1"}))  # a script can't set a model
