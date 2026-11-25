"""The ledger: one writer per row kind, and a read of the rows."""
from __future__ import annotations

import datetime as _dt
import json
import math
from pathlib import Path

from . import router, store


def spawn(session_id: str | None, via: str, decision: router.Decision, kind: str | None = None,
          on_record: bool = True) -> None:
    action = decision["action"]
    row = {"event": "spawn", "via": via, "session": session_id, "agent": decision["agent"], "action": action,
           "level": decision["level"],
           "model": None if action == "deny" else decision["model"] or decision["was"],
           "was": decision["was"], "effort": decision["effort"], "source": decision["source"],
           "escalated_from": decision["escalated_from"], "reason": decision["reason"]}
    if kind is not None:
        row["kind"] = kind
    if decision.get("held"):
        row["held"] = decision["held"]
    if not on_record:
        row["on_record"] = False
    store.append_ledger(row)


def result(session_id: str | None, agent: str, asked: str | None, resolved_model: object, status: object,
           models_used: list[str] | None = None) -> None:
    store.append_ledger({"event": "result", "session": session_id, "agent": agent, "asked": asked,
                         "resolved_model": resolved_model, "status": status,
                         **({"models_used": models_used} if models_used else {})})


def model_error(session_id: str | None, agent: object, model: str) -> None:
    store.append_ledger({"event": "model_error", "session": session_id, "agent": agent, "model": model})


def gate(session_id: str | None, kind: str) -> None:
    store.append_ledger({"event": "gate", "session": session_id, "kind": kind})


TOKENS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _tokens(usage: dict) -> dict:
    return {"model": usage["model"], **{k: int(store.stamp(usage.get(k))) for k in TOKENS}}


def _usage(value: object) -> bool:
    return isinstance(value, dict) and isinstance(value.get("model"), str)


def usage(session_id: str | None, agent_id: object, agent: str | None, models: object = None, cost: object = None,
          subscription: bool = False, turn: object = None) -> None:
    """One subagent turn's row. `models` and `cost` are Claude Code's figures for the agent so far, its
    tokens per model and its dollar estimate, so the agent's newest row stands for all of its turns.
    Without them `turn` is that turn's tokens alone: a workflow agent's, which Claude Code doesn't list."""
    if not isinstance(agent_id, str) or not agent_id:
        return
    listed = [_tokens(m) for m in models if _usage(m)] if isinstance(models, list) else []
    cost = cost if isinstance(cost, dict) else {}
    usd = cost.get("usd")
    if listed or cost:
        row = {"models": listed, "running": True,
               "cost_usd": usd if isinstance(usd, (int, float)) and not isinstance(usd, bool) and math.isfinite(usd) else None,
               **({"unknown_price": True} if cost.get("hasUnknownPrice") is True else {}),
               **({"subscription": True} if subscription else {})}
    elif _usage(turn):
        row = {"models": [_tokens(turn)]}
    else:
        return
    store.append_ledger({"event": "usage", "session": session_id, "agent_id": agent_id,
                         **({"agent": agent} if agent else {}), **row})


def read(days: int | None = None, path: Path | None = None, session: str | None = None) -> list[dict]:
    """The rows of the last `days` days, or all; with `session`, only that session's."""
    rows: list[dict] = []
    # Only a line holding the session's id is parsed: the status line reads the ledger after every spawn.
    mark = f'"session": {json.dumps(session)}' if session else ""
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days) if days else None
    try:
        lines = (path or store.ledger_path()).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return rows
    for line in lines:
        if mark not in line:
            continue
        try:
            row = store.loads(line)
            if cutoff and _dt.datetime.fromisoformat(row["ts"]) < cutoff:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        if isinstance(row, dict) and (not session or row.get("session") == session):
            rows.append(row)
    return rows


def since(days: int, path: Path | None = None) -> str | None:
    """The oldest row's `ts` when the ledger starts inside the last `days` days, so `read(days)` covers
    less than asked: a new ledger, or one `store.append_ledger` cut to its newer half."""
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    try:
        with (path or store.ledger_path()).open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    ts = store.loads(line)["ts"]
                    return ts if _dt.datetime.fromisoformat(ts) > cutoff else None
                except (KeyError, ValueError, TypeError):
                    continue
    except OSError:
        pass
    return None
