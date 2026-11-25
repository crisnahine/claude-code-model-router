"""The plugin's options and the environment facts routing depends on, read once per hook
call or command and passed to whatever needs them.

Hooks get the options as CLAUDE_PLUGIN_OPTION_<KEY> variables. Skill commands run through
the Bash tool, which doesn't carry them, so each skill passes them to cli.py as flags."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from . import allowlist, health, ladder
from . import config as cfg
from .config import Level, parse_mode
from .registry import Registry

CLOUDS = ("bedrock", "vertex", "foundry")
PROVIDERS = (*CLOUDS, "anthropic_aws", "anthropic_google_cloud", "mantle")
# The userConfig keys in plugin.json.
OPTIONS = ("routing_mode", "max_tier", "verify_gate")
UNKNOWN = "unknown"  # a provider whose aliases aren't documented: only aliases are sent, and none counts as moving


@dataclass(frozen=True)
class Settings:
    mode: str = "auto"  # routing_mode
    cap: str | None = None  # max_tier, when it names a registry model
    verify_gate: bool = True
    subagent_model: str | None = None  # CLAUDE_CODE_SUBAGENT_MODEL
    force: bool = False  # CLAUDE_CODE_SUBAGENT_MODEL_FORCE
    subagent_effort: str | None = None  # CLAUDE_CODE_SUBAGENT_EFFORT
    workflow_model: str | None = None  # CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL, which pins every workflow agent's model
    effort_level: str | None = None  # CLAUDE_CODE_EFFORT_LEVEL
    agent_teams: bool = False  # CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS
    cloud: str | None = None  # bedrock, vertex or foundry, from CLAUDE_CODE_USE_<NAME>
    provider: str | None = None  # the provider the CLAUDE_CODE_USE_<NAME> switches select: None is the Anthropic API


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _on(value: str | None) -> bool:
    return value not in ("false", "0", "no", "off")


def _provider(on: set) -> str | None:
    """Bedrock and Foundry win over Claude Platform on AWS, and Claude Models on Google Cloud
    over Agent Platform (claude-platform-on-aws.md, claude-models-on-google-cloud-anthropic-managed.md).
    Mantle, or any other mix, has no documented alias table, so it is UNKNOWN."""
    if not on:
        return None
    if "mantle" in on:
        return UNKNOWN
    if on & {"bedrock", "foundry"}:
        on = on - {"anthropic_aws"}
    if "anthropic_google_cloud" in on:
        on = on - {"vertex"}
    return next(iter(on)) if len(on) == 1 else UNKNOWN


def resolve(reg: Registry | None = None) -> Settings:
    """`reg` validates the cap."""

    def option(name: str) -> str | None:
        value = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name.upper()}")
        return value.strip().lower() if isinstance(value, str) and value.strip() else None

    def env(name: str) -> str | None:
        return os.environ.get(name) or None

    cap = option("max_tier")
    return Settings(
        mode=parse_mode(option("routing_mode")) or "auto",
        cap=cap if reg and cap in reg.models else None,
        verify_gate=_on(option("verify_gate")),
        subagent_model=env("CLAUDE_CODE_SUBAGENT_MODEL"),
        force=truthy(env("CLAUDE_CODE_SUBAGENT_MODEL_FORCE")),
        subagent_effort=env("CLAUDE_CODE_SUBAGENT_EFFORT"),
        workflow_model=env("CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL"),
        effort_level=env("CLAUDE_CODE_EFFORT_LEVEL"),
        agent_teams=truthy(env("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")),
        cloud=next((c for c in CLOUDS if truthy(env(f"CLAUDE_CODE_USE_{c.upper()}"))), None),
        # A host that manages the provider ignores these switches in settings files, and its alias table isn't documented.
        provider=UNKNOWN if env("CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST")
        else _provider({p for p in PROVIDERS if truthy(env(f"CLAUDE_CODE_USE_{p.upper()}"))}),
    )


class Snapshot:
    """What one hook call or command knows about this setup, built once at its entry. The
    registry and options are read at once; the rest on first use, so a hook that never asks
    for the allowlist or the health table never reads them."""

    def __init__(self, hook_input: dict | None = None, sends_ids: bool = True) -> None:
        self.hook_input = hook_input
        self.session_id = (hook_input or {}).get("session_id")
        self.registry = cfg.load(hook_input)
        self.options = resolve(self.registry)
        self.project: Path = cfg.project_dir(hook_input)
        self.sends_ids = sends_ids  # False on the Agent call fallback, whose `model` takes only an alias

    @cached_property
    def health(self) -> health.Health:
        return health.load()

    @cached_property
    def allowed(self) -> allowlist.Allowlist:
        return allowlist.read(self.session_id, (self.hook_input or {}).get("settings"), self.options.provider)

    @cached_property
    def sendable(self) -> frozenset[str]:
        """Families whose alias runs an older model here and whose newest ID this path sends in its place."""
        reg, provider = self.registry, self.options.provider
        return frozenset(n for n in reg.older_aliases.get(provider or "", {})
                         if self.sends_ids and reg.newest_id(n, provider, self.allowed, self.health))

    @cached_property
    def lagging(self) -> frozenset[str]:
        """Families whose alias runs an older model here with no newest ID sent in its place, unless the
        user pinned the family's model. The router's own pick moves one family up from them."""
        return frozenset(n for n in self.registry.older_aliases.get(self.options.provider or "", {})
                         if n not in self.sendable and not os.environ.get(f"ANTHROPIC_DEFAULT_{n.upper()}_MODEL"))

    def pick(self, level: Level) -> ladder.Pick:
        """The ladder's pick for `level` among this setup's usable models."""
        return ladder.pick(self.registry, level, self.usable, self.lagging)

    def settle(self) -> None:
        """Write what reads only noticed: an outage the health table found too long. Dry runs skip it."""
        if "health" in vars(self):
            try:
                self.health.settle()
            except OSError:
                pass

    @cached_property
    def usable(self) -> list[str]:
        return self.registry.available(self.options.cap, allowed=self.allowed, table=self.health, sendable=self.sendable)
