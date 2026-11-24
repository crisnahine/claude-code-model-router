"""Regression tests for the mod's spawn answer and for what counts as a model error."""
from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from test_model_router import Sandbox, cli, health, hooks, modpath

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
        self.agent("reader", "sonnet[1m]")
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
