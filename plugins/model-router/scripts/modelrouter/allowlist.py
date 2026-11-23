"""Which registry models availableModels and deniedModels leave open.

The mod reads each settings source from the engine and sends the four keys that matter;
without the mod, or when that read fails, the settings files are read at their documented
paths. Either way the rules live here, in `from_layers`."""
from __future__ import annotations

import os
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


def managed_root(platform: str = sys.platform) -> Path:
    return MANAGED_ROOT or Path(MANAGED_ROOTS.get(platform, "/etc/claude-code"))


def _layer(raw: object) -> dict:
    """One source cut to the four keys, each with the right shape; a bad key counts as unset."""
    if not isinstance(raw, dict):
        return {}
    layer: dict = {}
    for key in ("availableModels", "deniedModels"):
        if isinstance(raw.get(key), list):
            layer[key] = [e for e in raw[key] if isinstance(e, str)]
    if isinstance(raw.get("availableModelsMatch"), str):
        layer["availableModelsMatch"] = raw["availableModelsMatch"]
    if isinstance(raw.get("modelOverrides"), dict):
        layer["modelOverrides"] = raw["modelOverrides"]
    return layer


def _layers(raw: object) -> dict[str, dict]:
    raw = raw if isinstance(raw, dict) else {}
    return {source: _layer(raw.get(source)) for source in SOURCES}


@dataclass(frozen=True)
class Allowlist:
    allow: tuple[str, ...] | None  # None: no source set availableModels
    deny: tuple[str, ...] | None
    exact: bool  # availableModelsMatch is "exact"
    overrides: bool  # some source sets modelOverrides
    source: str  # "engine" or "files"

    def permitted(self, config: dict) -> set[str]:
        """Registry models the lists leave open. An allow entry matches its family, so
        claude-opus-4-6 permits opus. A deny entry blocks a family only when it names the
        family: Claude Code moves the alias to the newest version still permitted, so a
        denied version leaves the alias usable."""
        names = set(registry.models(config))
        if self.allow is not None:
            found = {registry.family(config, e) for e in self.allow}
            names = {n for n in names if n in found}
        if self.deny is not None:
            aliases = config.get("aliases") or {}
            names -= {aliases.get(e.lower(), e.lower()) for e in self.deny
                      if e.lower() not in ("best", "opusplan", "default")}
        return names

    def permits_id(self, name: str, model_id: str) -> bool:
        """Whether the full ID of family `name` may run: not ruled out by the lists or by
        modelOverrides, which Claude Code applies to the ID itself."""
        def names(entries: tuple[str, ...], prefix: bool) -> bool:
            return any(e.lower() in (name, model_id) or prefix and model_id.startswith(e.lower() + "-")
                       for e in entries)

        if self.allow is not None and not names(self.allow, not self.exact):
            return False
        if self.deny is not None and names(self.deny, True):
            return False
        return not self.overrides


def from_layers(layers: dict, source: str = "engine") -> Allowlist:
    """availableModels as Claude Code applies it: a policy list applies alone, otherwise the
    user, project, local and flag lists are concatenated. deniedModels and
    availableModelsMatch count only in policy."""
    layers = _layers(layers)
    policy = layers["policy"]
    allow = policy.get("availableModels")
    if allow is None:
        lists = [layers[s]["availableModels"] for s in SOURCES[1:] if "availableModels" in layers[s]]
        allow = sum(lists, []) if lists else None
    deny = policy.get("deniedModels")
    return Allowlist(allow=None if allow is None else tuple(allow), deny=None if deny is None else tuple(deny),
                     exact=policy.get("availableModelsMatch") == "exact",
                     overrides=any(layer.get("modelOverrides") for layer in layers.values()), source=source)


def remember(session_id: str | None, sent: object) -> dict[str, dict] | None:
    """Clean the mod's `settings` payload and keep it for the session's later hooks. None
    when the mod sent no layers, which is also what a failed settings read looks like."""
    if not isinstance(sent, dict):
        return None
    layers = _layers(sent)
    try:
        if session_id and store.load_entry("allowlist", session_id).get("layers") != layers:
            store.save_entry("allowlist", session_id, {"layers": layers})
    except OSError:
        pass  # these layers still answer this call
    return layers


def read(session_id: str | None, project: Path, sent: object = None) -> Allowlist:
    """The layers sent now, else the latest the session sent, else the settings files."""
    layers = remember(session_id, sent)
    if layers is None:
        stored = store.load_entry("allowlist", session_id).get("layers")
        layers = stored if isinstance(stored, dict) else None
    if layers is None:
        return from_layers(file_layers(project), "files")
    return from_layers(layers)


def file_layers(project: Path) -> dict[str, dict]:
    """The settings files as the sources they stand for. The flag source and the managed
    tiers that have no file (server-managed, MDM, registry) can't be seen from here."""
    return {"policy": _policy(), "user": _layer(store.load_dict(store.claude_home() / "settings.json")),
            "project": _layer(store.load_dict(project / ".claude" / "settings.json")), "local": _local(project),
            "flag": {}}


def _policy() -> dict:
    """managed-settings.json, then each non-hidden managed-settings.d/*.json in name order:
    lists combine, a later single value replaces an earlier one."""
    main = managed_root() / "managed-settings.json"
    try:
        drop_ins = sorted(p for p in (main.parent / "managed-settings.d").iterdir()
                          if p.suffix == ".json" and not p.name.startswith("."))
    except OSError:
        drop_ins = []
    merged: dict = {}
    for path in [main, *drop_ins]:
        for key, value in _layer(store.load_dict(path)).items():
            if isinstance(value, list):
                value = merged.get(key, []) + [e for e in value if e not in merged.get(key, [])]
            elif isinstance(value, dict):
                value = {**merged.get(key, {}), **value}
            merged[key] = value
    return merged


def _local(project: Path) -> dict:
    """settings.local.json at the repository root, over the session folder's own."""
    own = _layer(store.load_dict(project / ".claude" / "settings.local.json"))
    root = _repository_root(project)
    if root is None:
        return own
    return {**own, **_layer(store.load_dict(root / ".claude" / "settings.local.json"))}


def _repository_root(project: Path) -> Path | None:
    """Where Claude Code keeps the local file in a git repository, or None to read it beside
    settings.json (https://code.claude.com/docs/en/settings.md). A worktree's root is the main
    checkout's. None also on Windows, for the home folder, and for a root, `.git` or `.claude`
    that another user owns: those are what a planted repository could point at."""
    if os.name == "nt":
        return None
    try:
        for folder in (project, *project.parents):
            git = folder / ".git"
            if git.is_dir():
                root = folder
            elif git.is_file():
                gitdir = folder / git.read_text(encoding="utf-8").split("\n")[0].removeprefix("gitdir:").strip()
                root = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve().parent
            else:
                continue
            owned = all(os.stat(p).st_uid == os.getuid() for p in (root, root / ".git", root / ".claude"))
            return root if owned and root.resolve() != Path.home().resolve() else None
    except (OSError, ValueError, RuntimeError):
        return None
    return None
