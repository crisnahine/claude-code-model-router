"""Score how much reasoning a request or a subtask needs, from its wording and from
the agent it is given to. Deterministic and fast: this runs on every agent call.

It is an estimate. Claude sees the code and can overrule it by naming a model on a
call, and the feedback loop in learn.py corrects levels that keep proving too low."""
from __future__ import annotations

import re

from .config import LEVELS

SIGNALS = {
    "risk": r"secur\w*|vulnerab\w*|auth|authn|authz|authenticat\w*|authoriz\w*|oauth\w*|passwords?|"
            r"credentials?|secrets?|encrypt\w*|decrypt\w*|payments?|billing|migrat\w*|schemas?|databases?|"
            r"production|data loss|permissions?|concurren\w*|race conditions?|deadlocks?|public api|"
            r"breaking changes?|backward.?compat\w*",
    "scope": r"across|every (?:service|module|package|file)|all (?:services|modules|packages)|multiple "
             r"(?:services|modules|repos|packages)|entire|whole (?:codebase|system|app)|architect\w*|"
             r"redesign\w*|rewrit\w*|monorepo|end.to.end|large.scale|system.wide",
    "ambiguity": r"design|investigat\w*|root.?causes?|intermittent\w*|flaky|figure out|not sure|"
                 r"why (?:does|is|do|are|did)|research|evaluate|trade.?offs?|best (?:way|approach)|"
                 r"should we|strategy|proposal",
}
# Words that mean the subtask changes something or has to reason its way to an answer.
# A subtask that does neither is a lookup, whatever it happens to mention.
_CHANGE_VERB = (r"(?:add|implement|fix|change|update|refactor|rename|remove|delete|write|create|build|make(?! sure)|"
                r"migrate|convert|upgrade|optimi[sz]e|rewrite|redesign|replace)\b(?!-)")
_CHANGE = rf"\b{_CHANGE_VERB}"
# "Do not change any files" limits a subtask; it doesn't ask for a change. The limit ends with
# its clause, so "Without changing the API, fix the race" and "Do not stop until X is fixed"
# keep their task. A limit that starts with a verb runs past a comma only into a list of short
# verb items closed by "or"/"nor" ("do not add, remove, or rename files"); "and" doesn't close one.
# "Make sure" isn't here: it introduces the task, so _CHANGE skips it instead.
_CLAUSE_WORD = r"\b(?:until|unless|before|after|while|once|but|because)\b"
_CLAUSE = rf"(?:(?!{_CLAUSE_WORD})[^.,;:!?\n])*"
_LIST_VERB = rf"\b(?:{_CHANGE_VERB}|(?:edit|modify|move|touch)\b(?!-))"
_ITEM = rf"{_LIST_VERB}(?:\s+[\w'-]+){{0,3}}?"
# At most eight more items, so a pasted run of negations inside one list stays linear.
_LIST = rf",\s*(?:{_ITEM}(?:\s*,\s*{_ITEM}){{0,8}}?(?:\s*,)?\s+)?(?:or|nor)\s+{_LIST_VERB}{_CLAUSE}"
_NEGATED = (rf"\bwithout\b{_CLAUSE}|\b(?:do not|don't|never|no need to)\b(?:\s*,(?:[^.,;:!?\n]{{0,40}},)?)?"
            rf"(?:\s*(?={_LIST_VERB}){_CLAUSE}(?:{_LIST})*|{_CLAUSE})")
_REASONING = (r"\b(?:review|audit|analy[sz]e|debug|diagnos\w*|plan|design|prove|verify the logic|"
              r"investigat\w*|troubleshoot\w*|figure out|root.?caus\w*|reproduc\w*|bisect)\b")
_THOROUGH = r"\b(?:thorough(?:ly)?|comprehensive(?:ly)?|in[- ]depth|deep dive|exhaustive(?:ly)?)\b"
_LOOKUP = (r"^\s*(?:where|what|which|who|how many|list|show|find|locate|explain|describe|summari[sz]e|"
           r"is there|does)\b")
# What an agent is for, read from its name. Names are short and deliberate; descriptions
# mention too many things in passing to be read this way.
_HEAVY_NAME = r"architect|review|audit|secur|debug|design|plann|solver"
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
    "het een van niet voor não com uma em na ao dos de en".split())


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
    asked = re.sub(_NEGATED, "", text)
    hits = signals(asked)
    why = list(hits)
    if re.search(_CHANGE, asked) or re.search(_REASONING, asked):
        if len(hits) >= 3:
            level = "frontier" if re.search(_FRONTIER, asked) else "hardest"
        elif len(hits) == 2 or "risk" in hits:
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
        if LEVELS.index(level) < LEVELS.index("complex"):
            level, why = "complex", why + ["agent's role"]
    elif re.search(_LIGHT_NAME, name):
        if LEVELS.index(level) > LEVELS.index("standard"):
            level, why = "standard", why + ["search or check agent"]
    return {"level": level, "signals": why}
