"""Which registry models availableModels and deniedModels leave open.

The mod reads each settings source from the engine and sends the four keys that matter;
without the mod, or for a source whose read failed, the settings files are read at their
documented paths. The managed files are read either way and can only narrow what the engine
reports. The rules live here, in `from_layers`."""
from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from . import registry, store

SOURCES = ("policy", "user", "project", "local", "flag")
KEYS = ("availableModels", "deniedModels", "availableModelsMatch", "modelOverrides")
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


def _layer(raw: object, policy: bool = False) -> dict:
    """One source cut to the four keys, each with the right shape; a bad key counts as unset.
    modelOverrides keeps only whether it holds anything. A deny list over the cap, or in the
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
    return layer


def _layers(raw: object) -> dict[str, dict]:
    """The sources present in `raw`, cleaned. A source that is not there is left out."""
    raw = raw if isinstance(raw, dict) else {}
    return {source: _layer(raw[source], source == "policy") for source in SOURCES if source in raw}


def _blocked_family(config: dict, entry: str) -> str | None:
    """The registry model a deny entry blocks whole: one that names the family with no version.
    A version, or a context variant such as sonnet[1m], blocks that model only: Claude Code
    moves the alias to the newest permitted version, so the alias stays usable."""
    e = entry.strip().lower()
    if e in ("best", "opusplan", "default") or "[" in e:
        return None
    if e in registry.models(config):
        return e
    return None if any(c.isdigit() for c in e) else registry.family(config, e)


@dataclass(frozen=True)
class Allowlist:
    allow: tuple[str, ...] | None  # None: no source set availableModels
    deny: tuple[str, ...] | None
    exact: bool  # availableModelsMatch is "exact"
    overrides: bool  # some source sets modelOverrides
    source: str  # "engine" or "files"
    floor_allow: tuple[str, ...] | None = None  # the managed files' list, which must permit too

    def permitted(self, config: dict) -> set[str]:
        """Registry models the lists leave open. An allow entry matches its family, so
        claude-opus-4-6 permits opus. A deny entry blocks a family only when it names the family."""
        names = set(registry.models(config))
        for entries in (self.allow, self.floor_allow):
            if entries is not None:
                found = {registry.family(config, e) for e in entries}
                names = {n for n in names if n in found}
        if self.deny is not None:
            blocked = {_blocked_family(config, e) for e in self.deny} - {None}
            # A project's model that resolves to a blocked family, such as `best`, goes with it.
            names = {n for n in names if registry.family(config, n) not in blocked}
        return names

    def permits_id(self, name: str, model_id: str) -> bool:
        """Whether the full ID of family `name` may run: not ruled out by the lists or by
        modelOverrides, which Claude Code applies to the ID itself."""
        def names(entries: tuple[str, ...], prefix: bool) -> bool:
            return any(e.lower() in (name, model_id) or prefix and model_id.startswith(e.lower() + "-")
                       for e in entries)

        for entries in (self.allow, self.floor_allow):
            if entries is not None and not names(entries, not self.exact):
                return False
        if self.deny is not None and names(self.deny, True):
            return False
        return not self.overrides


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
                     source=source, floor_allow=None if held is None else tuple(held))


def remember(session_id: str | None, sent: object) -> dict[str, dict] | None:
    """Clean the mod's `settings` payload and keep it for the session's later hooks. None
    when the mod sent no layers. A source it left out is one whose read failed."""
    if not isinstance(sent, dict):
        return None
    stored = _stored(session_id)
    layers = {**stored, **_layers(sent)}  # a source the mod left out keeps what it last sent
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


def read(session_id: str | None, project: Path, sent: object = None) -> Allowlist:
    """Each source from the layers sent now, else the latest the session sent, else its
    settings file. The managed files always apply as a floor."""
    layers = remember(session_id, sent)
    if layers is None:
        layers = _stored(session_id)
    files = file_layers(project)
    merged = {s: layers[s] if s in layers else files[s] for s in SOURCES}
    return from_layers(merged, "engine" if layers else "files", floor=files["policy"])


def file_layers(project: Path) -> dict[str, dict]:
    """The settings files as the sources they stand for. The flag source and the managed
    tiers that have no file (server-managed, MDM, registry) can't be seen from here."""
    return {"policy": _policy(), "user": _file(store.claude_home() / "settings.json"),
            "project": _file(project / ".claude" / "settings.json"), "local": _local(project), "flag": {}}


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
