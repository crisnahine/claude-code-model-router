"""The mod path as its own adapter: hooks/router.ts forwards the engine's events here, and each
call answers with what the mod applies. A spawn goes through the one spawn procedure and answers
the model, effort and reason; a main-loop turn's effort is a pure decision over TurnFacts.
Every call fails open like the settings hooks: cli.py swallows what a handler raises."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import allowlist, intake, ledger, request, router, session, settings, store, upstream
from . import config as cfg
from . import spawn as procedure
from .config import Level
from .registry import Registry

# A turn at this level or above that gets no effort says why in the ledger; lower ones are most turns.
LOGGED_FROM = Level.complex


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


@dataclass(frozen=True)
class TurnFacts:
    """Everything that decides a main-loop turn's effort. The defaults describe a turn nothing
    stops, so a case names only what differs."""
    level: Level | None = None  # the person's request level; None when the turn isn't the person's
    mode: str = "auto"  # routing mode
    main_effort: bool = True  # the option
    effort_level: bool = False  # CLAUDE_CODE_EFFORT_LEVEL is set
    first_party: bool | None = None  # the mod's credential check: False behind a gateway or a third-party provider
    model: str | None = None  # the session's model, as an ID when known
    cloud: str | None = None  # bedrock, vertex or foundry
    mantle: bool = False
    gateway: bool = False  # ANTHROPIC_BASE_URL
    no_betas: bool = False  # CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS
    cache_off: tuple[str, ...] = ()  # DISABLE_PROMPT_CACHING switches on: "" for all models, else a family
    effort_cap: str | None = None  # maxEffortLevel for the model


@dataclass(frozen=True)
class TurnEffort:
    """`effort` is None when the turn gets none; `reason` says why, or what held the effort lower."""
    effort: str | None
    level: Level | None
    reason: str | None = None


def turn_effort(facts: TurnFacts, reg: Registry) -> TurnEffort:
    """The effort for one main-loop turn: raised from the request's level, never lowered (turn.step
    applies only a raise), and left alone wherever a change would re-read the context uncached
    (https://code.claude.com/docs/en/prompt-caching.md#changing-effort-level)."""
    level = facts.level
    if level is None:
        return TurnEffort(None, None, "the turn isn't the person's request")
    why = (f"routing is {facts.mode}" if facts.mode != "auto" else
           "the main_effort option is off" if not facts.main_effort else
           "CLAUDE_CODE_EFFORT_LEVEL sets the effort" if facts.effort_level else
           "the session holds no first-party credential (a gateway or a third-party provider)"
           if facts.first_party is False else _cache_drop(reg, facts))
    if why:
        return TurnEffort(None, level, why)
    effort = reg.main_effort.get(level)
    if effort not in cfg.EFFORTS:
        return TurnEffort(None, level, f"no main-loop effort is set for {level}")
    effort, held = router.capped(effort, facts.effort_cap)
    return TurnEffort(effort, level, held.lstrip("; ") or None)


def _cache_drop(reg: Registry, facts: TurnFacts) -> str | None:
    """Why an effort change would drop the prompt cache here, or None. Only the models in
    `main.effort_keeps_cache`, on Anthropic's API without a gateway, Mantle, pre-release betas turned
    off or prompt caching turned off for them, keep it. A model with no known version, as after
    /clear, gets the benefit of the doubt."""
    if facts.cloud == "foundry":
        return "the docs don't list Foundry among the places an effort change keeps the prompt cache"
    if facts.cloud:
        return f"an effort change drops the prompt cache on {facts.cloud}"
    if facts.mantle:
        return "an effort change drops the prompt cache on Bedrock's Mantle endpoint"
    if facts.gateway:
        return "ANTHROPIC_BASE_URL sets a gateway, and what stays cached through it depends on the gateway"
    if facts.no_betas:
        return "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS is set, and an effort change then drops the prompt cache"
    if "" in facts.cache_off:
        return "prompt caching is turned off (DISABLE_PROMPT_CACHING)"
    found = upstream.version_of(facts.model)
    if not found:
        return None
    family, version = found
    if family in facts.cache_off:
        return f"prompt caching is turned off for {family}"
    rule = reg.keeps_cache.get(family)
    if isinstance(rule, str) and re.fullmatch(r"(?:>=)?\d+(?:\.\d+)*", rule):
        wanted = tuple(int(part) for part in rule.lstrip(">=").split("."))
        if (version >= wanted) if rule.startswith(">=") else (version == wanted):
            return None
    return f"an effort change drops the prompt cache on {family} {'.'.join(map(str, version))}"


def _facts(hook_input: dict, snap: settings.Snapshot, seen: intake.Intake) -> TurnFacts:
    options, reg = snap.options, snap.registry
    model = None
    cap = None
    if seen.person and seen.level in reg.main_effort:  # a level with no effort to give reads nothing
        # The mod's live model, when it is an ID; a record can miss a fallback to another model.
        model = hook_input.get("model")
        model = model if upstream.version_of(model) else session.model(hook_input.get("session_id"))
        cap = snap.allowed.effort_cap(reg, reg.family(model))
    first_party = hook_input.get("first_party")
    return TurnFacts(
        level=seen.level if seen.person else None, mode=options.mode, main_effort=options.main_effort,
        effort_level=bool(options.effort_level), first_party=first_party if isinstance(first_party, bool) else None,
        model=model, cloud=options.cloud, mantle=options.mantle, gateway=options.base_url,
        no_betas=options.no_betas, cache_off=options.cache_off, effort_cap=cap)


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
    decided = turn_effort(_facts(hook_input, snap, seen), reg)
    if decided.level and options.mode != "off" and (decided.effort or decided.level >= LOGGED_FROM):
        ledger.turn(session_id, decided.level, decided.effort, seen.command, reason=decided.reason)
    return {"level": decided.level, "effort": decided.effort} if decided.effort else None


def usage(hook_input: dict) -> None:
    """One ledger row per turn, its cost the sum of its requests, each priced on its own."""
    found = hook_input.get("usage")
    if isinstance(found, dict) and isinstance(found.get("model"), str):
        stage = hook_input.get("subagent_type")
        ledger.usage(settings.Snapshot(hook_input).registry, hook_input.get("session_id"), hook_input.get("agent_id"),
                     found, hook_input.get("requests"), stage if isinstance(stage, str) and stage else None)
