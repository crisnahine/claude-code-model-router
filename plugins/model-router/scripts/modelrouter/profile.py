"""Score how much reasoning a request or a subtask needs, from its wording and from
the agent it is given to. Deterministic and fast: this runs on every agent call.

It is an estimate. Claude sees the code and can overrule it by naming a model on a
call, and the feedback loop in learn.py corrects levels that keep proving too low."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .config import PLUGIN_NAME, Level

# Two or more systems named in a list ("across the API, the web client and ...") are cross-module work.
_SYSTEM = r"(?:services?|clients?|api|gateway|backend|frontend|workers?|apps?|layers?|modules?)"
_SYSTEMS = rf"(?:across|touching|between)\s+(?:the\s+)?(?:[\w-]+\s+){{0,2}}?{_SYSTEM},\s+(?:the\s+)?(?:[\w-]+\s+){{0,2}}?{_SYSTEM}"
# Only the forms that ask for a choice: "decided", "chose" and "picked" report one already made.
_CHOOSE = r"choos(?:e|es|ing)|pick(?:s|ing)?"
_ASK_CHOICE = rf"decid(?:e|es|ing)|{_CHOOSE}|weigh(?:s|ing)?"
# Asked to make a choice, not to pick an item: decide how, choose a scheme, weigh options.
_DECISION = (rf"(?:{_ASK_CHOICE})\s+(?:how|which|whether|between|what|on|among)|"
             rf"(?:{_CHOOSE})\s+(?:a|an|the)\s+(?:[\w-]+\s+){{0,2}}"
             r"(?:schemes?|strateg\w*|approach\w*|architectures?|algorithms?|data models?|layouts?|protocols?)|"
             r"weigh(?:s|ing)?\s+(?:the\s+)?(?:options|alternatives|trade.?offs?)|"
             r"which\s+(?:[\w-]+\s+){1,3}should\s+(?:we|i|it|they)\s+(?:use|adopt|pick|choose|go with)")
SIGNALS = {
    "risk": r"secur\w*|vulnerab\w*|auth|authn|authz|authenticat\w*|authoriz\w*|oauth\w*|passwords?|"
            r"credentials?|secrets?|encrypt\w*|decrypt\w*|payments?|billing|migrat\w*(?!\s+guides?\b)|"
            r"schemas?|databases?|"
            r"production|data loss|permissions?|concurren\w*|race conditions?|deadlocks?|public api|"
            r"breaking changes?|backward.?compat\w*|breaking existing|existing clients|"
            r"(?:session|access|refresh|auth|api|bearer|csrf|id)\s+tokens?|jwts?|(?:token|session)\s+refresh|"
            r"revoked\s+tokens?|races?\s+with|injection|xss|"
            r"cross.site|role checks?|privileges?|access control|soft.?delet\w*|"
            r"los(?:e|es|ing|t)\s+(?:\w+\s+)?(?:data|drafts?|work|changes|edits|records|messages)|rate.?limit\w*|"
            r"rollouts?|invalidat\w*|thread.?safe\w*|mutex\w*|(?:data.)?consistency|data.correctness|stale (?:data|reads?)|"
            r"deprecations|deprecated\s+(?:apis?|calls?|functions?|methods?|settings)",
    "scope": r"across|every (?:service|module|package|file)|all (?:services|modules|packages)|multiple "
             r"(?:services|modules|repos|packages)|entire|whole (?:codebase|system|app)|architect\w*|"
             r"redesign\w*|rewrit\w*|monorepo|end.to.end|large.scale|system.wide|" + _SYSTEMS,
    # Capacity, freshness and speed limits a choice has to be worked out against, not just honoured in passing.
    "load": r"performance|throughput|latency|capacity|staleness|freshness|(?:reads?|writes?|requests?|queries) "
            r"(?:per|a|/) ?(?:second|sec|s)|qps|rps|bottlenecks?|under (?:heavy |peak )?load|"
            r"\d[\d,.]*k?\s*(?:per second|/s)|(?:under|below|within|less than|at most|p\d\d(?: of)?)\s+\d+\s*ms",
    "ambiguity": _DECISION + r"|design|investigat\w*|root.?causes?|intermittent\w*|flaky|figure out|not sure|"
                 r"why (?:does|is|do|are|did)|research(?!\.\w)|evaluate|trade.?offs?|best (?:way|approach)|"
                 r"should we|strategy|proposal|sometimes|occasionally|(?:find|identify) the cause",
}
# Words that mean the subtask changes something or has to reason its way to an answer.
# A subtask that does neither is a lookup, whatever it happens to mention.
_OBJECT = r"\s+(?:the|a|an|it|this|that|these|those|its|their|our|my|your|all|each|every)\b"
# "Port", "limit" and "secure" are often a noun or an adjective ("which port", "the retry limit"), so they
# count only before an object, the way a verb takes one; "limit the output" shapes the answer, not the code.
_CHANGE_VERB = (r"(?:add|implement|fix|change|update|refactor|rename|remove|delete|write|create|build|make(?! sure)|"
                r"migrate|convert|upgrade|optimi[sz]e|rewrite|redesign|replace|bump|encrypt|harden|"
                r"limit(?!\s+(?:(?:the|your|its)\s+)?(?:output|results?|list|answer|reply|response|summary|report)\b)"
                rf"(?=\s+[`'\"]|{_OBJECT})|(?:port|secure)(?=\s+[`'\"]|{_OBJECT})|"
                r"escape(?!\s+(?:sequences?|characters?|chars?|codes?|hatch(?:es)?)\b))\b(?!-)")
_CHANGE = rf"\b{_CHANGE_VERB}"
# "Do not change any files" limits a subtask; it doesn't ask for a change. The limit ends with
# its clause, so "Without changing the API, fix the race" and "Do not stop until X is fixed"
# keep their task. A limit that starts with a verb runs past a comma only into a list of short
# verb items closed by "or"/"nor" ("do not add, remove, or rename files"); "and" doesn't close one.
# "Make sure" isn't here: it introduces the task, so _CHANGE skips it instead. "And" or "then"
# ends the limit before a task verb ("do not touch tests and just fix the auth bug"), unless the
# word before it is a limit verb too ("do not add and remove files" is a list). A verb that is
# often a noun ends it only with an object after it, so "the config and build scripts" doesn't,
# and no verb ends it as the first half of a compound noun such as "debug logs" or "audit trails".
_JUDGMENT_VERBS = r"review|audit|design|plan|evaluate|assess|propose"
# Only nouns that form a compound with these verbs; scripts, builds, records and the like are their objects.
_COMPOUND = r"\s+(?:logs?|output|trails?|notes?|mode|flags?|messages?|statements?|info|guides?|reports?)\b"
# "Find why it fails" or "find the race" hunts a defect; "find the regression tests" or "the bug template" looks one up.
_FIND_DEFECT = (r"find(?:s|ing)?\s+(?:out\s+)?why|find(?:s|ing)?\s+(?:the|a|an|this|that)\s+(?:[\w-]+\s+)?"
                r"(?:bugs?|race(?:\s+conditions?)?|races|leaks?|deadlocks?|regressions?|crash(?:es)?|culprits?|"
                rf"causes?)\b(?!-|{_COMPOUND}|\s+(?:tests?|suites?|templates?|trackers?|tickets?|labels?|fix\w*)\b)")
_HUNT_OTHER = (rf"diagnos\w*|investigat\w*|troubleshoot\w*|reproduc\w*|bisect|analy[sz]e|prove|"
               rf"verify the logic|figure out|{_FIND_DEFECT}")
_HUNT_VERBS = rf"debug|{_HUNT_OTHER}"
_REASONING_WORDS = (rf"{_JUDGMENT_VERBS}|{_HUNT_VERBS}|find out (?:why|how|whether|what caus\w*)|root.?caus\w*|"
                    r"compare|recommend")
# Asked for a judgment (review it, design it, plan it), not a hunt for a known kind of bug. Only the verb
# counts, where it starts a sentence or follows and/then/to/please; "the design doc" is a noun.
_JUDGMENT = rf"(?:^[ \t]*|\n[ \t]*|[.;:!?]\s+|\b(?:and|then|to|please)\s+)(?:{_JUDGMENT_VERBS})\b"
_TASK_VERB = (r"(?:implement|fix|refactor|rename|migrate|optimi[sz]e|rewrite|convert|upgrade|redesign|audit|"
              rf"{_HUNT_VERBS}|encrypt|harden|bump)\b(?!-)")
_NOUN_VERB = (r"(?:build|update|change|design|plan|review|add|write|create|delete|remove|replace|make(?! sure)|"
              r"root.?caus\w*|port|limit|secure|escape)\b(?!-)")
_LIMIT_WORD = rf"(?:{_CHANGE_VERB}|(?:edit|modify|move|touch|{_REASONING_WORDS})\b(?!-))"
# Only where a whole token starts: a \b inside "a-b-c" would rescan the rest of the run at every hyphen.
_JOIN = (rf"(?<![\w'-])(?!{_LIMIT_WORD})[\w'-]+\s+(?:and|then)\s+(?:(?:also|then|instead|please|just)\s+){{0,2}}"
         rf"(?:{_TASK_VERB}|{_NOUN_VERB}(?={_OBJECT}))(?!{_COMPOUND})")
_CLAUSE_WORD = rf"\b(?:until|unless|before|after|while|once|but|because)\b|{_JOIN}"
_CLAUSE = rf"(?:(?!{_CLAUSE_WORD})[^.,;:!?\n])*"
_LIST_VERB = rf"\b(?:{_CHANGE_VERB}|(?:edit|modify|move|touch)\b(?!-))"
_ITEM = rf"{_LIST_VERB}(?:\s+[\w'-]+){{0,3}}?"
# At most eight more items, so a pasted run of negations inside one list stays linear.
_LIST = rf",\s*(?:{_ITEM}(?:\s*,\s*{_ITEM}){{0,8}}?(?:\s*,)?\s+)?(?:or|nor)\s+{_LIST_VERB}{_CLAUSE}"
_NEGATED = (rf"\bwithout\b{_CLAUSE}|\b(?:do not|don't|never|no need to)\b(?:\s*,(?:[^.,;:!?\n]{{0,40}},)?)?"
            rf"(?:\s*(?={_LIST_VERB}){_CLAUSE}(?:{_LIST})*|{_CLAUSE})")
_REASONING = rf"\b(?:{_REASONING_WORDS})\b"
# Ruled out for the defect check only: stripping these clauses from the whole brief would also drop a task
# that follows ("avoid breaking the format and add tests").
_RULED_OUT = rf"\b(?:avoid|must not|not to|out of scope)\b:?{_CLAUSE}"
# Work whose point is a defect. "fix" and "debug" count only as verbs ("the fix", "fixture", "debug logging" don't),
# and "fix" before "#" is a closing keyword. The other hunt verbs are matched as they are.
_VERB_START = r"(?:^|\n)[ \t]*|[.;:!?,]\s+|\b(?:and|then|to|please|must|should|or|so)\s+"
_FIX = (rf"(?:{_VERB_START})(?:fix(?:es|ing)?\b(?!\s*#)|debug\b(?!{_COMPOUND}|\s+logging\b))|"
        rf"\bfix(?:es|ing)?(?={_OBJECT})")
_HUNT = rf"\b(?:{_HUNT_OTHER})\b"
_DEFECT = rf"{_FIX}|{_HUNT}"
# A person's request says "analyze the sales numbers" about data as often as about code, so there a hunt verb
# counts only beside a word for a defect.
_DEFECT_NOUN = (r"\b(?:bugs?|failures?|failing|errors?|flaky|crash(?:es|ed|ing)?|regressions?|broken|races?|"
                r"leak(?:s|ing)?)\b")
_THOROUGH = r"\b(?:thorough(?:ly)?|comprehensive(?:ly)?|in[- ]depth|deep dive|exhaustive(?:ly)?)\b"
_LOOKUP = (r"^\s*(?:where|what|which|who|how many|list|show|find|locate|explain|describe|summari[sz]e|"
           r"is there|does)\b")
# What an agent is for, read from its name. Names are short and deliberate; descriptions
# mention too many things in passing to be read this way. Each pattern must match a whole part of
# the name, so "researcher", "relocation-migrator" and "information-extractor" aren't search agents.
_HEAVY_NAME = (r"architect(?:s|ure|ural)?|review(?:s|ers?|ing)?|audit(?:s|ors?|ing)?|(?:cyber)?secur(?:e|ity)|"
               r"debug(?:gers?|ging)?|design(?:ers?|ing)?|plann(?:ers?|ing)|solver")
_LIGHT_NAME = (r"explor(?:e|ers?|ing)|search(?:ers?|ing)?|scouts?|find(?:ers?)?|lookups?|locat(?:e|ors?)|"
               r"fetch(?:ers?)?|summar(?:y|ies|i[sz]ers?)|format(?:ters?)?|lint(?:ers?)?|runners?|"
               r"verif(?:y|iers?|ication)|grep")
# Light only before another part: "docs-lookup" reads the docs, "update-docs" writes them.
_LIGHT_LEAD = r"docs?"


def _named_for(name: str, pattern: str, lead: str | None = None) -> bool:
    parts = re.split(r"[-_:]", name)
    return (any(re.fullmatch(pattern, part) for part in parts)
            or bool(lead) and any(re.fullmatch(lead, part) for part in parts[:-1]))


@dataclass(frozen=True)
class Role:
    """What an agent is for, which no brief changes: a heavy role is raised to its floor, a light one held to
    its cap. `strong` agents start no lower than the request's level, a `checker` reports on finished work, and
    an agent that doesn't `write` has no Write or Edit tool."""
    kind: str | None = None
    floor: Level | None = None
    cap: Level | None = None
    strong: bool = False
    checker: bool = False
    writes: bool = True


ARCHITECT, REVIEWER, DEEP_SOLVER = "architect", "reviewer", "deep-solver"
# Our own role agents by full name, then the two kinds any agent's name can show. Another plugin's solver is
# only a heavy role; ours is for what the others couldn't do.
ROLES = {
    f"{PLUGIN_NAME}:{ARCHITECT}": Role("heavy", floor=Level.complex, strong=True, writes=False),
    f"{PLUGIN_NAME}:{REVIEWER}": Role("heavy", floor=Level.complex, strong=True, writes=False),
    f"{PLUGIN_NAME}:{DEEP_SOLVER}": Role("heavy", floor=Level.hardest, strong=True),
    "heavy": Role("heavy", floor=Level.complex),
    "light": Role("light", cap=Level.standard),
}
_KIND_NAMES = {"heavy": (_HEAVY_NAME, None), "light": (_LIGHT_NAME, _LIGHT_LEAD)}
_CHECKERS = ("verifier", "reviewer")


def role(agent_type: object) -> Role:
    """The role an agent's full name (`plugin:agent` or `agent`) stands for."""
    name = agent_type if isinstance(agent_type, str) else ""
    found = ROLES.get(name)
    if found is None:
        short = name.split(":")[-1].lower()
        found = next((ROLES[kind] for kind, (pattern, lead) in _KIND_NAMES.items()
                      if _named_for(short, pattern, lead)), Role())
    return replace(found, checker=True) if name.endswith(_CHECKERS) else found


LONG_BRIEF = 4000
# Open-ended work with no known approach: the only level the ladder gives its strongest model.
_FRONTIER = (r"\b(?:research-grade|open[- ]ended|no known (?:fix|approach|cause|solution)|first principles|"
             r"novel|unknown (?:root )?cause|long[- ]running investigation|multi[- ]day|unsolved)\b")


# Every pattern above is English, so a brief in another language can't show what it asks for.
# Such a brief is told apart by its small words, never by the absence of English ones: short
# English like "grep TODO src/*.py" has none either.
_ENGLISH_WORDS = frozenset(
    "the a an and or of to in on for with is are be it this that from by at as if not all any each "
    "into then so but which what where how why when who should must can does you we i my our your".split())
_OTHER_WORDS = frozenset(
    "le la les des du dans pour avec une est sur sont pas qui que et au aux cette "
    "el los las del para una por con sin como este esta "
    "il gli della delle nel nella sono di che "
    "der das und ist nicht mit für ein eine den dem zu von auf im "
    "het een van niet voor não com uma em na ao dos de en "
    "função funções conforme adicione implemente corrija crie remova altere arquivo arquivos".split())


# Paths, file names and identifiers ("locales/de.json", "des_encrypt") aren't language evidence.
# A match starts only where an ASCII word run starts, so a long run without a separator stays linear.
_PATHLIKE = r"(?<![A-Za-z0-9-])(?:[A-Za-z0-9-]+[/._\\])+[A-Za-z0-9-]+"


def _not_english(text: str) -> bool:
    text = re.sub(_PATHLIKE, " ", text)
    letters = [c for c in text if c.isalpha()]
    if letters and 2 * sum(not c.isascii() for c in letters) > len(letters):
        return True  # mostly another script, such as Japanese or Cyrillic
    words = re.findall(r"[^\W\d_]+", text)
    other = sum(w in _OTHER_WORDS for w in words)
    return other >= 2 and other > sum(w in _ENGLISH_WORDS for w in words)


# A load limit only makes a brief harder when a design or a choice has to be worked out against it.
# Asking forms only: "the plan", "as designed", "the selected approach" report a choice already made.
_PLAN = r"(?:(?<![\w])(?:to|and|then|please|let's)\s+plan|^[ \t]*plan|\n[ \t]*plan|[.;:!?]\s+plan)\b|\bplanning\b"
_CHOICE = (rf"\b(?:design(?:s|ing)?|{_ASK_CHOICE}|choices?|trade.?offs?|strateg\w*|siz(?:e|es|ing)|"
           rf"select(?:s|ing)?|how many|estimat(?:e|es|ing)|targets?|budgets?)\b|{_PLAN}")


def signals(text: str) -> list[str]:
    found = [name for name, pattern in SIGNALS.items() if re.search(r"\b(?:%s)\b" % pattern, text)]
    return [name for name in found if name != "load" or re.search(_CHOICE, text)]


def _fixes_a_defect(asked: str, request: bool = False) -> bool:
    asked = re.sub(_RULED_OUT, "", asked)
    if not request:
        return bool(re.search(_DEFECT, asked))
    return bool(re.search(_FIX, asked) or re.search(rf"\b(?:{_FIND_DEFECT})", asked)
                or re.search(_HUNT, asked) and re.search(_DEFECT_NOUN, asked))


@dataclass(frozen=True)
class Reading:
    """What a brief says, read once and the same way for a call and for a request: the difficulty signals,
    whether it asks for a change or for reasoning, whether it fixes or hunts a defect, frontier and
    thoroughness wording, whether it is phrased as a lookup, whether it is English, and how long it is.
    `judgment` (asks to review, design or plan) and `systems` (names several systems in a list) are the
    call's own extra reasons for complex, and `request_defect` is the request's narrower defect rule."""
    signals: tuple[str, ...]
    acts: frozenset[str]  # "change" and "reason"
    defect: bool
    frontier: bool
    thorough: bool
    lookup_form: bool
    english: bool
    length: int
    judgment: bool = False
    systems: bool = False
    request_defect: bool = False


def read(text: str, limits: bool = True) -> Reading:
    """Read a brief. A negated limit ("do not change any files") restricts the work and doesn't ask for it, so
    with `limits` its scope, ambiguity and change words are set aside, and its risk words still count. A request
    is read with `limits` off: its wording counts as it stands, except for the defect rule."""
    text = (text or "").lower()
    asked = re.sub(_NEGATED, "", text)
    seen = asked if limits else text
    hits = signals(seen)
    if limits and "risk" not in hits and "risk" in signals(text):
        hits.insert(0, "risk")
    acts = set()
    if re.search(_CHANGE, seen):
        acts.add("change")
    if re.search(_REASONING, seen) or re.search(rf"\b(?:{_DECISION})\b", seen):
        acts.add("reason")
    return Reading(signals=tuple(hits), acts=frozenset(acts), defect=_fixes_a_defect(asked),
                   frontier=bool(re.search(_FRONTIER, seen)), thorough=bool(re.search(_THOROUGH, text)),
                   lookup_form=bool(re.search(_LOOKUP, text)), english=not _not_english(text), length=len(text),
                   judgment=bool(re.search(_JUDGMENT, seen)), systems=bool(re.search(rf"\b{_SYSTEMS}\b", seen)),
                   request_defect=_fixes_a_defect(asked, request=True))


def level(reading: Reading, kind: str, role: Role | None = None) -> dict:
    """The level a reading needs, as a "call" or as a "request", with the reasons. The two differ on purpose.
    A call that asks for nothing is a lookup, while a request with signals and no verb still scores; a request
    over 1,500 characters gains scope, and a call over 4,000 only lifts a lookup to standard."""
    if kind == "request":
        return _request_level(reading)
    return _call_level(reading, role or Role())


def _request_level(r: Reading) -> dict:
    hits = list(r.signals)
    if r.length > 1500 and "scope" not in hits:
        hits.append("scope")  # a long brief is rarely a small task
    if len(hits) >= 3:
        result = Level.frontier if r.frontier else Level.hardest
    elif len(hits) == 2:
        result = Level.complex
    elif "change" in r.acts or hits:
        result = Level.standard
    elif (r.lookup_form or r.length < 80) and r.english:
        result = Level.lookup
    else:
        result = Level.standard
    if result < Level.complex and r.request_defect:
        result, hits = Level.complex, hits + ["fixes or hunts a defect"]
    return {"level": result, "signals": hits}


def _call_level(r: Reading, held: Role) -> dict:
    hits = list(r.signals)
    why = list(hits)
    if r.acts:
        if len(hits) >= 3:
            result = Level.frontier if r.frontier else Level.hardest
        elif len(hits) == 2 and r.frontier:
            result = Level.hardest  # research-grade wording on broad or open work
        elif len(hits) == 2 or "risk" in hits or (hits and r.judgment) or r.systems:
            result = Level.complex
        else:
            result = Level.standard
        if result < Level.complex and r.defect:
            result, why = Level.complex, why + ["fixes or hunts a defect"]
    elif len(hits) >= 2:
        result = Level.standard  # finds or explains something broad or sensitive, but only reads
    elif not r.english:
        result, why = Level.standard, why + ["not in English"]
    else:
        result = Level.lookup
    if result == Level.lookup and (r.thorough or r.length > LONG_BRIEF):
        result, why = Level.standard, why + ["thorough or long brief"]
    if held.floor and result < held.floor:
        result, why = held.floor, why + ["agent's role"]
    elif held.cap and result > held.cap:
        result, why = held.cap, why + ["search or check agent"]
    return {"level": result, "signals": why}


def profile_prompt(prompt: str) -> dict:
    """The user's request as a whole. Used to tell whether the conversation's own model
    is strong enough for it. Fixing or hunting a defect is at least complex, as in profile_call, except that a
    hunt verb such as "analyze" counts only beside a defect word such as "bug" or "failing"."""
    reading = read(prompt, limits=False)
    return {**level(reading, "request"), "changes": "change" in reading.acts}


def profile_call(tool_input: dict, agent_type: str) -> dict:
    """One agent call, judged by its own brief and by what its agent is for."""
    text = f"{tool_input.get('description') or ''}\n{tool_input.get('prompt') or ''}"
    return level(read(text), "call", role(agent_type))


def asks_for_work(text: str) -> bool:
    """Whether text says what to do rather than only naming a target: once paths, numbers, flags and URLs are
    set aside, a change, reasoning or lookup verb with at least one other word. Length alone, or a signal word
    ("the payments service"), names a target without asking for anything."""
    words = [w for w in (text or "").lower().split() if not re.search(r"[/\\.:#]", w) and w[0] not in "-0123456789"]
    phrase = " ".join(words)
    return len(words) >= 2 and bool(re.search(_CHANGE, phrase) or re.search(_REASONING, phrase)
                                    or re.search(_LOOKUP, phrase))
