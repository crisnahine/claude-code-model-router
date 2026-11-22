"""The registry file and the user's and a project's override files, merged."""
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
# Who can choose a call's model and still leave its effort to the call's level.
LEVEL_SOURCES = ("router", "pin", "rule")


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


def parse_mode(value: Any) -> str | None:
    value = value.strip().lower() if isinstance(value, str) else None
    return value if value in MODES else None


def _clean(override: Any) -> dict:
    """Keep only the well-formed parts of an override file, so a typo can't break a
    hook or put a value on an agent call that Claude Code would reject."""
    if not isinstance(override, dict):
        return {}
    out: dict = {}
    if parse_mode(override.get("routing_mode")):
        out["routing_mode"] = parse_mode(override.get("routing_mode"))
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


def tool_input(hook_input: dict) -> dict:
    value = hook_input.get("tool_input")
    return value if isinstance(value, dict) else {}


def trusted_models(config: dict) -> list[str]:
    """Models whose strength the shipped registry or the user vouches for. A repository's
    additions carry only the repository's claim, so they don't count under a cap."""
    user = _clean(store.load_dict(store.claude_home() / OVERRIDE_FILE))
    vouched = set(base_config().get("models") or {}) | set(user.get("models") or {})
    return sorted(vouched & set(config.get("models") or {}))


def base_config() -> dict:
    return store.load_dict(PLUGIN_ROOT / "config" / "models.json")


def load_config(hook_input: dict | None = None) -> dict:
    """The shipped registry, then the user's own ~/.claude/model-router.json, then the
    project's .claude/model-router.json. The user's file is trusted in full. A project's
    file may add a model but not re-rank one the user's cap is measured against."""
    base = base_config()
    user = _clean(store.load_dict(store.claude_home() / OVERRIDE_FILE))
    project = _clean(store.load_dict(project_dir(hook_input) / ".claude" / OVERRIDE_FILE))
    trusted = set(base.get("models") or {}) | set(user.get("models") or {})
    if "models" in project:
        # A name inside a trusted one, or around it, would capture that model's IDs.
        project["models"] = {k: v for k, v in project["models"].items()
                             if not any(k in t or t in k for t in trusted)}
    file_modes = [m for m in (user.pop("routing_mode", None), project.pop("routing_mode", None)) if m]
    config = _merge(_merge(base, user), project)
    # A model needs a strength to be routed to; drop half-defined ones.
    config["models"] = {k: v for k, v in (config.get("models") or {}).items()
                        if isinstance(v, dict) and _number(v.get("strength"))}
    config["project_models"] = sorted(project.get("models") or {})
    config.pop("routing_mode", None)
    if file_modes:
        config["routing_mode"] = min(file_modes, key=MODES.index)
    return config
