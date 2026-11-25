"""Tests for the session model and teammate detection."""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from test_model_router import HARD, Sandbox, hooks, session


class SessionModel(Sandbox):
    def transcript(self, model: str, seconds_ago: float) -> str:
        when = datetime.datetime.fromtimestamp(self.clock - seconds_ago, datetime.timezone.utc)
        path = Path(self.tmp.name) / f"main-{model}-{seconds_ago}.jsonl"
        path.write_text(json.dumps({"type": "assistant", "timestamp": when.isoformat().replace("+00:00", "Z"),
                                    "message": {"model": model, "content": []}}) + "\n")
        return str(path)

    def test_a_model_switch_after_a_reply_wins_over_the_older_transcript_row(self):
        hooks.session_start({"session_id": "s1", "model": "claude-opus-5-5"})
        older = self.transcript("claude-opus-5-5", seconds_ago=60)
        hooks.model_switch({"session_id": "s1", "to_model": "claude-haiku-5-5"})
        self.assertEqual(session.model("s1", older), "claude-haiku-5-5")
        # A reply written after the record still wins, as does a row without a readable time.
        self.advance(120)
        self.assertEqual(session.model("s1", self.transcript("claude-sonnet-5-5", seconds_ago=1)), "claude-sonnet-5-5")
        undated = Path(self.tmp.name) / "undated.jsonl"
        undated.write_text(json.dumps({"type": "assistant", "message": {"model": "claude-sonnet-5-5"}}) + "\n")
        self.assertEqual(session.model("s1", str(undated)), "claude-sonnet-5-5")


class Teammates(Sandbox):
    def test_a_named_call_is_a_teammate_only_when_agent_teams_are_on(self):
        self.options(CLAUDE_CODE_SUBAGENT_EFFORT="low")
        self.assertIsNone(self.decide("general-purpose", HARD, name="auditor", model="opus")["effort"])
        self.options(CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS="1")
        out = self.pre("general-purpose", HARD, name="auditor", model="opus")
        self.assertEqual(out["updatedInput"]["effort"], "high")
