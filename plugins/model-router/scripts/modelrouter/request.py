"""The current request's id, and the model a prompt names. The only module that reads or writes the
`prompts` entry for a session. Where each call was routed is `calls.py`'s.

A new request replaces the whole record. A message from someone else never starts one."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import store
from .registry import Registry

_NEGATION = re.compile(r"(?:\bnot|n't|\bnever|\bwithout|\binstead of|\bavoid|\bno)\s+(?:\w+\s+){0,3}$")


def named_model(reg: Registry, prompt: str) -> str | None:
    """A model the user may be asking for: "use opus", "run this on haiku". These are
    ordinary words too, so this only prompts Claude to decide; it forces nothing."""
    # A model only a project adds is left out: its name could be any word, and the note would put it on calls.
    names = "|".join(re.escape(n) for n, m in reg.models.items() if m.trusted)
    if not names:
        return None
    text = (prompt or "").lower()
    pattern = r"\b(?:use|using|with|on|via|to)\s+(?:the\s+)?(?:claude\s+)?(%s)\b(?!\s*(?:agent|subagent)s?\b)" % names
    for match in re.finditer(pattern, text):
        if not _NEGATION.search(text[max(0, match.start() - 40):match.start()]):
            return match.group(1)
    return None


@dataclass(frozen=True)
class Request:
    prompt_id: str | None = None


def load(session_id: str | None) -> Request:
    prompt_id = store.load_entry("prompts", session_id).get("prompt_id")
    return Request(prompt_id if isinstance(prompt_id, str) else None)


def start(session_id: str | None, *, prompt_id: str | None) -> None:
    """The person's new request."""
    store.save_entry("prompts", session_id, {"prompt_id": prompt_id})
