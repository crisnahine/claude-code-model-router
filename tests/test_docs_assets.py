"""The docs assets are current and valid. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "bench"))
import analyze  # noqa: E402
import docs_assets  # noqa: E402
import docs_pages  # noqa: E402


def check() -> tuple:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = docs_assets.main(["--check"])
    return code, err.getvalue()


def bench_summary(solved=3, cps=0.12344, runs=4):
    """A summary_data dict with one session per BENCH_SERIES key and one class that ran every series config."""
    sessions = []
    for model, (_, series) in docs_assets.BENCH_SERIES.items():
        configs = {c: {"runs": runs, "solved": runs, "cost_per_solved": 0.2} for c, _, _ in series}
        configs["plugin-next"] = {"runs": runs, "solved": solved, "cost_per_solved": cps}
        sessions.append({"model": model, "router": "plugin-next", "plugin_ref": None, "pooled": {},
                         "classes": [{"class": "lookup", "status": "PASS", "configs": configs}]})
    return {"runs": 12, "usd": 1.0, "dates": ["2030-01-01", "2030-01-02"], "claude_versions": ["2.1.0"],
            "sessions": sessions}


class DocsAssets(unittest.TestCase):
    def test_a_page_head_comes_from_the_page_index_and_replaces_the_old_one(self):
        pages = [p if p[0] != "docs/BENCHMARKS.md" else (*p[:3], "A new question?", *p[4:]) for p in docs_pages.PAGES]
        old = docs_pages.with_head("docs/BENCHMARKS.md", "# Benchmarks\n")
        page = docs_pages.with_head("docs/BENCHMARKS.md", old, pages)
        self.assertIn('alt="Benchmarks: A new question?"', page)
        self.assertEqual(page.count("<!-- Page head"), 1)
        self.assertTrue(page.endswith("<!-- End of page head. -->\n\n# Benchmarks\n"))

    def test_analyze_keeps_a_head_that_ends_where_the_index_says(self):
        head = docs_pages.page_head("docs/BENCHMARKS.md")
        self.assertTrue(head.startswith(docs_pages.HEAD_START) and head.endswith(docs_pages.HEAD_END))
        self.assertIs(analyze.HEAD_END, docs_pages.HEAD_END)

    def test_the_chart_draws_from_the_summary_not_the_page(self):
        with mock.patch.object(docs_assets, "bench_summary", return_value=bench_summary()):
            svg = docs_assets.bench_chart()
        self.assertIn("$0.1234 (3 of 4)", svg)
        self.assertIn("12 runs on 2030-01-01", svg)

    def test_a_cell_with_nothing_solved_is_not_drawn_as_not_run(self):
        with mock.patch.object(docs_assets, "bench_summary", return_value=bench_summary(0, None)):
            svg = docs_assets.bench_chart()
        self.assertIn("none solved (0 of 4)", svg)
        self.assertNotIn("not run", svg)

    def test_a_series_configuration_with_no_runs_stops_the_build(self):
        data = bench_summary()
        del data["sessions"][0]["classes"][0]["configs"]["defaults"]
        with mock.patch.object(docs_assets, "bench_summary", return_value=data):
            with self.assertRaises(SystemExit) as caught:
                docs_assets.bench_chart()
        self.assertIn(f"no defaults runs for {data['sessions'][0]['model']}; update BENCH_SERIES", str(caught.exception))

    def test_the_committed_assets_match_the_script(self):
        code, err = check()
        self.assertEqual(code, 0, "python3 tools/docs_assets.py --check:\n" + err)

    def test_every_page_under_docs_research_is_in_PAGES(self):
        listed = {row[0] for row in docs_pages.PAGES}
        found = {p.relative_to(ROOT).as_posix() for p in (ROOT / "docs" / "research").glob("*.md")}
        self.assertEqual(found - listed, set())

    def test_a_stale_file_fails_the_check(self):
        with mock.patch.object(docs_assets, "build", return_value={"README.md": "stale"}):
            code, err = check()
        self.assertEqual(code, 1)
        self.assertIn("README.md: out of date", err)

    def test_the_figures_from_the_committed_results(self):
        f = docs_assets.bench_figures(docs_assets.bench_summary())
        self.assertEqual((f["runs"], f["usd"], f["dates"], f["opus.commit"]),
                         ("1,120", "$134.97", "2026-11-22 to 2026-11-23", "780ee86"))
        self.assertEqual(f["opus.commit_code"], "`780ee86`")
        self.assertEqual((f["opus.pooled.plugin-next"], f["opus.pooled.defaults"], f["opus.pooled_less.defaults"]),
                         ("$0.121", "$0.151", "20"))
        self.assertEqual((f["opus.below.defaults"], f["opus.less.defaults.min"], f["opus.less.defaults.max"],
                          f["opus.level.defaults"]), ("5", "18", "32", "design"))
        self.assertEqual((f["opus.behind.min"], f["opus.behind.max"], f["opus.status.FAIL"]), ("60", "144", "6"))
        self.assertEqual((f["sonnet.status.PASS"], f["sonnet.behind.min"], f["sonnet.behind.max"],
                          f["sonnet.solved.plugin-next"], f["sonnet.solved.defaults"]),
                         ("0", "7", "63", "69 of 70", "68 of 70"))
        self.assertNotIn("sonnet.pass_classes", f)

    def test_the_trace_text_lists_every_baseline_with_its_interval(self):
        data = bench_summary()
        del data["sessions"][1:]
        session = data["sessions"][0]
        session["pooled"] = {"plugin-next": {"runs": 10, "solved": 9, "cost_usd": 1.0},
                             "defaults": {"runs": 10, "solved": 10, "cost_usd": 1.0}}
        session["classes"] = [{
            "class": "root-cause", "status": "FAIL", "best_baseline": "defaults",
            "configs": {"plugin-next": {"runs": 10, "solved": 9, "cost_per_solved": 0.2},
                        "defaults": {"runs": 10, "solved": 10, "cost_per_solved": 0.1}},
            "differences": {"defaults": (0.05, 0.15)}}]
        f = docs_assets.bench_figures(data)
        self.assertEqual(f["opus.trace"],
                         "root cause FAIL (router $0.200, 9 of 10 solved; `defaults` $0.100 (+0.050 to +0.150))")

    def test_a_changed_figure_makes_the_docs_stale(self):
        real = docs_assets.bench_figures(docs_assets.bench_summary())
        with mock.patch.object(docs_assets, "bench_figures", return_value={**real, "opus.pooled.plugin-next": "$9.999"}):
            code, err = check()
        self.assertEqual(code, 1)
        for path in ("README.md", "docs/FAQ.md", "site/index.html", "docs/TRACEABILITY.md"):
            self.assertIn(f"{path}: out of date", err)

    def test_an_unknown_figure_stops_the_build(self):
        with self.assertRaises(SystemExit) as raised:
            docs_assets.with_figures("x.md", "a <!-- bench:nope -->1<!-- /bench --> b", {})
        self.assertIn("x.md: unknown benchmark figure nope", str(raised.exception))
        with self.assertRaises(SystemExit) as raised:
            docs_assets.fill("a {nope} b", {}, "alt")
        self.assertIn("alt: unknown benchmark figure nope", str(raised.exception))

    def patched_build(self):
        real = docs_assets.bench_figures(docs_assets.bench_summary())
        with mock.patch.object(docs_assets, "bench_figures", return_value={**real, "opus.pooled.plugin-next": "$9.999",
                                                                          "opus.cell.bugfix.plugin-next": "$9.9999"}):
            return docs_assets.build()

    def test_the_figure_alt_text_follows_the_figures(self):
        files = self.patched_build()
        for path in ("README.md", "site/index.html"):
            alt = re.search(r'benchmark\.svg"[^>]*? alt="([^"]*)"', files[path]).group(1)
            self.assertIn("bugfix $9.9999 against", alt, path)

    def test_no_figure_span_sits_inside_backticks(self):
        for path in ("README.md", "docs/FAQ.md", "site/index.html", "docs/TRACEABILITY.md"):
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"`<!-- bench:|<!-- /bench -->`", text), path)

    def test_the_site_faq_shows_figures_without_marks(self):
        page = self.patched_build()["site/index.html"]
        for marks in (docs_assets.FAQ_HTML, docs_assets.FAQ_LD):
            block = page.split(marks[0], 1)[1].split(marks[1], 1)[0]
            self.assertNotIn("<!-- bench:", block)
            self.assertIn("$9.999", block)

    def test_the_version_badge_alt_text_follows_the_plugin_version(self):
        with mock.patch.object(docs_assets, "version", return_value="9.8.7"), \
                mock.patch.object(docs_assets, "release_date", return_value="2030-01-01"):
            files = docs_assets.build()
        for path in ("README.md", "site/index.html"):
            self.assertIn('badge-version.svg', files[path], path)
            self.assertIn('alt="version 9.8.7"', files[path], path)

    def test_an_invalid_or_inaccessible_svg_is_reported(self):
        ok = ('<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="a"><title>a</title>'
              "<style>@keyframes k{}@media (prefers-reduced-motion: reduce){*{animation:none}}</style></svg>")
        self.assertEqual(docs_assets.problems_in_svg("ok.svg", ok), [])
        self.assertIn("not valid XML", docs_assets.problems_in_svg("a.svg", "<svg>")[0])
        self.assertIn("aria-label", docs_assets.problems_in_svg("b.svg", ok.replace(' aria-label="a"', ""))[0])
        self.assertIn("reduced-motion", docs_assets.problems_in_svg("c.svg", ok.replace("prefers-reduced", "x"))[0])


if __name__ == "__main__":
    unittest.main()
