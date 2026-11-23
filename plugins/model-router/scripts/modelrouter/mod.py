"""The mod path as its own adapter: hooks/router.ts forwards the engine's events here, and each
call answers with what the mod applies. A spawn goes through the one spawn procedure and answers
the model, effort and reason; a main-loop turn gets an effort from the request's level.
Every call fails open like the settings hooks: cli.py swallows what a handler raises."""
from __future__ import annotations

import re

from . import allowlist, intake, ledger, request, router, session, settings, store, upstream
from . import config as cfg
from . import spawn as procedure
from .registry import Registry


def active(session_id: str | None) -> bool:
    """Whether the mod has announced itself for this session, so the fallback stands down."""
    return bool(store.load_entry("mod", session_id).get("active"))


def _heard(hook_input: dict) -> None:
    """The mod's session.start fires once per process, never after /clear, so every turn and spawn
    says the mod is running. They also bring the settings layers the mod read."""
    session_id = hook_input.get("session_id")
    if session_id and not active(session_id):
        store.save_entry("mod", session_id, {"active": True, "at": store.now()})
    allowlist.remember(session_id, hook_input.get("settings"))


def start(hook_input: dict) -> None:
    _heard(hook_input)


def spawn(hook_input: dict) -> dict | None:
    """The model and effort for one agent about to start, or None to leave the call as it is."""
    _heard(hook_input)
    # The spawn procedure reads agent_id as "the call comes from inside a subagent".
    snap = settings.Snapshot(hook_input)
    spawned = procedure.decide({**hook_input, "agent_id": hook_input.get("parent_agent_id")}, snap)
    decision = spawned.decision
    newest = _newest(snap, decision)
    procedure.record(spawned, "mod", newest)
    if decision["action"] != "rewrite" and not newest:
        return None
    return {"model": newest or decision["model"], "effort": decision["effort"], "reason": decision["reason"]}


def _newest(snap: settings.Snapshot, decision: dict) -> str | None:
    """The newest full ID to send in place of the decided family alias, on a provider whose
    alias lags. Only agent.spawn takes a full ID; the Agent tool's model takes an alias
    (https://code.claude.com/docs/en/sub-agents.md)."""
    if decision["action"] == "deny" or decision["version"]:
        return None
    return snap.registry.newest_id(decision["model"], snap.options.provider, snap.allowed, snap.health)


def _effort_keeps_cache(reg: Registry, options: settings.Settings, model: object) -> bool:
    """Only the models in `main.effort_keeps_cache`, on Anthropic's API without a gateway,
    Mantle, pre-release betas turned off or prompt caching turned off for them, keep the prompt
    cache when effort changes (https://code.claude.com/docs/en/prompt-caching.md#changing-effort-level).
    A model with no known version, as after /clear, gets the benefit of the doubt."""
    if options.cloud or options.mantle or options.base_url or options.no_betas or "" in options.cache_off:
        return False
    found = upstream.version_of(model)
    if not found:
        return True
    family, version = found
    if family in options.cache_off:
        return False
    rule = reg.keeps_cache.get(family)
    if not isinstance(rule, str) or not re.fullmatch(r"(?:>=)?\d+(?:\.\d+)*", rule):
        return False
    wanted = tuple(int(part) for part in rule.lstrip(">=").split("."))
    return version >= wanted if rule.startswith(">=") else version == wanted


def turn(hook_input: dict) -> dict | None:
    """Effort for one main-loop turn, from the request. Effort is the lever that keeps the
    prompt cache on current models; switching the conversation's model would not."""
    session_id = hook_input.get("session_id")
    _heard(hook_input)
    snap = settings.Snapshot(hook_input)
    reg, options = snap.registry, snap.options
    seen = intake.from_turn(hook_input.get("text"), hook_input.get("command"), hook_input.get("origin"))
    if options.mode != "off" and seen.person and seen.command:
        # The level is the request's, which the floor and the Stop check read, whatever the effort switches say.
        try:
            request.raise_to(session_id, seen, reg, snap.usable, snap.raises, priority=options.priority)
        except OSError:
            pass
    if (options.mode != "auto" or not options.main_effort or options.effort_level
            or hook_input.get("first_party") is False):
        return None
    if not seen.person:
        return None
    # The mod's live model, when it is an ID; a record can miss a fallback to another model.
    model = hook_input.get("model")
    model = model if upstream.version_of(model) else session.model(session_id)
    if not _effort_keeps_cache(reg, options, model):
        return None
    level = seen.level
    effort = reg.main_effort.get(level)
    if effort not in cfg.EFFORTS:
        return None
    effort, held = router.capped(effort, snap.allowed.effort_cap(reg, reg.family(model)))
    ledger.turn(session_id, level, effort, seen.command, reason=held.lstrip("; ") or None)
    return {"level": level, "effort": effort}


def usage(hook_input: dict) -> None:
    """One ledger row per turn, its cost the sum of its requests, each priced on its own."""
    found = hook_input.get("usage")
    if isinstance(found, dict) and isinstance(found.get("model"), str):
        ledger.usage(settings.Snapshot(hook_input).registry, hook_input.get("session_id"), hook_input.get("agent_id"),
                     found, hook_input.get("requests"))
