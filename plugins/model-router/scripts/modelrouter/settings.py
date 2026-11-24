"""The plugin's options and the environment facts routing depends on, read once per hook
call or command and passed to whatever needs them.

Hooks get the options as CLAUDE_PLUGIN_OPTION_<KEY> variables. Skill commands run through
the Bash tool, which doesn't carry them, so they read what the last SessionStart recorded."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from . import allowlist, health, ladder, learn, store
from . import config as cfg
from .config import MODES, PRIORITIES, Level, parse_mode
from .registry import Registry

CLOUDS = ("bedrock", "vertex", "foundry")
PROVIDERS = (*CLOUDS, "anthropic_aws", "anthropic_google_cloud", "mantle")


@dataclass(frozen=True)
class Settings:
    mode: str = "auto"  # routing_mode merged with the override files: the weakest wins
    routing_mode: str = "auto"  # the user's own option
    priority: str = "balanced"
    cap: str | None = None  # max_tier, when it names a registry model
    pins: str = "respect"  # agent_pins: respect or override
    main_effort: bool = False
    update_check: bool = True
    verify_gate: bool = True
    strong_agent_gate: bool = True
    subagent_model: str | None = None  # CLAUDE_CODE_SUBAGENT_MODEL
    force: bool = False  # CLAUDE_CODE_SUBAGENT_MODEL_FORCE
    subagent_effort: str | None = None  # CLAUDE_CODE_SUBAGENT_EFFORT
    workflow_model: str | None = None  # CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL, which pins every workflow agent's model
    effort_level: str | None = None  # CLAUDE_CODE_EFFORT_LEVEL
    cloud: str | None = None  # bedrock, vertex or foundry, from CLAUDE_CODE_USE_<NAME>
    provider: str | None = None  # the one provider the CLAUDE_CODE_USE_<NAME> switches select, if clear
    api_key: str | None = field(default=None, repr=False)  # ANTHROPIC_API_KEY
    base_url: bool = False  # ANTHROPIC_BASE_URL is set
    no_betas: bool = False  # CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS
    mantle: bool = False  # CLAUDE_CODE_USE_MANTLE
    cache_off: tuple[str, ...] = ()  # the suffix of each DISABLE_PROMPT_CACHING* switch that is on: "" for all models


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _on(value: str | None) -> bool:
    return value not in ("false", "0", "no", "off")


def _provider(on: set) -> str | None:
    """Bedrock and Foundry win over Claude Platform on AWS, and Claude Models on Google Cloud
    over Agent Platform (claude-platform-on-aws.md, claude-models-on-google-cloud-anthropic-managed.md).
    Mantle, or any other mix, has no documented alias table, so it counts as unknown."""
    if "mantle" in on:
        return None
    if on & {"bedrock", "foundry"}:
        on = on - {"anthropic_aws"}
    if "anthropic_google_cloud" in on:
        on = on - {"vertex"}
    return next(iter(on)) if len(on) == 1 else None


def _cache_off() -> tuple[str, ...]:
    """Found by prefix, so a new per-model variant needs no code
    (https://code.claude.com/docs/en/env-vars.md)."""
    prefix = "DISABLE_PROMPT_CACHING"
    return tuple(sorted(name[len(prefix):].lstrip("_").lower() for name, value in os.environ.items()
                        if name.startswith(prefix) and name[len(prefix):len(prefix) + 1] in ("", "_") and truthy(value)))


def resolve(reg: Registry | None = None, recorded: bool = False) -> Settings:
    """`reg` validates the cap and carries the override files' routing_mode.
    `recorded` falls back to what SessionStart recorded, for skill commands."""
    state = store.load_state() if recorded else {}

    def option(name: str) -> str | None:
        value = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name.upper()}")
        if not value:
            value = state.get(name)
        return value.strip().lower() if isinstance(value, str) and value.strip() else None

    def env(name: str) -> str | None:
        return os.environ.get(name) or None

    user = parse_mode(option("routing_mode")) or "auto"
    from_files = parse_mode(reg.routing_mode) if reg else None
    cap = option("max_tier")
    return Settings(
        mode=from_files if from_files and MODES.index(from_files) < MODES.index(user) else user,
        routing_mode=user,
        priority=option("priority") if option("priority") in PRIORITIES else "balanced",
        cap=cap if reg and cap in reg.models else None,
        pins="override" if option("agent_pins") == "override" else "respect",
        main_effort=truthy(option("main_effort")),
        update_check=_on(option("update_check")),
        verify_gate=_on(option("verify_gate")),
        strong_agent_gate=_on(option("strong_agent_gate")),
        subagent_model=env("CLAUDE_CODE_SUBAGENT_MODEL"),
        force=truthy(env("CLAUDE_CODE_SUBAGENT_MODEL_FORCE")),
        subagent_effort=env("CLAUDE_CODE_SUBAGENT_EFFORT"),
        workflow_model=env("CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL"),
        effort_level=env("CLAUDE_CODE_EFFORT_LEVEL"),
        cloud=next((c for c in CLOUDS if truthy(env(f"CLAUDE_CODE_USE_{c.upper()}"))), None),
        provider=_provider({p for p in PROVIDERS if truthy(env(f"CLAUDE_CODE_USE_{p.upper()}"))}),
        api_key=env("ANTHROPIC_API_KEY"),
        base_url=bool(env("ANTHROPIC_BASE_URL")),
        no_betas=truthy(env("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS")),
        mantle=truthy(env("CLAUDE_CODE_USE_MANTLE")),
        cache_off=_cache_off(),
    )


class Snapshot:
    """What one hook call or command knows about this setup, built once at its entry. The
    registry and options are read at once; the rest on first use, so a hook that never asks
    for the allowlist or the health table never reads them."""

    def __init__(self, hook_input: dict | None = None, recorded: bool = False) -> None:
        self.hook_input = hook_input
        self.session_id = (hook_input or {}).get("session_id")
        self.registry = cfg.load(hook_input)
        self.options = resolve(self.registry, recorded)
        self.project: Path = cfg.project_dir(hook_input)

    @cached_property
    def health(self) -> health.Health:
        return health.load()

    @cached_property
    def allowed(self) -> allowlist.Allowlist:
        return allowlist.read(self.session_id, self.project, (self.hook_input or {}).get("settings"))

    @cached_property
    def raises(self) -> dict[str, int]:
        return learn.raises()

    def pick(self, level: Level) -> ladder.Pick:
        """The ladder's pick for `level` under this setup's priority, usable models and learned raises."""
        return ladder.pick(self.registry, level, self.options.priority, self.usable, self.raises)

    def settle(self) -> None:
        """Write what reads only noticed: an outage the health table found too long. Dry runs skip it."""
        if "health" in vars(self):
            try:
                self.health.settle()
            except OSError:
                pass

    @cached_property
    def usable(self) -> list[str]:
        return self.registry.available(self.options.cap, allowed=self.allowed, table=self.health)


def record(settings: Settings) -> None:
    """Save every option where skill commands can read it."""
    flag = {True: "true", False: "false"}
    options = {"routing_mode": settings.routing_mode, "priority": settings.priority,
               "max_tier": settings.cap or "", "agent_pins": settings.pins,
               "main_effort": flag[settings.main_effort], "update_check": flag[settings.update_check],
               "verify_gate": flag[settings.verify_gate],
               "strong_agent_gate": flag[settings.strong_agent_gate]}
    if any(store.load_state().get(k) != v for k, v in options.items()):
        store.update_state(lambda state: state.update(options))
