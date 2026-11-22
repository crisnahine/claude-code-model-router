"""Which models exist, which are usable in this setup right now, and how to pick one.

Nothing here names a model. The list comes from config/models.json plus the user's and
the project's additions, and is narrowed by what this setup shows: the user's cap, the
availableModels and deniedModels settings, and models that have been failing, silently
running as something else, or not accepted on agent calls."""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import config as cfg
from . import store

# How long a model is routed around after each run of trouble: 1 day, then 3, then 7.
OUTAGE_DAYS = (1, 3, 7)
MODEL_ERROR = re.compile(
    r"model.{0,80}(?:not[ _]found|not available|unavailable|does not exist|not supported|no access|"
    r"not allowed|invalid|permission)|(?:not_found_error|invalid model|unknown model).{0,80}", re.I | re.S)


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


def health() -> dict:
    table = store.load_state().get("health")
    return table if isinstance(table, dict) else {}


def healthy(name: str, table: dict | None = None) -> bool:
    info = (health() if table is None else table).get(name)
    return not (isinstance(info, dict) and (info.get("unavailable_until") or 0) > time.time())


def edit_health(name: str, change: Callable[[dict], Any]) -> dict:
    result: dict = {}

    def apply(state: dict) -> None:
        table = state.get("health") if isinstance(state.get("health"), dict) else {}
        info = table.get(name) if isinstance(table.get(name), dict) else {}
        change(info)
        table[name] = info
        state["health"] = table
        result.update(info)

    store.update_state(apply)
    return result


def update_health(name: str, **changes: Any) -> dict:
    return edit_health(name, lambda info: info.update(changes))


def _route_around(info: dict) -> None:
    outages = int(info.get("outages") or 0) + 1
    days = OUTAGE_DAYS[min(outages, len(OUTAGE_DAYS)) - 1]
    info.update(outages=outages, unavailable_until=time.time() + days * 86400)


def record_failure(name: str, error: str) -> int:
    """A call on this model failed in a way that points at the model itself. The second
    time, it is routed around. After that outage, one more failure is enough."""
    def change(info: dict) -> None:
        info["fail"] = int(info.get("fail") or 0) + 1
        info["last_error"] = error[:200]
        if info["fail"] >= 2:
            _route_around(info)
    return int(edit_health(name, change).get("fail") or 0)


def record_result(name: str, ran_as: str | None, ran_family: str | None) -> None:
    """We asked for `name`; Claude Code reports the model that really ran."""
    info = health().get(name)
    info = info if isinstance(info, dict) else {}
    if ran_family == name:
        if (info.get("last_model") != ran_as or info.get("fail") or info.get("substituted")
                or info.get("outages") or info.get("unavailable_until")):
            update_health(name, last_model=ran_as, fail=0, substituted=0, outages=0, unavailable_until=0)
        return

    # Asked for one model, ran on another: the provider or an allowlist substituted it.
    def change(current: dict) -> None:
        current["substituted"] = int(current.get("substituted") or 0) + 1
        current["substituted_by"] = ran_as
        if current["substituted"] >= 2:
            _route_around(current)
    edit_health(name, change)


def _settings_files(project: Path) -> list[Path]:
    files = [store.claude_home() / "settings.json", project / ".claude" / "settings.json",
             project / ".claude" / "settings.local.json"]
    if sys.platform == "darwin":
        files.append(Path("/Library/Application Support/ClaudeCode/managed-settings.json"))
    elif os.name == "nt":
        files.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ClaudeCode" / "managed-settings.json")
    else:
        files.append(Path("/etc/claude-code/managed-settings.json"))
    return files


def permitted(config: dict, project: Path) -> set[str]:
    """Registry models that the availableModels and deniedModels settings leave open."""
    names = set(models(config))
    for path in _settings_files(project):
        settings = store.load_dict(path)
        allow = settings.get("availableModels")
        if isinstance(allow, list) and allow:
            entries = [str(e).lower() for e in allow]
            names = {n for n in names if any(e == n or n in e for e in entries)}
        deny = settings.get("deniedModels")
        if isinstance(deny, list):
            names -= {str(e).lower() for e in deny}
    return names


def available(config: dict, project: Path | None = None, cap: str | None = None) -> list[str]:
    """Usable models, weakest first: inside the cap, permitted by settings, and healthy.
    The settings and health filters are dropped rather than leave nothing to route to."""
    names = by_strength(config)
    if cap:
        # Under a cap, only models whose strength the user vouches for count: a model a
        # repository added carries the repository's own claim about its rank.
        trusted = cfg.trusted_models(config) or names
        names = [n for n in names if n in trusted and strength(config, n) <= strength(config, cap)]
    if project is not None:
        allowed = permitted(config, project)
        names = [n for n in names if n in allowed] or names
    table = health()
    return [n for n in names if healthy(n, table)] or names


def ceiling(config: dict, project: Path | None = None, cap: str | None = None) -> str | None:
    names = available(config, project, cap)
    return names[-1] if names else None


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


def cost(config: dict, model_id: str, tokens: dict) -> float | None:
    """USD for one turn's tokens at list price, or None for a model with no price."""
    rates = price(config, model_id)
    if not rates:
        return None
    # shortcut: Haiku's 5x rate past a 100K prompt is per request, and a turn sums its
    # requests, so it isn't applied here; read cost from the transcript if that matters.
    total = (tokens.get("input_tokens", 0) * rates.get("input", 0)
             + tokens.get("output_tokens", 0) * rates.get("output", 0)
             + tokens.get("cache_read_input_tokens", 0) * rates.get("cache_read", 0)
             + tokens.get("cache_creation_input_tokens", 0) * rates.get("cache_write", 0))
    return round(total / 1_000_000, 6)
