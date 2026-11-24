"""Pass when test_extract.py, unchanged, passes and, run in a child, safe_extract and both of its callers refuse
every symlink chain below before writing anything (each one escapes the shipped checks), while normal archives
with links still extract exactly as they are. An audit hook in the probe sees every write, so one made outside the
folder (the destination, or the plugins folder for an install) and removed again before the call returns still
fails."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

HERE = Path(__file__).resolve().parent
EVIL = json.dumps({"name": "evil", "version": "1.0.0", "entry": "main.py"})
HELLO = json.dumps({"name": "hello", "version": "1.2.0", "entry": "main.py"})
PWN = ("file", "{}/pwn.txt", "owned\n")
NO_MANIFEST = json.dumps({"files": {}, "links": {}})
REFUSED = {"extract": "UnsafeArchive", "install": "BundleRejected", "restore": "UnsafeArchive"}
SIDESTEP = [("dir", "deep"), ("dir", "deep/er"), ("dir", "side"), ("link", "deep/er/l", "../../side"),
            ("link", "deep/er/l/x", "../../../l3"), ("file", "deep/er/l/x/pwn.txt", "owned\n")]


def ladder(n):
    """n links that each lead to the one before, down to the destination, and a link that climbs out past them."""
    return ([("link", "h0", ".")] + [("link", f"h{i}", f"h{i - 1}") for i in range(1, n)] +
            [("link", "esc", f"h{n - 1}/..")])


# Each chain is made of links that pass the shipped checks one at a time.
CHAINS = [
    ("a link to . and a link under it", "extract",
     [("link", "a", "."), ("link", "a/b", ".."), ("file", "b/pwn.txt", "owned\n")]),
    ("a link out of a subfolder and a link under it", "extract",
     [("dir", "d"), ("link", "d/up", ".."), ("link", "d/up/out", ".."), ("file", "out/pwn.txt", "owned\n")]),
    ("a link whose target runs through another link", "extract", [("link", "a", "."), ("link", "b", "a/..")]),
    ("a deeper chain", "extract",
     [("link", "x/y/z", "../.."), ("link", "x/y/z/w", "../.."), ("file", "w/pwn.txt", "owned\n")]),
    ("a later link that redirects an earlier one", "extract",
     [("link", "b", "x/.."), ("link", "x", "."), ("file", "b/pwn.txt", "owned\n")]),
    ("a folder made through a chain", "extract", [("link", "a", "."), ("link", "a/b", ".."), ("dir", "b/made")]),
    ("a plugin bundle with a chain", "install",
     [("file", "plugin.json", EVIL), ("file", "main.py", ""), ("link", "a", "."), ("link", "a/b", ".."),
      ("file", "b/pwn.txt", "owned\n")]),
    ("a backup with a chain", "restore",
     [("file", "MANIFEST.json", NO_MANIFEST), ("dir", "d"), ("link", "d/up", ".."), ("link", "d/up/out", ".."),
      ("file", "out/pwn.txt", "owned\n")]),
    ("a link that a later link turns outward", "extract", [("link", "p", "q/.."), ("link", "q", ".")]),
    # No link here points at a folder holding it, and pwn.txt lands in the folder holding the destination.
    ("a link made through another link", "extract", SIDESTEP),
    ("a backup with a link made through another link", "restore", [("file", "MANIFEST.json", NO_MANIFEST)] + SIDESTEP),
    # A link's target can run through links that themselves need following.
    ("a chain through two links", "extract",
     [("link", "a", "."), ("link", "b", "a"), ("link", "c", "b/.."), ("file", "c/pwn.txt", "owned\n")]),
    ("a link through two links", "extract", [("link", "a", "."), ("link", "b", "a"), ("link", "c", "b/..")]),
    ("a link through three links", "extract",
     [("link", "a", "."), ("link", "b", "a"), ("link", "c", "b"), ("link", "d", "c/..")]),
    ("a plugin bundle with a chain out of the plugins folder", "install",
     [("file", "plugin.json", EVIL), ("file", "main.py", ""), ("link", "x/y/z", "../.."), ("link", "x/y/z/w", "../.."),
      ("file", "w/pwn.txt", "owned\n")]),
    # A folder beside the destination whose name starts with the destination's.
    ("a link to a folder that shares the prefix", "extract",
     [("link", "x", "../destiny"), ("dir", "x/sub"), ("file", "x/sub/pwn.txt", "owned\n")]),
    ("a backup with a link to a folder that shares the prefix", "restore",
     [("file", "MANIFEST.json", NO_MANIFEST), ("link", "x", "../restoredx"), ("dir", "x/sub"),
      ("file", "x/sub/pwn.txt", "owned\n")]),
    # Past any hop limit a resolver would pick: the kernel follows 31 links here (macOS allows 32), 40 below (Linux 40).
    ("a chain of thirty links", "extract", ladder(30) + [("file", "esc/pwn.txt", "owned\n")]),
    ("a link through thirty-nine links", "extract", ladder(39)),
]
DEPLOY = [("link", "config", "current/app.conf"), ("link", "current", "releases/v2"), ("dir", "empty"),
          ("link", "latest.log", "logs/2024-06.log"), ("dir", "logs"), ("dir", "notes"), ("dir", "releases"),
          ("dir", "shared"), ("link", "previous", "releases/v0"), ("link", "prev.conf", "previous/app.conf"),
          ("link", "old.log", "logs/2023-12.log"), ("file", "logs/2024-05.log", "may\n"),
          ("file", "logs/2024-06.log", "june\n"),
          ("file", "notes/café.txt", "ok\n"), ("dir", "releases/v1"), ("dir", "releases/v2"),
          ("file", "releases/v1/app.conf", "port=8000\n"), ("file", "releases/v2/app.conf", "port=8080\n"),
          ("dir", "releases/v2/bin"), ("link", "releases/v2/shared", "../../shared"),
          ("file", "releases/v2/bin/run", "#!/bin/sh\n", 0o755), ("file", "shared/data.db", "db")]
PLAN = "# plan\n"
NORMAL = [
    ("a deploy backup", "extract", DEPLOY),
    ("names that share a prefix", "extract",
     [("link", "a", "b"), ("dir", "b"), ("file", "b/x", "1"), ("file", "ab/y", "2"), ("link", "ab/z", "../a"),
      ("link", "c", "ab")]),
    ("a plugin bundle", "install",
     [("file", "plugin.json", HELLO), ("file", "main.py", "def setup(app):\n    pass\n"), ("dir", "assets"),
      ("file", "assets/logo.svg", "<svg/>"), ("link", "logo.svg", "assets/logo.svg"),
      ("file", "bin/hello", "#!/bin/sh\n", 0o755), ("link", "docs", "assets")]),
    ("a backup", "restore",
     [("dir", "docs"), ("file", "docs/plan.md", PLAN), ("link", "plan", "docs/plan.md"),
      ("link", "docs/latest", "plan.md"), ("link", "docs/draft", "drafts/plan.md"),
      ("file", "MANIFEST.json", json.dumps({"files": {"docs/plan.md": hashlib.sha256(PLAN.encode()).hexdigest()},
                                            "links": {"docs/latest": "plan.md", "plan": "docs/plan.md",
                                                      "docs/draft": "drafts/plan.md"}}))]),
]
BASE = "l1/l2/l3"
WHERE = {"extract": f"{BASE}/dest", "install": f"{BASE}/plugins/hello", "restore": f"{BASE}/restored"}
MARKER = {"install": ".installed", "restore": ".restored"}


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def untouched(via):
    tree = {"outside.txt": ["f", sha("keep\n"), False], "l1": ["d"], "l1/l2": ["d"], BASE: ["d"]}
    if via == "extract":
        tree[f"{BASE}/dest"] = ["d"]
    if via == "install":
        tree[f"{BASE}/plugins"] = ["d"]
    return tree


def extracted(via, members):
    tree = untouched(via)
    top = WHERE[via]
    tree[top] = ["d"]
    for kind, name, *rest in members:
        parts = name.split("/")
        for n in range(1, len(parts)):
            tree.setdefault(f"{top}/{'/'.join(parts[:n])}", ["d"])
        if kind == "file":
            tree[f"{top}/{name}"] = ["f", sha(rest[0]), bool((rest[1] if len(rest) > 1 else 0o644) & 0o100)]
        else:
            tree[f"{top}/{name}"] = ["d"] if kind == "dir" else ["l", rest[0]]
    if via in MARKER:
        tree[f"{top}/{MARKER[via]}"] = ["f", "any", False]
    return tree


def changes(got, want):
    """The paths whose entry differs, sorted, with a marker file's contents left out."""
    def same(path):
        a, b = got.get(path), want.get(path)
        return a == b or (b is not None and b[1:2] == ["any"] and a is not None and a[0] == "f")
    return sorted(p for p in set(got) | set(want) if not same(p))


def wrong(results):
    for name, via, _ in CHAINS:
        got = results.get(name) or {}
        if not isinstance(got.get("tree"), dict):
            return f"{name}: the probe raised {got.get('error')}"
        diff = changes(got["tree"], untouched(via))
        if got["error"] is None:
            return f"{name}: extracted instead of being refused, changing {', '.join(diff[:3])}"
        if got["error"] != REFUSED[via]:
            return f"{name}: raised {got['error']}, expected {REFUSED[via]}"
        if diff:
            return f"{name}: refused, but left {', '.join(diff[:3])} changed"
        if got.get("outside"):
            return f"{name}: refused, but first wrote outside the folder it was given ({', '.join(got['outside'][:2])})"
    for name, via, members in NORMAL:
        got = results.get(name) or {}
        if got.get("error") is not None or not isinstance(got.get("tree"), dict):
            return f"{name}: raised {got.get('error')}"
        want = extracted(via, members)
        diff = changes(got["tree"], want)
        if diff:
            return f"{name}: {diff[0]} is {got['tree'].get(diff[0])}, expected {want.get(diff[0])}"
        if got.get("outside"):
            return f"{name}: wrote outside the folder it was given ({', '.join(got['outside'][:2])})"
    return None


work = Path(sys.argv[1])
if not passcheck.tests_pass(work, HERE / "repo", "test_extract.py"):
    passcheck.finish(False, "test_extract.py doesn't pass unchanged")
cases = [{"name": name, "via": via, "members": members} for name, via, members in CHAINS + NORMAL]
results = passcheck.probe(work, HERE / "probe.py", {"cases": cases})
if not isinstance(results, dict):
    passcheck.finish(False, "the probe gave no result")
reason = wrong(results)
passcheck.finish(reason is None, (reason or "")[:300])
