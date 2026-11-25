"""The runtime behind the mod's status line and its /model-router command."""
from __future__ import annotations

import json

from test_model_router import FIXTURES, HARD, LOOKUP, Sandbox, hooks, modpath, report, settings, store

OPUS = "claude-opus-5-5"


def snapshot(session_id: str | None = "s1", **extra) -> settings.Snapshot:
    return settings.Snapshot({**({"session_id": session_id} if session_id else {}), **extra})


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

class StatusLine(Sandbox):
    def spawn(self, session_id: str = "s1", **tool) -> None:
        modpath.spawn({"session_id": session_id, "parent_model": OPUS, "kind": "subagent",
                       "tool_input": {"subagent_type": "general-purpose", "prompt": LOOKUP, **tool}})

    def line(self, session_id: str = "s1") -> str:
        return report.status(snapshot(session_id))["line"]

    def test_it_counts_this_sessions_routed_calls_and_redos(self):
        self.assertEqual(self.line(), "auto · mod · 0 routed · 0 redos")
        self.spawn(model="haiku", description="d")  # the effort is filled in: routed
        self.spawn(model="haiku", effort="medium", prompt="list the docs")  # kept as named
        self.spawn("s2", model="haiku")
        self.done("general-purpose", LOOKUP, "claude-haiku-5-5", description="d")
        self.spawn(model="haiku", description="Redo: d")
        self.assertEqual(self.line(), "auto · mod · 2 routed · 1 redo")
        self.assertEqual(self.line("s2"), "auto · mod · 1 routed · 0 redos")

    def test_it_names_the_cap_and_the_path_that_routed_last(self):
        self.options(max_tier="opus")
        self.session_on(OPUS)
        self.pre("general-purpose", LOOKUP, model="haiku")
        self.assertEqual(self.line(), "auto · fallback · cap opus · 1 routed · 0 redos")

    def test_routing_off_reads_off_and_a_session_switch_is_marked(self):
        self.options(routing_mode="off")
        self.assertEqual(self.line(), "off")
        settings.switch("s1", "auto")
        self.assertEqual(self.line(), "auto (this session) · mod · 0 routed · 0 redos")
        self.options(routing_mode="auto")
        settings.switch("s1", "off")
        self.assertEqual(self.line(), "off (this session)")

    def test_it_stays_under_80_characters(self):
        self.options(max_tier="sonnet", routing_mode="off")
        settings.switch("s1", "auto")
        row = {"event": "spawn", "via": "fallback", "session": "s1", "action": "rewrite", "escalated_from": "lookup"}
        path = store.ledger_path()
        path.write_text("".join(json.dumps({"ts": "2099-01-01T00:00:00+00:00", **row}) + "\n" for _ in range(10000)))
        line = self.line()
        self.assertEqual(line, "auto (this session) · fallback · cap sonnet · 10000 routed · 10000 redos")
        self.assertLess(len(line), 80)

    def test_the_text_adds_how_to_switch(self):
        text = report.status(snapshot())["text"].splitlines()
        self.assertEqual(text, ["auto · mod · 0 routed · 0 redos",
                                "Switch it off for this session with /model-router off, or for good with Routing mode in /config."])
        settings.switch("s1", "off")
        self.assertEqual(report.status(snapshot())["text"].splitlines()[-1],
                         "Switch it on for this session with /model-router on, or for good with Routing mode in /config.")

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


class ModCommands(Sandbox):
    def setUp(self):
        super().setUp()
        self.payloads = json.loads((FIXTURES / "mod-payloads.json").read_text(encoding="utf-8"))

    def test_status_answers_the_line_the_text_and_whether_routing_is_on(self):
        out = hooks.HANDLERS["mod-status"](self.payloads["mod-status"])
        self.assertEqual(set(out), {"ok", "line", "text", "routing"})
        self.assertIs(out["routing"], True)

    def test_a_switch_answers_its_reply(self):
        out = hooks.HANDLERS["mod-status"]({"session_id": "s1", "routing": "off"})
        self.assertEqual((out["line"], out["routing"], out["reply"]),
                         ("off (this session)", False, "Routing is off for this session. To keep it off, set Routing mode in /config."))
        out = hooks.HANDLERS["mod-status"]({"session_id": "s1", "routing": "on"})
        self.assertEqual((out["routing"], out["reply"]), (True, "Routing is on, as Routing mode in /config sets it."))
        self.assertNotIn("reply", hooks.HANDLERS["mod-status"]({"session_id": "s1", "routing": "maybe"}))

    def test_end_forgets_the_session(self):
        settings.switch("s1", "off")
        self.assertEqual(hooks.HANDLERS["mod-end"](self.payloads["mod-end"]), {"ok": True})
        self.assertEqual(store.load_entry(settings.SWITCHES, "s1"), {})

    def test_entries_older_than_a_day_are_dropped_on_the_next_write(self):
        settings.switch("old", "off")
        self.advance(settings.SWITCH_KEPT + 60)
        settings.switch("s1", "off")
        self.assertEqual(store.load_entry(settings.SWITCHES, "old"), {})
