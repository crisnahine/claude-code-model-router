"""The registry file, the user's and a project's overrides, and the plugin's options."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import store

PLUGIN_NAME = "model-router"
PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parent.parent.parent)
OVERRIDE_FILE = "model-router.json"
MODES = ("off", "advise", "auto")
PRIORITIES = ("fast", "balanced", "quality")
LEVELS = ("lookup", "standard", "complex", "hardest", "frontier")
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def flag(name: str, default: bool = True) -> bool:
    value = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name}")
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in ("false", "0", "no", "off")


def project_dir(hook_input: dict | None = None) -> Path:
    if hook_input and isinstance(hook_input.get("cwd"), str) and hook_input["cwd"]:
        return Path(hook_input["cwd"])
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _mode(value: Any) -> str | None:
    value = value.strip().lower() if isinstance(value, str) else None
    return value if value in MODES else None


def _clean(override: Any) -> dict:
    """Keep only the well-formed parts of an override file, so a typo can't break a
    hook or put a value on an agent call that Claude Code would reject."""
    if not isinstance(override, dict):
        return {}
    out: dict = {}
    if _mode(override.get("routing_mode")):
        out["routing_mode"] = _mode(override.get("routing_mode"))
    models = override.get("models")
    if isinstance(models, dict):
        out["models"] = {
            name.lower(): {f: spec[f] for f in ("strength", "speed", "cost") if _number(spec.get(f))}
            for name, spec in models.items() if isinstance(name, str) and isinstance(spec, dict)}
    levels = override.get("levels")
    if isinstance(levels, dict):
        out["levels"] = {}
        for name, spec in levels.items():
            if not isinstance(name, str) or name not in LEVELS or not isinstance(spec, dict):
                continue
            kept: dict = {}
            if _number(spec.get("needs")):
                kept["needs"] = spec["needs"]
            if "effort" in spec and (spec["effort"] is None or spec["effort"] in EFFORTS):
                kept["effort"] = spec["effort"]
            out["levels"][name] = kept
    agents = override.get("agents")
    if isinstance(agents, dict):
        out["agents"] = {}
        for name, spec in agents.items():
            if not isinstance(name, str) or not isinstance(spec, dict):
                continue
            kept = {}
            if isinstance(spec.get("model"), str):
                kept["model"] = spec["model"]
            if isinstance(spec.get("level"), str) and spec["level"] in LEVELS:
                kept["level"] = spec["level"]
            if isinstance(spec.get("route"), bool):
                kept["route"] = spec["route"]
            out["agents"][name] = kept
    return out


def base_config() -> dict:
    return store.load_dict(PLUGIN_ROOT / "config" / "models.json")


def load_config(hook_input: dict | None = None) -> dict:
    """The shipped registry, then the user's own ~/.claude/model-router.json, then the
    project's .claude/model-router.json. The user's file is trusted in full. A project's
    file may add a model but not re-rank one the user's cap is measured against."""
    base = base_config()
    user = _clean(store.load_json(store.claude_home() / OVERRIDE_FILE, {}))
    project = _clean(store.load_json(project_dir(hook_input) / ".claude" / OVERRIDE_FILE, {}))
    trusted = set(base.get("models") or {}) | set(user.get("models") or {})
    if "models" in project:
        project["models"] = {k: v for k, v in project["models"].items() if k not in trusted}
    file_modes = [m for m in (user.pop("routing_mode", None), project.pop("routing_mode", None)) if m]
    config = _merge(_merge(base, user), project)
    # A model needs a strength to be routed to; drop half-defined ones.
    config["models"] = {k: v for k, v in (config.get("models") or {}).items()
                        if isinstance(v, dict) and _number(v.get("strength"))}
    config["_trusted"] = sorted(trusted & set(config["models"]))
    config.pop("routing_mode", None)
    if file_modes:
        config["routing_mode"] = min(file_modes, key=MODES.index)
    return config


def _option(name: str, from_state: bool) -> str | None:
    """Hooks get plugin options in the environment. Skill commands run through the Bash
    tool, which doesn't, so they read what the last SessionStart recorded."""
    value = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name.upper()}")
    if not value and from_state:
        value = store.load_state().get(name)
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def cap(config: dict, from_state: bool = False) -> str | None:
    """The user's cap: a model name, or None for no cap."""
    value = _option("max_tier", from_state)
    return value if value in (config.get("models") or {}) else None


def priority(from_state: bool = False) -> str:
    value = _option("priority", from_state)
    return value if value in PRIORITIES else "balanced"


def pins(from_state: bool = False) -> str:
    """respect (default): an agent that names its own model keeps it. override: route it too."""
    return "override" if _option("agent_pins", from_state) == "override" else "respect"


def mode(config: dict | None = None) -> str:
    """auto (default): set the model and effort of every agent call. advise: only tell
    Claude what the estimate is. off: do nothing but keep the ledger.

    An override file may set routing_mode too. The weakest setting wins, so a
    repository can opt out but can't switch routing on for its users."""
    user = _mode(os.environ.get("CLAUDE_PLUGIN_OPTION_ROUTING_MODE")) or "auto"
    from_files = _mode((config or {}).get("routing_mode"))
    if from_files and MODES.index(from_files) < MODES.index(user):
        return from_files
    return user
