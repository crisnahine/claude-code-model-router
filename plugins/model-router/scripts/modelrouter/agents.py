"""Find an agent's definition, wherever it comes from: the project, the user's own
agents, this plugin, another installed plugin, or Claude Code's built-ins."""
from __future__ import annotations

import os
import re
from itertools import islice
from pathlib import Path

from . import config as cfg
from . import store

# Built-ins that run on the conversation's model unless a call says otherwise.
BUILTIN = ("general-purpose", "Explore", "Plan", "claude")
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def _field(block: str, name: str) -> str | None:
    # A plain or quoted scalar, with an optional trailing YAML comment.
    match = re.search(r"^%s:[ \t]*['\"]?([^'\"\n#]+?)['\"]?[ \t]*(?:#.*)?$" % re.escape(name), block, re.M)
    return match.group(1).strip() if match else None


def parse(path: Path, source: str) -> dict | None:
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as fh:  # -sig drops a leading BOM
            head = fh.read(6000)
    except OSError:
        return None
    block = _FRONTMATTER.match(head)
    if not block:
        return None
    text = block.group(1)
    model = _field(text, "model")
    return {
        "source": source,
        "name": _field(text, "name") or path.stem,
        "model": None if (model or "inherit").lower() == "inherit" else model,
        "effort": _field(text, "effort"),
    }


def _search(folders: list[tuple[str, Path]], name: str) -> dict | None:
    for source, folder in folders:
        try:
            files = sorted(folder.rglob("*.md"))[:500]
        except OSError:
            continue
        for path in files:
            found = parse(path, source)
            if found and found["name"] == name:
                return found
    return None


_SKIP_DIRS = ("node_modules", ".git", "__pycache__")


def _markdown_files(root: Path):
    """The .md files under `root` in a stable order, skipping dependency and VCS trees."""
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(files):
            if name.lower().endswith(".md"):
                yield Path(current) / name


def installed_roots(plugin: str) -> list[Path]:
    """Install folders of a plugin from Claude Code's own install record alone. It reads one small file and
    walks nothing, so a hot path can use it; a plugin the record doesn't list, such as one loaded with
    --plugin-dir, isn't found."""
    roots: list[Path] = []
    record = store.load_dict(store.claude_home() / "plugins" / "installed_plugins.json").get("plugins")
    for key, installs in record.items() if isinstance(record, dict) else []:
        if str(key).split("@")[0] != plugin:
            continue
        for install in installs if isinstance(installs, list) else [installs]:
            if isinstance(install, dict) and isinstance(install.get("installPath"), str):
                roots.append(Path(install["installPath"]))
    return roots


def _listed(plugin: str, root: Path) -> list[Path]:
    """The agent files the plugin's manifest and its marketplace entry name. Naming any replaces the agents/
    scan; the entry's are appended to the manifest's, or are the manifest when the plugin has none."""
    plugins = store.claude_home() / "plugins"
    sources = [store.load_dict(root / ".claude-plugin" / "plugin.json")]
    record = store.load_dict(plugins / "installed_plugins.json").get("plugins")
    known = store.load_dict(plugins / "known_marketplaces.json")
    for key, installs in record.items() if isinstance(record, dict) else []:
        name, _, market = str(key).partition("@")
        installs = installs if isinstance(installs, list) else [installs]
        if name != plugin or str(root) not in [i.get("installPath") for i in installs if isinstance(i, dict)]:
            continue
        location = known.get(market)
        location = location.get("installLocation") if isinstance(location, dict) else None
        entries = store.load_dict(Path(location) / ".claude-plugin" / "marketplace.json").get("plugins") \
            if isinstance(location, str) else None
        sources += [e for e in entries if isinstance(e, dict) and e.get("name") == plugin] \
            if isinstance(entries, list) else []
    listed = []
    for source in sources:
        value = source.get("agents")
        for item in [value] if isinstance(value, str) else value if isinstance(value, list) else []:
            # Claude Code doesn't load a listed path that escapes the plugin root.
            if isinstance(item, str) and (root / item).resolve().is_relative_to(root.resolve()):
                listed.append(root / item)
    return listed


def _plugin_roots(plugin: str) -> list[Path]:
    """Install folders of a plugin: the install record, else a bounded walk of the plugins folder by name."""
    roots = installed_roots(plugin)
    if roots:
        return roots
    base = store.claude_home() / "plugins"
    seen = 0
    for current, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS + ("data",)]
        seen += len(files) + 1
        if seen > 20000:
            break
        if Path(current).name == plugin:
            roots.append(Path(current))
            dirs[:] = []
    return roots


def find(agent_type: str, project: Path) -> dict | None:
    """The agent's definition, or None when it can't be found. An agent we can't see is
    left alone: it may have a model of its own that we would be overriding blind."""
    if ":" in agent_type:
        plugin, name = agent_type.split(":", 1)
        leaf = name.split(":")[-1]
        ours = plugin == cfg.PLUGIN_NAME
        roots = [cfg.PLUGIN_ROOT] if ours else _plugin_roots(plugin)
        listed = [path for root in roots for path in _listed(plugin, root)] if not ours else []
        if listed:
            for path in listed:
                found = parse(path, "plugin")
                if found and found["name"] == leaf:
                    return found
            return None
        found = _search([("plugin", root / "agents") for root in roots], leaf)
        if found or ours:
            return found
        # A plugin may keep its agents somewhere other than agents/.
        for root in roots:
            try:
                paths = [p for p in islice(_markdown_files(root), 2000) if "agents" in p.parts]
            except OSError:
                continue
            for path in paths:
                found = parse(path, "plugin")
                if found and found["name"] == leaf:
                    return found
        return None
    found = _search([("project", project / ".claude" / "agents"),
                     ("user", store.claude_home() / "agents")], agent_type)
    if found:
        return found
    if agent_type in BUILTIN:
        return {"source": "builtin", "name": agent_type, "model": None, "effort": None}
    return None
