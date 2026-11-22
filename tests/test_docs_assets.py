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
import docs_assets  # noqa: E402


def check() -> tuple:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = docs_assets.main(["--check"])
    return code, err.getvalue()


class DocsAssets(unittest.TestCase):
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
