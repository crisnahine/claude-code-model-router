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
READ_ONLY_BUILTINS = ("Explore", "Plan")
# The tools that change files or run commands: an agent with none of them can only read.
CHANGES = frozenset(("Edit", "Write", "NotebookEdit", "Bash"))
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.S)
HEAD_CHARS = 6000  # the frontmatter is read from this much of a file


def _field(block: str, name: str) -> str | None:
    # A plain or quoted scalar, with an optional trailing YAML comment.
    match = re.search(r"^%s:[ \t]*['\"]?([^'\"\n#]+?)['\"]?[ \t]*(?:#.*)?$" % re.escape(name), block, re.M)
    return match.group(1).strip() if match else None


# A tool entry as sub-agents.md writes them: Read, Bash(git status), mcp__github__*, Agent(worker, researcher).
_TOOL = re.compile(r"[A-Za-z_*][\w*.-]*(?:\(.*\))?")
_NOT_TEXT = re.compile(r"(?i)null|~|true|false|yes|no|on|off|[-+.\d]+")  # scalars YAML reads as no string


def _uncomment(text: str) -> str:
    """`text` without a YAML comment: a # at the start or after a blank, outside quotes."""
    quote = None
    for i, c in enumerate(text):
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].strip()
    return text.strip()


def _split(text: str) -> list[str] | None:
    """Comma-separated entries, a comma inside quotes or parentheses kept; None at a nested list or map."""
    items, current, depth, quote = [], "", 0, None
    for c in text:
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c in "[]{}":
            return None
        elif c in "()":
            depth += 1 if c == "(" else -1
        elif c == "," and depth == 0:
            items.append(current)
            current = ""
            continue
        current += c
    return [*items, current]


def _below(lines: list[str]) -> list[str]:
    """The lines under a key, comments dropped, up to the next key."""
    found = []
    for line in lines:
        text = _uncomment(line)
        if text and line[:1] not in (" ", "\t", "-"):
            break
        if text:
            found.append(text)
    return found


def _tools(entries: list[str] | None) -> list[str] | None:
    """The entries unquoted, when there are some and each is a tool's name; else None."""
    items = [i for i in (e.strip().strip("'\"").strip() for e in entries or ()) if i]
    return items if items and all(_TOOL.fullmatch(i) for i in items) else None


def _list(block: str, name: str) -> list[str] | None:
    """A comma list, a [flow] list (over several lines too) or `- item` lines, as YAML reads them. None when the field
    is absent, null or empty, and when its value can't be read with confidence: read_only then takes the safe side,
    every tool for `tools` and none removed for `disallowedTools`."""
    lines = [line.rstrip("\r") for line in block.split("\n")]
    at = next((i for i, line in enumerate(lines) if re.match(r"%s:(?:[ \t]|$)" % re.escape(name), line)), None)
    if at is None:
        return None
    value, following = _uncomment(lines[at][len(name) + 1:]), lines[at + 1:]
    below = _below(following)
    if not value:
        if below and all(re.match(r"-(?:\s|$)", line) for line in below):
            return _tools([line[1:] for line in below])
        value, following, below = " ".join(below), [], []  # a flow list or a scalar indented under the key
    if value.startswith("["):
        for line in following:  # the closing bracket can be lines below, even at a line's start
            if "]" in value:
                break
            value += " " + _uncomment(line)
        inner, close, after = value[1:].partition("]")
        return _tools(_split(inner)) if close and not after else None
    # Null or another non-string, a scalar over several lines, a block scalar, an anchor or alias, a tag, a map.
    quoted = value[:1] in ("'", '"')
    if (not value or below or value[0] in "|>&*!{?" or _NOT_TEXT.fullmatch(value)
            or (quoted and (len(value) < 2 or value[-1] != value[0]))):
        return None
    return _tools(_split(value[1:-1] if quoted else value))


def read_only(definition: dict) -> bool:
    """Whether the agent can change nothing: its tools hold none of CHANGES, or its disallowedTools remove them.
    A scoped entry such as Bash(git status) counts as the whole tool in either list (sub-agents.md, frontmatter)."""
    if definition.get("source") == "builtin":
        return definition.get("name") in READ_ONLY_BUILTINS
    tools = definition.get("tools")
    names = None if tools is None else {t.split("(")[0].strip() for t in tools}
    can = CHANGES if names is None or "*" in names else CHANGES & names
    return not (can - {t.split("(")[0].strip() for t in definition.get("disallowed") or ()})


def parse(path: Path, source: str) -> dict | None:
    raw = store.read_head(path, 4 * HEAD_CHARS)  # however large the file, and never a FIFO in its place
    if raw is None:
        return None
    block = _FRONTMATTER.match(raw.decode("utf-8-sig", "replace")[:HEAD_CHARS])  # -sig drops a leading BOM
    if not block:
        return None
    text = block.group(1)
    model = _field(text, "model")
    return {
        "source": source,
        "name": _field(text, "name") or path.stem,
        "model": None if (model or "inherit").lower() == "inherit" else model,
        # An explicit inherit is the agent's own choice of the parent's model, which a missing field isn't.
        "inherits": (model or "").lower() == "inherit",
        "effort": _field(text, "effort"),
        "tools": _list(text, "tools"),
        "disallowed": _list(text, "disallowedTools"),
    }


def _search(folders: list[tuple[str, Path]], name: str) -> dict | None:
    for source, folder in folders:
        for path in islice(_markdown_files(folder), 500):
            found = parse(path, source)
            if found and found["name"] == name:
                return found
    return None


_SKIP_DIRS = ("node_modules", ".git", "__pycache__")
# A walk's bounds. A repository's .claude/agents can be a symlink to /, which the walk follows (sub-agents.md says
# nothing of symlinks); os.walk recurses before Python 3.12, so depth also keeps it inside the recursion limit.
WALK_ENTRIES = 20000
WALK_DEPTH = 10


def _shallow_walk(root: Path):
    """os.walk from `root`, top down, at most WALK_DEPTH folders deep and WALK_ENTRIES entries in all."""
    seen, top = 0, len(Path(root).parts)
    for current, dirs, files in os.walk(root):
        seen += len(dirs) + len(files)
        if seen > WALK_ENTRIES:
            return
        if len(Path(current).parts) - top >= WALK_DEPTH:
            dirs[:] = []
        yield current, dirs, files


def _markdown_files(root: Path):
    """The .md files under `root` in a stable order, skipping dependency and VCS trees."""
    for current, dirs, files in _shallow_walk(root):
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
            if isinstance(item, str) and _inside(root / item, root):
                listed.append(root / item)
    return listed


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):  # a symlink loop, or a NUL in a marketplace file's path
        return False


def find(agent_type: str, project: Path) -> dict | None:
    """The agent's definition, or None when it can't be found. An agent we can't see is
    left alone: it may have a model of its own that we would be overriding blind."""
    if ":" in agent_type:
        plugin, name = agent_type.split(":", 1)
        leaf = name.split(":")[-1]
        ours = plugin == cfg.PLUGIN_NAME
        roots = [cfg.PLUGIN_ROOT] if ours else installed_roots(plugin)
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
    found = _search([*(("project", f / ".claude" / "agents") for f in cfg.project_folders(project)),
                     ("user", store.claude_home() / "agents")], agent_type)
    if found:
        return found
    if agent_type in BUILTIN:
        return {"source": "builtin", "name": agent_type, "model": None, "effort": None}
    return None
