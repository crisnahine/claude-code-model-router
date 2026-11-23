"""The Agent call path's own pieces when no mod routes the spawn: the rewrite to `updatedInput`,
the rewrite waiting for its result, the refused-rewrite guard and the one deny. What a result means
for the call is `calls.py`'s, the same on both paths."""
from __future__ import annotations

from . import health, settings, spawn, store
from . import config as cfg
from . import router
from .registry import Registry

# shortcut: a refused call re-issued within this many seconds is rewritten again; key on a message id if hooks get one
REFUSED_AFTER = 5


def route(hook_input: dict, snap: settings.Snapshot) -> dict | None:
    """The PreToolUse answer: the decision as the call's rewritten input and a note, or the
    one deny that holds the user's cap."""
    spawned = spawn.decide(hook_input, snap)
    session_id, decision = hook_input.get("session_id"), spawned.decision
    _guard(session_id, spawned.key, decision, snap.registry, snap.options)
    spawn.record(spawned, "fallback")
    if decision["action"] == "rewrite":
        # A rewrite auto mode denies after review gets no result, so it stays pending and
        # the next copy counts as refused (https://code.claude.com/docs/en/hooks.md#pretooluse-decision-control).
        try:
            remember(session_id, spawned.key, decision)
        except OSError:
            pass
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "updatedInput": _rewritten_input(cfg.tool_input(hook_input), decision),
            "additionalContext": _note(decision)}}
    if decision["action"] == "deny":
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": decision["reason"]}}
    return None


def remember(session_id: str | None, key: str, decision: router.Decision) -> None:
    """A rewrite waits here for its result."""
    pending = {"model": decision["model"] or decision["was"]}
    store.mutate_entry("pending", session_id, lambda waiting: store.keep(waiting, key, pending))


def forget(session_id: str | None, key: str) -> None:
    """A result or an error came back, so the call's waiting rewrite was accepted."""
    if key in store.load_entry("pending", session_id):
        store.mutate_entry("pending", session_id, lambda waiting: waiting.pop(key, None))


def refused(session_id: str | None, key: str) -> dict | None:
    """This call's fallback rewrite, when the call shows up again before its result: the
    rewrite didn't run. A rewrite only seconds from now, either way, is a copy issued alongside
    it: a clock stepped back a little mustn't pass a fan-out's copies through unrouted, and one
    stamped far ahead still counts as refused, so a big jump back can't loop a rewrite."""
    rewrite = store.load_entry("pending", session_id).get(key)
    if not isinstance(rewrite, dict) or abs(store.now() - (rewrite.get("at") or 0)) < REFUSED_AFTER:
        return None
    return rewrite


def _guard(session_id: str | None, key: str, decision: router.Decision, reg: Registry,
           options: settings.Settings) -> None:
    """We rewrote this very call before and no result came back: the rewrite wasn't
    accepted, or something else stopped the call. Rewriting it again could loop."""
    if decision["action"] != "rewrite" or not decision["model"]:
        return
    earlier = refused(session_id, key)
    if earlier is None:
        return
    cap, untouched = options.cap, decision["was"]
    if cap and reg.strength(untouched) > reg.strength(cap):
        decision.update(action="deny", reason=(
            f"Model Router: subagents are capped at {cap} by the user, and this call would run on "
            f"{untouched}. Re-issue it with model: \"{decision['model']}\"."))
        return
    decision.update(action="keep", model=None, repeat_of=earlier.get("model"), reason=(
        "this call was rewritten once and came back without having run, so it is passed through unchanged"))
    try:
        health.refused(decision["repeat_of"])
    except OSError:
        pass


def _rewritten_input(tool_input: dict, decision: router.Decision) -> dict:
    """The whole call as it should run: Claude Code replaces the input, it doesn't merge."""
    updated = dict(tool_input)
    if decision.get("model"):
        updated["model"] = decision["model"]
    if decision.get("effort"):
        updated["effort"] = decision["effort"]
    return updated


def _note(decision: router.Decision) -> str:
    """One line for Claude, delivered with the agent's result."""
    if decision.get("model"):
        effort = f" at {decision['effort']} effort" if decision.get("effort") else ""
        what = f"model to {decision['model']}{effort}"
    else:
        what = f"effort to {decision['effort']}"
    return (f"Model Router set this agent's {what} ({decision['reason']}). "
            "The same call re-issued after a weak result runs one level higher.")
