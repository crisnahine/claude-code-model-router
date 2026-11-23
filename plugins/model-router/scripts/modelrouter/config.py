"""The registry file and the user's and a project's override files, built into one Registry."""
from __future__ import annotations

import math
import operator
import os
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Any

from . import store
from .registry import Held, LevelSpec, Model, Registry, Rule

PLUGIN_NAME = "model-router"
PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parent.parent.parent)
OVERRIDE_FILE = "model-router.json"
# The shape of config/models.json and model-router.json; a file without `schema` is version 1.
# docs/ARCHITECTURE.md "Compatibility" says when it changes.
CONFIG_SCHEMA = 1
MODES = ("off", "advise", "auto")
PRIORITIES = ("fast", "balanced", "quality")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Who can choose a call's model and still leave its effort to the call's level.
LEVEL_SOURCES = ("router", "pin", "rule")

USER_FILE = "your model-router.json"
PROJECT_FILE = "the project's model-router.json"


class Level(str, Enum):
    """How much reasoning a call needs. A str, so it is written to state and JSON as its bare name, but
    ordered by difficulty: a str mixin would sort alphabetically, so the comparisons are overridden."""
    lookup = "lookup"
    standard = "standard"
    complex = "complex"
    hardest = "hardest"
    frontier = "frontier"

    def __str__(self) -> str:
        return self.value

    def __format__(self, spec: str) -> str:
        return format(self.value, spec)

    @classmethod
    def parse(cls, value: object) -> Level | None:
        """The level `value` names, or None for anything else."""
        if isinstance(value, Level):
            return value
        try:
            return cls(value) if isinstance(value, str) else None
        except ValueError:
            return None

    @property
    def rank(self) -> int:
        return LEVELS.index(self)

    def up(self) -> Level | None:
        """The next level, or None at the top."""
        return LEVELS[self.rank + 1] if self.rank + 1 < len(LEVELS) else None

    def _compare(self, other: object, op: Any) -> Any:
        found = Level.parse(other)
        return NotImplemented if found is None else op(self.rank, found.rank)

    def __lt__(self, other: object) -> Any:
        return self._compare(other, operator.lt)

    def __le__(self, other: object) -> Any:
        return self._compare(other, operator.le)

    def __gt__(self, other: object) -> Any:
        return self._compare(other, operator.gt)

    def __ge__(self, other: object) -> Any:
        return self._compare(other, operator.ge)


LEVELS = tuple(Level)


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
    """A finite number: json.loads accepts NaN and Infinity, which would poison every comparison."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an integer too large for a float
        return False


def parse_mode(value: Any) -> str | None:
    value = value.strip().lower() if isinstance(value, str) else None
    return value if value in MODES else None


def _clean(override: Any, source: str, problems: list[str]) -> dict:
    """Keep only the well-formed parts of an override file, so a typo can't break a hook or put a
    value on an agent call that Claude Code would reject. What is dropped is added to `problems`."""
    if not isinstance(override, dict):
        return {}

    def bad(where: str, why: str) -> None:
        problems.append(f"{source}: {where} {why}, dropped")

    out: dict = {}
    if parse_mode(override.get("routing_mode")):
        out["routing_mode"] = parse_mode(override.get("routing_mode"))
    elif override.get("routing_mode") is not None:
        bad("routing_mode", "is not off, advise or auto")
    models = override.get("models")
    if isinstance(models, dict):
        out["models"] = {}
        for name, spec in models.items():
            if not isinstance(name, str) or not isinstance(spec, dict):
                bad(f"models.{name}", "is not an object")
                continue
            for field in ("strength", "speed", "cost"):
                if spec.get(field) is not None and not _number(spec[field]):
                    bad(f"models.{name}.{field}", "is not a finite number")
            out["models"][name.lower()] = {f: spec[f] for f in ("strength", "speed", "cost") if _number(spec.get(f))}
    elif models is not None:
        bad("models", "is not an object")
    levels = override.get("levels")
    if isinstance(levels, dict):
        out["levels"] = {}
        for name, spec in levels.items():
            if not isinstance(name, str) or name not in LEVELS or not isinstance(spec, dict):
                bad(f"levels.{name}", "is not a level with an object")
                continue
            kept: dict = {}
            if _number(spec.get("needs")):
                kept["needs"] = spec["needs"]
            elif spec.get("needs") is not None:
                bad(f"levels.{name}.needs", "is not a finite number")
            if "effort" in spec and (spec["effort"] is None or spec["effort"] in EFFORTS):
                kept["effort"] = spec["effort"]
            elif "effort" in spec:
                bad(f"levels.{name}.effort", f"is not one of {', '.join(EFFORTS)}")
            out["levels"][name] = kept
    elif levels is not None:
        bad("levels", "is not an object")
    agents = override.get("agents")
    if isinstance(agents, dict):
        out["agents"] = {}
        for name, spec in agents.items():
            if not isinstance(name, str) or not isinstance(spec, dict):
                bad(f"agents.{name}", "is not an object")
                continue
            kept = {}
            if isinstance(spec.get("model"), str):
                kept["model"] = spec["model"]
            elif spec.get("model") is not None:
                bad(f"agents.{name}.model", "is not a model name")
            if isinstance(spec.get("level"), str) and spec["level"] in LEVELS:
                kept["level"] = spec["level"]
            elif spec.get("level") is not None:
                bad(f"agents.{name}.level", "is not a level")
            if isinstance(spec.get("route"), bool):
                kept["route"] = spec["route"]
            elif spec.get("route") is not None:
                bad(f"agents.{name}.route", "is not true or false")
            out["agents"][name] = kept
    elif agents is not None:
        bad("agents", "is not an object")
    return out


def _prices(value: Any, source: str, problems: list[str]) -> dict:
    """The numeric rate fields of a prices block, keyed by lowercase model ID."""
    if not isinstance(value, dict):
        return {}
    fields = ("input", "output", "cache_read", "cache_write", "cache_write_1h")
    out = {}
    for name, spec in value.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            problems.append(f"{source}: prices.{name} is not an object, dropped")
            continue
        for field in fields:
            if spec.get(field) is not None and not _number(spec[field]):
                problems.append(f"{source}: prices.{name}.{field} is not a finite number, dropped")
        out[name.lower()] = {f: spec[f] for f in fields if _number(spec.get(f))}
    return {k: v for k, v in out.items() if v}


def tool_input(hook_input: dict) -> dict:
    value = hook_input.get("tool_input")
    return value if isinstance(value, dict) else {}


def _effort_rank(effort: Any) -> int:
    return EFFORTS.index(effort) if effort in EFFORTS else -1


def _raises_only(project: dict, shipped: dict, held: list[Held]) -> None:
    """A project's level may ask for a stronger model or more effort than the shipped or the user's
    value, never less: a cloned repository can't lower a level the user relies on."""
    for name, spec in project.items():
        floor = shipped.get(name) or {}
        if _number(floor.get("needs")) and spec.get("needs", floor["needs"]) < floor["needs"]:
            held.append(Held("level", name, f"needs {spec.pop('needs')} is below {floor['needs']}"))
        if "effort" in spec and _effort_rank(spec["effort"]) < _effort_rank(floor.get("effort")):
            held.append(Held("level", name, f"effort {spec.pop('effort')} is below {floor['effort']}"))


def _newer(file: dict) -> bool:
    """An override file written for a newer release. It is still read, as far as _clean understands it:
    refusing it would drop its `routing_mode: off` and turn routing back on."""
    version = file.get("schema")
    return isinstance(version, int) and not isinstance(version, bool) and version > CONFIG_SCHEMA


def base_config() -> dict:
    return store.load_dict(PLUGIN_ROOT / "config" / "models.json")


def _optional(spec: dict, key: str) -> float | None:
    return spec[key] if _number(spec.get(key)) else None


def _models(raw: dict, added: set, base: dict, problems: list[str]) -> dict[str, Model]:
    """A model needs a strength to be routed to; half-defined ones are dropped."""
    found = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict) or not _number(spec.get("strength")):
            if name not in base:
                problems.append(f"models.{name} has no strength, so it is left out")
            continue
        use_for = spec.get("use_for")
        found[name] = Model(name, spec["strength"], _optional(spec, "speed"), _optional(spec, "cost"),
                            use_for if isinstance(use_for, str) else "", project=name in added)
    return found


def _levels(raw: dict, problems: list[str]) -> dict[Level, LevelSpec]:
    """Every level, in order, with a finite `needs` that never falls as the level rises: a raise
    lifts the levels above it too."""
    found: dict[Level, LevelSpec] = {}
    running = -math.inf
    for level in LEVELS:
        spec = raw.get(level.value)
        spec = spec if isinstance(spec, dict) else {}
        needs = float(spec["needs"]) if _number(spec.get("needs")) and spec["needs"] else float(level.rank + 1)
        effort = spec.get("effort")
        if effort is not None and effort not in EFFORTS:
            problems.append(f"levels.{level}.effort is not one of {', '.join(EFFORTS)}, dropped")
            effort = None
        needs = running = max(needs, running)
        found[level] = LevelSpec(needs, effort)
    return found


def _rules(base: dict, user: dict, project: dict, held: list[Held]) -> dict[str, Rule]:
    rules: dict[str, Rule] = {}
    for name, spec in _merge(base, user).items():
        if isinstance(spec, dict):
            rules[name] = Rule(model=spec.get("model"), level=Level.parse(spec.get("level")), route=spec.get("route"))
    for name, spec in project.items():
        if spec.get("route") is False:
            held.append(Held("rule", name, "route false is ignored: a project can't switch routing off for an agent"))
        rules[name] = replace(rules.get(name, Rule()), floor=Level.parse(spec.get("level")), suggested=spec.get("model"))
    return rules


def build(base: dict, user: dict | None = None, project: dict | None = None) -> Registry:
    """The shipped registry, then the user's own override file, then the project's. The user's file is
    trusted in full. A project's file may add a model but not re-rank one the user's cap is measured
    against, may raise a level's needs or effort but not lower them, and may set a rule for an agent
    that only raises."""
    problems: list[str] = []
    held: list[Held] = []
    user_file, project_file = user or {}, project or {}
    mine = _clean(user_file, USER_FILE, problems)
    mine["prices"] = _prices(user_file.get("prices"), USER_FILE, problems)
    theirs = _clean(project_file, PROJECT_FILE, problems)
    vouched = set(base.get("models") or {}) | set(mine.get("models") or {})
    added = {}
    for name, spec in (theirs.get("models") or {}).items():
        # A name inside a trusted one, or around it, would capture that model's IDs.
        if any(name in t or t in name for t in vouched):
            held.append(Held("model", name, "its name overlaps a shipped or your own model's, so it is ignored"))
        else:
            added[name] = spec
    file_modes = [m for m in (mine.get("routing_mode"), theirs.get("routing_mode")) if m]
    shipped_levels = _merge(base.get("levels") or {}, mine.get("levels") or {})
    project_levels = theirs.get("levels") or {}
    _raises_only(project_levels, shipped_levels, held)
    models = _models(_merge(_merge(base.get("models") or {}, mine.get("models") or {}), added), set(added),
                     base.get("models") or {}, problems)
    aliases = {}
    for alias, target in (base.get("aliases") or {}).items():
        if isinstance(target, str) and target in models:
            aliases[alias] = target
        else:
            problems.append(f"aliases.{alias} names no model, dropped")
    main = base.get("main") if isinstance(base.get("main"), dict) else {}
    cache_rules = main.get("effort_keeps_cache") if isinstance(main.get("effort_keeps_cache"), dict) else {}
    return Registry(
        models=models,
        levels=_levels(_merge(shipped_levels, project_levels), problems),
        aliases=aliases,
        prices=_merge(base.get("prices") or {}, mine.get("prices") or {}),
        main_effort={Level(k): e for k, e in (main.get("effort") or {}).items() if Level.parse(k) and e in EFFORTS},
        keeps_cache={k: v for k, v in cache_rules.items() if not k.startswith("$") and isinstance(v, str)},
        newest={k: v for k, v in (base.get("newest") or {}).items() if not k.startswith("$") and isinstance(v, str)},
        older_aliases={k: tuple(v) for k, v in (base.get("older_aliases") or {}).items()
                       if not k.startswith("$") and isinstance(v, list)},
        crew=dict(base.get("crew") or {}),
        rules=_rules(base.get("agents") or {}, mine.get("agents") or {}, theirs.get("agents") or {}, held),
        routing_mode=min(file_modes, key=MODES.index) if file_modes else None,
        problems=tuple(problems),
        held=tuple(held),
    )


def load(hook_input: dict | None = None) -> Registry:
    """The registry for this user and project, from the three files, each read once."""
    paths = (store.claude_home() / OVERRIDE_FILE, project_dir(hook_input) / ".claude" / OVERRIDE_FILE)
    user, project = (store.load_dict(path) for path in paths)
    newer = tuple(path for path, file in zip(paths, (user, project)) if _newer(file))
    return replace(build(base_config(), user, project), newer=newer)


def shipped() -> Registry:
    """The registry as the plugin ships it, with no override file: what the benchmark measures."""
    return build(base_config())
