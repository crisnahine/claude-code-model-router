"""Tests for tools/upstream_check.py, the maintainer's daily comparison with compat/baseline.json."""
from __future__ import annotations

import http.client
import http.server
import io
import json
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT / "tools"))
import upstream_check as up  # noqa: E402

DOCS = "https://code.claude.com/docs/en/"
TABLE = ("| Model alias | Behavior |\n| --- | --- |\n| **`default`** | x |\n| **`best`** | x |\n"
         "| **`fable`** | x |\n| **`sonnet`** | x |\n| **`opus`** | x |\n| **`haiku`** | x |\n"
         "| **`sonnet[1m]`** | x |\n| **`opusplan`** | x |\n| **`saga`** | x |\n")
INDEX = (FIXTURES / "llms.txt").read_text(encoding="utf-8") + f"- [Model configuration]({up.MODEL_CONFIG_PAGE}): Aliases.\n"


def fake(url: str) -> str:
    if url == up.CHANGELOG_PAGE:
        return (FIXTURES / "changelog.md").read_text(encoding="utf-8")
    if url == up.DOCS_INDEX:
        return INDEX
    if url == up.DEPRECATIONS_PAGE:
        return (FIXTURES / "model-deprecations.md").read_text(encoding="utf-8")
    return TABLE if url == up.MODEL_CONFIG_PAGE else f"the text of {url}"


class Parsers(unittest.TestCase):
    def test_parse_fixtures(self):
        releases = up.parse_changelog((FIXTURES / "changelog.md").read_text(encoding="utf-8"))
        self.assertEqual(releases[:2], [{"version": "2.1.331", "date": "November 23, 2026"},
                                        {"version": "2.1.330", "date": "November 20, 2026"}])
        self.assertEqual(len(releases), 3)
        self.assertIn(DOCS + "hooks.md", up.parse_llms_index(INDEX))
        life = up.parse_deprecations((FIXTURES / "model-deprecations.md").read_text(encoding="utf-8"))
        self.assertEqual(life["claude-opus-5-5"]["state"], "Active")
        self.assertEqual(life["claude-sonnet-4-5-20250929"],
                         {"state": "Deprecated", "deprecated": "September 30, 2026", "retires": "November 30, 2026",
                          "replacement": "claude-sonnet-5-5"})

    def test_a_release_without_a_date_line_still_counts(self):
        self.assertEqual(up.parse_changelog("**2.1.9**\n\n* Fixed a thing\n\n**Added**\n"),
                         [{"version": "2.1.9", "date": ""}])

    def test_aliases_and_families(self):
        aliases = up.parse_model_aliases(TABLE)
        self.assertIn("sonnet[1m]", aliases)
        self.assertEqual(up.model_families(aliases), ["fable", "haiku", "opus", "saga", "sonnet"])
        self.assertGreater(up.version_key("2.1.330"), up.version_key("2.1.99"))

    def test_the_pages_routing_rests_on_are_watched(self):
        # The effort order, the cache rules, fast mode, the advisor and headless runs all decide what the router may do.
        for slug in ("env-vars", "prompt-caching", "fast-mode", "advisor", "headless", "best-practices", "workflows",
                     "hooks", "plugins/manifest-reference", "plugins/mods/api"):
            self.assertTrue(up.watched(DOCS + slug + ".md"), slug)
        self.assertFalse(up.watched(DOCS + "voice.md"))
        for url in up.PLATFORM_PAGES:
            self.assertTrue(up.watched(url), url)

    def test_the_shipped_baseline_lists_the_registry_models(self):
        baseline = json.loads((ROOT / "compat" / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(set(up.model_families(baseline["aliases"])), set(json.loads(up.REGISTRY.read_text())["models"]))
        self.assertTrue(baseline["watched"] and all(up.watched(url) for url in baseline["watched"]))


class Diff(unittest.TestCase):
    def test_a_body_change_to_a_watched_page_is_flagged_and_other_pages_only_when_added_or_removed(self):
        reviewed = {"claude_code_version": "2.1.100", "pages": [DOCS + "hooks.md", DOCS + "voice.md"],
                    "watched": {DOCS + "hooks.md": "a"}, "aliases": ["haiku", "sonnet", "opus", "fable"]}
        live = {"releases": [{"version": "2.1.101", "date": ""}, {"version": "2.1.99", "date": ""}],
                "pages": [DOCS + "hooks.md", DOCS + "voice.md", DOCS + "plugins/new.md", DOCS + "other.md"],
                "watched": {DOCS + "hooks.md": "b", DOCS + "plugins/new.md": "a"},
                "aliases": up.parse_model_aliases(TABLE)}
        changes = up.diff(reviewed, live)
        self.assertEqual([r["version"] for r in changes["new_releases"]], ["2.1.101"])
        self.assertEqual(changes["pages_added"], [DOCS + "other.md", DOCS + "plugins/new.md"])
        self.assertEqual(changes["watched_pages_touched"], [DOCS + "hooks.md", DOCS + "plugins/new.md"])
        self.assertEqual(changes["new_model_families"], ["saga"])
        self.assertEqual(changes["removed_model_families"], [])
        self.assertTrue(up.has_pending(up.diff(reviewed, {"aliases": live["aliases"], "watched": reviewed["watched"]})))
        self.assertEqual(up.diff({}, {"aliases": ["haiku", "sonnet", "opus"]})["removed_model_families"], ["fable"])
        self.assertEqual(up.diff({}, {"aliases": []})["removed_model_families"], [])

    def test_retiring_models_are_flagged_once_and_a_first_review_floods_nothing(self):
        old = {"lifecycle": {"claude-sonnet-4-5-20250929": {"state": "Active"}, "claude-opus-5-5": {"state": "Active"}}}
        new = {"lifecycle": {"claude-sonnet-4-5-20250929": {"state": "Deprecated", "retires": "November 30, 2026"},
                             "claude-opus-5-5": {"state": "Active"}}}
        changes = up.diff(old, new)
        self.assertEqual([m["model"] for m in changes["newly_deprecated"]], ["claude-sonnet-4-5-20250929"])
        self.assertTrue(up.has_pending(changes))
        self.assertIn("retires November 30, 2026", up.report(changes))
        self.assertEqual(up.diff({}, new)["newly_deprecated"], [])
        self.assertFalse(up.has_pending(up.diff(new, new)))
        self.assertIn("Nothing to review.", up.report(up.diff(new, new)))


class Command(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.baseline, self.changes = Path(self.tmp.name) / "baseline.json", Path(self.tmp.name) / "changes.json"

    def run_check(self, *argv: str, get=fake) -> str:
        with mock.patch.object(up, "http_get", side_effect=get), redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(up.main(["--baseline", str(self.baseline), *argv]), 0)
        return buf.getvalue()

    def pending(self) -> bool:
        return json.loads(self.changes.read_text())["pending"]

    def test_it_writes_whether_anything_is_pending(self):
        self.assertIn("Baseline written for Claude Code 2.1.331", self.run_check("--write"))
        self.run_check("--json-out", str(self.changes))
        self.assertIs(self.pending(), False)
        self.baseline.write_text(json.dumps({**json.loads(self.baseline.read_text()), "claude_code_version": "2.1.330"}))
        self.assertIn("- 2.1.331 (November 23, 2026)", self.run_check("--json-out", str(self.changes)))
        self.assertIs(self.pending(), True)

    def test_a_watched_page_whose_text_changed_is_pending(self):
        self.run_check("--write")
        out = self.run_check("--json-out", str(self.changes),
                             get=lambda url: "new text" if url == DOCS + "hooks.md" else fake(url))
        self.assertIs(self.pending(), True)
        self.assertIn(DOCS + "hooks.md", out)

    def failed_check(self, *argv: str, get) -> tuple[int, str, str]:
        with mock.patch.object(up, "http_get", side_effect=get), redirect_stdout(io.StringIO()) as out, \
                redirect_stderr(io.StringIO()) as err:
            code = up.main(["--baseline", str(self.baseline), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_an_empty_feed_is_an_error_not_data(self):
        for feed in (up.CHANGELOG_PAGE, up.DOCS_INDEX, up.MODEL_CONFIG_PAGE, up.DEPRECATIONS_PAGE):
            def down(url, feed=feed):
                return "<html>Down for maintenance</html>" if url == feed else fake(url)

            with self.subTest(feed=feed):
                self.assertEqual(self.failed_check("--write", get=down)[0], 1)
                self.assertEqual(self.failed_check("--json-out", str(self.changes), get=down)[0], 1)
            self.assertFalse(self.baseline.exists())
            self.assertFalse(self.changes.exists())

    def test_a_failed_check_is_one_line_in_the_report_and_fails_ci(self):
        def offline(url):
            raise OSError("no network")

        self.run_check("--write")
        code, out, err = self.failed_check(get=offline)
        self.assertEqual((code, out, err), (0, "Upstream check failed: no network\n", ""))
        code, out, err = self.failed_check("--json-out", str(self.changes), get=offline)
        self.assertEqual((code, out, err), (1, "", "Upstream check failed: no network\n"))


class Requests(unittest.TestCase):
    def serve(self, raw: dict, delay: float) -> str:
        """Serves each path's raw bytes one at a time, `delay` seconds apart, or a dripped 200 body."""

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path not in raw:
                    self.send_response(200)
                    self.end_headers()
                try:
                    for byte in raw.get(self.path, b"x" * 1000):
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
        base = self.serve({}, delay=0.05)
        started = time.monotonic()
        with mock.patch.object(up, "HTTP_TIMEOUT", 0.5), self.assertRaises(OSError):
            up.http_get(base + "/slow")
        self.assertLess(time.monotonic() - started, 2)

    def test_a_body_cut_short_is_a_failed_request(self):
        base = self.serve({"/cut": b"HTTP/1.0 200 OK\r\nContent-Length: 100\r\n\r\n" + b"x" * 10}, delay=0)
        with self.assertRaises(http.client.IncompleteRead):
            up.http_get(base + "/cut")


if __name__ == "__main__":
    unittest.main()
