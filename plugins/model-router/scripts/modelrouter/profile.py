"""Score how much reasoning a request or a subtask needs, from its wording and from
the agent it is given to. Deterministic and fast: this runs on every agent call.

It is an estimate. Claude sees the code and can overrule it by naming a model on a
call, and the feedback loop in learn.py corrects levels that keep proving too low."""
from __future__ import annotations

import re

from .config import LEVELS, PLUGIN_NAME

# Two or more systems named in a list ("across the API, the web client and ...") are cross-module work.
_SYSTEM = r"(?:services?|clients?|api|gateway|backend|frontend|workers?|apps?|layers?|modules?)"
_SYSTEMS = rf"(?:across|touching|between)\s+(?:the\s+)?(?:[\w-]+\s+){{0,2}}?{_SYSTEM},\s+(?:the\s+)?(?:[\w-]+\s+){{0,2}}?{_SYSTEM}"
SIGNALS = {
    "risk": r"secur\w*|vulnerab\w*|auth|authn|authz|authenticat\w*|authoriz\w*|oauth\w*|passwords?|"
            r"credentials?|secrets?|encrypt\w*|decrypt\w*|payments?|billing|migrat\w*|schemas?|databases?|"
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
            r"(?:per|a|/) ?(?:second|sec|s)|qps|rps|bottlenecks?|under (?:heavy |peak )?load",
    "ambiguity": r"design|investigat\w*|root.?causes?|intermittent\w*|flaky|figure out|not sure|"
                 r"why (?:does|is|do|are|did)|research|evaluate|trade.?offs?|best (?:way|approach)|"
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
_HUNT_VERBS = (r"debug|diagnos\w*|investigat\w*|troubleshoot\w*|reproduc\w*|bisect|analy[sz]e|prove|"
               r"verify the logic|figure out")
_REASONING_WORDS = (rf"{_JUDGMENT_VERBS}|{_HUNT_VERBS}|find out (?:why|how|whether|what caus\w*)|root.?caus\w*|"
                    r"compare|recommend")
# Asked for a judgment (review it, design it, plan it), not a hunt for a known kind of bug. Only the verb
# counts, where it starts a sentence or follows and/then/to/please; "the design doc" is a noun.
_JUDGMENT = rf"(?:^\s*|\n\s*|[.;:!?]\s+|\b(?:and|then|to|please)\s+)(?:{_JUDGMENT_VERBS})\b"
_TASK_VERB = (r"(?:implement|fix|refactor|rename|migrate|optimi[sz]e|rewrite|convert|upgrade|redesign|audit|"
              rf"{_HUNT_VERBS}|encrypt|harden|bump)\b(?!-)")
_NOUN_VERB = (r"(?:build|update|change|design|plan|review|add|write|create|delete|remove|replace|make(?! sure)|"
              r"root.?caus\w*|port|limit|secure|escape)\b(?!-)")
# Only nouns that form a compound with these verbs; scripts, builds, records and the like are their objects.
_COMPOUND = r"\s+(?:logs?|output|trails?|notes?|mode|flags?|messages?|statements?|info|guides?|reports?)\b"
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
_THOROUGH = r"\b(?:thorough(?:ly)?|comprehensive(?:ly)?|in[- ]depth|deep dive|exhaustive(?:ly)?)\b"
_LOOKUP = (r"^\s*(?:where|what|which|who|how many|list|show|find|locate|explain|describe|summari[sz]e|"
           r"is there|does)\b")
# What an agent is for, read from its name. Names are short and deliberate; descriptions
# mention too many things in passing to be read this way.
_HEAVY_NAME = r"architect|review|audit|secur|debug|design|plann|(?:^|[-_])solver(?:$|[-_])"
_LIGHT_NAME = r"explor|search|scout|find|lookup|locat|fetch|summar|format|lint|runner|verif|docs?[-_]|grep"
LONG_BRIEF = 4000
# Open-ended work with no known approach: the only level the ladder gives its strongest model.
_FRONTIER = (r"\b(?:research(?:-grade)?|open[- ]ended|no known (?:fix|approach|cause|solution)|first principles|"
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


def signals(text: str) -> list[str]:
    return [name for name, pattern in SIGNALS.items() if re.search(r"\b(?:%s)\b" % pattern, text)]


def profile_prompt(prompt: str) -> dict:
    """The user's request as a whole. Used to tell whether the conversation's own model
    is strong enough for it."""
    text = (prompt or "").lower()
    hits = signals(text)
    changes = bool(re.search(_CHANGE, text))
    if len(text) > 1500 and "scope" not in hits:
        hits.append("scope")  # a long brief is rarely a small task
    if len(hits) >= 3:
        level = "frontier" if re.search(_FRONTIER, text) else "hardest"
    elif len(hits) == 2:
        level = "complex"
    elif changes or hits:
        level = "standard"
    elif (re.search(_LOOKUP, text) or len(text) < 80) and not _not_english(text):
        level = "lookup"
    else:
        level = "standard"
    return {"level": level, "signals": hits, "changes": changes}


def profile_call(tool_input: dict, agent_type: str) -> dict:
    """One agent call, judged by its own brief and by what its agent is for."""
    text = f"{tool_input.get('description') or ''}\n{tool_input.get('prompt') or ''}".lower()
    # A limit still names what the work touches, so its risk counts; its scope and ambiguity
    # words describe what to avoid, so they don't, and neither do its change words.
    asked = re.sub(_NEGATED, "", text)
    hits = signals(asked)
    if "risk" not in hits and "risk" in signals(text):
        hits.insert(0, "risk")
    why = list(hits)
    if re.search(_CHANGE, asked) or re.search(_REASONING, asked):
        if len(hits) >= 3:
            level = "frontier" if re.search(_FRONTIER, asked) else "hardest"
        elif len(hits) == 2 and re.search(_FRONTIER.replace("research(?:-grade)?", "research-grade"), asked):
            level = "hardest"  # research-grade wording on broad or open work
        elif (len(hits) == 2 or "risk" in hits or (hits and re.search(_JUDGMENT, asked))
              or re.search(rf"\b{_SYSTEMS}\b", asked)):
            level = "complex"
        else:
            level = "standard"
    elif len(hits) >= 2:
        level = "standard"  # finds or explains something broad or sensitive, but only reads
    elif _not_english(text):
        level, why = "standard", why + ["not in English"]
    else:
        level = "lookup"
    if level == "lookup" and (re.search(_THOROUGH, text) or len(text) > LONG_BRIEF):
        level, why = "standard", why + ["thorough or long brief"]
    name = agent_type.split(":")[-1].lower()
    if re.search(_HEAVY_NAME, name):
        # Our deep-solver is for what the others couldn't do; another plugin's solver is only a heavy role.
        floor = "hardest" if agent_type.lower() == f"{PLUGIN_NAME}:deep-solver" else "complex"
        if LEVELS.index(level) < LEVELS.index(floor):
            level, why = floor, why + ["agent's role"]
    elif re.search(_LIGHT_NAME, name):
        if LEVELS.index(level) > LEVELS.index("standard"):
            level, why = "standard", why + ["search or check agent"]
    return {"level": level, "signals": why}


def asks_for_work(text: str) -> bool:
    """Whether text says what to do rather than only naming a target: once paths, numbers, flags and URLs are
    set aside, a change, reasoning or lookup verb with at least one other word. Length alone, or a signal word
    ("the payments service"), names a target without asking for anything."""
    words = [w for w in (text or "").lower().split() if not re.search(r"[/\\.:#]", w) and w[0] not in "-0123456789"]
    phrase = " ".join(words)
    return len(words) >= 2 and bool(re.search(_CHANGE, phrase) or re.search(_REASONING, phrase)
                                    or re.search(_LOOKUP, phrase))
