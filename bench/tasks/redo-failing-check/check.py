"""Pass when CHANGELOG.md has the 2.4.0 section the shipped tools/check_changelog.py accepts against the shipped
commits.txt (each PR's heading, the breaking mark, the reverted pair and the left-out types), dated 2026-11-20, each
line written for customers (no commit hash, no commit subject pasted from commits.txt and no commit-type prefix), with
every earlier section and link left as it was. What a line says is not graded, since every word list tried misgraded
right lines, and a false fail here would count as a redo the work didn't need."""
import importlib.util
import re
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

HERE = Path(__file__).resolve().parent
# The shipped copies, so a run that edits the check script or the commit list changes nothing here.
lint = passcheck.load(HERE / "repo" / "tools", "check_changelog")
shipped = (HERE / "repo" / "CHANGELOG.md").read_text(encoding="utf-8")
commits = (HERE / "repo" / "commits.txt").read_text(encoding="utf-8")
TYPES = "feat|fix|perf|deprecate|remove|refactor|test|docs|ci|chore|build|revert"
# Only at the start of a line's text, so prose such as "see the docs: /docs/checkout" passes.
TYPED = re.compile(rf"\s*(?:\*\*Breaking:\*\*\s*)?(?:(?:{TYPES})(?:\([^)]*\))?!?\s*:|[\[(](?:{TYPES})(?:\([^)]*\))?!?[\])])",
                   re.I)
COMMIT = re.compile(r"^([0-9a-f]{7}) (.+) \(#(\d+)\)$", re.M)


def words(text):
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


SUBJECTS = {pr: words(subject) for _, subject, pr in COMMIT.findall(commits)}
HASHES = [sha for sha, _, _ in COMMIT.findall(commits)]

try:
    text = (Path(sys.argv[1]) / "CHANGELOG.md").read_text(encoding="utf-8")
except (OSError, UnicodeDecodeError):
    passcheck.finish(False, "CHANGELOG.md is missing or isn't UTF-8 text")
found = lint.problems(text, commits, "2.4.0")
if found:
    passcheck.finish(False, found[0])
body = next(body for name, _, body in lint.sections(text) if name == "2.4.0")
for line in body.splitlines():
    m = re.match(r"- .*\(#(\d+)\)$", line.rstrip())
    if not m:
        continue
    pr = m.group(1)
    said = line[2:line.rindex("(#")]
    if TYPED.match(said) or any(subject in words(said) for subject in SUBJECTS.values()):
        passcheck.finish(False, f"the #{pr} line is a commit subject, not written for customers: {line}")
    sha = next((sha for sha in HASHES if re.search(rf"\b{sha}\b", said, re.I)), None)
    if sha:
        passcheck.finish(False, f"the #{pr} line names commit {sha}, which customers don't need: {line}")
if "\n## [2.4.0] - 2026-11-20\n" not in text:
    passcheck.finish(False, "the 2.4.0 heading isn't dated 2026-11-20, the release date")
history = shipped[shipped.index("## [2.3.1]"):shipped.index("\n[Unreleased]: ")]
if history not in text:
    passcheck.finish(False, "the sections for 2.3.1 and earlier were changed")
old_links = [line for line in shipped.splitlines() if line.startswith(("[2.3.1]: ", "[2.3.0]: "))]
passcheck.finish(all(line in text.splitlines() for line in old_links), "the links for 2.3.1 and earlier were changed")
