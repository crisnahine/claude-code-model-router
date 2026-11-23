"""The docs assets are current and valid. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "bench"))
import analyze  # noqa: E402
import docs_assets  # noqa: E402


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
    def test_the_head_marker_matches_analyze(self):
        self.assertEqual(analyze.HEAD_END, docs_assets.HEAD_END)

    def test_the_benchmarks_head_comes_from_pages(self):
        pages = [p if p[0] != "docs/BENCHMARKS.md" else (*p[:3], "A new question?", *p[4:]) for p in docs_assets.PAGES]
        with mock.patch.object(docs_assets, "PAGES", pages):
            page = docs_assets.build()["docs/BENCHMARKS.md"]
        self.assertIn('alt="Benchmarks: A new question?"', page)
        self.assertIn("\n# Benchmarks\n", page)

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

    def test_a_stale_file_fails_the_check(self):
        with mock.patch.object(docs_assets, "build", return_value={"README.md": "stale"}):
            code, err = check()
        self.assertEqual(code, 1)
        self.assertIn("README.md: out of date", err)

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
