"""The model the conversation itself runs on, which subagents inherit by default."""
from __future__ import annotations

import json
import os
from typing import Iterator

from . import store


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


def from_transcript(transcript_path: object) -> str | None:
    """The model of the newest main-thread assistant message, read from the file's tail."""
    tail = transcript_tail(transcript_path, 512 * 1024)
    if tail is None:
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
