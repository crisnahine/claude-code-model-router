"""The docs assets are current and valid. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import io
import json
import re
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

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


BASELINES = {analyze.OPUS: ["defaults", "haiku", "haiku-high", "sonnet-via-opusplan"],
             analyze.SONNET: ["defaults", "sonnet-medium", "sonnet-high", "sonnet-via-opusplan"]}


def fixture_results(solved=3, cps=0.12344, runs=4, router="mine", cls="lookup"):
    """analyze.Results for a router and a few baselines, one task class, on each session."""
    rows, ids = [], BASELINES
    for model in ids:
        for config in [router, *ids[model]]:
            ok, cost = (solved, cps * solved / runs) if config == router else (runs, 0.2)
            rows += [{"config": config, "class": cls, "task": f"{cls}-1", "total_cost_usd": cost, "passed": i < ok,
                      "main_model": model, "started_at": "2030-01-01T10:00:00+00:00", "claude_version": "2.1.0",
                      "schema": analyze.run.ROW_SCHEMA}
                     for i in range(runs)]
    configs = [{"id": router, "plugin": True, "judged": True, "env": {}}, {"id": "defaults", "plugin": False, "env": {}},
               *({"id": c, "plugin": False, "env": {"X": "1"}} for c in sorted({c for v in ids.values() for c in v})
                 if c != "defaults")]
    return analyze.Results(files=[], rows=rows, stale=[], broken=[], aside=[], archive=[], configs=configs, tasks=[],
                           registry={}, routers=frozenset({router}), own_model=frozenset(), forced={})


def bench_data(**kwargs):
    return analyze.figures(fixture_results(**kwargs))


class DocsAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = docs_assets.snapshot()

    def build(self, **changes):
        return docs_assets.build(dataclasses.replace(self.snap, **changes))

    def with_changed_figures(self):
        """The snapshot's benchmark figures with the Opus router's pooled cost and its bugfix cost changed."""
        data = copy.deepcopy(self.snap.bench)
        opus = next(s for s in data["sessions"] if s["model"] == analyze.OPUS)
        opus["pooled"][opus["router"]]["cost_usd"] = 9.999 * opus["pooled"][opus["router"]]["solved"]
        bugfix = next(c for c in opus["classes"] if c["class"] == "bugfix")
        bugfix["configs"][opus["router"]]["cost_per_solved"] = 9.9999
        return data

    def test_the_page_heads_come_from_the_snapshots_page_index(self):
        pages = [p if p[0] != "docs/BENCHMARKS.md" else (p[0], "New title", *p[2:]) for p in self.snap.pages]
        files = self.build(pages=pages)
        self.assertIn('Next: <a href="BENCHMARKS.md">New title</a>', files["docs/CLAUDE_CODE_DOCS.md"])
        self.assertIn("\n# Benchmarks\n", files["docs/BENCHMARKS.md"])

    def test_build_makes_what_it_is_given_and_reads_no_file(self):
        files = self.build(texts={"README.md": self.snap.texts["README.md"]})
        self.assertEqual({p for p in files if not p.startswith("docs/assets/")}, {"README.md", "CITATION.cff"})

    def test_a_page_head_comes_from_the_page_index_and_replaces_the_old_one(self):
        pages = [p if p[0] != "docs/TEST_RESULTS.md" else (p[0], "New title", *p[2:]) for p in docs_pages.PAGES]
        old = docs_pages.with_head("docs/BENCHMARKS.md", "# Benchmarks\n")
        page = docs_pages.with_head("docs/BENCHMARKS.md", old, pages)
        self.assertIn('Next: <a href="TEST_RESULTS.md">New title</a>', page)
        self.assertNotIn("<img", page)
        self.assertEqual(page.count("<!-- Page head"), 1)
        self.assertTrue(page.endswith("<!-- End of page head. -->\n\n# Benchmarks\n"))

    def test_analyze_keeps_a_head_that_ends_where_the_index_says(self):
        head = docs_pages.page_head("docs/BENCHMARKS.md")
        self.assertTrue(head.startswith(docs_pages.HEAD_START) and head.endswith(docs_pages.HEAD_END))
        self.assertIs(analyze.HEAD_END, docs_pages.HEAD_END)

    def test_renaming_the_router_changes_no_figure_but_its_own_name(self):
        old, new = bench_data(), bench_data(router="renamed")
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

    def test_a_stale_file_fails_the_check_and_a_write_fixes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("old", encoding="utf-8")
            self.assertEqual(docs_assets.sync({"README.md": "new"}, True, root), ["README.md: out of date, run python3 tools/docs_assets.py"])
            self.assertEqual((root / "README.md").read_text(encoding="utf-8"), "old")
            self.assertEqual(docs_assets.sync({"README.md": "new"}, False, root), [])
            self.assertEqual((root / "README.md").read_text(encoding="utf-8"), "new")
            self.assertEqual(docs_assets.sync({"README.md": "new"}, True, root), [])

    def test_the_figures_from_the_committed_results(self):
        f = docs_assets.bench_figures(self.snap.bench)
        self.assertEqual((f["runs"], f["usd"], f["dates"], f["opus.commit"]),
                         ("1,120", "$134.97", "2026-11-22 to 2026-11-23", "780ee86"))
        self.assertEqual(f["opus.commit_code"], "`780ee86`")
        self.assertEqual((f["opus.pooled.router"], f["opus.pooled.defaults"], f["opus.pooled_less.defaults"]),
                         ("$0.121", "$0.151", "20"))
        # No stored class has a verdict, so nothing is below, level or behind.
        self.assertEqual((f["opus.status.REFUSED"], f["sonnet.status.REFUSED"], f["opus.status.WIN"]), ("8", "8", "0"))
        self.assertEqual((f["opus.below.defaults"], f["sonnet.solved.router"], f["sonnet.solved.defaults"]),
                         ("0", "69 of 70", "68 of 70"))
        for gone in ("opus.level.defaults", "opus.behind.min", "opus.less.defaults.min"):
            self.assertNotIn(gone, f)

    def test_the_figures_are_keyed_by_role(self):
        f = docs_assets.bench_figures(self.snap.bench)
        self.assertEqual((f["opus.pooled.best"], f["opus.pooled.strongest"], f["opus.solved.best"]),
                         (f["opus.pooled.sonnet-via-opusplan"], f["opus.pooled.fable-high"], "69 of 70"))
        self.assertEqual((f["opus.saturated"], f["sonnet.saturated"], f["opus.router_code"]), ("5", "0", "`plugin-next`"))
        self.assertEqual((f["tasks"], f["tasks_total"]), ("14", "35"))
        self.assertFalse([k for k in f if "plugin-next" in k])

    def test_the_figures_do_not_read_the_wording_of_a_reason(self):
        data = copy.deepcopy(self.snap.bench)
        for session in data["sessions"]:
            for c in session["classes"]:
                c["reason"] = "reworded"
        self.assertEqual(docs_assets.bench_figures(data), docs_assets.bench_figures(self.snap.bench))

    def test_the_trace_text_lists_every_baseline_with_its_interval(self):
        data = bench_data(solved=9, cps=0.2, runs=10, cls="root-cause")
        trace = docs_assets.bench_figures(data)["opus.trace"]
        verdict = r"(WIN|LOSE|no difference shown)"
        self.assertRegex(trace, rf"^root cause {verdict} \(router \$0\.200, 9 of 10 solved; `\w[\w-]*` \$0\.200 "
                                rf"\({verdict}, [+-]\d\.\d{{3}} to [+-]\d\.\d{{3}}\)")
        for cfg in ("defaults", "haiku", "haiku-high", "sonnet-via-opusplan"):
            self.assertRegex(trace, rf"`{cfg}` \$0\.200 \({verdict}, [+-]\d\.\d{{3}} to [+-]\d\.\d{{3}}\)")

    def test_a_changed_figure_makes_the_docs_stale(self):
        problems = docs_assets.sync(self.build(bench=self.with_changed_figures()), True)
        for path in ("README.md", "docs/FAQ.md", "site/index.html", "site/llms.txt", "docs/TRACEABILITY.md"):
            self.assertIn(f"{path}: out of date, run python3 tools/docs_assets.py", problems)

    def test_renaming_the_router_changes_no_file_but_the_trace_in_traceability(self):
        base = analyze.load(analyze.default_paths())
        swap = lambda name: "renamed" if name == "plugin-next" else name  # noqa: E731
        results = dataclasses.replace(
            base, legacy=[dict(r, config=swap(r["config"])) for r in base.legacy],
            configs=[dict(c, id=swap(c["id"])) for c in base.configs], routers=frozenset(map(swap, base.routers)))
        with unittest.mock.patch.object(analyze, "LEGACY_ROUTER", "renamed"):
            renamed = analyze.figures(results, legacy=True)
        before, after = self.build(), self.build(bench=renamed)
        self.assertEqual({p for p in before if before[p] != after[p]}, {"docs/TRACEABILITY.md"})
        self.assertIn("<!-- bench:opus.router_code -->`renamed`<!-- /bench -->", after["docs/TRACEABILITY.md"])

    def test_an_unknown_figure_stops_the_build(self):
        with self.assertRaises(SystemExit) as raised:
            docs_assets.with_figures("x.md", "a <!-- bench:nope -->1<!-- /bench --> b", {})
        self.assertIn("x.md: unknown benchmark figure nope", str(raised.exception))

    def patched_build(self):
        return self.build(bench=self.with_changed_figures())

    def test_no_figure_span_sits_inside_backticks(self):
        for path in (*docs_assets.FIGURE_PAGES, "site/index.html"):
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"`<!-- bench:|<!-- /bench -->`", text), path)

    def test_the_site_faq_shows_figures_without_marks(self):
        page = self.patched_build()["site/index.html"]
        block = page.split(docs_assets.FAQ_HTML[0], 1)[1].split(docs_assets.FAQ_HTML[1], 1)[0]
        self.assertNotIn("<!-- bench:", block)
        self.assertIn("$9.999", block)

    def test_the_claude_code_floor_comes_from_the_manifest(self):
        files = self.build(claude_code="3.0.0")
        for path in ("README.md", "site/index.html", "site/llms.txt", "docs/assets/badge-claude-code.svg"):
            self.assertIn("3.0.0 or later", files[path], path)
            self.assertNotRegex(files[path], r"\b2\.1\.\d+ or later", path)

    def test_the_site_asks_for_no_asset_that_docs_assets_lacks(self):
        self.assertEqual(docs_assets.site_asset_problems('<img src="assets/ladder.svg">'), [])
        self.assertEqual(docs_assets.site_asset_problems('<meta content="https://x.io/r/assets/gone.png">'),
                         ["site/index.html: assets/gone.png is not in docs/assets"])

    def test_the_site_carries_no_crawler_file_and_no_release_date_as_a_page_date(self):
        files = self.build(released="2030-01-01")
        self.assertNotIn("site/robots.txt", files)
        self.assertNotIn("lastmod", files["site/sitemap.xml"])
        self.assertNotIn("dateModified", files["site/index.html"])

    def test_the_redo_diagram_names_the_redo_mark(self):
        source = self.build()["docs/assets/redo-escalation.svg"]
        self.assertIn('"Redo:"', source)
        self.assertNotIn("issued again", source)

    def test_no_generated_svg_animates(self):
        for path, body in self.build().items():
            if path.endswith(".svg"):
                self.assertNotIn("animation", body, path)

    def test_the_version_badge_alt_text_follows_the_plugin_version(self):
        files = self.build(version="9.8.7", released="2030-01-01")
        for path in ("README.md", "site/index.html"):
            self.assertIn('badge-version.svg', files[path], path)
            self.assertIn('alt="version 9.8.7"', files[path], path)

    def test_the_sites_structured_data_parses_and_follows_the_plugin(self):
        def apps(page):
            blocks = [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)]
            self.assertEqual(len(blocks), 1)
            return next(n for n in blocks[0]["@graph"] if n["@type"] == "SoftwareApplication")
        self.assertEqual(apps(self.build()["site/index.html"])["softwareVersion"], docs_assets.version())
        app = apps(self.build(version="9.8.7", released="2030-01-01", claude_code="3.0.0")["site/index.html"])
        self.assertEqual((app["softwareVersion"], app["datePublished"]), ("9.8.7", "2026-11-23"))
        self.assertTrue(app["softwareRequirements"].startswith("Claude Code 3.0.0 or later;"))
        self.assertFalse({"dateModified", "downloadUrl", "operatingSystem"} & set(app))
        ladder = "Five levels: " + ", ".join(f"{lv} on {m} at {e}" for lv, m, e, *_ in docs_assets.LADDER)
        self.assertIn(ladder, app["featureList"])

    def test_every_structured_data_reference_names_a_node(self):
        graph = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>',
                                     self.build()["site/index.html"], re.S).group(1))["@graph"]
        refs = re.findall(r'\{"@id": "([^"]+)"\}', json.dumps(graph))
        self.assertTrue(refs)
        self.assertLessEqual(set(refs), {n["@id"] for n in graph})

    def test_a_site_rewrite_whose_text_is_gone_stops_the_build(self):
        texts = dict(self.snap.texts)
        texts["site/index.html"] = texts["site/index.html"].replace('"softwareVersion"', '"version"')
        with self.assertRaises(SystemExit) as raised:
            self.build(texts=texts)
        self.assertIn("softwareVersion", str(raised.exception))

    def test_an_invalid_or_inaccessible_svg_is_reported(self):
        ok = '<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="a"><title>a</title></svg>'
        self.assertEqual(docs_assets.problems_in_svg("ok.svg", ok), [])
        self.assertIn("not valid XML", docs_assets.problems_in_svg("a.svg", "<svg>")[0])
        self.assertIn("aria-label", docs_assets.problems_in_svg("b.svg", ok.replace(' aria-label="a"', ""))[0])


class BrokenInputs(unittest.TestCase):
    """A missing marker or a torn file stops the tools with the file and what is missing, not a traceback."""

    def stops(self, call, *named):
        with self.assertRaises(SystemExit) as caught:
            call()
        for text in named:
            self.assertIn(text, str(caught.exception))

    def test_a_page_head_with_no_end_marker(self):
        self.stops(lambda: docs_pages.with_head("docs/FAQ.md", f"{docs_pages.HEAD_START} cut off\n# FAQ\n"),
                   "docs/FAQ.md", docs_pages.HEAD_END.strip())

    def test_cards_with_no_end_marker(self):
        self.stops(lambda: docs_assets.with_cards(f"# Docs\n{docs_assets.CARDS_START}\n<table>\n"),
                   "docs/README.md", docs_assets.CARDS_END)

    def test_a_site_page_with_a_marker_missing(self):
        page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        site = lambda text: docs_assets.site_files("", {}, {"site/index.html": text}, "0.0.0", "2030-01-01", "2.1.0")  # noqa: E731
        self.stops(lambda: site(docs_assets.SITE_URL.sub("", page)), "site/index.html", "SITE_URL")
        for mark in docs_assets.FAQ_HTML:
            self.stops(lambda: site(page.replace(mark, "")), "site/index.html", mark)

    def test_a_class_the_router_never_ran(self):
        results = fixture_results()
        extra = [dict(r, **{"class": "bugfix", "task": "bugfix-1"}) for r in results.rows if r["config"] != "mine"]
        data = analyze.figures(dataclasses.replace(results, rows=results.rows + extra))
        self.stops(lambda: docs_assets.bench_figures(data), "no mine runs", "bugfix")

    def test_a_results_file_with_a_torn_last_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            torn = Path(tmp) / "results" / "a-lookup.jsonl"
            torn.parent.mkdir()
            torn.write_text('{"task": "lookup-todo-count"}\n{"task": "look', encoding="utf-8")
            self.stops(lambda: analyze.load([torn]), f"{torn.resolve()}:2")


if __name__ == "__main__":
    unittest.main()
