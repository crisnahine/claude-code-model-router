"""Fails when a plugin change keeps a version the base branch has ever carried or a v* tag holds.

Installs keep their cached copy until the version string changes. Run from the repository root:
python3 tools/plugin_version.py <base ref>
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

PLUGIN = "plugins/model-router"
MANIFEST = f"{PLUGIN}/.claude-plugin/plugin.json"


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True)


def check(base: str) -> tuple[bool, str]:
    changed = subprocess.run(
        ["git", "diff", "--quiet", base, "HEAD", "--", PLUGIN, f":(exclude){PLUGIN}/README.md"]
    ).returncode
    if changed == 0:
        return True, "No plugin change."
    with open(MANIFEST, encoding="utf-8") as f:
        version = json.load(f)["version"]
    # -m shows a merge's own diff too, so a version set while resolving a merge counts.
    history = git("log", "-p", "-m", "--full-history", "--format=", base, "--", MANIFEST).stdout
    if version in re.findall(r'^[+-]\s*"version"\s*:\s*"([^"]+)"', history, re.M):
        return False, f"::error file={MANIFEST}::version {version} was already carried on {base}; set a version it has never carried"
    if git("tag", "-l", f"v{version}").stdout.strip():
        return False, f"::error file={MANIFEST}::version {version} is already tagged v{version}; set a new version"
    return True, f"Version {version} is new."


if __name__ == "__main__":
    ok, message = check(sys.argv[1])
    print(message)
    sys.exit(0 if ok else 1)
