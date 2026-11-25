"""A call that nothing names a model for: the router picks its level from what the agent can do, never its brief."""
from __future__ import annotations

import os

from test_model_router import (HAIKU, LOOKUP, PLUGIN, Sandbox, agents, cfg, health, hooks, ladder, modpath, report,
                               router, settings, spawn)
from modelrouter.config import Level

READER = "tools: Read, Grep, Glob\n"
BRIEF = "make the parser keep quoted fields whole"
OPUS = "claude-opus-5-5"


class AgentTools(Sandbox):
    def parsed(self, extra: str) -> dict:
        self.agent("probe", extra=extra)
        return agents.find("probe", self.project)

    def test_the_tool_lists_are_read_in_every_frontmatter_form(self):
        self.assertEqual(self.parsed("tools: Read, Grep\n")["tools"], ["Read", "Grep"])
        self.assertEqual(self.parsed("tools: [Read, 'Bash(git status)']\n")["tools"], ["Read", "Bash(git status)"])
        self.assertEqual(self.parsed("tools:\n  - Read\n  - Edit\n")["tools"], ["Read", "Edit"])
        self.assertEqual(self.parsed("disallowedTools: Write, Edit\n")["disallowed"], ["Write", "Edit"])
        plain = self.parsed("")
        self.assertEqual((plain["tools"], plain["disallowed"]), (None, None))
        # Each form YAML reads and Claude Code takes (sub-agents.md: "a comma-separated string ... or a YAML list").
        forms = {
            "tools: Read, Bash  # runs the tests\n": ["Read", "Bash"],
            "tools: [Read, Bash] # a flow list with a comment\n": ["Read", "Bash"],
            "tools: [\n  Read,\n  Write\n]\n": ["Read", "Write"],
            "tools: [Read,  # the reader\n        Edit]\n": ["Read", "Edit"],
            "tools:\n  [Read, Bash]\n": ["Read", "Bash"],
            "tools:\n  - Read\n  - Edit  # fixes the code\n": ["Read", "Edit"],
            "tools:\n- Read\n- Bash\n": ["Read", "Bash"],
            "tools:\n  # a reader that can test\n  - Read\n\n  - 'Bash(git status)'\n": ["Read", "Bash(git status)"],
            "tools: \"Read, Bash\"\n": ["Read", "Bash"],
            "tools: 'Read, Edit' # quoted\n": ["Read", "Edit"],
            "tools: Agent(worker, researcher), Read, Bash\n": ["Agent(worker, researcher)", "Read", "Bash"],
            "tools: [\"Bash(git log, git diff)\", Read]\n": ["Bash(git log, git diff)", "Read"],
            "tools: mcp__github__*, Read\n": ["mcp__github__*", "Read"],
            "tools: Read, Grep\r\nmodel: haiku\r\n": ["Read", "Grep"],
            "disallowedTools: Write, Edit  # no file writes\n": ["Write", "Edit"],
        }
        for extra, tools in forms.items():
            with self.subTest(extra=extra):
                found = self.parsed(extra)
                self.assertEqual(found["disallowed" if extra.startswith("disallowed") else "tools"], tools)

    def test_a_tool_list_that_cant_be_read_with_confidence_counts_as_every_tool(self):
        # The safe side: every tool for `tools`, so the agent can change files, and nothing removed for `disallowedTools`.
        for extra in ("tools:\n", "tools: []\n", "tools: ~\n", "tools: null\n", "tools: true\n", "tools: >\n  Read\n",
                      "tools: |\n  Read\n", "tools: *readers\n", "tools: !!str Read\n", "tools: {Read: true}\n",
                      "tools: Read,\n  Grep\n", "tools: [Read, [Grep]]\n", "tools: [Read, Grep\n", "tools: \"Read, Grep\n",
                      "tools:\n  - Read\n  - [Grep]\n", "tools:\n  - Read\n  Grep\n", "tools: Read Grep\n",
                      "tools: Bash(echo #x)\n"):
            with self.subTest(extra=extra):
                found = self.parsed(extra)
                self.assertIsNone(found["tools"])
                self.assertFalse(agents.read_only(found))
        found = self.parsed("tools: Read, Grep\ndisallowedTools: >\n  Bash\n")
        self.assertEqual((found["tools"], found["disallowed"], agents.read_only(found)), (["Read", "Grep"], None, True))
        self.assertFalse(agents.read_only(self.parsed("disallowedTools: [Edit, Write, NotebookEdit,\n")))

    def test_an_agent_whose_list_carries_a_comment_or_spans_lines_is_routed_by_the_tools_it_has(self):
        for extra in ("tools: Read, Bash  # runs the tests\n", "tools:\n  - Read\n  - Edit  # fixes the code\n",
                      "tools: [\n  Read,\n  Write\n]\n"):
            with self.subTest(extra=extra):
                self.agent("worker", extra=extra)
                self.assertEqual(self.decide("worker", BRIEF)["level"], "standard")

    def test_an_agent_reads_only_when_it_can_edit_write_or_run_nothing(self):
        reads = lambda extra: agents.read_only(self.parsed(extra))  # noqa: E731
        self.assertTrue(reads(READER))
        self.assertTrue(reads("tools: Read, WebFetch, mcp__docs__search\n"))
        for writes in ("tools: Read, Bash\n", "tools: Read, Bash(git status)\n", "tools: Edit\n", "tools: Write\n",
                       "tools: NotebookEdit\n", ""):
            self.assertFalse(reads(writes), writes)
        self.assertTrue(reads("disallowedTools: Edit, Write, NotebookEdit, Bash\n"))
        self.assertFalse(reads("disallowedTools: Edit, Write\n"))
        self.assertTrue(reads("disallowedTools: Edit, Write, NotebookEdit, Bash(rm:*)\n"))  # a scoped entry removes the tool
        self.assertTrue(reads("tools: Read, Bash\ndisallowedTools: Bash\n"))
        for builtin, only_reads in (("Explore", True), ("Plan", True), ("general-purpose", False), ("claude", False)):
            self.assertEqual(agents.read_only(agents.find(builtin, self.project)), only_reads, builtin)
        self.assertFalse(agents.read_only({"source": "workflow", "name": "stage", "model": None}))
        for role, only_reads in (("scout", True), ("reviewer", True), ("architect", True), ("verifier", False),
                                 ("implementer", False)):
            self.assertEqual(agents.read_only(agents.find(f"model-router:{role}", self.project)), only_reads, role)


class UnnamedCalls(Sandbox):
    def setUp(self):
        super().setUp()
        self.session_on(OPUS)
        self.agent("reader", extra=READER)
        self.agent("writer")
        self.config = cfg.shipped()

    def at(self, level: Level) -> tuple:
        return ladder.model_for(self.config, level), ladder.effort(self.config, level)

    def mod(self, agent: str, kind: str = "subagent", parent: str = OPUS, **extra) -> dict | None:
        return modpath.spawn({"session_id": "s1", "cwd": str(self.project), "parent_model": parent, "kind": kind,
                              "tool_input": {"subagent_type": agent, "prompt": BRIEF, **extra}})

    def test_an_agent_that_only_reads_runs_at_lookup_and_any_other_at_standard(self):
        for agent, level, why in (("reader", Level.lookup, "no model is named, and this agent only reads: lookup"),
                                  ("Explore", Level.lookup, "no model is named, and this agent only reads: lookup"),
                                  ("writer", Level.standard, "no model is named, and this agent can change files: standard"),
                                  ("general-purpose", Level.standard,
                                   "no model is named, and this agent can change files: standard")):
            with self.subTest(agent):
                out = self.pre(agent, BRIEF)
                self.assertEqual((out["updatedInput"]["model"], out["updatedInput"]["effort"]), self.at(level))
                row = self.ledger()[-1]
                self.assertEqual((row["action"], row["level"], row["source"]), ("rewrite", level, "router"))
                self.assertTrue(row["reason"].startswith(why), row["reason"])
                self.assertIn(why, out["additionalContext"])

    def test_the_pick_never_reads_the_brief(self):
        for brief in (LOOKUP, "investigate why auth is flaky and redesign it across every service"):
            d = self.decide("writer", brief)
            self.assertEqual((d["model"], d["effort"], d["level"]), (*self.at(Level.standard), "standard"), brief)

    def test_a_model_from_anywhere_else_wins_over_the_pick(self):
        self.agent("pinned", "sonnet", extra=READER)
        self.assertEqual(self.decide("pinned", BRIEF)["source"], "pin")
        self.assertEqual(self.decide("reader", BRIEF, model="opus")["source"], "caller")
        self.override({"agents": {"reader": {"model": "fable"}}}, user=True)
        self.assertEqual(self.decide("reader", BRIEF)["source"], "rule")
        self.override({"agents": {"reader": {"level": "complex"}}}, user=True)  # a rule's level raises the pick
        d = self.decide("reader", BRIEF)
        self.assertEqual((d["source"], d["level"], d["model"]), ("rule", "complex", self.at(Level.complex)[0]))
        self.override({})
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="sonnet")
        d = self.decide("reader", BRIEF)
        self.assertEqual((d["action"], d["model"]), ("keep", None))
        # The variable alone doesn't move the built-in Explore and Plan (sub-agents.md), so they keep the pick.
        for builtin in ("Explore", "Plan"):
            d = self.decide(builtin, BRIEF)
            self.assertEqual((d["source"], d["level"], d["model"]), ("router", "lookup", self.at(Level.lookup)[0]), builtin)
            self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", d["reason"])
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="opus", CLAUDE_CODE_SUBAGENT_MODEL_FORCE="1")
        self.assertIn("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", self.decide("reader", BRIEF)["reason"])

    def test_a_workflow_pin_wins_over_the_pick_for_a_stage(self):
        self.options(CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL="sonnet")
        answer = self.mod("stage", kind="workflow")
        self.assertNotEqual(answer.get("model"), "haiku")
        self.assertIn("CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL", answer["reason"])

    def test_an_agent_nobody_can_find_a_fork_and_a_rule_or_mode_that_switches_routing_off_are_left_alone(self):
        for agent in ("mystery:agent", "nobody", "fork"):
            self.assertEqual(self.decide(agent, BRIEF)["action"], "keep", agent)
        self.override({"agents": {"reader": {"route": False}}}, user=True)
        self.assertEqual(self.decide("reader", BRIEF)["action"], "keep")
        self.override({})
        self.options(routing_mode="off")
        self.assertEqual(self.decide("reader", BRIEF)["action"], "keep")

    def test_the_pick_is_held_to_the_cap_the_allowlist_health_and_the_session_floor(self):
        self.route_around("haiku")
        d = self.decide("reader", BRIEF)
        self.assertEqual((d["model"], d["effort"]), ("sonnet", "medium"))
        self.assertIn("haiku is over the cap or unavailable here", d["reason"])
        self.session_on(OPUS, "s2")
        modpath.start({"session_id": "s2", "settings": {"user": {"availableModels": ["opus", "fable"]}}})
        self.assertEqual(self.decide("reader", BRIEF, session_id="s2")["model"], "opus")
        health.ran("haiku", HAIKU, "haiku", forced=False)
        self.session_on(HAIKU, "s3")
        hook_input = {**self.call("reader", BRIEF, session_id="s3"), "effort": {"level": "xhigh"}}
        snap = settings.Snapshot(hook_input)
        d = router.decide(spawn.facts(hook_input, snap), snap.registry, snap.options)
        self.assertEqual((d["model"], d["effort"]), ("haiku", "xhigh"))  # the session's own model, at its level
        self.assertIn("at least the session's xhigh", d["reason"])

    def test_an_agents_own_effort_is_kept(self):
        self.agent("careful", extra=READER + "effort: max\n")
        d = self.decide("careful", BRIEF)
        self.assertEqual((d["model"], d["effort"]), ("haiku", None))

    def test_the_mod_routes_subagents_teammates_and_workflow_stages(self):
        self.assertEqual((self.mod("reader")["model"], self.mod("reader")["effort"]), self.at(Level.lookup))
        self.assertEqual((self.mod("stage", kind="workflow")["model"], self.mod("stage", kind="workflow")["effort"]),
                         self.at(Level.standard))
        # A teammate keeps one model for all its tasks, so it starts no lower than standard.
        for agent in ("reader", "writer", "auditor"):
            answer = self.mod(agent, kind="teammate")
            self.assertEqual((answer["model"], answer["effort"]), self.at(Level.standard), agent)
        self.assertIn("a teammate starts no lower than standard", self.mod("reader", kind="teammate")["reason"])

    def test_a_nested_call_on_the_fallback_is_routed_too(self):
        hook_input = {**self.call("reader", BRIEF), "agent_id": "sub1"}
        snap = settings.Snapshot(hook_input)
        d = router.decide(spawn.facts(hook_input, snap), snap.registry, snap.options)
        self.assertEqual((d["action"], d["model"], d["effort"]), ("rewrite", *self.at(Level.lookup)))

    def test_the_mod_and_the_fallback_decide_the_same_unnamed_call(self):
        self.agent("careful", extra="effort: low\n")
        for parent in (OPUS, HAIKU):
            self.session_on(parent)
            for agent in ("reader", "writer", "careful", "Explore", "Plan", "general-purpose"):
                for extra in ({}, {"effort": "xhigh"}):
                    with self.subTest(parent=parent, agent=agent, **extra):
                        fallback = self.pre(agent, BRIEF, **extra)
                        seen = {"fallback": self.ledger()[-1], "mod": (self.mod(agent, parent=parent, **extra),
                                                                       self.ledger()[-1])[1]}
                        picked = {path: (r["action"], r.get("model"), r.get("effort"), r["level"], r["source"],
                                         r["reason"]) for path, r in seen.items()}
                        self.assertEqual(picked["fallback"], picked["mod"])
                        self.assertEqual(picked["mod"][4], "router")
                        if fallback:
                            self.assertEqual(fallback["updatedInput"].get("model"), picked["fallback"][1])

    def test_a_redo_of_an_unnamed_call_climbs_from_what_it_ran_at(self):
        self.pre("reader", BRIEF, description="Find quoting")
        self.done("reader", BRIEF, "claude-haiku-5-5", description="Find quoting")
        again = self.decide("reader", BRIEF, description="Redo: find quoting")
        self.assertEqual((again["model"], again["effort"], again["escalated_from"]), ("haiku", "high", "lookup"))
        self.assertIn("(haiku at medium): one effort step up on haiku", again["reason"])
        unmarked = self.decide("writer", BRIEF, description="Redo: parser fix")  # nothing on record
        self.assertEqual((unmarked["model"], unmarked["effort"], unmarked["escalated_from"]), ("sonnet", "high", "standard"))
        self.assertIn("the run it would get unmarked (haiku at high)", unmarked["reason"])

    def test_explain_shows_the_pick(self):
        snap = settings.Snapshot({"session_id": "s1", "cwd": str(self.project)})
        text = report.explain(snap, BRIEF, "reader")
        self.assertIn("Level: **lookup**", text)
        self.assertIn("Why: no model is named, and this agent only reads: lookup.", text)
        self.assertIn("else at the level its tools put it", self.out("policy"))


class UnnamedCallNotes(Sandbox):
    def note(self, **env) -> str:
        self.options(**env)
        return hooks.session_start({"session_id": "s1", "model": OPUS})["hookSpecificOutput"]["additionalContext"]

    def test_the_notes_say_where_a_call_naming_neither_runs(self):
        self.assertIn("A call that names neither runs on its agent's own model, else at the level its tools put it.",
                      self.note())
        self.assertIn("A call that names neither runs on its agent's own model or CLAUDE_CODE_SUBAGENT_MODEL.",
                      self.note(CLAUDE_CODE_SUBAGENT_MODEL="sonnet"))
        os.environ.pop("CLAUDE_CODE_SUBAGENT_MODEL")
        staged = hooks.mod_start({"session_id": "s1", "cwd": str(self.project)})["workflow_rules"]
        self.assertIn("A stage that names neither runs on its agent's own model, else at the level its tools put it.",
                      staged)
        self.options(CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL="sonnet")
        self.assertIn("A stage that names neither runs on sonnet.",
                      hooks.mod_start({"session_id": "s1", "cwd": str(self.project)})["workflow_rules"])

    def test_the_route_skill_names_the_pick(self):
        skill = (PLUGIN / "skills" / "route" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("the level its tools put it", skill)


class InheritingAgents(Sandbox):
    """`model: inherit` is the agent's own choice, the parent's model, which beats CLAUDE_CODE_SUBAGENT_MODEL
    (sub-agents.md, "Choose a model"). Only a definition with no model field gets the pick."""

    def setUp(self):
        super().setUp()
        self.session_on(OPUS)
        self.agent("heir", "inherit", extra=READER)

    def test_an_explicit_inherit_is_kept_apart_from_a_missing_model(self):
        heir = agents.find("heir", self.project)
        self.assertEqual((heir["model"], heir["inherits"]), (None, True))
        self.agent("plain")
        self.assertFalse(agents.find("plain", self.project)["inherits"])

    def test_an_agent_that_inherits_runs_on_the_parents_model_and_gets_no_pick(self):
        d = self.decide("heir", BRIEF)
        self.assertEqual((d["action"], d["model"], d["was"], d["level"]), ("keep", None, "opus", "hardest"))
        self.assertIn("the agent's definition says inherit, so it runs on the parent's model, opus", d["reason"])
        self.options(CLAUDE_CODE_SUBAGENT_MODEL="sonnet")  # step 2 beats the variable
        d = self.decide("heir", BRIEF)
        self.assertEqual((d["action"], d["was"]), ("keep", "opus"))

    def test_an_agent_that_inherits_is_held_to_the_cap_and_raised_to_a_rules_level(self):
        self.options(max_tier="sonnet")
        d = self.decide("heir", BRIEF)
        self.assertEqual((d["action"], d["model"], d["effort"]), ("rewrite", "sonnet", "high"))
        os.environ.pop("CLAUDE_PLUGIN_OPTION_MAX_TIER")
        self.session_on(HAIKU)
        self.override({"agents": {"heir": {"level": "complex"}}}, user=True)
        d = self.decide("heir", BRIEF)
        self.assertEqual((d["model"], d["source"], d["level"]), ("sonnet", "rule", "complex"))

    def test_a_redo_of_an_agent_that_inherits_climbs_from_the_parents_model(self):
        self.pre("heir", BRIEF, description="Find quoting")
        self.done("heir", BRIEF, OPUS, description="Find quoting")
        again = self.decide("heir", BRIEF, description="Redo: find quoting")
        self.assertEqual((again["model"], again["effort"], again["escalated_from"]), ("opus", "high", "hardest"))


class RuleLevelsOnThePick(Sandbox):
    def setUp(self):
        super().setUp()
        self.session_on(OPUS)
        self.agent("reader", extra=READER)

    def test_a_users_level_rule_raises_the_pick_and_never_moves_it_to_the_parents_model(self):
        for level, model, effort, source in (("lookup", "haiku", "medium", "router"),
                                             ("standard", "haiku", "high", "rule"),
                                             ("complex", "sonnet", "high", "rule")):
            with self.subTest(level):
                self.override({"agents": {"reader": {"level": level}}}, user=True)
                d = self.decide("reader", BRIEF)
                self.assertEqual((d["model"], d["effort"], d["level"], d["source"]), (model, effort, level, source))
