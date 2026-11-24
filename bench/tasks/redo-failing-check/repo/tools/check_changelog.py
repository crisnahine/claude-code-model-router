"""Check a release's section of CHANGELOG.md against commits.txt.

Usage: python3 tools/check_changelog.py <version>
Prints each problem it finds and exits 1, or exits 0 when the section is ready.
"""
import re
import sys
from pathlib import Path

ORDER = ("Added", "Changed", "Deprecated", "Removed", "Fixed")
HEADING_FOR = {"feat": "Added", "perf": "Changed", "deprecate": "Deprecated", "remove": "Removed", "fix": "Fixed"}
LEFT_OUT = {"refactor", "test", "docs", "ci", "chore", "build"}
COMPARE = "https://git.example.com/shop/compare/"
COMMIT = re.compile(r"^[0-9a-f]{7} (.+) \(#(\d+)\)$")
TYPED = re.compile(r"^(\w+)(?:\([\w-]+\))?!?: ")
REVERT = re.compile(r'^Revert "(.+) \(#(\d+)\)"$')
RELEASE = re.compile(r"^## \[([^\]]+)\](.*)$", re.M)
LINK = re.compile(r"^\[([^\]]+)\]: (\S+)$", re.M)


def read_commits(text):
    """{pr: type} for each commit, and {pr: the pr that reverted it}."""
    kinds, reverted = {}, {}
    for line in text.splitlines():
        m = COMMIT.match(line.strip())
        if not m:
            continue
        subject, pr = m.groups()
        revert = REVERT.match(subject)
        if revert:
            reverted[revert.group(2)] = pr
            continue
        typed = TYPED.match(subject)
        if not typed:
            raise ValueError(f"commits.txt: #{pr} has no type: {subject}")
        kinds[pr] = typed.group(1)
    return kinds, reverted


def sections(changelog):
    """[(name, rest of the heading line, body)] for each ## heading, in order."""
    found = list(RELEASE.finditer(changelog))
    out = []
    for i, m in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(changelog)
        body = changelog[m.end():end]
        link = LINK.search(body)
        out.append((m.group(1), m.group(2), body[:link.start()] if link else body))
    return out


def listed(body, version):
    """{pr: [heading, ...]} for the section's lines, plus problems with its shape."""
    problems, seen, heading, used = [], {}, None, []
    for line in body.splitlines():
        line = line.rstrip()
        if line.startswith("### "):
            heading = line[4:].strip()
            if heading not in ORDER:
                problems.append(f"## [{version}] has a heading ### {heading}; use only {', '.join(ORDER)}")
            used.append(heading)
        elif line.startswith("- "):
            if heading is None:
                problems.append(f"the line {line!r} in ## [{version}] isn't under a ### heading")
                continue
            m = re.search(r"\(#(\d+)\)$", line)
            if not m:
                problems.append(f"the line {line!r} in ## [{version}] doesn't end with its PR number, like (#123)")
                continue
            seen.setdefault(m.group(1), []).append(heading)
        elif line.strip() and heading is not None:
            problems.append(f"the line {line!r} in ## [{version}] isn't a list item")
    known = [h for h in used if h in ORDER]
    if known != sorted(known, key=ORDER.index) or len(set(known)) != len(known):
        problems.append(f"the headings in ## [{version}] must each appear once, in the order {', '.join(ORDER)}")
    for h in used:
        if not re.search(rf"^### {re.escape(h)}\n+- ", body, re.M):
            problems.append(f"### {h} in ## [{version}] has nothing under it; leave it out")
    return seen, problems


def problems(changelog, commits, version):
    found = sections(changelog)
    names = [name for name, _, _ in found]
    if version not in names:
        return [f"CHANGELOG.md has no ## [{version}] section"]
    out = []
    if names[:2] != ["Unreleased", version]:
        out.append(f"## [Unreleased] must stay at the top with ## [{version}] directly under it")
    if "Unreleased" in names and re.search(r"^- ", found[names.index("Unreleased")][2], re.M):
        out.append("## [Unreleased] still lists changes; this release's changes go under ## "
                   f"[{version}]")
    _, rest, body = found[names.index(version)]
    if not re.fullmatch(r" - \d{4}-\d{2}-\d{2}", rest):
        out.append(f"the heading must read ## [{version}] - YYYY-MM-DD, with the release date")
    seen, shape = listed(body, version)
    out += shape
    kinds, reverted = read_commits(commits)
    for pr, headings in sorted(seen.items(), key=lambda p: int(p[0])):
        kind = kinds.get(pr)
        if len(headings) > 1:
            out.append(f"#{pr} is listed {len(headings)} times")
        if pr in reverted:
            out.append(f"#{pr} was reverted in this release by #{reverted[pr]}, so it doesn't go in the changelog")
        elif pr in set(reverted.values()):
            out.append(f"#{pr} is a revert of #{next(k for k, v in reverted.items() if v == pr)}; leave both out")
        elif kind is None:
            out.append(f"#{pr} isn't one of this release's commits")
        elif kind in LEFT_OUT:
            out.append(f"#{pr} is a {kind} commit; those don't go in the changelog")
        elif headings[0] != HEADING_FOR[kind]:
            out.append(f"#{pr} is under {headings[0]}; a {kind} commit goes under {HEADING_FOR[kind]}")
    for pr, kind in sorted(kinds.items(), key=lambda p: int(p[0])):
        if kind in HEADING_FOR and pr not in reverted and pr not in seen:
            out.append(f"#{pr} ({kind}) is missing from ## [{version}]; it goes under {HEADING_FOR[kind]}")
    out += link_problems(changelog, names, version)
    return out


def link_problems(changelog, names, version):
    links = dict(LINK.findall(changelog))
    order = [name for name, _ in LINK.findall(changelog)]
    out = []
    after = names.index(version) + 1
    previous = names[after] if after < len(names) else None
    want = {"Unreleased": f"{COMPARE}v{version}...HEAD"}
    if previous:
        want[version] = f"{COMPARE}v{previous}...v{version}"
    for name, url in want.items():
        if name not in links:
            out.append(f"there is no [{name}] link at the bottom of CHANGELOG.md; it should be [{name}]: {url}")
        elif links[name] != url:
            out.append(f"the [{name}] link at the bottom of CHANGELOG.md is {links[name]}; it should be {url}")
    missing = [n for n in names if n not in links and n not in want]
    if missing:
        out.append(f"no link at the bottom of CHANGELOG.md for {', '.join(missing)}")
    if [n for n in order if n in names] != [n for n in names if n in links]:
        out.append("the links at the bottom of CHANGELOG.md must be in the same order as the sections")
    return out


def main(argv):
    if len(argv) != 1:
        print(__doc__.strip())
        return 2
    root = Path(__file__).resolve().parent.parent
    found = problems((root / "CHANGELOG.md").read_text(encoding="utf-8"),
                     (root / "commits.txt").read_text(encoding="utf-8"), argv[0])
    for problem in found:
        print(problem)
    if found:
        print(f"{len(found)} problem(s) in CHANGELOG.md for {argv[0]}")
        return 1
    print(f"CHANGELOG.md is ready for {argv[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
