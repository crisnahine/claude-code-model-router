"""The plugin's options and the environment facts routing depends on, read once per hook
call or command and passed to whatever needs them.

Hooks get the options as CLAUDE_PLUGIN_OPTION_<KEY> variables. Skill commands run through
the Bash tool, which doesn't carry them, so they read what the last SessionStart recorded."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import store
from .config import MODES, PRIORITIES, _mode

CLOUDS = ("bedrock", "vertex", "foundry")


@dataclass(frozen=True)
class Settings:
    mode: str = "auto"  # routing_mode merged with the override files: the weakest wins
    routing_mode: str = "auto"  # the user's own option
    priority: str = "balanced"
    cap: str | None = None  # max_tier, when it names a registry model
    pins: str = "respect"  # agent_pins: respect or override
    main_effort: bool = True
    update_check: bool = True
    verify_gate: bool = True
    subagent_model: str | None = None  # CLAUDE_CODE_SUBAGENT_MODEL
    force: bool = False  # CLAUDE_CODE_SUBAGENT_MODEL_FORCE
    subagent_effort: str | None = None  # CLAUDE_CODE_SUBAGENT_EFFORT
    effort_level: str | None = None  # CLAUDE_CODE_EFFORT_LEVEL
    cloud: str | None = None  # bedrock, vertex or foundry, from CLAUDE_CODE_USE_<NAME>
    api_key: str | None = field(default=None, repr=False)  # ANTHROPIC_API_KEY
    base_url: bool = False  # ANTHROPIC_BASE_URL is set


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _on(value: str | None) -> bool:
    return value is None or value not in ("false", "0", "no", "off")


def resolve(config: dict | None = None, recorded: bool = False) -> Settings:
    """`config` validates the cap and carries the override files' routing_mode.
    `recorded` falls back to what SessionStart recorded, for skill commands."""
    state = store.load_state() if recorded else {}

    def option(name: str) -> str | None:
        value = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name.upper()}")
        if not value:
            value = state.get(name)
        return value.strip().lower() if isinstance(value, str) and value.strip() else None

    def env(name: str) -> str | None:
        return os.environ.get(name) or None

    config = config or {}
    user = _mode(option("routing_mode")) or "auto"
    from_files = _mode(config.get("routing_mode"))
    cap = option("max_tier")
    return Settings(
        mode=from_files if from_files and MODES.index(from_files) < MODES.index(user) else user,
        routing_mode=user,
        priority=option("priority") if option("priority") in PRIORITIES else "balanced",
        cap=cap if cap in (config.get("models") or {}) else None,
        pins="override" if option("agent_pins") == "override" else "respect",
        main_effort=_on(option("main_effort")),
        update_check=_on(option("update_check")),
        verify_gate=_on(option("verify_gate")),
        subagent_model=env("CLAUDE_CODE_SUBAGENT_MODEL"),
        force=truthy(env("CLAUDE_CODE_SUBAGENT_MODEL_FORCE")),
        subagent_effort=env("CLAUDE_CODE_SUBAGENT_EFFORT"),
        effort_level=env("CLAUDE_CODE_EFFORT_LEVEL"),
        cloud=next((c for c in CLOUDS if truthy(env(f"CLAUDE_CODE_USE_{c.upper()}"))), None),
        api_key=env("ANTHROPIC_API_KEY"),
        base_url=bool(env("ANTHROPIC_BASE_URL")),
    )


def record(settings: Settings) -> None:
    """Save every option where skill commands can read it."""
    flag = {True: "true", False: "false"}
    options = {"routing_mode": settings.routing_mode, "priority": settings.priority,
               "max_tier": settings.cap or "", "agent_pins": settings.pins,
               "main_effort": flag[settings.main_effort], "update_check": flag[settings.update_check],
               "verify_gate": flag[settings.verify_gate]}
    if any(store.load_state().get(k) != v for k, v in options.items()):
        store.update_state(lambda state: state.update(options))
