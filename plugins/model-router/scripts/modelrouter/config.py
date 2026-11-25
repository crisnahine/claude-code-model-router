"""The registry file and the user's and a project's override files, built into one Registry."""
from __future__ import annotations

import math
import operator
import os
import re
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
MODES = ("off", "auto")
EFFORTS = ("low", "medium", "high", "xhigh", "max")

USER_FILE = "your model-router.json"
PROJECT_FILE = "the project's model-router.json"
# A repository's key names are printed into skill output Claude reads, so only short plain names are kept.
_PLAIN_NAME = re.compile(r"[A-Za-z0-9._:/\[\]-]{1,64}")


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


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    # Private DLL objects, so these prototypes never change ctypes.windll for other code.
    _advapi32, _kernel32 = ctypes.WinDLL("advapi32"), ctypes.WinDLL("kernel32")
    _PP = ctypes.POINTER(ctypes.c_void_p)
    _GetNamedSecurityInfoW = _advapi32.GetNamedSecurityInfoW
    _GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, _PP, _PP, _PP, _PP, _PP]
    _GetNamedSecurityInfoW.restype = wintypes.DWORD
    _OpenProcessToken = _advapi32.OpenProcessToken
    _OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _OpenProcessToken.restype = wintypes.BOOL
    _GetTokenInformation = _advapi32.GetTokenInformation
    _GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.DWORD)]
    _GetTokenInformation.restype = wintypes.BOOL
    _IsValidSid = _advapi32.IsValidSid
    _IsValidSid.argtypes = [ctypes.c_void_p]
    _IsValidSid.restype = wintypes.BOOL
    _EqualSid = _advapi32.EqualSid
    _EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _EqualSid.restype = wintypes.BOOL
    _GetCurrentProcess = _kernel32.GetCurrentProcess
    _GetCurrentProcess.argtypes = []
    _GetCurrentProcess.restype = wintypes.HANDLE
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL
    _LocalFree = _kernel32.LocalFree
    _LocalFree.argtypes = [ctypes.c_void_p]
    _LocalFree.restype = ctypes.c_void_p
    _SE_FILE_OBJECT, _OWNER_SECURITY_INFORMATION, _TOKEN_QUERY = 1, 0x1, 0x8
    _TOKEN_USER, _TOKEN_OWNER = 1, 4

    def _owner_is_me(path: Path) -> bool:
        owner, descriptor, token = ctypes.c_void_p(), ctypes.c_void_p(), wintypes.HANDLE()
        try:
            # os.stat follows links, so the owner read here is the target's too.
            if _GetNamedSecurityInfoW(os.path.realpath(path), _SE_FILE_OBJECT, _OWNER_SECURITY_INFORMATION,
                                      ctypes.byref(owner), None, None, None, ctypes.byref(descriptor)):
                return False
            if not owner.value or not _IsValidSid(owner):
                return False
            if not _OpenProcessToken(_GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
                return False
            # TokenOwner too: an elevated admin's new files are owned by Administrators, not the user.
            for info_class in (_TOKEN_USER, _TOKEN_OWNER):
                needed = wintypes.DWORD()
                _GetTokenInformation(token, info_class, None, 0, ctypes.byref(needed))
                # TOKEN_USER and TOKEN_OWNER both start with the SID pointer, into this same buffer.
                buf = (ctypes.c_void_p * (needed.value // ctypes.sizeof(ctypes.c_void_p) + 1))()
                if (_GetTokenInformation(token, info_class, buf, ctypes.sizeof(buf), ctypes.byref(needed))
                        and buf[0] and _IsValidSid(buf[0]) and _EqualSid(owner, buf[0])):
                    return True
            return False
        except Exception:
            return False
        finally:
            if token.value:
                _CloseHandle(token)
            if descriptor.value:
                _LocalFree(descriptor)


def _mine(path: Path) -> bool:
    """Whether this process's user owns `path`. False when the owner can't be read."""
    if os.name == "nt":
        return _owner_is_me(path)
    try:
        return os.stat(path).st_uid == os.getuid()
    except OSError:
        return False


def _up_to_root(start: Path) -> list[Path]:
    """`start` and each folder above it up to the repository root (the first one holding .git),
    nearest first. Only `start` outside a repository, or when that root is the home folder or
    another user's root, .git or .claude: those are what a planted repository could point at."""
    chain: list[Path] = []
    try:
        home = Path.home().resolve()
        for folder in (start, *start.parents):
            if folder.resolve() == home:
                break
            chain.append(folder)
            if (folder / ".git").exists():
                owned = all(_mine(p) for p in (folder, folder / ".git", folder / ".claude")
                            if p == folder or p.exists())
                return chain if owned else [start]
    except (OSError, RuntimeError):
        pass
    return [start]


def project_folders(project: Path) -> list[Path]:
    """Where project agents and files are looked for, nearest first: from `project` (the hook's cwd,
    which follows cd) and from CLAUDE_PROJECT_DIR, which stays put, each up to its repository root.
    Folders of `project`'s chain outside CLAUDE_PROJECT_DIR's repository come last."""
    if not os.environ.get("CLAUDE_PROJECT_DIR"):
        return _up_to_root(project)
    session = _up_to_root(Path(os.environ["CLAUDE_PROJECT_DIR"]))
    near = _up_to_root(project)
    inside = [f for f in near if f == session[-1] or session[-1] in f.parents]
    return list(dict.fromkeys([*inside, *session, *near]))


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


def _clean(override: Any, source: str, problems: list[str], plain_keys: bool = False) -> dict:
    """Keep only the well-formed parts of an override file, so a typo can't break a hook or put a
    value on an agent call that Claude Code would reject. What is dropped is added to `problems`.
    With `plain_keys`, a model or agent key that isn't a short plain name, or a levels key that isn't a level,
    is dropped unnamed."""
    if not isinstance(override, dict):
        return {}

    def bad(where: str, why: str) -> None:
        problems.append(f"{source}: {where} {why}, dropped")

    def plain(name: object, section: str) -> bool:
        if plain_keys and isinstance(name, str) and not _PLAIN_NAME.fullmatch(name):
            problems.append(f"{source}: a key under {section} that is not a plain name, dropped")
            return False
        return True

    out: dict = {}
    if "routing_mode" in override:
        bad("routing_mode", "is set only in /config")
    models = override.get("models")
    if isinstance(models, dict):
        out["models"] = {}
        for name, spec in models.items():
            if not plain(name, "models"):
                continue
            if not isinstance(name, str) or not isinstance(spec, dict):
                bad(f"models.{name}", "is not an object")
                continue
            for field in ("strength", "cost"):
                if spec.get(field) is not None and not _number(spec[field]):
                    bad(f"models.{name}.{field}", "is not a finite number")
            out["models"][name.lower()] = {f: spec[f] for f in ("strength", "cost") if _number(spec.get(f))}
    elif models is not None:
        bad("models", "is not an object")
    levels = override.get("levels")
    if isinstance(levels, dict):
        out["levels"] = {}
        for name, spec in levels.items():
            if plain_keys and name not in LEVELS:
                problems.append(f"{source}: a key under levels that is not a level, dropped")
                continue
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
            if not plain(name, "agents"):
                continue
            if not isinstance(name, str) or not isinstance(spec, dict):
                bad(f"agents.{name}", "is not an object")
                continue
            kept = {}
            if isinstance(spec.get("model"), str) and plain_keys and not _PLAIN_NAME.fullmatch(spec["model"]):
                problems.append(f"{source}: a model under agents that is not a plain name, dropped")
            elif isinstance(spec.get("model"), str):
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
        effort = floor.get("effort")
        # A level left at no effort runs at Claude Code's default, medium on the current models.
        if "effort" in spec and spec["effort"] != effort and _effort_rank(spec["effort"]) < _effort_rank(effort or "medium"):
            why = (f"is below {effort}" if effort
                   else "is below Claude Code's default medium, which your file leaves this level at")
            held.append(Held("level", name, f"effort {spec.pop('effort')} {why}"))


def _newer(file: dict) -> bool:
    """An override file written for a newer release. It is still read, as far as _clean understands it,
    so the settings this release knows still apply."""
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
        found[name] = Model(name, spec["strength"], _optional(spec, "cost"), use_for if isinstance(use_for, str) else "",
                            project=name in added)
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
        own, floor, suggested = rules.get(name, Rule()), Level.parse(spec.get("level")), spec.get("model")
        if own.level or own.model:
            if floor:
                held.append(Held("rule", name, f"level {floor} is ignored: your own rule sets this agent's level or model"))
            if suggested:
                held.append(Held("rule", name, f"model {suggested} is ignored: your own rule sets this agent's level or model"))
            floor = suggested = None
        rules[name] = replace(own, floor=floor, suggested=suggested)
    return rules


def build(base: dict, user: dict | None = None, project: dict | None = None) -> Registry:
    """The shipped registry, then the user's own override file, then the project's. The user's file is
    trusted in full. A project's file may add a model but not re-rank one the user's cap is measured
    against, may raise a level's needs or effort but not lower them, and may set a rule for an agent
    that only raises, and only for an agent whose level and model the user's own rule leaves open: a user
    rule that sets either holds both the project's level and its model for that agent."""
    problems: list[str] = []
    held: list[Held] = []
    user_file, project_file = user or {}, project or {}
    mine = _clean(user_file, USER_FILE, problems)
    theirs = _clean(project_file, PROJECT_FILE, problems, plain_keys=True)
    vouched = set(base.get("models") or {}) | set(mine.get("models") or {})
    added = {}
    for name, spec in (theirs.get("models") or {}).items():
        # A name inside a trusted one, or around it, would capture that model's IDs.
        if any(name in t or t in name for t in vouched):
            held.append(Held("model", name, "its name overlaps a shipped or your own model's, so it is ignored"))
        else:
            added[name] = spec
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
    return Registry(
        models=models,
        levels=_levels(_merge(shipped_levels, project_levels), problems),
        trusted_levels=_levels(shipped_levels, []),
        aliases=aliases,
        prices=dict(base.get("prices") or {}),
        newest={k: v for k, v in (base.get("newest") or {}).items() if not k.startswith("$") and isinstance(v, str)},
        older_aliases={k: dict(v) for k, v in (base.get("older_aliases") or {}).items()
                       if not k.startswith("$") and isinstance(v, dict)},
        session={k: v for k, v in (base.get("session") or {}).items() if k in models and v in EFFORTS},
        rules=_rules(base.get("agents") or {}, mine.get("agents") or {}, theirs.get("agents") or {}, held),
        problems=tuple(problems),
        held=tuple(held),
    )


def load(hook_input: dict | None = None) -> Registry:
    """The registry for this user and project, from the three files, each read once. The project's file is
    the nearest one up to the repository root from CLAUDE_PROJECT_DIR, else, for a skill command run
    through Bash, from cwd."""
    start = Path(os.environ["CLAUDE_PROJECT_DIR"]) if os.environ.get("CLAUDE_PROJECT_DIR") else project_dir(hook_input)
    found = [f / ".claude" / OVERRIDE_FILE for f in project_folders(start)]
    project_file = next((p for p in found if p.is_file()), found[0])
    paths = (store.claude_home() / OVERRIDE_FILE, project_file)
    user, project = (store.load_dict(path) for path in paths)
    newer = tuple(path for path, file in zip(paths, (user, project)) if _newer(file))
    return replace(build(base_config(), user, project), newer=newer)


def shipped() -> Registry:
    """The registry as the plugin ships it, with no override file: what the benchmark measures."""
    return build(base_config())
