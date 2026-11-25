"""Whether a prompt UserPromptSubmit hands over is the person's own request, or a message Claude Code
delivered from someone else or wrote itself."""
from __future__ import annotations

import re

# Observed engine behavior, not documented.
DELIVERED = "Another Claude session sent a message:"
DELIVERED_TAGS = ("<teammate-message", "<cross-session-message")
PASTED = "<pasted_content"
# The framings Claude Code wraps a turn in that isn't typed: a background task's notice, a typed command's or a `!`
# shell's echo, a local command's output, hook context. Observed, not documented.
MACHINE_TAG = re.compile(r"<(?:task-notification|command-[\w-]+|bash-[\w-]+|local-command-[\w-]+|system-reminder)\b")
# Nothing but tag elements is framing too, when every tag name is hyphenated, as each framing seen so far is:
# a person's prompt written in XML tags (<task>, <instructions>) has plain names. Each element ends at its first
# closing tag and the name's parts are capped, so a long prompt can't make the match backtrack.
ELEMENTS = re.compile(r"(?:\s*<([A-Za-z0-9_]{1,32}(?:-\w{1,32}){1,8})[^>]*>(?:(?!</\1\s*>).)*</\1\s*>)+\s*", re.S)


def is_delivered(text: object) -> bool:
    """A teammate's report or another session's message, framed as Claude Code delivers it."""
    first, _, rest = (text if isinstance(text, str) else "").lstrip().partition("\n")
    return first.strip() == DELIVERED and rest.lstrip().startswith(DELIVERED_TAGS)


def is_user_prompt(text: object) -> bool:
    """UserPromptSubmit also fires for background-task notifications, teammates' reports
    and other sessions' messages. Those must not get a note meant for the person's own
    request. Pasted text is the user's, though it arrives wrapped in a <pasted_content> tag, and so is
    a request that opens with a tag of its own, such as `<button> does nothing`."""
    text = (text if isinstance(text, str) else "").lstrip()
    if is_delivered(text):
        return False
    return text.startswith(PASTED) or not (MACHINE_TAG.match(text) or ELEMENTS.fullmatch(text))
