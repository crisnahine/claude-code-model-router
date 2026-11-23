"""The docs assets are current and valid. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import dataclasses
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


def fixture_results(solved=3, cps=0.12344, runs=4, router="mine", cls="lookup"):
    """analyze.Results for a router and every configuration BENCH_SERIES names, one task class, on each session."""
    rows, ids = [], {}
    for model, (_, series) in docs_assets.BENCH_SERIES.items():
        ids[model] = [c for c, _, _ in series if c != "router"]
        for config in [router, *ids[model]]:
            ok, cost = (solved, cps * solved / runs) if config == router else (runs, 0.2)
            rows += [{"config": config, "class": cls, "task": f"{cls}-1", "total_cost_usd": cost, "passed": i < ok,
                      "main_model": model, "started_at": "2030-01-01T10:00:00+00:00", "claude_version": "2.1.0"}
                     for i in range(runs)]
    configs = [{"id": router, "plugin": True, "env": {}}, {"id": "defaults", "plugin": False, "env": {}},
               *({"id": c, "plugin": False, "env": {"X": "1"}} for c in sorted({c for v in ids.values() for c in v})
                 if c != "defaults")]
    return analyze.Results(files=[], rows=rows, stale=[], broken=[], aside=[], archive=[], configs=configs, tasks=[],
                           registry={}, routers=frozenset({router}), own_model=frozenset(), forced={})


def bench_data(**kwargs):
    return analyze.figures(fixture_results(**kwargs))


def committed_data():
    return analyze.figures(analyze.load(analyze.default_paths()))


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

    def test_the_chart_draws_from_the_figures_not_the_page(self):
        data = bench_data()
        svg = docs_assets.bench_chart(data)
        self.assertIn("$0.1234 (3 of 4)", svg)
        self.assertIn(f"{data['runs']} runs on 2030-01-01", svg)

    def test_a_cell_with_nothing_solved_is_not_drawn_as_not_run(self):
        svg = docs_assets.bench_chart(bench_data(solved=0))
        self.assertIn("none solved (0 of 4)", svg)
        self.assertNotIn("not run", svg)

    def test_a_series_configuration_with_no_runs_stops_the_build(self):
        results = fixture_results()
        results = dataclasses.replace(results, rows=[r for r in results.rows if r["config"] != "defaults"])
        data = analyze.figures(results)
        with self.assertRaises(SystemExit) as caught:
            docs_assets.bench_chart(data)
        self.assertIn(f"no defaults runs for {data['sessions'][0]['model']}; update BENCH_SERIES", str(caught.exception))

    def test_renaming_the_router_changes_no_figure_but_its_own_name(self):
        old, new = bench_data(), bench_data(router="renamed")
        self.assertEqual(docs_assets.bench_chart(old), docs_assets.bench_chart(new))
        a, b = docs_assets.bench_figures(old), docs_assets.bench_figures(new)
        named = lambda f: {k: v for k, v in f.items() if not k.endswith((".router_code", ".trace"))}  # noqa: E731
        self.assertEqual(named(a), named(b))
        self.assertEqual(b["opus.router_code"], "`renamed`")
        self.assertFalse([k for k in b if "renamed" in k])

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
        f = docs_assets.bench_figures(committed_data())
        self.assertEqual((f["runs"], f["usd"], f["dates"], f["opus.commit"]),
                         ("1,120", "$134.97", "2026-11-22 to 2026-11-23", "780ee86"))
        self.assertEqual(f["opus.commit_code"], "`780ee86`")
        self.assertEqual((f["opus.pooled.router"], f["opus.pooled.defaults"], f["opus.pooled_less.defaults"]),
                         ("$0.121", "$0.151", "20"))
        self.assertEqual((f["opus.below.defaults"], f["opus.less.defaults.min"], f["opus.less.defaults.max"],
                          f["opus.level.defaults"]), ("5", "18", "32", "design"))
        self.assertEqual((f["opus.behind.min"], f["opus.behind.max"], f["opus.status.FAIL"]), ("60", "144", "6"))
        self.assertEqual((f["sonnet.status.PASS"], f["sonnet.behind.min"], f["sonnet.behind.max"],
                          f["sonnet.solved.router"], f["sonnet.solved.defaults"]),
                         ("0", "7", "63", "69 of 70", "68 of 70"))
        self.assertNotIn("sonnet.pass_classes", f)

    def test_the_figures_are_keyed_by_role(self):
        f = docs_assets.bench_figures(committed_data())
        self.assertEqual((f["opus.pooled.best"], f["opus.pooled.strongest"], f["opus.solved.best"]),
                         (f["opus.pooled.opusplan"], f["opus.pooled.fable-high"], "69 of 70"))
        self.assertEqual((f["opus.saturated"], f["sonnet.saturated"], f["opus.router_code"]), ("5", "0", "`plugin-next`"))
        self.assertFalse([k for k in f if "plugin-next" in k])

    def test_the_trace_text_lists_every_baseline_with_its_interval(self):
        data = bench_data(solved=9, cps=0.2, runs=10, cls="root-cause")
        trace = docs_assets.bench_figures(data)["opus.trace"]
        self.assertRegex(trace, r"^root cause (FAIL|PARTIAL) \(router \$0\.200, 9 of 10 solved; `\w[\w-]*` \$0\.200 "
                                r"\([+-]\d\.\d{3} to [+-]\d\.\d{3}\)")
        for cfg in ("defaults", "haiku", "haiku-high", "opusplan"):
            self.assertRegex(trace, rf"`{cfg}` \$0\.200 \([+-]\d\.\d{{3}} to [+-]\d\.\d{{3}}\)")

    def test_a_changed_figure_makes_the_docs_stale(self):
        real = docs_assets.bench_figures(docs_assets.bench_summary())
        with mock.patch.object(docs_assets, "bench_figures", return_value={**real, "opus.pooled.router": "$9.999"}):
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
        with mock.patch.object(docs_assets, "bench_figures", return_value={**real, "opus.pooled.router": "$9.999",
                                                                          "opus.cell.bugfix.router": "$9.9999"}):
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
