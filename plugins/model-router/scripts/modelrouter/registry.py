"""The registry: which models exist, which are usable in this setup right now, and how to pick one.

`config.load` builds one Registry per hook call from config/models.json and the override
files, and every step reads that value. Nothing here names a model: the list comes from the
files, narrowed by what this setup shows: the user's cap, the availableModels and
deniedModels settings, and models that have been failing, silently running as something
else, or not accepted on agent calls."""
from __future__ import annotations

import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from . import health

if TYPE_CHECKING:
    from . import allowlist
    from .config import Level


@dataclass(frozen=True)
class Model:
    name: str
    strength: float
    speed: float | None = None
    cost: float | None = None
    use_for: str = ""
    project: bool = False  # added by a project's override file, so its rank is only that file's claim

    @property
    def trusted(self) -> bool:
        return not self.project


@dataclass(frozen=True)
class LevelSpec:
    needs: float  # a strength: the weakest model the level runs on
    effort: str | None


@dataclass(frozen=True)
class Rule:
    """What an override file says about one agent. The user's file sets `model`, `level` and `route` in
    full. A project's file only raises: its `level` becomes `floor`, its `model` becomes `suggested` and
    applies only when it isn't weaker than what the call would run on without it, and its `route` is held back."""
    model: str | None = None
    level: Level | None = None
    route: bool | None = None
    floor: Level | None = None
    suggested: str | None = None


@dataclass(frozen=True)
class Held:
    """Something a project's override file asked for that the registry does not apply, and why."""
    kind: str  # level, model or rule
    name: str
    reason: str


@dataclass(frozen=True)
class Registry:
    models: Mapping[str, Model]
    levels: Mapping[Level, LevelSpec]  # in level order; `needs` never falls as the level rises
    aliases: Mapping[str, str] = field(default_factory=dict)
    prices: Mapping[str, Any] = field(default_factory=dict)
    newest: Mapping[str, str] = field(default_factory=dict)
    older_aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    crew: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    rules: Mapping[str, Rule] = field(default_factory=dict)
    routing_mode: str | None = None  # the weakest mode the override files set
    problems: tuple[str, ...] = ()  # keys an override file had wrong and the registry dropped
    held: tuple[Held, ...] = ()
    newer: tuple[Path, ...] = ()  # override files written for a newer release, read as far as understood

    def strength(self, name: Any) -> float:
        model = self.models.get(name) if isinstance(name, str) else None
        return float(model.strength) if model else 0.0

    def stronger(self, a: Any, b: Any) -> bool:
        return self.strength(a) > self.strength(b)

    def by_strength(self, names: Any = None) -> list[str]:
        names = self.models if names is None else names
        return sorted(names, key=lambda n: (self.strength(n), n))

    def family(self, model: Any) -> str | None:
        """Map an alias or full model ID to a registry name, or None if unknown or inherit."""
        if not model or not isinstance(model, str):
            return None
        m = model.lower().split("[")[0]
        m = self.aliases.get(m, m)
        if m in self.models:
            return m
        # A project's own names match last, so they can't capture the IDs of a vouched-for model.
        for name in sorted(self.models, key=lambda n: (self.models[n].project, -len(n))):
            if name in m:
                return name
        return None

    def newest_id(self, name: str | None, provider: str | None, allowed: allowlist.Allowlist,
                  table: health.Health) -> str | None:
        """The newest full ID of `name`'s family, where `provider`'s alias for it resolves to an
        older model, or None to send the alias. Never past the user's own choices: an
        ANTHROPIC_DEFAULT_<FAMILY>_MODEL pin, modelOverrides, or availableModels and deniedModels
        that rule the version out (https://code.claude.com/docs/en/model-config.md). An ID that
        failed here is routed around, and the alias runs until it is retried."""
        model_id = self.newest.get(name or "")
        if (not isinstance(model_id, str) or name not in (self.older_aliases.get(provider or "") or ())
                or os.environ.get(f"ANTHROPIC_DEFAULT_{str(name).upper()}_MODEL")):
            return None
        return model_id if allowed.permits_id(self, name, model_id) and table.healthy(model_id) else None

    def available(self, cap: str | None = None, *, allowed: allowlist.Allowlist | None = None,
                  table: health.Health | None = None) -> list[str]:
        """Usable models, weakest first: inside the cap, permitted by `allowed` (none: no
        allowlist filter), and healthy. The health filter is dropped rather than leave nothing
        to route to; the allowlist's is not."""
        names = self.by_strength()
        if cap:
            # Under a cap, only models whose strength the user vouches for count: a model a
            # repository added carries the repository's own claim about its rank.
            vouched = [n for n in names if self.models[n].trusted] or names
            names = [n for n in names if n in vouched and self.strength(n) <= self.strength(cap)]
        if allowed is not None:
            permitted = allowed.permitted(self)
            names = [n for n in names if n in permitted]
        table = table or health.load()
        return [n for n in names if table.healthy(n)] or names

    def clamp(self, name: str, usable: list[str]) -> str:
        """The usable model closest to `name` without exceeding it, else the weakest usable. The
        stand-in is never a model a repository added, whose rank is only that repository's claim."""
        if name in usable or not usable:
            return name
        trusted = [n for n in usable if self.models[n].trusted] or usable
        lower = [n for n in trusted if self.strength(n) <= self.strength(name)]
        return lower[-1] if lower else trusted[0]

    def select(self, needs: float, priority: str, usable: list[str]) -> str | None:
        """balanced: the cheapest model strong enough. quality: one step stronger than needed.
        fast: one step below, and the quickest of what qualifies. A model a repository added is
        never picked: its rank, cost and speed are only that repository's claim."""
        usable = [n for n in usable if self.models[n].trusted]
        if not usable:
            return None
        steps = sorted({self.strength(n) for n, m in self.models.items() if m.trusted})
        floor, top = steps[0], self.strength(usable[-1])

        def step(value: float, by: int) -> float:
            index = min(range(len(steps)), key=lambda i: abs(steps[i] - value)) + by
            return steps[max(0, min(index, len(steps) - 1))]

        if priority == "quality":
            needs = step(needs, 1)
        elif priority == "fast":
            needs = step(needs, -1)
        needs = max(floor, min(needs, top))
        qualifying = [n for n in usable if self.strength(n) >= needs]

        def cost(n: str) -> float:
            value = self.models[n].cost
            return float(value) if value is not None else self.strength(n)

        def speed(n: str) -> float:
            value = self.models[n].speed
            return float(value) if value is not None else -self.strength(n)

        if priority == "fast":
            return min(qualifying, key=lambda n: (-speed(n), cost(n)))
        return min(qualifying, key=lambda n: (cost(n), -speed(n)))

    def price(self, model_id: str) -> dict | None:
        """List prices for a full model ID, or None for a version the table doesn't list.
        A near match never counts: a new version is often priced differently."""
        table = self.prices
        model_id = re.sub(r"-\d{8}$", "", (model_id or "").lower().split("[")[0])
        if model_id in table:
            return table[model_id]
        families = [k for k in table if k.endswith("-*") and model_id.startswith(k[:-1])]
        return table[max(families, key=len)] if families else None

    def cost(self, model_id: str, tokens: dict, whole_turn: bool = False) -> float | None:
        """USD for one request's tokens at list price, or None for a model with no price. A
        whole turn's summed tokens can't say which request crossed a long-prompt threshold,
        so they are priced at the base rate."""
        rates = self.price(model_id)
        if not rates:
            return None
        long = rates.get("long_prompt")
        prompt = sum(tokens.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        if not whole_turn and isinstance(long, dict) and prompt > long.get("over", float("inf")):
            rates = long
        # The rest of the writes count as five minutes.
        hour = max(0, min(tokens.get("ephemeral_1h_input_tokens", 0), tokens.get("cache_creation_input_tokens", 0)))
        total = (tokens.get("input_tokens", 0) * rates.get("input", 0)
                 + tokens.get("output_tokens", 0) * rates.get("output", 0)
                 + tokens.get("cache_read_input_tokens", 0) * rates.get("cache_read", 0)
                 + (tokens.get("cache_creation_input_tokens", 0) - hour) * rates.get("cache_write", 0)
                 + hour * rates.get("cache_write_1h", 2 * rates.get("input", 0)))
        return round(total / 1_000_000, 6)
