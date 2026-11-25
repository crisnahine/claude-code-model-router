"""The docs assets are current and valid. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
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


def without_fences(text: str) -> str:
    return re.sub(r"^```.*?^```", "", text, flags=re.M | re.S)


def links(text: str) -> list:
    """The relative link targets in a Markdown page, outside code."""
    found = re.findall(r'\]\(([^)\s]+)\)|(?:href|src)="([^"]+)"', re.sub(r"`[^`\n]*`", "", without_fences(text)))
    return [t for pair in found for t in pair if t and not re.match(r"[a-z]+:", t)]


def anchors(text: str) -> set:
    """The anchors GitHub gives a Markdown page: one per heading, numbered when repeated, and each <a id>."""
    found, seen = set(re.findall(r'<a (?:id|name)="([^"]+)"', text)), {}
    for heading in re.findall(r"^#{1,6} (.+?)#*$", without_fences(text), re.M):
        heading = re.sub(r"\[([^\]]*)\]\([^)]*\)|<[^>]+>|`", r"\1", heading.strip())
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        found.add(slug if slug not in seen else f"{slug}-{seen[slug]}")
        seen[slug] = seen.get(slug, 0) + 1
    return found


class DocsAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = docs_assets.snapshot()

    def build(self, **changes):
        return docs_assets.build(dataclasses.replace(self.snap, **changes))

    def test_the_page_heads_come_from_the_snapshots_page_index(self):
        pages = [p if p[0] != "docs/BENCHMARKS.md" else (p[0], "New title", *p[2:]) for p in self.snap.pages]
        files = self.build(pages=pages)
        self.assertIn('Next: <a href="BENCHMARKS.md">New title</a>', files["docs/CLAUDE_CODE_DOCS.md"])
        self.assertIn("\n# Benchmarks\n", files["docs/BENCHMARKS.md"])

    def test_build_makes_what_it_is_given_and_reads_no_file(self):
        files = self.build(texts={"README.md": self.snap.texts["README.md"]})
        self.assertEqual({p for p in files if not p.startswith("docs/assets/")}, {"README.md"})

    def test_a_page_head_comes_from_the_page_index_and_replaces_the_old_one(self):
        pages = [p if p[0] != "docs/TESTING.md" else (p[0], "New title", *p[2:]) for p in docs_pages.PAGES]
        old = docs_pages.with_head("docs/BENCHMARKS.md", "# Benchmarks\n")
        page = docs_pages.with_head("docs/BENCHMARKS.md", old, pages)
        self.assertIn('Next: <a href="TESTING.md">New title</a>', page)
        self.assertNotIn("<img", page)
        self.assertEqual(page.count("<!-- Page head"), 1)
        self.assertTrue(page.endswith("<!-- End of page head. -->\n\n# Benchmarks\n"))

    def test_analyze_keeps_a_head_that_ends_where_the_index_says(self):
        head = docs_pages.page_head("docs/BENCHMARKS.md")
        self.assertTrue(head.startswith(docs_pages.HEAD_START) and head.endswith(docs_pages.HEAD_END))
        self.assertIs(analyze.HEAD_END, docs_pages.HEAD_END)

    def test_the_committed_assets_match_the_script(self):
        code, err = check()
        self.assertEqual(code, 0, "python3 tools/docs_assets.py --check:\n" + err)

    def test_every_page_in_the_index_exists_and_carries_its_head(self):
        for path, *_ in docs_pages.PAGES:
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertTrue(text.startswith(docs_pages.page_head(path)), path)

    def test_every_docs_page_is_in_the_index(self):
        listed = {row[0] for row in docs_pages.PAGES}
        found = {p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").glob("*.md")}
        self.assertEqual(found - listed, set())

    def test_every_relative_link_and_anchor_resolves(self):
        # The changelog records each release as it shipped, so it can name files removed since.
        pages = [p[0] for p in docs_pages.PAGES if p[0] != "CHANGELOG.md"]
        pages += ["README.md", "plugins/model-router/README.md", "CODE_OF_CONDUCT.md"]
        broken = []
        for page in pages:
            text = (ROOT / page).read_text(encoding="utf-8")
            for target in links(text):
                path, _, anchor = target.partition("#")
                file = (ROOT / page).parent / path if path else ROOT / page
                if not file.exists():
                    broken.append(f"{page}: {target}")
                elif anchor and file.suffix == ".md" and anchor not in anchors(file.read_text(encoding="utf-8")):
                    broken.append(f"{page}: {target}")
        self.assertEqual(broken, [])

    def test_a_stale_file_fails_the_check_and_a_write_fixes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("old", encoding="utf-8")
            self.assertEqual(docs_assets.sync({"README.md": "new"}, True, root), ["README.md: out of date, run python3 tools/docs_assets.py"])
            self.assertEqual((root / "README.md").read_text(encoding="utf-8"), "old")
            self.assertEqual(docs_assets.sync({"README.md": "new"}, False, root), [])
            self.assertEqual((root / "README.md").read_text(encoding="utf-8"), "new")
            self.assertEqual(docs_assets.sync({"README.md": "new"}, True, root), [])

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
        site = lambda text: docs_assets.site_files("", {"site/index.html": text}, "0.0.0", "2030-01-01", "2.1.0")  # noqa: E731
        self.stops(lambda: site(docs_assets.SITE_URL.sub("", page)), "site/index.html", "SITE_URL")
        for mark in docs_assets.FAQ_HTML:
            self.stops(lambda: site(page.replace(mark, "")), "site/index.html", mark)

    def test_a_results_file_with_a_torn_last_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            torn = Path(tmp) / "results" / "a-lookup.jsonl"
            torn.parent.mkdir()
            first = json.dumps({"task": "lookup-todo-count", "schema": analyze.run.ROW_SCHEMA})
            torn.write_text(first + '\n{"task": "look', encoding="utf-8")
            self.stops(lambda: analyze.load([torn]), f"{torn.resolve()}:2")


if __name__ == "__main__":
    unittest.main()
