"""What a person types after a skill command never aborts the skill."""
from __future__ import annotations

import io
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout

import test_model_router as t
from test_model_router import Sandbox, ledger
from modelrouter import cli
from modelrouter import mod as modpath

HUGE = "9" * 5000  # past int()'s default digit limit on Python 3.11 and later


@unittest.skipUnless(shutil.which("sh"), "needs sh")
class TypedWords(Sandbox):
    SET = t.SkillLines.SET
    run_line = t.SkillLines.run_line

    def test_every_skill_line_exits_0_whatever_follows_it(self):
        typed = ("", "30 days", "last week", "14 --verbose", "-r", "--verbose", "reset --force", "--json=1",
                 "--reset=yes", "--max-tier", "--r", "1000000", HUGE, "²", "0", "-3")
        for name in ("route", "models", "usage"):
            for values in (None, self.SET):
                for words in typed:
                    with self.subTest(skill=name, words=words[:20], values=bool(values)):
                        res = self.run_line(name, words, values)
                        self.assertEqual(res.returncode, 0, res.stderr)
                        self.assertNotIn("Traceback", res.stdout + res.stderr)
                        self.assertNotIn("error: unrecognized", res.stdout + res.stderr)

    def test_a_stray_word_is_named_and_the_output_still_shows(self):
        res = self.run_line("usage", "30 days")
        self.assertIn("last 30 days", res.stdout)
        self.assertIn("Ignored 'days': usage takes only a number of days and --json.", res.stdout)
        res = self.run_line("models", "--verbose", self.SET)
        self.assertIn("| haiku |", res.stdout)
        self.assertIn("Ignored '--verbose': models takes only reset, --json and the option flags.", res.stdout)

    def test_a_malformed_flag_gets_one_short_line(self):
        res = self.run_line("usage", "--json=1")
        self.assertEqual(res.stdout.strip().splitlines(),
                         ["model-router usage: argument --json: ignored explicit argument '1'. "
                          "It takes only a number of days and --json, so nothing was shown."])

    def test_under_json_a_malformed_flag_leaves_stdout_empty(self):
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["usage", "--json", "--json=1"]), 0)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("ignored explicit argument '1'", err.getvalue())

    def test_a_day_count_that_means_7_by_default_is_named(self):
        for typed in ("0", "-3", "٠٠"):
            with self.subTest(typed=typed):
                self.assertIn(f"Ignored {typed!r}", self.out("usage", typed))
        for typed in ("7", "07", "٧"):
            with self.subTest(typed=typed):
                self.assertNotIn("Ignored", self.out("usage", typed))

    def test_reset_counts_after_a_flag_like_word(self):
        for words in ("reset --force", "--force reset", "a --x reset"):
            with self.subTest(words=words):
                self.assertIn("availability", self.run_line("models", words, self.SET).stdout.splitlines()[0])

    def test_placeholder_text_typed_as_a_word_is_ignored(self):
        # Through sh a typed ${...} is a bad substitution before Model Router runs; the docs name that limit.
        self.assertIn("Ignored '${user_config.max_tier}'", self.out("usage", "${user_config.max_tier}"))
        self.assertIn("Cap: **none**", self.out("models", "--max-tier=${user_config.max_tier}"))

    def test_reset_typed_after_usage_is_named_as_ignored(self):
        self.assertIn("Ignored 'reset'", self.run_line("usage", "reset").stdout)


class DayCount(Sandbox):
    def test_a_day_count_is_a_positive_whole_number_capped_at_100_years(self):
        for typed, days in (("7", 7), ("30", 30), ("0030", 30), ("36500", 36500), ("1000000", 36500),
                            (HUGE, 36500), ("١٢", 12), ("٠٠٠٠٠٠٧", 7), ("٠٠٠٠٠١٢", 12), ("²", None),
                            ("0", None), ("٠", None), ("٠٠٠٠٠٠", None), ("-3", None), ("abc", None)):
            with self.subTest(typed=typed[:20]):
                self.assertEqual(cli._days(typed), days)
                days = days or 7
                self.assertIn(f"last {days} days", self.out("usage", typed))


class RouterColumn(Sandbox):
    def test_the_usage_table_says_the_router_rewrote_the_call_not_that_it_set_the_model(self):
        modpath.spawn({"session_id": "s1", "cwd": str(self.project), "parent_model": "claude-opus-5-5",
                       "kind": "subagent", "tool_input": {"subagent_type": "general-purpose",
                                                          "prompt": "list the files", "model": "haiku"}})
        row = next(r for r in ledger.read() if r.get("event") == "spawn")
        self.assertEqual((row["action"], row["model"], row["was"]), ("rewrite", "haiku", "haiku"))
        table = self.out("usage")
        self.assertIn("| Agent | Calls | Rewritten by router | Levels | Ran on |", table)
        self.assertNotIn("Model set by router", table)


if __name__ == "__main__":
    unittest.main()
