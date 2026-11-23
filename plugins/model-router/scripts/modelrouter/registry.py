"""Which models exist, which are usable in this setup right now, and how to pick one.

Nothing here names a model. The list comes from config/models.json plus the user's and
the project's additions, and is narrowed by what this setup shows: the user's cap, the
availableModels and deniedModels settings, and models that have been failing, silently
running as something else, or not accepted on agent calls."""
from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

from . import config as cfg
from . import health

if TYPE_CHECKING:
    from . import allowlist


def models(config: dict) -> dict[str, dict]:
    return config.get("models") or {}


def strength(config: dict, name: Any) -> float:
    spec = models(config).get(name) if isinstance(name, str) else None
    return float((spec or {}).get("strength") or 0)


def stronger(config: dict, a: Any, b: Any) -> bool:
    return strength(config, a) > strength(config, b)


def by_strength(config: dict, names: Any = None) -> list[str]:
    names = models(config) if names is None else names
    return sorted(names, key=lambda n: (strength(config, n), n))


def family(config: dict, model: Any) -> str | None:
    """Map an alias or full model ID to a registry name, or None if unknown or inherit."""
    if not model or not isinstance(model, str):
        return None
    m = model.lower().split("[")[0]
    m = (config.get("aliases") or {}).get(m, m)
    names = models(config)
    if m in names:
        return m
    # A project's own names match last, so they can't capture the IDs of a vouched-for model.
    added = set(config.get("project_models") or ())
    for name in sorted(names, key=lambda n: (n in added, -len(n))):
        if name in m:
            return name
    return None


def newest_id(config: dict, name: str | None, provider: str | None, allowed: allowlist.Allowlist,
              table: health.Health) -> str | None:
    """The newest full ID of `name`'s family, where `provider`'s alias for it resolves to an
    older model, or None to send the alias. Never past the user's own choices: an
    ANTHROPIC_DEFAULT_<FAMILY>_MODEL pin, modelOverrides, or availableModels and deniedModels
    that rule the version out (https://code.claude.com/docs/en/model-config.md). An ID that
    failed here is routed around, and the alias runs until it is retried."""
    model_id = (config.get("newest") or {}).get(name or "")
    if (not isinstance(model_id, str) or name not in ((config.get("older_aliases") or {}).get(provider or "") or ())
            or os.environ.get(f"ANTHROPIC_DEFAULT_{str(name).upper()}_MODEL")):
        return None
    return model_id if allowed.permits_id(name, model_id) and table.healthy(model_id) else None


def available(config: dict, cap: str | None = None, *, allowed: allowlist.Allowlist | None = None,
              table: health.Health | None = None) -> list[str]:
    """Usable models, weakest first: inside the cap, permitted by `allowed` (none: no
    allowlist filter), and healthy. The health filter is dropped rather than leave nothing
    to route to; the allowlist's is not."""
    names = by_strength(config)
    if cap:
        # Under a cap, only models whose strength the user vouches for count: a model a
        # repository added carries the repository's own claim about its rank.
        trusted = cfg.trusted_models(config) or names
        names = [n for n in names if n in trusted and strength(config, n) <= strength(config, cap)]
    if allowed is not None:
        permitted = allowed.permitted(config)
        names = [n for n in names if n in permitted]
    table = table or health.load()
    return [n for n in names if table.healthy(n)] or names


def clamp(config: dict, name: str, usable: list[str]) -> str:
    """The usable model closest to `name` without exceeding it, else the weakest usable."""
    if name in usable or not usable:
        return name
    lower = [n for n in usable if strength(config, n) <= strength(config, name)]
    return lower[-1] if lower else usable[0]


def select(config: dict, needs: float, priority: str, usable: list[str]) -> str | None:
    """balanced: the cheapest model strong enough. quality: one step stronger than needed.
    fast: one step below, and the quickest of what qualifies."""
    if not usable:
        return None
    info = models(config)
    steps = sorted({strength(config, n) for n in info})
    floor, top = steps[0], strength(config, usable[-1])

    def step(value: float, by: int) -> float:
        index = min(range(len(steps)), key=lambda i: abs(steps[i] - value)) + by
        return steps[max(0, min(index, len(steps) - 1))]

    if priority == "quality":
        needs = step(needs, 1)
    elif priority == "fast":
        needs = step(needs, -1)
    needs = max(floor, min(needs, top))
    qualifying = [n for n in usable if strength(config, n) >= needs]

    def cost(n: str) -> float:
        value = info[n].get("cost")
        return float(value) if cfg._number(value) else strength(config, n)

    def speed(n: str) -> float:
        value = info[n].get("speed")
        return float(value) if cfg._number(value) else -strength(config, n)

    if priority == "fast":
        return min(qualifying, key=lambda n: (-speed(n), cost(n)))
    return min(qualifying, key=lambda n: (cost(n), -speed(n)))


def price(config: dict, model_id: str) -> dict | None:
    """List prices for a full model ID, or None for a version the table doesn't list.
    A near match never counts: a new version is often priced differently."""
    table = config.get("prices") or {}
    model_id = re.sub(r"-\d{8}$", "", (model_id or "").lower().split("[")[0])
    if model_id in table:
        return table[model_id]
    families = [k for k in table if k.endswith("-*") and model_id.startswith(k[:-1])]
    return table[max(families, key=len)] if families else None


def cost(config: dict, model_id: str, tokens: dict, whole_turn: bool = False) -> float | None:
    """USD for one request's tokens at list price, or None for a model with no price. A
    whole turn's summed tokens can't say which request crossed a long-prompt threshold,
    so they are priced at the base rate."""
    rates = price(config, model_id)
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
