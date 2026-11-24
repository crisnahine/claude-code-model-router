"""Which registry models availableModels and deniedModels leave open, and the effort cap
maxEffortLevel sets for each.

The mod reads each settings source from the engine and sends the keys that matter;
without the mod, or for a source whose read failed, the settings files are read at their
documented paths. The managed files are read either way and can only narrow what the engine
reports. The rules live here, in `from_layers`."""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from . import store
from .config import EFFORTS
from .registry import Registry

SOURCES = ("policy", "user", "project", "local", "flag")
KEYS = ("availableModels", "deniedModels", "availableModelsMatch", "modelOverrides", "maxEffortLevel", "modelSettings")
# https://code.claude.com/docs/en/managed-settings.md. Fixed per OS: a trusted repository's
# `env` block reaches hook processes, so no environment variable may move this.
MANAGED_ROOTS = {"darwin": "/Library/Application Support/ClaudeCode", "win32": r"C:\Program Files\ClaudeCode"}
MANAGED_ROOT: Path | None = None  # tests only
MAX_ENTRIES = 200
MAX_CHARS = 200
MAX_BYTES = 1 << 20


def managed_root(platform: str = sys.platform) -> Path:
    return MANAGED_ROOT or Path(MANAGED_ROOTS.get(platform, "/etc/claude-code"))


def _entries(raw: list) -> list[str]:
    """An allow list cut to the cap: fewer entries allow less, so a cut is the safe direction."""
    found = (e.strip() for e in raw if isinstance(e, str))
    return [e for e in found if 0 < len(e) <= MAX_CHARS][:MAX_ENTRIES]


def _denied(raw: list) -> list[str] | None:
    """A deny list as it is, or None when it is over the cap: a cut would let a denied model through."""
    found = [e for e in (e.strip() for e in raw if isinstance(e, str)) if e]
    return None if len(found) > MAX_ENTRIES or any(len(e) > MAX_CHARS for e in found) else found


def _caps(raw: object) -> dict[str, dict]:
    """modelSettings cut to each model's maxEffortLevel."""
    found = ((k, v.get("maxEffortLevel")) for k, v in raw.items() if isinstance(v, dict)) if isinstance(raw, dict) else ()
    return dict([(k, {"maxEffortLevel": v}) for k, v in found if v in EFFORTS and 0 < len(k) <= MAX_CHARS][:MAX_ENTRIES])


def _layer(raw: object, policy: bool = False) -> dict:
    """One source cut to KEYS, each with the right shape; a bad key counts as unset.
    modelOverrides keeps only whether it holds anything, and modelSettings only its effort caps. A deny list over the cap, or in the
    policy a list key that isn't a list, marks the layer "unreadable", which from_layers turns
    into no model open: dropping it would drop a deny."""
    if not isinstance(raw, dict):
        return {}
    layer: dict = {}
    if policy and any(key in raw and not isinstance(raw[key], list) for key in ("availableModels", "deniedModels")):
        layer["unreadable"] = True
    if isinstance(raw.get("availableModels"), list):
        layer["availableModels"] = _entries(raw["availableModels"])
    if isinstance(raw.get("deniedModels"), list):
        denied = _denied(raw["deniedModels"])
        if denied is None:
            layer["unreadable"] = True
        else:
            layer["deniedModels"] = denied
    if raw.get("unreadable") is True:
        layer["unreadable"] = True
    if isinstance(raw.get("availableModelsMatch"), str):
        layer["availableModelsMatch"] = raw["availableModelsMatch"]
    if isinstance(raw.get("modelOverrides"), (dict, bool)):
        layer["modelOverrides"] = bool(raw["modelOverrides"])
    if raw.get("maxEffortLevel") in EFFORTS:
        layer["maxEffortLevel"] = raw["maxEffortLevel"]
    if _caps(raw.get("modelSettings")):
        layer["modelSettings"] = _caps(raw["modelSettings"])
    return layer


def _layers(raw: object) -> dict[str, dict]:
    """The sources present in `raw`, cleaned. A source that is not there is left out."""
    raw = raw if isinstance(raw, dict) else {}
    return {source: _layer(raw[source], source == "policy") for source in SOURCES if source in raw}


def _blocked_family(reg: Registry, entry: str) -> str | None:
    """The registry model a deny entry blocks whole: one that names the family with no version.
    A version, or a context variant such as sonnet[1m], blocks that model only: Claude Code
    moves the alias to the newest permitted version, so the alias stays usable."""
    e = entry.strip().lower()
    if e in ("best", "opusplan", "default") or "[" in e:
        return None
    if e in reg.models:
        return e
    return None if any(c.isdigit() for c in e) else reg.family(e)


def _effective(reg: Registry, entries: tuple[str, ...]) -> tuple[str, ...]:
    """An allow list as Claude Code applies it: an entry naming a specific model in a family (a
    version prefix or a full ID) turns off the entry that names the family itself
    (https://code.claude.com/docs/en/model-config.md, Merge behavior). An entry names a version
    when it carries a digit or a `claude-` or provider prefix; `opusplan` and `default` don't."""
    plain = set(reg.models) | set(reg.aliases)

    def bare(entry: str) -> str:
        return entry.strip().lower().split("[")[0]

    specific = {reg.family(e) for e in entries
                if bare(e) not in plain and re.search(r"\d|^claude-|^[\w-]+[./]", bare(e))} - {None}
    return tuple(e for e in entries if bare(e) not in specific)


@dataclass(frozen=True)
class Allowlist:
    allow: tuple[str, ...] | None  # None: no source set availableModels
    deny: tuple[str, ...] | None
    exact: bool  # availableModelsMatch is "exact"
    overrides: bool  # some source sets modelOverrides
    source: str  # "engine" or "files"
    floor_allow: tuple[str, ...] | None = None  # the managed files' list, which must permit too
    effort_caps: tuple[dict, ...] = ()  # each source's maxEffortLevel and modelSettings caps

    def permitted(self, reg: Registry) -> set[str]:
        """Registry models the lists leave open. An allow entry matches its family, so
        claude-opus-4-6 permits opus. A deny entry blocks a family only when it names the family."""
        names = set(reg.models)
        for entries in (self.allow, self.floor_allow):
            if entries is not None:
                found = {reg.family(e) for e in _effective(reg, entries)}
                names = {n for n in names if n in found}
        if self.deny is not None:
            blocked = {_blocked_family(reg, e) for e in self.deny} - {None}
            # A project's model that resolves to a blocked family, such as `best`, goes with it.
            names = {n for n in names if reg.family(n) not in blocked}
        return names

    def permits_id(self, reg: Registry, name: str, model_id: str) -> bool:
        """Whether the full ID of family `name` may run: not ruled out by the lists or by
        modelOverrides, which Claude Code applies to the ID itself."""
        def names(entries: tuple[str, ...], prefix: bool) -> bool:
            return any(e.lower() in (name, model_id) or prefix and model_id.startswith(e.lower() + "-")
                       for e in entries)

        for entries in (self.allow, self.floor_allow):
            if entries is not None and not names(_effective(reg, entries), not self.exact):
                return False
        if self.deny is not None and names(self.deny, True):
            return False
        return not self.overrides

    def effort_cap(self, reg: Registry, name: str | None) -> str | None:
        """The highest effort `name`'s family may run at, or None for no cap. The lowest cap of any
        source applies, and a source's modelSettings entry for the model replaces its top-level
        one; "max" sets none (https://code.claude.com/docs/en/settings-reference.md#maxeffortlevel)."""
        found = []
        for layer in self.effort_caps:
            # shortcut: modelSettings is keyed by model ID, and several IDs of one family read as the
            # highest of their caps; Claude Code still applies the exact one to each request.
            own = [v["maxEffortLevel"] for k, v in (layer.get("modelSettings") or {}).items()
                   if name and reg.family(k) == name]
            cap = max(own, key=EFFORTS.index) if own else layer.get("maxEffortLevel")
            if cap in EFFORTS and cap != "max":
                found.append(cap)
        return min(found, key=EFFORTS.index) if found else None


def from_layers(layers: dict, source: str = "engine", floor: dict | None = None) -> Allowlist:
    """availableModels as Claude Code applies it: a policy list applies alone, otherwise the
    user, project, local and flag lists are concatenated. deniedModels and
    availableModelsMatch count only in policy. `floor` is the policy the managed files hold: its
    deny entries add to the engine's, its allow list must permit too, and either one saying
    "exact" makes matching exact. A stricter answer is the safe one."""
    cleaned = _layers(layers)
    layers = {s: cleaned.get(s, {}) for s in SOURCES}
    policy, floor = layers["policy"], _layer(floor, True)
    if policy.get("unreadable") or floor.get("unreadable"):
        policy, floor = {"availableModels": []}, {"availableModels": []}  # a policy we can't trust leaves nothing open
    allow = policy.get("availableModels", floor.get("availableModels"))
    if allow is None:
        lists = [layers[s]["availableModels"] for s in SOURCES[1:] if "availableModels" in layers[s]]
        allow = sum(lists, []) if lists else None
    denies = [d for d in (policy.get("deniedModels"), floor.get("deniedModels")) if d is not None]
    deny = tuple(dict.fromkeys(sum(denies, []))) if denies else None
    held = floor.get("availableModels")
    return Allowlist(allow=None if allow is None else tuple(allow), deny=deny,
                     exact="exact" in (policy.get("availableModelsMatch"), floor.get("availableModelsMatch")),
                     overrides=any(layer.get("modelOverrides") for layer in [*layers.values(), floor]),
                     source=source, floor_allow=None if held is None else tuple(held),
                     effort_caps=tuple(layer for layer in [*layers.values(), floor]
                                       if "maxEffortLevel" in layer or "modelSettings" in layer))


def remember(session_id: str | None, sent: object) -> dict[str, dict] | None:
    """Clean the mod's `settings` payload and keep it for the session's later hooks. None
    when the mod sent no layers. A source it left out is one whose read failed."""
    layers, stored = _sent(session_id, sent)
    if layers is None:
        return None
    try:
        if session_id and layers != stored:
            store.save_entry("allowlist", session_id, {"layers": layers})
    except Exception:
        pass  # these layers still answer this call
    return layers


def _stored(session_id: str | None) -> dict[str, dict]:
    try:
        return _layers(store.load_entry("allowlist", session_id).get("layers"))
    except Exception:
        return {}


def _sent(session_id: str | None, sent: object) -> tuple[dict[str, dict] | None, dict[str, dict]]:
    """The layers the mod sent over the session's stored ones, None when it sent none, and the stored ones."""
    stored = _stored(session_id)
    return ({**stored, **_layers(sent)} if isinstance(sent, dict) else None), stored  # a source left out keeps what it last sent


def read(session_id: str | None, project: Path, sent: object = None) -> Allowlist:
    """Each source from the layers sent now, else the latest the session sent, else its
    settings file. The managed files always apply as a floor. Reading stores nothing: the mod's
    own hooks call `remember`."""
    layers, stored = _sent(session_id, sent)
    if layers is None:
        layers = stored
    files = file_layers(project)
    merged = {s: layers[s] if s in layers else files[s] for s in SOURCES}
    return from_layers(merged, "engine" if layers else "files", floor=files["policy"])


def file_layers(project: Path) -> dict[str, dict]:
    """The settings files as the sources they stand for. The flag source and the managed
    tiers that have no file (server-managed, MDM, registry) can't be seen from here. The shared file is
    read from the session's primary working directory, CLAUDE_PROJECT_DIR, which stays put through cd."""
    shared = Path(os.environ.get("CLAUDE_PROJECT_DIR") or project) / ".claude" / "settings.json"
    return {"policy": _policy(), "user": _file(store.claude_home() / "settings.json"),
            "project": _file(shared), "local": _local(project), "flag": {}}


def _load(path: Path) -> tuple[dict, bool]:
    """A settings file's object and whether it could be read. A file that isn't there, or is empty
    or blank, reads as empty. One that is there but isn't a small regular file holding a JSON object doesn't:
    a symlink to a device or a pipe must not hang the hook."""
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            return {}, False
        with path.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        if not raw.strip():
            return {}, True  # an empty file holds no setting
        value = json.loads(raw.decode("utf-8")) if len(raw) <= MAX_BYTES else None
    except (FileNotFoundError, NotADirectoryError):
        return {}, True
    except (OSError, ValueError, RecursionError):
        return {}, False
    return (value, True) if isinstance(value, dict) else ({}, False)


def _file(path: Path) -> dict:
    return _layer(_load(path)[0])


def _policy() -> dict:
    """managed-settings.json, then each non-hidden managed-settings.d/*.json in name order:
    lists combine, a later single value replaces an earlier one. A file that is there but
    can't be read leaves no model open: reading it as no policy would drop a deny. Folders are
    skipped, since Claude Code reads files."""
    main = managed_root() / "managed-settings.json"
    readable = True
    try:
        drop_ins = sorted(p for p in (main.parent / "managed-settings.d").iterdir()
                          if p.suffix == ".json" and not p.name.startswith(".") and not p.is_dir())
    except (FileNotFoundError, NotADirectoryError):
        drop_ins = []
    except OSError:
        drop_ins, readable = [], False
    merged: dict = {}
    for path in [main, *drop_ins]:
        data, ok = _load(path)
        readable = readable and ok
        for key, value in _layer(data, True).items():
            if isinstance(value, list):
                value = merged.get(key, []) + [e for e in value if e not in merged.get(key, [])]
            elif isinstance(value, bool):
                value = merged.get(key, False) or value
            elif isinstance(value, dict):
                value = {**merged.get(key, {}), **value}
            merged[key] = value
    return merged if readable else {**merged, "unreadable": True}


def _local(project: Path) -> dict:
    """settings.local.json at the repository root, over the session folder's own."""
    own = _file(project / ".claude" / "settings.local.json")
    root = _repository_root(project)
    if root is None:
        return own
    return {**own, **_file(root / ".claude" / "settings.local.json")}


def _first_line(path: Path) -> str:
    if not path.is_file():
        raise OSError(path)
    with path.open("r", encoding="utf-8") as handle:
        return handle.readline(4096).strip()


def _repository_root(project: Path) -> Path | None:
    """Where Claude Code keeps the local file in a git repository, or None to read it beside
    settings.json (https://code.claude.com/docs/en/settings.md). A worktree's root is the main
    checkout's, found only through the layout git itself writes: a `.git` file naming
    <main>/.git/worktrees/<name>, whose commondir leads back to <main>/.git. None also on
    Windows, for the home folder, and for a root, `.git` or `.claude` that another user owns:
    those are what a planted repository could point at."""
    if os.name == "nt":
        return None
    try:
        for folder in (project, *project.parents):
            git = folder / ".git"
            if git.is_dir():
                root = folder
            elif git.is_file():
                gitdir = (folder / _first_line(git).removeprefix("gitdir:").strip()).resolve()
                common = (gitdir / _first_line(gitdir / "commondir")).resolve()
                if gitdir.parent.name != "worktrees" or common != gitdir.parent.parent or common.name != ".git":
                    return None
                root = common.parent
            else:
                continue
            owned = all(os.stat(p).st_uid == os.getuid() for p in (root, root / ".git", root / ".claude"))
            return root if owned and root.resolve() != Path.home().resolve() else None
    except (OSError, ValueError, RuntimeError):
        return None
    return None
