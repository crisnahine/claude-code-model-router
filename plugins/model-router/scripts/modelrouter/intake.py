"""Whether a prompt or a turn is the person's request, and at what level, for both paths: the
UserPromptSubmit hook (a typed skill is read from its file on disk, a plugin's from its install folder) and the mod's turn.start
(the engine hands over the rendered skill body)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import agents, store
from . import config as cfg
from .config import Level
from .profile import asks_for_work, profile_prompt

# Where the mod saw a turn's prompt come from (prompt.submit's origin.kind): whether it is the person's.
# The machine kinds are skipped. unclassified is a same-user socket's typed prompt too, and a coordinator's
# hand-off is a worker's real task. A kind not listed is the person's.
ORIGINS = {
    "composer": True, "bridge": True, "sdk": True, "scheduled-trigger": True, "projects-relay": True,
    "channel": True, "coordinator": True, "auto-continuation": True, "unclassified": True,
    "slack-ping": True, "plugin": True,
    "task-notification": False, "peer": False, "peer-send-message": False,
    "observer": False, "observer-activity": False,
}
MACHINE_ORIGINS = frozenset(k for k, person in ORIGINS.items() if not person)
# Observed engine behavior, not documented.
DELIVERED = "Another Claude session sent a message:"
DELIVERED_TAGS = ("<teammate-message", "<cross-session-message")
PASTED = "<pasted_content"


def is_delivered(text: object) -> bool:
    """A teammate's report or another session's message, framed as Claude Code delivers it."""
    first, _, rest = (text if isinstance(text, str) else "").lstrip().partition("\n")
    return first.strip() == DELIVERED and rest.lstrip().startswith(DELIVERED_TAGS)


def is_user_prompt(text: object) -> bool:
    """UserPromptSubmit also fires for background-task notifications, teammates' reports
    and other sessions' messages. Those must not reset what is known about the user's
    request. Pasted text is the user's, though it arrives wrapped in a <pasted_content> tag."""
    text = (text if isinstance(text, str) else "").lstrip()
    if is_delivered(text):
        return False
    return not text.startswith("<") or text.startswith(PASTED)


# A skill's body is standing instructions, not the request: only its opening counts, so its length
# never reads as scope and a !`cmd` output or checklist further down can't make every use hard.
SKILL_BODY_CHARS = 1500


_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.S)


def _receives(body: str, frontmatter: str, count: int) -> bool:
    """Whether a raw skill file has a placeholder that receives one of `count` arguments: an unescaped
    $ARGUMENTS, $N or $ARGUMENTS[N] with N below count (0-based), or a declared name the body uses.
    A $ followed by an amount ($500, $1.00) is prose."""
    if re.search(r"(?<!\\)\$ARGUMENTS\b(?!\[)", body):
        return True
    for index in re.findall(r"(?<!\\)\$(?:ARGUMENTS\[(\d+)\]|(\d+)(?![\d.,]))", body):
        if int(index[0] or index[1]) < count:
            return True
    block = re.search(r"(?ms)^arguments\s*:(.*?)(?=^\S|\Z)", frontmatter)
    names = re.findall(r"[A-Za-z_]\w*", block.group(1)) if block else []
    return any(re.search(rf"(?<!\\)\${re.escape(name)}\b", body) for name in names)


def profile_skill(args: object, body: object, rendered: bool) -> dict:
    """A typed skill's profile. A skill whose content takes its arguments (a $ARGUMENTS, $1 or named
    placeholder) and is given a task in them runs that task, so the arguments alone set the level: its own
    instructions, a pipeline's say, are the same on every use. Arguments that only name a target leave the
    task to the body, at most complex. A skill that takes no placeholder, or is typed with no arguments, is
    its body: the higher of the arguments' level and the body's opening counts. `rendered` is the engine's expansion, where a skill that takes no
    placeholder gets the arguments appended as "ARGUMENTS: ..."; otherwise body is the raw file."""
    args = args.strip() if isinstance(args, str) else ""
    body = (body if isinstance(body, str) else "").lstrip("\ufeff")
    body = re.sub(r"\A\s*Base directory for this skill: [^\n]*\n", "", body)
    front = _FRONTMATTER.match(body)
    body = body[front.end():] if front else body
    if rendered:
        # The engine appends the trailer last, so only its last occurrence counts, and any trailer at all
        # means no placeholder took them: an unexpected format errs toward the higher level.
        head, trailer, _ = body.rpartition("\nARGUMENTS: ")
        takes_args = bool(args) and not trailer
        body = head if trailer else body
    else:
        takes_args = bool(args) and _receives(body, front.group(1) if front else "", len(args.split()))
    if takes_args and asks_for_work(args):
        return profile_prompt(args)
    parts = [profile_prompt(text) for text in (args, body[:SKILL_BODY_CHARS])]
    if takes_args and parts[1]["level"] > Level.complex:
        # A template that serves any target: its own wording, a pipeline's gates say, isn't one task's depth.
        parts[1] = {**parts[1], "level": Level.complex}
    return max(parts, key=lambda p: p["level"])


def _read_skill(paths: list[Path]) -> str | None:
    """The first of `paths` that is a regular file, up to 64 KB."""
    for path in paths:
        try:
            if not path.is_file():  # a FIFO or device would hang or never end
                continue
            with path.open("rb") as fh:
                return fh.read(64 * 1024).decode("utf-8", "replace")
        except OSError:
            continue
    return None


def _typed_skill(prompt: str, project: Path) -> dict | None:
    """The profile behind "/name args" when name is a project or user skill or command on disk, or
    "/plugin:name args". UserPromptSubmit sees only the typed line, so it reads the body from the file: under
    .claude for a project or user skill, under the plugin's install folder for a plugin's, found from the install
    record alone, since a walk of the plugins folder is too slow for every typed line. A plugin's skill it can't
    find is profiled by its arguments alone, which the mod's reading of the rendered body can raise."""
    match = re.match(r"/(?:([A-Za-z0-9_.-]+):)?([A-Za-z0-9_.-]+)(?:\s+(.*))?\Z", prompt.strip(), re.S)
    if not match:
        return None
    plugin, name, args = match.group(1), match.group(2), match.group(3) or ""
    if plugin:
        roots = [cfg.PLUGIN_ROOT] if plugin == cfg.PLUGIN_NAME else agents.installed_roots(plugin)
        body = _read_skill([root / "skills" / name / "SKILL.md" for root in roots])
        return profile_skill(args, body or "", rendered=False)
    project, home = project / ".claude", store.claude_home()
    body = _read_skill([project / "skills" / name / "SKILL.md", home / "skills" / name / "SKILL.md",
                        project / "commands" / f"{name}.md", home / "commands" / f"{name}.md"])
    return None if body is None else profile_skill(args, body, rendered=False)


@dataclass(frozen=True)
class Intake:
    delivered: bool
    person: bool
    level: Level | None
    signals: tuple[str, ...]
    command: str | None = None


def from_prompt(prompt: object, project: Path) -> Intake:
    """The UserPromptSubmit path."""
    text = prompt if isinstance(prompt, str) else ""
    delivered = is_delivered(text)
    if not is_user_prompt(text):
        return Intake(delivered, False, None, ())
    result = _typed_skill(text, project) or profile_prompt(text)
    return Intake(delivered, True, result["level"], tuple(result["signals"]))


def from_turn(text: object, command: object, origin: object) -> Intake:
    """The mod's turn.start path. The command's name, args and rendered body come from the engine alone;
    the text only shows whether the command's own line opens this turn."""
    text = text if isinstance(text, str) else ""
    name = command.get("name") if isinstance(command, dict) else None
    if not (name is not None and text.lstrip().startswith("<command-") and f"<command-name>/{name}</command-name>" in text):
        command = None
    delivered = is_delivered(text)
    if not ((command is not None or is_user_prompt(text)) and str(origin) not in MACHINE_ORIGINS):
        return Intake(delivered, False, None, ())
    result = profile_skill(command.get("args"), command.get("body"), rendered=True) if command else profile_prompt(text)
    return Intake(delivered, True, result["level"], tuple(result["signals"]), command.get("name") if command else None)
