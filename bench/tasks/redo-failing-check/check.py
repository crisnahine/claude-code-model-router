"""Pass when CHANGELOG.md has the 2.4.0 section the shipped tools/check_changelog.py accepts against the shipped
commits.txt, each line written for customers (no commit type, hash or subject in it) and naming the change its PR
made, dated 2026-11-20, with every earlier section and link left as it was. That is stricter than the visible check,
which reads only where a line sits and the PR number it ends with. What a line says is read by keywords and a few
patterns, so the common ways to get a change backwards fail, and a rarer wording that names the right things and
gets them wrong can still pass."""
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
SAYS = {"214": (("csv", "spreadsheet"), ("export", "download", "save", "as csv", "as a csv", "to csv", "to a csv")),
        "215": (("coupon", "promo"), ("expir",)), "217": (("krona", "swedish", "sek"),),
        "218": (("order history", "past orders"), ("fast", "quick", "speed", "perform", "improv", "one query",
                                                    "single query", "no longer slow")),
        "222": (("canad",), ("tax",)), "224": (("v1",),),
        "228": (("404", "not found"), ("delet", "no longer exist", "doesn't exist", "does not exist", "removed",
                                       "missing", "gone"))}
# The facts the other way round: a slower page, or a 500 where the fix gives a 404.
_NOT = r"(?<!no longer )(?<!not )(?<!n't )(?<!never )"
BACKWARDS = {"218": re.compile(rf"{_NOT}\b(slow(er|ed|s down)\b|less (fast|quick))", re.I),
             "228": re.compile(rf"{_NOT}\b(returns?|gives?|answers?|responds? with|now) (an? )?(http )?500\b"
                               r"|\binstead of (an? )?(http )?404\b", re.I)}
# #224 must say the endpoint itself is gone, so a line about one thing the endpoint stops doing fails.
_V1 = r"(the )?(deprecated |old |legacy )?/?v1[/ ]checkout( (endpoint|api|route))?"
_END = r"(?=\s*($|[.,;:!()-]|(and|so|but|in|from|as|after|now|completely|entirely|permanently|for good)\b))"
REMOVED = re.compile(
    rf"{_V1}(,[^,]*,| \([^)]*\))? ((is|was|are|were|has|have|got)( now)?( been)? )?(now |fully |permanently |finally )?"
    rf"(removed|gone|deleted|dropped|retired|shut down|turned off|unavailable|no longer (works?|exists?|available|"
    rf"is available|served|is served|responds)){_END}"
    rf"|\b(remove[ds]?|removing|removal of|drop(s|ped)?|delete[ds]?|retire[ds]?|shut down|turned off):?( support for)? "
    rf"{_V1}{_END}", re.I)
# Still to come, as the 2.3.0 deprecation said; a deprecation named beside a stated removal is only history.
LATER = re.compile(r"\b(will|is going to)( be)? (remov|drop|gone|delet|go away)|\bto be (remov|drop|delet)"
                   r"|\bin (3\.0|a (future|later) release|the next release)", re.I)
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
    if pr in BACKWARDS and BACKWARDS[pr].search(said):
        passcheck.finish(False, f"the #{pr} line gets the change backwards: {line}")
    missing = next((group for group in SAYS[pr] if not any(word in line.lower() for word in group)), None)
    if missing:
        passcheck.finish(False, f"the #{pr} line doesn't describe that change (it names none of "
                                f"{', '.join(missing)}): {line}")
    plain = said.replace("`", "").replace("*", "")
    if pr == "224" and (LATER.search(plain) or not REMOVED.search(plain)):
        if LATER.search(plain) or "deprecat" in plain.lower():
            passcheck.finish(False, f"the #224 line says the change is still to come, but it ships in 2.4.0: {line}")
        passcheck.finish(False, f"the #224 line doesn't say the /v1/checkout endpoint is gone: {line}")
if "\n## [2.4.0] - 2026-11-20\n" not in text:
    passcheck.finish(False, "the 2.4.0 heading isn't dated 2026-11-20, the release date")
history = shipped[shipped.index("## [2.3.1]"):shipped.index("\n[Unreleased]: ")]
if history not in text:
    passcheck.finish(False, "the sections for 2.3.1 and earlier were changed")
old_links = [line for line in shipped.splitlines() if line.startswith(("[2.3.1]: ", "[2.3.0]: "))]
passcheck.finish(all(line in text.splitlines() for line in old_links), "the links for 2.3.1 and earlier were changed")
