"""The runtime behind the mod's status line, its /model-router command and the session check."""
from __future__ import annotations

import json

from test_model_router import FIXTURES, HARD, LOOKUP, PLUGIN, Sandbox, cfg, hooks, modpath, report, session, settings, store

OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"


def snapshot(session_id: str | None = "s1", **extra) -> settings.Snapshot:
    return settings.Snapshot({**({"session_id": session_id} if session_id else {}), **extra})


class SessionSetups(Sandbox):
    def test_the_registry_holds_each_familys_lowest_recommended_effort_in_order(self):
        self.assertEqual(list(cfg.load().session.items()), [("opus", "medium"), ("fable", "medium"), ("sonnet", "xhigh")])

    def test_a_family_left_out_is_never_recommended(self):
        reg = cfg.load()
        for effort in cfg.EFFORTS:
            self.assertFalse(session.recommended(reg, "haiku", effort), effort)
        self.assertEqual([e for e in cfg.EFFORTS if session.recommended(reg, "sonnet", e)], ["xhigh", "max"])
        self.assertEqual([e for e in cfg.EFFORTS if session.recommended(reg, "opus", e)], ["medium", "high", "xhigh", "max"])
        self.assertFalse(session.recommended(reg, "opus", None))
        self.assertFalse(session.recommended(reg, None, "max"))

    def test_an_override_file_cannot_change_them(self):
        self.override({"session": {"haiku": "low"}})
        self.override({"session": {"haiku": "low"}}, user=True)
        self.assertNotIn("haiku", cfg.load().session)


class SessionSwitch(Sandbox):
    def test_a_switch_turns_routing_off_for_its_session_only(self):
        settings.switch("s1", "off")
        self.assertEqual((snapshot().options.mode, snapshot().options.switched), ("off", True))
        self.assertEqual((snapshot("s2").options.mode, snapshot(None).options.mode), ("auto", "auto"))

    def test_a_switch_on_overrides_a_saved_off(self):
        self.options(routing_mode="off")
        settings.switch("s1", "auto")
        self.assertEqual((snapshot().options.mode, snapshot().options.switched), ("auto", True))

    def test_a_switch_to_the_saved_mode_keeps_nothing(self):
        settings.switch("s1", "off")
        settings.switch("s1", "auto")
        self.assertEqual((snapshot().options.mode, snapshot().options.switched), ("auto", False))
        self.assertNotIn("routing", store.load_entry(settings.SWITCHES, "s1"))

    def test_forget_drops_the_sessions_entry(self):
        settings.switch("s1", "off")
        settings.switch("s2", "off")
        settings.forget("s1")
        self.assertEqual((snapshot().options.mode, snapshot("s2").options.mode), ("auto", "off"))
        settings.forget(None)

    def test_both_entry_points_record_routing_is_off(self):
        self.session_on(OPUS)
        settings.switch("s1", "off")
        mod = modpath.spawn({"session_id": "s1", "parent_model": OPUS, "kind": "subagent",
                             "tool_input": {"subagent_type": "general-purpose", "prompt": LOOKUP, "model": "fable"}})
        self.assertIsNone(mod)
        self.assertIsNone(self.pre("general-purpose", HARD, model="fable"))
        self.assertEqual([(r["via"], r["reason"]) for r in self.ledger() if r["event"] == "spawn"],
                         [("mod", "routing is off"), ("fallback", "routing is off")])
        self.assertIsNone(hooks.session_start({"session_id": "s1", "source": "compact", "cwd": str(self.project)}))

    def test_a_corrupt_entry_reads_as_no_switch(self):
        (self.data / f"{settings.SWITCHES}.json").parent.mkdir(parents=True, exist_ok=True)
        (self.data / f"{settings.SWITCHES}.json").write_text("{not json")
        self.assertEqual(snapshot().options.mode, "auto")

    def test_the_session_check_option(self):
        self.assertEqual(settings.resolve().session_check, "ask")
        for value, want in (("off", "off"), (" OFF", "off"), ("ask", "ask"), ("nonsense", "ask")):
            self.options(session_check=value)
            self.assertEqual(settings.resolve().session_check, want, value)
        field = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["userConfig"]["session_check"]
        self.assertEqual((field["default"], field["options"], field["title"]), ("ask", ["ask", "off"], "Session check"))


class StatusLine(Sandbox):
    def spawn(self, session_id: str = "s1", **tool) -> None:
        modpath.spawn({"session_id": session_id, "parent_model": OPUS, "kind": "subagent",
                       "tool_input": {"subagent_type": "general-purpose", "prompt": LOOKUP, **tool}})

    def line(self, session_id: str = "s1") -> str:
        return report.status(snapshot(session_id))["line"]

    def test_it_counts_this_sessions_calls_the_ones_changed_and_redos(self):
        self.assertEqual(self.line(), "auto · mod · 0 calls")
        self.spawn(model="haiku", description="d")  # the effort is filled in: changed
        self.spawn(model="haiku", effort="medium", prompt="list the docs")  # kept as named
        self.spawn("s2", model="haiku")
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", description="d")
        self.spawn(model="haiku", description="Redo: d")
        self.assertEqual(self.line(), "auto · mod · 3 calls · 2 changed · 1 redo")
        self.assertEqual(self.line("s2"), "auto · mod · 1 call · 1 changed")

    def test_it_names_the_cap_and_the_path_that_routed_last(self):
        self.options(max_tier="opus")
        self.session_on(OPUS)
        self.pre("general-purpose", LOOKUP, model="haiku")
        self.assertEqual(self.line(), "auto · fallback · cap opus · 1 call · 1 changed")

    def test_routing_off_reads_off_and_a_session_switch_is_marked(self):
        self.options(routing_mode="off")
        self.assertEqual(self.line(), "off")
        settings.switch("s1", "auto")
        self.assertEqual(self.line(), "auto (this session) · mod · 0 calls")
        self.options(routing_mode="auto")
        settings.switch("s1", "off")
        self.assertEqual(self.line(), "off (this session)")

    def test_it_stays_under_100_characters(self):
        self.options(max_tier="sonnet", routing_mode="off")
        settings.switch("s1", "auto")
        row = {"event": "spawn", "via": "fallback", "session": "s1", "action": "rewrite", "escalated_from": "lookup"}
        path = store.ledger_path()
        path.write_text("".join(json.dumps({"ts": "2099-01-01T00:00:00+00:00", **row}) + "\n" for _ in range(10000)))
        line = self.line()
        self.assertEqual(line, "auto (this session) · fallback · cap sonnet · 10000 calls · 10000 changed · 10000 redos")
        self.assertLess(len(line), 100)

    def test_the_text_adds_the_session_and_how_to_switch(self):
        text = report.status(snapshot())["text"].splitlines()
        self.assertEqual(text, ["auto · mod · 0 calls",
                                "Switch it off for this session with /model-router off, or for good with Routing mode in /config."])
        session.observe(snapshot(), SONNET, "medium")
        self.assertEqual(report.status(snapshot())["text"].splitlines()[1],
                         "session: sonnet at medium (below the recommended setups)")
        session.observe(snapshot(), OPUS, "medium")
        self.assertEqual(report.status(snapshot())["text"].splitlines()[1], "session: opus at medium")
        settings.switch("s1", "off")
        self.assertEqual(report.status(snapshot())["text"].splitlines()[-1],
                         "Switch it on for this session with /model-router on, or for good with Routing mode in /config.")

    def test_an_estimate_shows_until_a_request_confirms_it(self):
        session.check(snapshot(), SONNET)
        self.assertEqual(report.status(snapshot())["text"].splitlines()[1],
                         "session: sonnet at medium (below the recommended setups), estimated before the first request")

    def test_each_switch_says_whether_it_lasts(self):
        self.assertEqual(report.switched(snapshot(), "off"), "Routing stays on: the switch could not be saved.")
        settings.switch("s1", "off")
        self.assertEqual(report.switched(snapshot(), "off"),
                         "Routing is off for this session. To keep it off, set Routing mode in /config.")
        settings.switch("s1", "auto")
        self.assertEqual(report.switched(snapshot(), "auto"), "Routing is on, as Routing mode in /config sets it.")
        self.options(routing_mode="off")
        settings.switch("s1", "auto")
        self.assertEqual(report.switched(snapshot(), "auto"),
                         "Routing is on for this session. To keep it on, set Routing mode in /config.")
        settings.switch("s1", "off")
        self.assertEqual(report.switched(snapshot(), "off"), "Routing is off, as Routing mode in /config sets it.")


class SessionCheck(Sandbox):
    EFFORT = "Which effort for {}? Claude Code keeps this model and effort as your default for new sessions."
    NEVER = "Don't ask again (/model-router check on brings this back)"
    HAIKU = ["Opus (Recommended)", "Fable (the strongest model)", "Sonnet (at xhigh effort or higher)", "Stay on Haiku at medium"]

    def check(self, model: str = SONNET, session_id: str = "s1", locked: bool = False, **layers) -> dict:
        if layers:
            modpath.start({"session_id": session_id, "settings": layers})
        return session.check(snapshot(session_id), model, locked)

    def labels(self, step: dict) -> list[str]:
        return [o["label"] for o in step.get("options", [])]

    def choice(self, answer: dict, label: str) -> dict:
        return next(o for o in answer["options"] if o["label"].startswith(label))

    def test_a_recommended_setup_is_not_asked(self):
        for model, layers in ((OPUS, {}), ("claude-fable-5-2", {}), ("opus[1m]", {}),
                              (SONNET, {"user": {"modelSettings": {SONNET: {"effortLevel": "xhigh"}}}})):
            self.assertEqual(self.check(model, session_id=model, **layers), {}, model)

    def test_an_effort_flag_at_launch_counts_ahead_of_the_settings_files(self):
        # --effort is in no settings file, so the mod passes on what the session's own command line named.
        self.assertEqual(session.check(snapshot("flag"), SONNET, launched="xhigh"), {})
        low = session.check(snapshot("low"), OPUS, launched="low")
        self.assertIn("This session is on Opus at low.", low["question"])
        # The flag holds on whichever model is picked, so the effort command runs or not by it.
        self.assertEqual(self.choice(self.choice(low, "Opus")["then"], "medium").get("effort"), "medium")
        high = session.check(snapshot("high"), "claude-haiku-5-5", launched="xhigh")
        self.assertNotIn("effort", self.choice(self.choice(high, "Sonnet")["then"], "xhigh"))
        self.assertIn("Sonnet at medium", session.check(snapshot("junk"), SONNET, launched="--model")["question"])
        self.options(CLAUDE_CODE_EFFORT_LEVEL="medium")  # the variable outranks the flag
        self.assertIn("Sonnet at medium", session.check(snapshot("env"), SONNET, launched="xhigh")["question"])

    def test_step_one_offers_each_usable_model_then_stay(self):
        answer = self.check("claude-haiku-5-5")
        self.assertEqual(self.labels(answer), self.HAIKU)
        self.assertEqual(answer["header"], "Model Router")
        self.assertLessEqual(len(answer["header"]), 12)
        self.assertEqual(answer["question"], "Model Router works best with a strong session to plan and delegate. This "
                                             "session is on Haiku at medium. Which model should it run on?")
        self.assertEqual({k: v for k, v in self.choice(answer, "Opus").items() if k != "then"},
                         {"label": "Opus (Recommended)", "family": "opus", "model": "opus"})

    def test_the_recommended_and_strongest_marks_follow_what_is_usable(self):
        for name, layers, want in (
                ("opus blocked", {"policy": {"deniedModels": ["opus"]}},
                 ["Fable (Recommended)", "Sonnet (at xhigh effort or higher)"]),
                ("fable blocked", {"policy": {"deniedModels": ["fable"]}}, ["Opus (Recommended)", "Sonnet (at xhigh effort or higher)"]),
                ("only sonnet", {"policy": {"availableModels": ["haiku", "sonnet"]}}, ["Sonnet (Recommended, at xhigh effort or higher)"])):
            self.assertEqual(self.labels(self.check("claude-haiku-5-5", name, **layers))[:-1], want, name)

    def test_step_two_offers_each_familys_efforts(self):
        answer = self.check("claude-haiku-5-5")
        for name, efforts in (("Opus", ["medium (Recommended, Claude Code's default)", "high", "xhigh", "max (this session only)"]),
                              ("Fable", ["medium (Recommended, Claude Code's default)", "high", "xhigh", "max (this session only)"]),
                              ("Sonnet", ["xhigh (Recommended)", "max (this session only)"])):
            then = self.choice(answer, name)["then"]
            self.assertEqual((then["question"], then["header"]), (self.EFFORT.format(name), "Model Router"))
            self.assertEqual(self.labels(then), efforts, name)
            levels = [e.split()[0] for e in efforts]
            self.assertEqual([o["done"] for o in then["options"]], [f"This session is now on {name} at {e}." for e in levels])
            # The session would run each model at medium, so medium needs no /effort.
            self.assertEqual([o.get("effort") for o in then["options"]], [None if e == "medium" else e for e in levels], name)

    def test_a_session_on_a_recommended_family_at_too_low_an_effort_keeps_its_model(self):
        answer = self.check(OPUS, user={"modelSettings": {OPUS: {"effortLevel": "low"}}})
        self.assertEqual(self.labels(answer)[-1], "Stay on Opus at low")
        opus = self.choice(answer, "Opus")
        self.assertNotIn("model", opus)
        self.assertEqual(opus["then"]["options"][0], {"label": "medium (Recommended, Claude Code's default)",
                                                      "done": "This session is now on Opus at medium.", "effort": "medium"})
        sonnet = self.choice(self.check(SONNET, "s2"), "Sonnet")
        self.assertNotIn("model", sonnet)
        self.assertEqual([o.get("effort") for o in sonnet["then"]["options"]], ["xhigh", "max"])

    def test_stay_asks_whether_to_ask_again(self):
        stay = self.choice(self.check(SONNET), "Stay")
        self.assertEqual(stay["label"], "Stay on Sonnet at medium")
        self.assertEqual(stay["then"], {"question": "Ask about this again?", "header": "Model Router",
                                        "options": [{"label": "Ask again next session"}, {"label": self.NEVER, "check": "off"}]})
        self.assertEqual(self.choice(self.check(SONNET, "s2", locked=True), "Stay"), {"label": "Stay on Sonnet at medium"})

    def test_max_tier_filters_nothing(self):
        self.options(max_tier="haiku")
        self.assertEqual(self.labels(self.check("claude-haiku-5-5")), self.HAIKU)

    def test_no_usable_setup_asks_nothing(self):
        self.assertEqual(self.check("claude-haiku-5-5", policy={"availableModels": ["haiku"]}), {})
        self.assertEqual(self.check(SONNET, "s2", policy={"maxEffortLevel": "low"}), {})

    def test_max_effort_level_holds_the_efforts_and_drops_a_family_below_its_floor(self):
        caps = {"maxEffortLevel": "xhigh", "modelSettings": {SONNET: {"maxEffortLevel": "high"}, OPUS: {"maxEffortLevel": "high"}}}
        answer = self.check("claude-haiku-5-5", user=caps)
        self.assertEqual(self.labels(answer), ["Opus (Recommended)", "Fable (the strongest model)", "Stay on Haiku at medium"])
        self.assertEqual(self.labels(self.choice(answer, "Opus")["then"]), ["medium (Recommended, Claude Code's default)", "high"])
        self.assertEqual(self.labels(self.choice(answer, "Fable")["then"])[-1], "xhigh")
        only = self.check("claude-haiku-5-5", "s2", user={"modelSettings": {SONNET: {"maxEffortLevel": "xhigh"}}})
        self.assertEqual(self.choice(only, "Sonnet"), {"label": "Sonnet (at xhigh effort)", "family": "sonnet", "model": "sonnet",
                                                       "done": "This session is now on Sonnet at xhigh.",
                                                       "effort": "xhigh"})  # one effort: no second step

    def test_the_effort_estimate_follows_claude_codes_order(self):
        def stay(**layers) -> str:
            store.save_entry(settings.SWITCHES, "s1", {})
            return self.choice(self.check(SONNET, **layers), "Stay")["label"]
        self.assertEqual(stay(), "Stay on Sonnet at medium")  # the model's default
        self.assertEqual(stay(user={"modelSettings": {"sonnet": {"effortLevel": "high"}}}), "Stay on Sonnet at high")
        self.assertEqual(stay(user={"effortLevel": "high"}), "Stay on Sonnet at medium")  # not for the current models
        self.assertEqual(stay(user={"modelSettings": {SONNET: {"effortLevel": "high"}}}, project={"effortLevel": "low"}),
                         "Stay on Sonnet at low")  # a project file outranks the user's
        self.assertEqual(stay(policy={"effortLevel": "high"}, local={"modelSettings": {SONNET: {"effortLevel": "low"}}}),
                         "Stay on Sonnet at high")
        self.assertEqual(stay(policy={}, local={}, user={"modelSettings": {SONNET: {"effortLevel": "high"}}},
                              project={"maxEffortLevel": "medium"}), "Stay on Sonnet at medium")
        self.options(CLAUDE_CODE_EFFORT_LEVEL="high")
        self.assertEqual(stay(user={"modelSettings": {SONNET: {"effortLevel": "low"}}}, project={}), "Stay on Sonnet at high")

    def test_an_effort_the_environment_pins_is_never_switched(self):
        self.options(CLAUDE_CODE_EFFORT_LEVEL="medium")
        answer = self.check(SONNET)
        self.assertEqual(answer["options"][:2], [
            {"label": "Opus (Recommended)", "family": "opus", "model": "opus", "done": "This session is now on Opus at medium."},
            {"label": "Fable (the strongest model)", "family": "fable", "model": "fable",
             "done": "This session is now on Fable at medium."}])
        self.assertEqual(self.labels(answer)[2:], ["Stay on Sonnet at medium"])

    def test_an_effort_the_session_already_saves_for_a_model_needs_no_effort_command(self):
        answer = self.check(SONNET, user={"modelSettings": {OPUS: {"effortLevel": "high"}}})
        self.assertEqual([o.get("effort") for o in self.choice(answer, "Opus")["then"]["options"]], ["medium", None, "xhigh", "max"])

    def test_a_lagging_alias_sends_the_newest_id_or_is_left_out(self):
        self.options(CLAUDE_CODE_USE_BEDROCK="1")
        low = {"user": {"modelSettings": {OPUS: {"effortLevel": "low"}}}}
        answer = self.check(OPUS, **low)
        self.assertEqual([(o["label"], o.get("model")) for o in answer["options"][:3]],
                         [("Opus (Recommended)", None), ("Fable (the strongest model)", "fable"),
                          ("Sonnet (at xhigh effort or higher)", SONNET)])
        answer = self.check(OPUS, "s2", **low, policy={"availableModels": ["opus", "fable", "sonnet", "claude-sonnet-4-5"]})
        self.assertEqual(self.labels(answer), ["Opus (Recommended)", "Fable (the strongest model)", "Stay on Opus at low"])

    def test_a_session_on_an_older_model_of_its_family_is_below_the_setups_and_is_offered_the_newest(self):
        self.options(CLAUDE_CODE_USE_BEDROCK="1")  # where the sonnet alias runs Sonnet 4.5
        for session_id, model in (("alias", "sonnet"), ("id", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")):
            answer = session.check(snapshot(session_id), model, launched="xhigh")
            self.assertEqual(self.choice(answer, "Sonnet").get("model"), SONNET, model)
        self.assertEqual(session.check(snapshot("newest"), SONNET, launched="xhigh"), {})
        session.observe(snapshot("seen"), "sonnet", "xhigh")
        self.assertFalse(store.load_entry(settings.SWITCHES, "seen")["setup"]["recommended"])
        self.options(CLAUDE_CODE_USE_BEDROCK="0")  # the Anthropic API's alias runs the newest
        answer = session.check(snapshot("api"), "claude-sonnet-4-5", launched="xhigh")
        self.assertEqual(self.choice(answer, "Sonnet").get("model"), "sonnet")
        self.assertEqual(session.check(snapshot("api-alias"), "sonnet", launched="xhigh"), {})

    def test_routing_off_the_option_off_or_an_unknown_model_asks_nothing(self):
        self.assertEqual(self.check("gpt-5", "a"), {})
        settings.switch("b", "off")
        self.assertEqual(self.check(SONNET, "b"), {})
        self.options(session_check="off")
        self.assertEqual(self.check(SONNET, "c"), {})
        self.options(session_check="ask", routing_mode="off")
        self.assertEqual(self.check(SONNET, "d"), {})

    def test_it_asks_once_per_session(self):
        self.assertTrue(self.check(SONNET))
        self.assertEqual(self.check(SONNET), {})  # the reload a don't-ask-again write causes runs the check again
        self.assertTrue(self.check(SONNET, "s2"))
        self.assertEqual(session.check(snapshot(None), SONNET), {})

    def test_a_request_records_the_exact_setup(self):
        self.assertEqual(self.check(OPUS), {})
        self.assertEqual(store.load_entry(settings.SWITCHES, "s1")["estimate"],
                         {"model": "opus", "effort": "medium", "recommended": True})
        session.observe(snapshot(), SONNET, "medium")  # the estimate missed: no question mid-work
        entry = store.load_entry(settings.SWITCHES, "s1")
        self.assertEqual(entry["setup"], {"model": "sonnet", "effort": "medium", "recommended": False})
        self.assertEqual(self.check(SONNET), {})
        session.observe(snapshot(), "x", None)
        session.observe(snapshot(None), SONNET, "high")
        self.assertEqual(store.load_entry(settings.SWITCHES, "s1")["setup"]["model"], "sonnet")


class ModCommands(Sandbox):
    def setUp(self):
        super().setUp()
        self.payloads = json.loads((FIXTURES / "mod-payloads.json").read_text(encoding="utf-8"))

    def test_status_answers_the_line_the_text_and_whether_routing_is_on(self):
        out = hooks.HANDLERS["mod-status"](self.payloads["mod-status"])
        self.assertEqual(set(out), {"ok", "line", "text", "routing"})
        self.assertIs(out["routing"], True)
        self.assertIn("session: opus at medium", out["text"])

    def test_a_switch_answers_its_reply(self):
        out = hooks.HANDLERS["mod-status"]({"session_id": "s1", "routing": "off"})
        self.assertEqual((out["line"], out["routing"], out["reply"]),
                         ("off (this session)", False, "Routing is off for this session. To keep it off, set Routing mode in /config."))
        out = hooks.HANDLERS["mod-status"]({"session_id": "s1", "routing": "on"})
        self.assertEqual((out["routing"], out["reply"]), (True, "Routing is on, as Routing mode in /config sets it."))
        self.assertNotIn("reply", hooks.HANDLERS["mod-status"]({"session_id": "s1", "routing": "maybe"}))

    def test_check_answers_the_question_and_end_forgets_the_session(self):
        out = hooks.HANDLERS["mod-check"](self.payloads["mod-check"])
        self.assertEqual(out["ok"], True)
        self.assertEqual(out["options"][-1], {"label": "Stay on Sonnet at medium"})  # the fixture's option is locked
        self.assertEqual(hooks.HANDLERS["mod-check"](self.payloads["mod-check"]), {"ok": True})
        self.assertEqual(hooks.HANDLERS["mod-end"](self.payloads["mod-end"]), {"ok": True})
        self.assertEqual(store.load_entry(settings.SWITCHES, "s1"), {})

    def test_entries_older_than_a_day_are_dropped_on_the_next_write(self):
        settings.switch("old", "off")
        self.advance(settings.SWITCH_KEPT + 60)
        settings.switch("s1", "off")
        self.assertEqual(store.load_entry(settings.SWITCHES, "old"), {})
