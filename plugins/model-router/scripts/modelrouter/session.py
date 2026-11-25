"""The model the conversation itself runs on, which subagents inherit by default, and whether it is one of the
setups the registry recommends for the session that plans and delegates."""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Iterator

from . import router, settings, store
from .config import EFFORTS
from .registry import Registry

HEADER = "Model Router"  # $.ui.ask's chip takes 12 characters at most
AGAIN = "Ask again next session"
NEVER = "Don't ask again (/model-router check on brings this back)"


def recommended(reg: Registry, family: str | None, effort: str | None) -> bool:
    floor = reg.session.get(family or "")
    return bool(floor) and effort in EFFORTS and EFFORTS.index(effort) >= EFFORTS.index(floor)


def effort_on(snap: settings.Snapshot, family: str) -> str | None:
    """The effort the main session runs at on `family` before its first request shows it: CLAUDE_CODE_EFFORT_LEVEL, then
    the settings files, then Claude Code's default, held to maxEffortLevel. A --effort flag and an organization's
    default are not visible here (https://code.claude.com/docs/en/model-config.md#adjust-effort-level)."""
    reg, env = snap.registry, snap.options.effort_level
    effort = env if env in EFFORTS else snap.allowed.saved_effort(reg, family) or router.DEFAULT_EFFORT
    return router.capped(effort, snap.allowed.effort_cap(reg, family))[0]


def _named(family: str, effort: str | None) -> str:
    return f"{family.capitalize()}{f' at {effort}' if effort else ''}"


def _efforts(snap: settings.Snapshot, family: str) -> list[str]:
    """The efforts a session on `family` may pick: from its lowest recommended one up, held to maxEffortLevel. Under
    CLAUDE_CODE_EFFORT_LEVEL only that one, since it outranks /effort."""
    reg, env = snap.registry, snap.options.effort_level
    floor, cap = reg.session[family], snap.allowed.effort_cap(reg, family) or "max"
    levels = [e for e in EFFORTS[EFFORTS.index(floor):] if EFFORTS.index(e) <= EFFORTS.index(cap)]
    if env in EFFORTS:
        return [env] if env in levels else []
    return levels


def _remarks(*remarks: str | None) -> str:
    found = [r for r in remarks if r]
    return f" ({', '.join(found)})" if found else ""


def _model_choice(snap: settings.Snapshot, family: str, name: str) -> dict | None:
    """Step one's choice of `name` without its label, with step two's efforts, or None when it can't run here at a
    recommended effort."""
    reg, options = snap.registry, snap.options
    efforts = _efforts(snap, name)
    if not efforts or name not in snap.allowed.permitted(reg, snap.sendable):
        return None
    # An alias that runs an older model here moves to the family's newest ID, or the family is left out.
    lagging = name in reg.older_aliases.get(options.provider or "", {})
    model = None if name == family else reg.newest_id(name, options.provider, snap.allowed, snap.health) if lagging else name
    if name != family and not model:
        return None
    runs = effort_on(snap, name)
    # model-config.md: Claude Code's default effort on the current models, and max lasting one session unless the
    # environment sets it, which a pinned effort never reaches here.
    picks = [{"label": e + _remarks("Recommended" if not i else None, "Claude Code's default" if e == router.DEFAULT_EFFORT else None,
                                    "this session only" if e == "max" else None),
              "done": f"This session is now on {_named(name, e)}.", **({"effort": e} if e != runs else {})}
             for i, e in enumerate(efforts)]
    choice = {"family": name, **({"model": model} if model else {})}
    if len(picks) == 1:  # $.ui.ask pads one option with Yes and No, so a single effort goes with the model
        return {**choice, **{k: v for k, v in picks[0].items() if k != "label"}, "efforts": efforts}
    return {**choice, "efforts": efforts, "then": {
        "question": f"Which effort for {name.capitalize()}? Claude Code keeps this model and effort as your default for new sessions.",
        "header": HEADER, "options": picks}}


def check(snap: settings.Snapshot, model: object, locked: bool = False) -> dict:
    """The session check for a session that starts below the recommended setups, or {} to ask nothing: a question whose
    options may carry the next question (`then`), what picking them does (`model`, `effort`, `check`) and the line
    that says so after (`done`). Asks once per session: a plugin reload, as a don't-ask-again write causes, runs it
    again. `locked`: managed settings own session_check. max_tier caps subagents, not the session, so it filters nothing."""
    reg, options, session_id = snap.registry, snap.options, snap.session_id
    if not isinstance(session_id, str) or not isinstance(model, str) or options.mode != "auto" or options.session_check == "off":
        return {}
    if store.load_entry(settings.SWITCHES, session_id).get("checked"):
        return {}
    family = reg.family(model)
    effort = effort_on(snap, family) if family else None
    fine = recommended(reg, family, effort)

    def mark(entry: dict) -> None:
        entry["checked"] = True
        if family:
            entry["estimate"] = {"model": family, "effort": effort, "recommended": fine}
    store.mutate_entry(settings.SWITCHES, session_id, mark, max_age=settings.SWITCH_KEPT)
    choices = [c for c in (_model_choice(snap, family, n) for n in reg.session) if c] if family and not fine else []
    if not choices:
        return {}
    strongest = max(choices, key=lambda c: reg.strength(c["family"]))
    for i, choice in enumerate(choices):
        floor, efforts = reg.session[choice["family"]], choice.pop("efforts")
        above = EFFORTS.index(floor) > EFFORTS.index(router.DEFAULT_EFFORT)
        choice["label"] = choice["family"].capitalize() + _remarks(
            "Recommended" if not i else "the strongest model" if choice is strongest else None,
            f"at {floor} effort{' or higher' if len(efforts) > 1 else ''}" if above else None)
    stay = {"label": f"Stay on {_named(family, effort)}", **({} if locked else {"then": {
        "question": "Ask about this again?", "header": HEADER, "options": [{"label": AGAIN}, {"label": NEVER, "check": "off"}]}})}
    question = (f"Model Router works best with a strong session to plan and delegate. This session is on "
                f"{_named(family, effort)}. Which model should it run on?")
    return {"question": question, "header": HEADER, "options": [*choices, stay]}


def observe(snap: settings.Snapshot, model: object, effort: object) -> None:
    """What the main loop's latest request carried, which the estimate at the session's start only guessed."""
    family = snap.registry.family(model)
    if not isinstance(snap.session_id, str) or not family:
        return
    level = effort if effort in EFFORTS else None
    setup = {"model": family, "effort": level, "recommended": recommended(snap.registry, family, level)}
    store.mutate_entry(settings.SWITCHES, snap.session_id, lambda entry: entry.update(setup=setup), max_age=settings.SWITCH_KEPT)


def record(session_id: str | None, model: object) -> None:
    if session_id and isinstance(model, str) and model:
        store.save_entry("sessions", session_id, {"model": model})


def transcript_tail(transcript_path: object, size: int) -> str | None:
    """The last `size` bytes of the transcript as text, or None when it can't be read."""
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    try:
        with open(os.path.expanduser(transcript_path), "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - size))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return None


def tool_calls(transcript_path: object, marker: str) -> Iterator[tuple[dict, dict]]:
    """`(row, block)` for each tool_use block in the transcript's last 1 MiB. Only lines holding
    `marker` are parsed, and unreadable rows are skipped."""
    tail = transcript_tail(transcript_path, 1024 * 1024)
    for line in (tail or "").splitlines():
        if '"tool_use"' not in line or marker not in line:
            continue
        try:
            row = json.loads(line)
            content = row["message"]["content"]
        except (ValueError, KeyError, TypeError):
            continue
        if not isinstance(row, dict):
            continue
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                yield row, block


def handed_back(transcript_path: object) -> bool:
    """Did the agent deliver a report through SubagentHandback?"""
    for _, block in tool_calls(transcript_path, '"SubagentHandback"'):
        message = (block.get("input") or {}).get("message") if isinstance(block.get("input"), dict) else None
        if block.get("name") == "SubagentHandback" and isinstance(message, str) and message.strip():
            return True
    return False


def from_transcript(transcript_path: object) -> tuple[str | None, float | None]:
    """The model of the newest main-thread assistant message, read from the file's tail, and
    when it was written (None when its timestamp doesn't parse)."""
    tail = transcript_tail(transcript_path, 512 * 1024)
    if tail is None:
        return None, None
    for line in reversed(tail.splitlines()):
        if '"assistant"' not in line or '"model"' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("isSidechain"):
            continue  # a subagent's message, in versions that keep those in the same file
        message = row.get("message")
        model = message.get("model") if isinstance(message, dict) else None
        if isinstance(model, str) and model and not model.startswith("<"):
            try:
                at = _dt.datetime.fromisoformat(str(row.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
            except ValueError:
                at = None
            return model, at
    return None, None


def model(session_id: str | None, transcript_path: object = None) -> str | None:
    """Best effort: the newer of the transcript's last main-thread reply and what SessionStart or
    PostModelSwitch recorded, so a /model switch counts before the new model replies. A reply with
    no readable time wins. SessionStart doesn't always carry `model`."""
    seen, at = from_transcript(transcript_path)
    entry = store.load_entry("sessions", session_id)
    recorded = entry.get("model")
    if recorded and (not seen or (at is not None and store.stamp(entry.get("_at")) > at)):
        return recorded
    return seen or recorded
