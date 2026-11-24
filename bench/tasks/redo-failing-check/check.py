"""Pass when CHANGELOG.md has the 2.4.0 section the shipped tools/check_changelog.py accepts against the shipped
commits.txt, each line written for customers (no commit type, hash or subject in it) and naming the change its PR
made, dated 2026-11-20, with every earlier section and link left as it was. That is stricter than the visible check,
which reads only where a line sits and the PR number it ends with. What a line says is read by words, the negations
and framings before them ("no longer", "used to", "instead of") and a few patterns for #224, so a line that states
its change the wrong way round fails. That is a word reading, not an understanding: a rare wording can still be
misread either way, and each one found goes in wrong/ or right/."""
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
                                                    "single", "one database query", "less time", "efficien",
                                                    "slow")),
        "222": (("canad",), ("tax", "gst", "hst", "pst", "qst")), "224": (("v1",),),
        "228": (("404", "not found"), ("delet", "no longer exist", "doesn't exist", "does not exist", "removed",
                                       "missing", "gone"))}
# Words that state each PR's change (good) or its opposite (bad). A word counts as said the other way after an odd
# number of negations or framings such as "used to" or "instead of", so "no longer loses its total" is right.
FACTS = {"214": (r"export\w*|download\w*|sav(e|es|ed|ing)\b|availab\w*|exist\w*|work(s|ed|ing)?\b",
                 r"gone\b|remov\w*|drop\w*|delet\w*|unavailable|disabled|broken|retired"),
         "215": (r"keep\w*|kept|stay\w*|retain\w*|preserv\w*|hold\w*|held|right\b|correct\w*|intact",
                 r"los(e|es|ing|t)\b|drop\w*|reset\w*|clear\w*|wipe\w*|disappear\w*|vanish\w*|wrong|incorrect\w*"
                 r"|zero\w*|break\w*|broke\w*|gone\b|empt\w*"),
         "217": (r"pick\w*|choos\w*|chose\w*|select\w*|availab\w*|support\w*|pay\w*|add\w*|accept\w*",
                 r"gone\b|remov\w*|drop\w*|unavailable|disabled|missing|unsupported"),
         "218": (r"fast\w*|quick\w*|speed\w*|perform\w*|improv\w*|one (database )?query|single\b|efficien\w*",
                 r"slow\w*|less (fast|quick)\w*|worse|longer to load|quer(y|ies) (per|for) (each|every)|n\+1"
                 r"|tim(e|es|ed|ing) out"),
         "222": (r"right\b|correct\w*|accurate\w*|proper\w*",
                 r"wrong\w*|incorrect\w*|miscalc\w*|inaccura\w*|bad\b|broken"),
         "228": (r"404", r"500|server error|internal error|crash\w*|fail\w*")}
# 404 phrases name the deleted order, not what the request gets.
_TOPIC_228 = re.compile(r"not found|(no longer|doesn't|does not|didn't|did not|don't|do not|never) exist\w*")
_FLIP = (r"fix(es|ed)? (a|an|the) (bug|issue|problem)( where| that| when| in which| causing| with)?"
         r"|(bug|issue|problem) (where|that|when|causing)|used to|previously|formerly|before|would|instead of"
         r"|rather than|in place of")
_POLARITY = r"(?P<neg>no longer|no more|n't\b|\bnot\b|\bnever\b|\bcannot\b|\bstop(s|ped|ping)?\b)" \
            rf"|(?P<flip>\b({_FLIP})\b)|(?P<guard>\b(fix(es|ed|ing)?|prevent\w*|avoid\w*|without)\b)" \
            r"|(?P<reset>[;:.!?](\s|$)|\bnow\b|\bbut\b)|(?P<conj>\b(and|so)\b)"
_PAST = re.compile(r"\b(was|were|had|used|showed|returned|gave|got|became)\b|ed$|\b(lost|broke)$")
POLARITY = {pr: re.compile(rf"{_POLARITY}|(?P<bad>\b({bad}))|(?P<good>\b({good}))", re.I)
            for pr, (good, bad) in FACTS.items()}


def backwards(pr, text):
    """Whether the line states its PR's change the wrong way round, as best a word reading can tell."""
    text = text.lower().replace("\u2019", "'")
    if pr == "228":
        text = _TOPIC_228.sub(" ", text)
    neg = flip = guard = 0
    clause, reversed_at = 0, []
    for m in POLARITY[pr].finditer(text):
        kind = m.lastgroup
        if kind == "neg":
            neg += 1
        elif kind == "flip":
            flip += 1
        elif kind == "guard":
            guard += 1
        elif kind == "conj":
            neg = 0
        elif kind == "reset":
            # A fact said in the past before a "now" is the old behavior, not the new one.
            if m.group().strip() == "now":
                reversed_at = [r for r in reversed_at if not r]
            neg = flip = guard = 0
            clause = m.end()
        else:
            negated = (neg + flip) % 2 == 1
            if negated if kind == "good" else not (negated or guard):
                reversed_at.append(bool(_PAST.search(text[clause:m.start()]) or _PAST.search(m.group())))
    return bool(reversed_at)


# #224 must say the endpoint itself is gone, so a line about one thing the endpoint stops doing fails.
def _path(group):
    return (rf"(the )?((deprecated|old|legacy) )?/?v(?P<{group}>\d+)[/ ]checkout( (api|http|rest|public))?"
            r"( (endpoints?|apis?|routes?|service))?")


_END = (r"(?=\s*($|[.,;:!()-]|(and|so|but|in|as|after|now|too|also|as well|completely|entirely|permanently|for good"
        r"|from (the )?(api|shop|server|service|platform))\b))")
_VERB = r"(remove[ds]?|removing|removal of|drop(s|ped)?|delete[ds]?|retire[ds]?|shut down|turned off)"
REMOVED = re.compile(
    rf"{_path('a')}(,[^,]*,| \([^)]*\))? ((is|was|are|has been) no longer (supported|available|maintained|offered),?"
    r" and )?((is|was|are|were|has|have|got)( now)?( been)? )?(now |also |fully |permanently |finally )?"
    r"(removed|gone|deleted|dropped|retired|shut down|turned off|unavailable|no longer (works?|exists?|available|"
    rf"is available|served|is served|responds)){_END}"
    rf"|\b{_VERB}:?( support for)? {_path('b')}(,? and (also )?{_path('d')})?{_END}"
    rf"|\b{_VERB} (the )?((old|legacy|deprecated|original|checkout|api|http|rest|endpoint) ){{0,4}}"
    rf"\(/?v(?P<c>\d+)/checkout\){_END}", re.I)
# Said to have gone in an earlier release; 2.3.0 only deprecated it.
EARLIER = re.compile(r"\s*(back )?(in|since|as of|with) (v|version |release )?(?P<v>\d+(\.\d+)+)", re.I)
# Still to come, as the 2.3.0 deprecation said; a deprecation named beside a stated removal is only history.
LATER = re.compile(r"\b(will|is going to)( be)? (remov|drop|gone|delet|go away)|\bto be (remov|drop|delet)"
                   r"|\bin (3\.0|a (future|later) release|the next release)", re.I)
HISTORY = re.compile(r"2\.3\.0|announced|marked|scheduled|planned|said|deprecat", re.I)
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
    plain = said.replace("`", "").replace("*", "")
    if pr in FACTS and backwards(pr, plain):
        passcheck.finish(False, f"the #{pr} line gets the change backwards: {line}")
    missing = next((group for group in SAYS[pr] if not any(word in line.lower() for word in group)), None)
    if missing:
        passcheck.finish(False, f"the #{pr} line doesn't describe that change (it names none of "
                                f"{', '.join(missing)}): {line}")
    if pr == "224":
        gone = [(m, {m.group(g) for g in "abcd" if m.group(g)}) for m in REMOVED.finditer(plain)]
        ours = [m for m, versions in gone if "1" in versions]
        if not ours or (LATER.search(plain) and not HISTORY.search(plain)):
            if LATER.search(plain):
                passcheck.finish(False, f"the #224 line says the change is still to come, but it ships in 2.4.0: {line}")
            passcheck.finish(False, f"the #224 line doesn't say the /v1/checkout endpoint is gone: {line}")
        other = next((v for _, versions in gone for v in sorted(versions) if v != "1"), None)
        if other:
            passcheck.finish(False, f"the #224 line says /v{other}/checkout is removed, but only /v1 is: {line}")
        when = next((w.group("v") for w in (EARLIER.match(plain, m.end()) for m in ours)
                     if w and w.group("v") not in ("2.4", "2.4.0")), None)
        if when:
            passcheck.finish(False, f"the #224 line says it was removed in {when}, but it is removed in 2.4.0: {line}")
if "\n## [2.4.0] - 2026-11-20\n" not in text:
    passcheck.finish(False, "the 2.4.0 heading isn't dated 2026-11-20, the release date")
history = shipped[shipped.index("## [2.3.1]"):shipped.index("\n[Unreleased]: ")]
if history not in text:
    passcheck.finish(False, "the sections for 2.3.1 and earlier were changed")
old_links = [line for line in shipped.splitlines() if line.startswith(("[2.3.1]: ", "[2.3.0]: "))]
passcheck.finish(all(line in text.splitlines() for line in old_links), "the links for 2.3.1 and earlier were changed")
