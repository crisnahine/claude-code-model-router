"""Pass when CHANGELOG.md has the 2.4.0 section the shipped tools/check_changelog.py accepts against the shipped
commits.txt, each line written for customers (no commit type, hash or subject in it) and naming the change its PR
made, dated 2026-11-20, with every earlier section and link left as it was. That is stricter than the visible check,
which reads only where a line sits and the PR number it ends with. What a line says is read by keywords, so a line
that names the right things and gets them backwards (500 instead of 404, say) still passes."""
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
# Each PR's line must name its change with a word from every group, so a line that says another PR's change, or
# nothing, fails.
SAYS = {"214": (("csv", "spreadsheet"), ("export", "download")), "215": (("coupon", "promo"), ("expir",)),
        "217": (("krona", "swedish", "sek"),), "218": (("order history", "past orders"),
                                                       ("fast", "quick", "speed", "slow", "one query", "single query")),
        "222": (("canad",), ("tax",)), "224": (("v1",), ("remov", "drop", "gone", "no longer", "delet")),
        "228": (("404", "not found"), ("delet",))}
# The endpoint goes in this release; a line that puts it off says the 2.3.0 deprecation again.
LATER = {"224": re.compile(r"\b(will|is going to)( be)? (remov|drop|gone|delet|go\b|stop)|\bto be (remov|drop|delet)"
                           r"|\b(is|are) (now )?deprecated\b|\b(scheduled|planned)\b"
                           r"|\bin (3\.0|a (future|later) release|the next release)", re.I)}
TYPES = "feat|fix|perf|deprecate|remove|refactor|test|docs|ci|chore|build|revert"
TYPED = re.compile(rf"(?<![\w-])(?:{TYPES})(?:\([^)]*\))?!?\s*:|[\[(](?:{TYPES})(?:\([^)]*\))?!?[\])]", re.I)
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
    if TYPED.search(said) or any(subject in words(said) for subject in SUBJECTS.values()):
        passcheck.finish(False, f"the #{pr} line is a commit subject, not written for customers: {line}")
    sha = next((sha for sha in HASHES if re.search(rf"\b{sha}\b", said, re.I)), None)
    if sha:
        passcheck.finish(False, f"the #{pr} line names commit {sha}, which customers don't need: {line}")
    missing = next((group for group in SAYS[pr] if not any(word in line.lower() for word in group)), None)
    if missing:
        passcheck.finish(False, f"the #{pr} line doesn't describe that change (it names none of "
                                f"{', '.join(missing)}): {line}")
    if pr in LATER and LATER[pr].search(said):
        passcheck.finish(False, f"the #{pr} line says the change is still to come, but it ships in 2.4.0: {line}")
if "\n## [2.4.0] - 2026-11-20\n" not in text:
    passcheck.finish(False, "the 2.4.0 heading isn't dated 2026-11-20, the release date")
history = shipped[shipped.index("## [2.3.1]"):shipped.index("\n[Unreleased]: ")]
if history not in text:
    passcheck.finish(False, "the sections for 2.3.1 and earlier were changed")
old_links = [line for line in shipped.splitlines() if line.startswith(("[2.3.1]: ", "[2.3.0]: "))]
passcheck.finish(all(line in text.splitlines() for line in old_links), "the links for 2.3.1 and earlier were changed")
