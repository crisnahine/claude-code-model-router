"""The model the conversation itself runs on, which subagents inherit by default."""
from __future__ import annotations

import json
import os

from . import store


def record(session_id: str | None, model: object) -> None:
    if session_id and isinstance(model, str) and model:
        store.save_entry("sessions", session_id, {"model": model})


def from_transcript(transcript_path: object) -> str | None:
    """The model of the newest main-thread assistant message, read from the file's tail."""
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    try:
        with open(os.path.expanduser(transcript_path), "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 512 * 1024))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
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
            return model
    return None


def model(session_id: str | None, transcript_path: object = None) -> str | None:
    """Best effort: what the transcript shows, else what SessionStart or PostModelSwitch
    recorded. SessionStart doesn't always carry `model`."""
    return from_transcript(transcript_path) or store.load_entry("sessions", session_id).get("model")
