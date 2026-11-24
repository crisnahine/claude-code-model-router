"""The ledger: one writer per row kind, the turn Spend rule, and a read of the rows."""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from . import router, store
from .registry import Registry


def spawn(session_id: str | None, via: str, decision: router.Decision, description: object,
          kind: str | None = None) -> None:
    action = decision["action"]
    row = {"event": "spawn", "via": via, "session": session_id, "agent": decision["agent"], "action": action,
           "level": decision["level"], "signals": decision["signals"],
           "model": None if action == "deny" else decision["model"] or decision["was"],
           "was": decision["was"], "effort": decision["effort"], "source": decision["source"],
           "escalated_from": decision["escalated_from"], "reason": decision["reason"],
           "description": str(description or "")[:120]}
    if kind is not None:
        row["kind"] = kind
    if decision.get("held"):
        row["held"] = decision["held"]
    store.append_ledger(row)


def result(session_id: str | None, agent: str, asked: str | None, resolved_model: object, status: object,
           total_tokens: object, models_used: list[str] | None = None) -> None:
    store.append_ledger({"event": "result", "session": session_id, "agent": agent, "asked": asked,
                         "resolved_model": resolved_model, "status": status, "total_tokens": total_tokens,
                         **({"models_used": models_used} if models_used else {})})


def model_error(session_id: str | None, agent: object, model: str) -> None:
    store.append_ledger({"event": "model_error", "session": session_id, "agent": agent, "model": model})


def turn(session_id: str | None, level: str, effort: str | None, command: str | None = None,
         reason: str | None = None) -> None:
    """A main-loop turn: the effort it was raised to, or null with the reason it wasn't."""
    store.append_ledger({"event": "turn", "session": session_id, "level": level, "effort": effort,
                         **({"command": command} if command else {}), **({"reason": reason} if reason else {})})


def _tokens(usage: dict) -> dict:
    tokens = {k: int(usage.get(k) or 0) for k in
              ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
    if "ephemeral_1h_input_tokens" in usage:
        tokens["ephemeral_1h_input_tokens"] = int(usage.get("ephemeral_1h_input_tokens") or 0)
    return tokens


def _usage(value: object) -> bool:
    return isinstance(value, dict) and isinstance(value.get("model"), str)


def turn_cost(reg: Registry, usage: dict, requests: object) -> float | None:
    """The turn's dollars: each request priced on its own model, None if any has no price.
    Without requests the summed tokens are priced at the base rate."""
    requests = [r for r in requests if _usage(r)] if isinstance(requests, list) else []
    if not requests:
        return reg.cost(usage["model"], _tokens(usage), whole_turn=True)
    costs = [reg.cost(r["model"], _tokens(r)) for r in requests]
    return None if None in costs else round(sum(costs), 6)


def usage(reg: Registry, session_id: str | None, agent_id: object, usage: object, requests: object,
          agent: str | None = None) -> None:
    if not _usage(usage):
        return
    store.append_ledger({"event": "usage", "session": session_id, "agent_id": agent_id, "model": usage["model"],
                         **_tokens(usage), "cost_usd": turn_cost(reg, usage, requests), **({"agent": agent} if agent else {})})


def read(days: int | None = None, path: Path | None = None) -> list[dict]:
    rows: list[dict] = []
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days) if days else None
    try:
        lines = (path or store.ledger_path()).read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
            if cutoff and _dt.datetime.fromisoformat(row["ts"]) < cutoff:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows
