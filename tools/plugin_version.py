"""Fails when a plugin change keeps a version the base branch has carried since its first release, or that a
v* tag holds.

Installs keep their cached copy until the version string changes. A version the branch carried only before its
first v* tag is free: nothing was released then, and only an install made from the branch itself holds a copy.
Run from the repository root:
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


def _versions(span: str) -> set:
    """Every version the manifest held over `span`. -m shows a merge's own diff too, so one set while resolving a merge counts."""
    history = git("log", "-p", "-m", "--full-history", "--format=", span, "--", MANIFEST).stdout
    return set(re.findall(r'^[+-]\s*"version"\s*:\s*"([^"]+)"', history, re.M))


def check(base: str) -> tuple[bool, str]:
    changed = subprocess.run(
        ["git", "diff", "--quiet", base, "HEAD", "--", PLUGIN, f":(exclude){PLUGIN}/README.md"]
    ).returncode
    if changed == 0:
        return True, "No plugin change."
    with open(MANIFEST, encoding="utf-8") as f:
        version = json.load(f)["version"]
    if git("tag", "-l", f"v{version}").stdout.strip():
        return False, f"::error file={MANIFEST}::version {version} is already tagged v{version}; set a new version"
    released = git("tag", "-l", "v*", "--merged", base, "--sort=creatordate").stdout.split()
    carried = _versions(f"{released[0]}..{base}" if released else base)
    carried.add(json.loads(git("show", f"{base}:{MANIFEST}").stdout)["version"])
    if version in carried:
        return False, f"::error file={MANIFEST}::version {version} was already carried on {base}; set a version it has not carried since its first release"
    if released and version in _versions(base):
        return True, f"Version {version} was last carried before the first release, {released[0]}."
    return True, f"Version {version} is new."


if __name__ == "__main__":
    ok, message = check(sys.argv[1])
    print(message)
    sys.exit(0 if ok else 1)
