"""Pass when CHANGELOG.md has the 2.4.0 section the shipped tools/check_changelog.py accepts against the shipped
commits.txt, each line written for customers (no commit type, hash or subject in it) and naming its PR's topic,
the #224 line saying /v1/checkout is gone now (not that it will go, or went in another release), dated 2026-11-20,
with every earlier section and link left as it was. That is stricter than the visible check, which reads only where
a line sits and the PR number it ends with. A line that states its change the wrong way round on purpose is out of
scope and can pass. Beyond that, what a line says is not graded, so a #224 line that says a field of /v1/checkout is
removed, or that the endpoint is gone from the docs, passes too: a word check that tries misgrades right lines, and
a false fail here corrupts the redo rate this task measures."""
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
# Each PR's line must name its topic, read on its words, so a line that says another PR's change, or nothing, fails.
SAYS = {"214": r"csv|spreadsheet|export|download", "215": r"coupon|promo|discount|voucher",
        "217": r"krona|swed|\bsek\b", "218": r"history|\b(past|previous|earlier|older) orders|orders page",
        "222": r"canad.*\b(tax|gst|hst|pst|qst|vat)|\b(tax|gst|hst|pst|qst|vat)\b.*canad", "224": r"\bv1\b",
        "228": r"order.*(delet|exist|missing|remov)|(delet|missing|remov).*order"}
GONE = re.compile(r"remov|\bgone\b|delet|\bdrop|retir|shut (it )?down|shutdown|turned off|switched off"
                  r"|taken (down|offline)|\bsunset|discontinu|unavailable|decommission|end of life"
                  r"|no longer (be )?(work|exist|avail|serv|respond|support)|\b410\b", re.I)
# A removal said to come later, or in another release, is the deprecation, not this release's removal.
LATER_BEFORE = re.compile(r"(?:\b(?:will|would|shall|to)\s+be\s+|\b(?:for|pending|ahead of|before|prior to|until"
                          r"|upcoming|future|eventual|planned|scheduled)\s+(?:its\s+|the\s+)?)$", re.I)
LATER_AFTER = re.compile(r"^\w*,?\s+(?:(?:is|are)\s+)?(?:planned|scheduled|due|expected|coming|pending"
                         r"|in\s+(?:a\s+|the\s+)?(?:later|future|coming|upcoming|next|subsequent)\b"
                         r"|in\s+(?:version\s+|release\s+)?v?(?!2\.4\.0\b)\d)", re.I)
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
    if not re.search(SAYS[pr], words(said)):
        passcheck.finish(False, f"the #{pr} line doesn't describe that change: {line}")
    if pr == "224" and not any(not LATER_BEFORE.search(said[:m.start()]) and not LATER_AFTER.match(said[m.end():])
                               for m in GONE.finditer(said)):
        passcheck.finish(False, f"the #224 line doesn't say the /v1/checkout endpoint is gone: {line}")
if "\n## [2.4.0] - 2026-11-20\n" not in text:
    passcheck.finish(False, "the 2.4.0 heading isn't dated 2026-11-20, the release date")
history = shipped[shipped.index("## [2.3.1]"):shipped.index("\n[Unreleased]: ")]
if history not in text:
    passcheck.finish(False, "the sections for 2.3.1 and earlier were changed")
old_links = [line for line in shipped.splitlines() if line.startswith(("[2.3.1]: ", "[2.3.0]: "))]
passcheck.finish(all(line in text.splitlines() for line in old_links), "the links for 2.3.1 and earlier were changed")
