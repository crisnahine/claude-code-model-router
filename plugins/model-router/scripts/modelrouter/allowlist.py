"""Which registry models availableModels and deniedModels leave open, the effort cap
maxEffortLevel sets for each, and the effort a settings file saves for one.

The mod reads each settings source from the engine and sends the keys that matter. Without
those layers no list applies here: Claude Code enforces the lists on every subagent model and
falls back instead of failing (https://code.claude.com/docs/en/sub-agents.md). The rules live
here, in `from_layers`."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import store
from .config import EFFORTS, project_dir
from .registry import Registry

SOURCES = ("policy", "user", "project", "local", "flag")
KEYS = ("availableModels", "deniedModels", "availableModelsMatch", "modelOverrides", "maxEffortLevel", "modelSettings",
        "effortLevel")
# Settings precedence, highest first (https://code.claude.com/docs/en/settings.md#settings-precedence).
PRECEDENCE = ("policy", "flag", "local", "project", "user")
# Where a blocked family alias moves to the newest permitted version (https://code.claude.com/docs/en/model-config.md).
# None is the Anthropic API.
SUBSTITUTING = (None, "anthropic_aws")
MAX_ENTRIES = 200
MAX_CHARS = 200


def _entries(raw: list) -> list[str]:
    """An allow list cut to the cap: fewer entries allow less, so a cut is the safe direction."""
    found = (e.strip() for e in raw if isinstance(e, str))
    return [e for e in found if 0 < len(e) <= MAX_CHARS][:MAX_ENTRIES]


def _denied(raw: list) -> list[str] | None:
    """A deny list as it is, or None when it is over the cap: a cut would let a denied model through."""
    found = [e for e in (e.strip() for e in raw if isinstance(e, str)) if e]
    return None if len(found) > MAX_ENTRIES or any(len(e) > MAX_CHARS for e in found) else found


def _caps(raw: object) -> dict[str, dict]:
    """modelSettings cut to each model's maxEffortLevel and effortLevel."""
    found = ((k, {f: v[f] for f in ("maxEffortLevel", "effortLevel") if v.get(f) in EFFORTS})
             for k, v in raw.items() if isinstance(v, dict)) if isinstance(raw, dict) else ()
    return dict([(k, v) for k, v in found if v and 0 < len(k) <= MAX_CHARS][:MAX_ENTRIES])


def _layer(raw: object, policy: bool = False) -> dict:
    """One source cut to KEYS, each with the right shape; a bad key counts as unset.
    modelOverrides keeps only whether it holds anything, and modelSettings only its effort fields. A deny list over the cap, or in the
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
    for key in ("maxEffortLevel", "effortLevel"):
        if raw.get(key) in EFFORTS:
            layer[key] = raw[key]
    if _caps(raw.get("modelSettings")):
        layer["modelSettings"] = _caps(raw["modelSettings"])
    return layer


def _layers(raw: object) -> dict[str, dict]:
    """The sources present in `raw`, cleaned. A source that is not there is left out."""
    raw = raw if isinstance(raw, dict) else {}
    return {source: _layer(raw[source], source == "policy") for source in SOURCES if source in raw}


def _blocked_family(reg: Registry, entry: str) -> str | None:
    """The registry model a deny entry blocks whole: one that names the family with no version.
    A version, or a context variant such as sonnet[1m], blocks that model only. Where the alias
    doesn't move to the newest permitted version, `Allowlist.permitted` checks the version it runs."""
    e = entry.strip().lower()
    if e in ("best", "opusplan", "default") or "[" in e:
        return None
    if e in reg.models:
        return e
    return None if any(c.isdigit() for c in e) else reg.family(e, twins=False)


def _effective(reg: Registry, entries: tuple[str, ...]) -> tuple[str, ...]:
    """An allow list as Claude Code applies it: an entry naming a specific model in a family (a
    version prefix or a full ID) turns off the entry that names the family itself
    (https://code.claude.com/docs/en/model-config.md, Merge behavior). An entry names a version
    when it carries a digit or a `claude-` or provider prefix; `opusplan` and `default` don't."""
    plain = set(reg.models) | set(reg.aliases)

    def bare(entry: str) -> str:
        return entry.strip().lower().split("[")[0]

    specific = {reg.family(e, twins=False) for e in entries
                if bare(e) not in plain and re.search(r"\d|^claude-|^[\w-]+[./]", bare(e))} - {None}
    return tuple(e for e in entries if bare(e) not in specific)


@dataclass(frozen=True)
class Allowlist:
    allow: tuple[str, ...] | None  # None: no source set availableModels
    deny: tuple[str, ...] | None
    exact: bool  # availableModelsMatch is "exact"
    overrides: bool  # some source sets modelOverrides
    effort_caps: tuple[dict, ...] = ()  # each source's maxEffortLevel and modelSettings caps
    provider: str | None = None  # settings.provider: whether a blocked alias moves to a permitted version
    efforts: tuple[tuple[str, dict], ...] = ()  # each source and its layer, in PRECEDENCE order

    def permitted(self, reg: Registry, sendable: frozenset = frozenset()) -> set[str]:
        """Registry models the lists leave open. An allow entry matches its family, so
        claude-opus-4-6 permits opus. A deny entry blocks a family only when it names the family.
        On a provider whose alias doesn't move, a family also needs the version its alias runs to be
        permitted, unless it is in `sendable` (`Snapshot.sendable`): its newest ID is sent instead."""
        names = set(reg.models)
        if self.allow is not None:
            found = {reg.family(e, twins=False) for e in _effective(reg, self.allow)}
            names = {n for n in names if n in found}
        if self.deny is not None:
            blocked = {_blocked_family(reg, e) for e in self.deny} - {None}
            # A project's model that resolves to a blocked family, such as `best`, goes with it.
            names = {n for n in names if reg.family(n) not in blocked}
        if self.provider in SUBSTITUTING:
            return names
        runs = reg.older_aliases.get(self.provider or "") or {}
        return {n for n in names if n not in reg.newest or n in sendable
                or self._lists_permit(reg, n, runs.get(n, reg.newest[n]))}

    def _lists_permit(self, reg: Registry, name: str, model_id: str) -> bool:
        own = reg.family(model_id, twins=False) == name  # a twin's ID isn't in its family's name to Claude Code

        def names(entries: tuple[str, ...], prefix: bool) -> bool:
            return any(e.lower() == model_id or own and e.lower() == name or prefix and model_id.startswith(e.lower() + "-")
                       for e in entries)

        if self.allow is not None and not names(_effective(reg, self.allow), not self.exact):
            return False
        return self.deny is None or not names(self.deny, True)

    def permits_id(self, reg: Registry, name: str, model_id: str) -> bool:
        """Whether the full ID of family `name` may run: not ruled out by the lists or by
        modelOverrides, which Claude Code applies to the ID itself."""
        return self._lists_permit(reg, name, model_id) and not self.overrides

    def permits_named(self, reg: Registry, name: str, model_id: str) -> bool:
        """Whether a full ID the caller or the agent named may run. Only the lists count: Claude Code
        maps that ID through modelOverrides whether the router sends it or leaves the call alone."""
        return self._lists_permit(reg, name, model_id)

    def effort_cap(self, reg: Registry, name: str | None) -> str | None:
        """The highest effort `name`'s family may run at, or None for no cap. The lowest cap of any
        source applies, and a source's modelSettings entry for the model replaces its top-level
        one; "max" sets none (https://code.claude.com/docs/en/settings-reference.md#maxeffortlevel)."""
        found = []
        for layer in self.effort_caps:
            # shortcut: modelSettings is keyed by model ID, and several IDs of one family read as the
            # highest of their caps; Claude Code still applies the exact one to each request.
            own = [v["maxEffortLevel"] for k, v in (layer.get("modelSettings") or {}).items()
                   if name and "maxEffortLevel" in v and reg.family(k) == name]
            cap = max(own, key=EFFORTS.index) if own else layer.get("maxEffortLevel")
            if cap in EFFORTS and cap != "max":
                found.append(cap)
        return min(found, key=EFFORTS.index) if found else None

    def saved_effort(self, reg: Registry, name: str | None) -> str | None:
        """The effort the settings files set for `name`'s family: the first source in precedence order with a level
        for that model, or a top-level one that applies to it (https://code.claude.com/docs/en/settings-reference.md#modelsettings)."""
        for source, layer in self.efforts:
            own = [v["effortLevel"] for k, v in (layer.get("modelSettings") or {}).items()
                   if name and "effortLevel" in v and reg.family(k) == name]
            # shortcut: a user file's top-level effortLevel is skipped, as Opus 5.5 and later models skip it; older models
            # still take it, so read it for them if the registry ever routes to one.
            top = layer.get("effortLevel") if source != "user" else None
            if own or top:
                return max(own, key=EFFORTS.index) if own else top
        return None


def from_layers(layers: dict, provider: str | None = None) -> Allowlist:
    """availableModels as Claude Code applies it: a policy list applies alone, otherwise the
    user, project, local and flag lists are concatenated. deniedModels and
    availableModelsMatch count only in policy."""
    cleaned = _layers(layers)
    layers = {s: cleaned.get(s, {}) for s in SOURCES}
    policy = layers["policy"]
    if policy.get("unreadable"):
        policy = {"availableModels": []}  # a policy we can't trust leaves nothing open
    allow = policy.get("availableModels")
    if allow is None:
        lists = [layers[s]["availableModels"] for s in SOURCES[1:] if "availableModels" in layers[s]]
        allow = sum(lists, []) if lists else None
    deny = policy.get("deniedModels")
    return Allowlist(allow=None if allow is None else tuple(allow), deny=None if deny is None else tuple(deny),
                     exact=policy.get("availableModelsMatch") == "exact",
                     overrides=any(layer.get("modelOverrides") for layer in layers.values()),
                     effort_caps=tuple(layer for layer in layers.values()
                                       if "maxEffortLevel" in layer or "modelSettings" in layer),
                     provider=provider, efforts=tuple((s, layers[s]) for s in PRECEDENCE))


def remember(session_id: str | None, sent: object) -> dict[str, dict] | None:
    """Clean the mod's `settings` payload and keep it for the session's later hooks. None
    when the mod sent no layers. A source it left out is one whose read failed."""
    layers, stored = _sent(session_id, sent)
    if layers is None:
        return None
    try:
        if session_id and layers != stored:
            store.save_entry("allowlist", session_id, {"layers": layers, "project": str(project_dir())})
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


def _latest() -> dict[str, dict]:
    """The layers the most recent session in this project sent, else the most recent session's.
    shortcut: a skill command has no session id, so with no session of its project on record it reads
    another project's; pass skill commands the session id if that misleads a report."""
    try:
        entries = [e for e in store.load_state_file(store.data_dir() / "allowlist.json", durable=False).values() if isinstance(e, dict)]
        here = [e for e in entries if e.get("project") == str(project_dir())]
        return _layers(max(here or entries, key=lambda e: store.stamp(e.get("_at")), default={}).get("layers"))
    except Exception:
        return {}


def read(session_id: str | None, sent: object = None, provider: str | None = None) -> Allowlist:
    """Each source from the layers sent now, else the latest the session sent. With none, no list
    applies. Reading stores nothing: the mod's own hooks call `remember`."""
    layers, stored = _sent(session_id, sent)
    if layers is None and not session_id:
        stored = _latest()
    return from_layers(stored if layers is None else layers, provider)
