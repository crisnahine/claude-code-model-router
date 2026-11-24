"""Pass when CHANGELOG.md has the 2.4.0 section the shipped tools/check_changelog.py accepts against the shipped
commits.txt, each line written for customers (not a commit subject) and naming the change its PR made, dated
2026-11-20, with every earlier section and link left as it was. That is stricter than the visible check, which reads
only where a line sits and the PR number it ends with."""
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
# Each PR's line must name its change with one of these, so a line that says another PR's change, or nothing, fails.
SAYS = {"214": ("csv", "spreadsheet"), "215": ("coupon", "promo"), "217": ("krona", "swedish", "sek"),
        "218": ("order history", "past orders"), "222": ("canad",), "224": ("v1",), "228": ("404", "not found")}
SUBJECT = re.compile(r"^- (?:\*\*Breaking:\*\* )?(?:feat|fix|perf|deprecate|remove|refactor|test|docs|ci|chore|build|"
                     r"revert)(?:\([\w-]+\))?!?: ", re.I)

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
    if SUBJECT.match(line):
        passcheck.finish(False, f"the #{pr} line is a commit subject, not written for customers: {line}")
    if not any(word in line.lower() for word in SAYS[pr]):
        passcheck.finish(False, f"the #{pr} line doesn't describe that change (it names none of "
                                f"{', '.join(SAYS[pr])}): {line}")
if "\n## [2.4.0] - 2026-11-20\n" not in text:
    passcheck.finish(False, "the 2.4.0 heading isn't dated 2026-11-20, the release date")
history = shipped[shipped.index("## [2.3.1]"):shipped.index("\n[Unreleased]: ")]
if history not in text:
    passcheck.finish(False, "the sections for 2.3.1 and earlier were changed")
old_links = [line for line in shipped.splitlines() if line.startswith(("[2.3.1]: ", "[2.3.0]: "))]
passcheck.finish(all(line in text.splitlines() for line in old_links), "the links for 2.3.1 and earlier were changed")
