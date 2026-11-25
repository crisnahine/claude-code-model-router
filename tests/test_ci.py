"""Checks on the CI workflows and the version check they run."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "tools" / "plugin_version.py"
MANIFEST = Path("plugins/model-router/.claude-plugin/plugin.json")


class PluginVersion(unittest.TestCase):
    def setUp(self):
        # Versions moved around before the first release, then one release was tagged and two changes followed.
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.git("init", "-q")
        for step, version in enumerate(("0.1.0", "0.2.0", "0.1.1", "0.1.0")):
            self.set_version(version, extra=str(step))
            self.commit()
        self.git("tag", "v0.1.0")
        for version in ("0.1.3", "0.1.2"):
            self.set_version(version)
            self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
            cwd=self.repo, capture_output=True, text=True, check=True,
        ).stdout

    def set_version(self, version, extra=""):
        path = self.repo / MANIFEST
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"name": "model-router", "version": version, "extra": extra}, indent=2) + "\n")

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "change")

    def run_check(self):
        result = subprocess.run([sys.executable, str(CHECK), self.base], cwd=self.repo, capture_output=True, text=True)
        return result.returncode, result.stdout

    def test_no_plugin_change_passes(self):
        self.assertEqual(self.run_check(), (0, "No plugin change.\n"))

    def test_a_readme_only_change_keeps_the_version(self):
        (self.repo / "plugins/model-router/README.md").write_text("new words\n")
        self.commit()
        self.assertEqual(self.run_check()[0], 0)

    def test_a_change_with_the_base_version_fails(self):
        self.set_version("0.1.2", extra="x")
        self.commit()
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("0.1.2 was already carried", out)

    def test_a_version_carried_since_the_first_release_fails_even_untagged(self):
        self.set_version("0.1.3", extra="x")
        self.commit()
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("0.1.3 was already carried", out)

    def test_a_version_carried_only_before_the_first_release_passes(self):
        # Nobody could install a release then, so no install holds a cached copy under that version.
        for version in ("0.2.0", "0.1.1"):
            self.set_version(version, extra="x")
            self.commit()
            code, out = self.run_check()
            self.assertEqual((code, out), (0, f"Version {version} was last carried before the first release, v0.1.0.\n"))

    def test_the_first_releases_own_version_fails(self):
        self.set_version("0.1.0", extra="x")
        self.commit()
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("already tagged v0.1.0", out)

    def test_a_tagged_version_fails(self):
        self.git("tag", "v0.3.0", self.base)
        self.set_version("0.3.0")
        self.commit()
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("already tagged v0.3.0", out)

    def test_a_new_version_passes(self):
        self.set_version("0.2.1")
        self.commit()
        self.assertEqual(self.run_check(), (0, "Version 0.2.1 is new.\n"))


class Workflows(unittest.TestCase):
    def test_validate_runs_on_the_engines_floor_and_latest(self):
        manifest = json.loads((ROOT / "plugins/model-router/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
        floor = re.fullmatch(r">=(\S+)", manifest["engines"]["claude-code"]).group(1)
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        matrix = re.findall(r'"([^"]+)"', re.search(r"claude-code: \[([^\]]*)\]", ci).group(1))
        self.assertEqual(matrix[0], floor)
        self.assertIn("latest", matrix)

    def test_the_version_job_runs_the_version_check(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn('python3 tools/plugin_version.py "origin/$BASE"', ci)


if __name__ == "__main__":
    unittest.main()
