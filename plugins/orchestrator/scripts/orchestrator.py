#!/usr/bin/env python3
"""Orchestrator plugin runtime: hook handlers and helper commands.

Standard library only, so it runs anywhere Python 3.9+ is installed.

Hook commands (read hook JSON on stdin):
  session-start    Print routing context and pending upstream-change notices.
  user-prompt      UserPromptSubmit: restate the routing policy, note agents the user named.
  pre-agent        PreToolUse on Agent: enforce the model tier cap, redirect generic
                   built-in agents to ours, log the spawn.
  post-agent       PostToolUse on Agent: log the model the subagent actually ran on.
  model-switch     PostModelSwitch: remember the session's model, which inheriting
                   subagents run on.
  refresh-cache    Async SessionStart: refresh the upstream cache at most once a day.

Helper commands (used by skills and CI):
  routing-table    Print the effective routing policy as Markdown.
  refresh [--force]
  status [--json]  Show Claude Code releases and docs pages that changed since the
                   last acknowledged review.
  ack              Mark everything in the cache as reviewed.
  usage [DAYS]     Summarize the subagent ledger by agent and model.
  upstream-check --baseline FILE [--write]
                   CI mode: compare live upstream against a committed baseline.

Hooks must never break a session, so every hook handler swallows its own errors
and exits 0, except a deliberate deny from pre-agent.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parent.parent)
CHANGELOG_RSS = "https://code.claude.com/docs/en/changelog/rss.xml"
DOCS_INDEX = "https://code.claude.com/docs/llms.txt"
CACHE_TTL_SECONDS = 24 * 3600
HTTP_TIMEOUT = 10
TIER_RANK_DEFAULT = {"haiku": 1, "sonnet": 2, "opus": 3, "fable": 4}
# Docs pages that describe the surfaces this plugin is built on. A change to one of
# these is flagged as high impact in `status`.
WATCHED_PAGES = (
    "plugins", "sub-agents", "hooks", "skills", "model-config", "workflows",
    "plugin-evals", "mcp", "settings", "agent-teams", "costs",
)


# --------------------------------------------------------------------------- paths


def data_dir() -> Path:
    env = os.environ.get("CLAUDE_PLUGIN_DATA")
    path = Path(env) if env else Path.home() / ".claude" / "plugins" / "data" / "orchestrator-local"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path() -> Path:
    return data_dir() / "state.json"


def ledger_path() -> Path:
    return data_dir() / "ledger.jsonl"


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path: Path, value: Any) -> None:
    # A unique temp name per process, so two sessions writing at once can't clobber
    # each other's half-written file; the rename itself is atomic.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------------- config


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def project_dir(hook_input: dict | None = None) -> Path:
    if hook_input and hook_input.get("cwd"):
        return Path(hook_input["cwd"])
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def _clean_override(override: Any) -> dict:
    """Keep only well-formed parts of a project override, so a typo can't break hooks."""
    if not isinstance(override, dict):
        return {}
    out: dict = {}
    redirect = override.get("redirect")
    if isinstance(redirect, dict):
        out["redirect"] = {k: [n for n in v if isinstance(n, str)] for k, v in redirect.items()
                           if isinstance(k, str) and isinstance(v, list)}
    for section in ("agents", "tiers"):
        value = override.get(section)
        if isinstance(value, dict):
            out[section] = {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, dict)}
    for section in ("builtin_agents",):
        value = override.get(section)
        if isinstance(value, dict):
            out[section] = {k: v for k, v in value.items() if isinstance(v, str)}
    return out


def base_config() -> dict:
    return load_json(PLUGIN_ROOT / "config" / "routing.json", {})


def load_config(hook_input: dict | None = None) -> dict:
    config = base_config()
    override = _clean_override(load_json(project_dir(hook_input) / ".claude" / "orchestrator.json", {}))
    config = _merge(config, override)
    config["_override_agents"] = sorted((override.get("agents") or {}).keys())
    return config


def tier_ranks(config: dict) -> dict[str, int]:
    ranks = dict(TIER_RANK_DEFAULT)
    for name, tier in (config.get("tiers") or {}).items():
        if isinstance(tier, dict) and isinstance(tier.get("rank"), int):
            ranks[name] = tier["rank"]
    return ranks


def model_family(model: str | None) -> str | None:
    """Map an alias or full model ID to a tier name, or None if unknown/inherit."""
    if not model:
        return None
    m = model.lower().split("[")[0]
    if m == "best":
        return "opus"
    for family in ("fable", "opus", "sonnet", "haiku"):
        if m == family or family in m:
            return family
    return None


def strip_namespace(agent_type: str) -> str:
    return agent_type.split(":", 1)[1] if ":" in agent_type else agent_type


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def local_agent_names(project: Path) -> set[str]:
    """Names of user and project subagents, which shadow nothing of ours (ours are
    namespaced) but do share bare names such as `reviewer`."""
    names: set[str] = set()
    for folder in (project / ".claude" / "agents", Path.home() / ".claude" / "agents"):
        try:
            files = list(folder.rglob("*.md"))
        except OSError:
            continue
        for path in files[:500]:
            try:
                head = path.read_text(encoding="utf-8", errors="replace")[:2000]
            except OSError:
                continue
            match = re.search(r"^---\s*\n(?:.*\n)*?name:\s*['\"]?([^'\"\n]+?)['\"]?\s*\n", head)
            if match:
                names.add(match.group(1).strip())
    return names


def resolve_agent_model(config: dict, subagent_type: str, requested: str | None,
                        session_model: str | None = None, project: Path | None = None) -> str | None:
    """Return the tier a spawn will most likely run on, or None when it can't be known.

    Mirrors the order in the subagents docs: a forced CLAUDE_CODE_SUBAGENT_MODEL wins,
    then the per-call model, then the agent's own model, then CLAUDE_CODE_SUBAGENT_MODEL,
    then the parent conversation's model.
    """
    env_model = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL")
    if _truthy(os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL_FORCE")):
        return model_family(env_model) or model_family(session_model)
    if requested:
        return model_family(requested)

    subagent_type = subagent_type or "general-purpose"
    agents = config.get("agents") or {}
    base_agents = (base_config().get("agents") or {})
    defined: str | None = None
    known = False
    if ":" in subagent_type:
        prefix, name = subagent_type.split(":", 1)
        if prefix == "orchestrator" and name in agents:
            defined, known = agents[name].get("model"), True
    else:
        name = subagent_type
        if name in (config.get("_override_agents") or []):
            defined, known = agents[name].get("model"), True
        elif name in (config.get("builtin_agents") or {}):
            defined, known = config["builtin_agents"][name], True
        elif name in base_agents and name not in local_agent_names(project or Path.cwd()):
            # A bare name for one of ours, with no user or project agent of that name.
            defined, known = agents[name].get("model"), True
    if not known:
        return None  # another plugin's or the user's own agent: its model is its business

    if defined and defined != "inherit":
        return model_family(defined)
    parent = model_family(env_model) or model_family(session_model)
    if subagent_type == "Explore" and parent == "fable":
        return "opus"  # documented: Explore steps down from Fable to Opus on most connections
    return parent


def agent_label(name: str) -> str:
    """Plugin agents are namespaced; project agents registered by override aren't."""
    base = (load_json(PLUGIN_ROOT / "config" / "routing.json", {}).get("agents") or {})
    return f"orchestrator:{name}" if name in base else name


def max_tier(from_state: bool = False) -> str:
    """The tier cap. Hooks read it from the environment, where Claude Code puts plugin
    options. Skill commands run through the Bash tool, which doesn't get plugin
    options, so they pass from_state and read what the last SessionStart recorded."""
    value = os.environ.get("CLAUDE_PLUGIN_OPTION_MAX_TIER")
    if not value and from_state:
        value = load_json(state_path(), {}).get("max_tier")
    value = value or "fable"
    value = value.strip().lower()
    return value if value in TIER_RANK_DEFAULT else "fable"


ROUTING_MODES = ("off", "guide", "steer", "strict")


def routing_mode() -> str:
    """off: no routing guidance or redirects (cap and ledger still work).
    guide: routing policy is injected as context only.
    steer (default): also redirect generic built-in agent calls to ours, once per prompt.
    strict: always refuse generic built-in agent calls the user didn't ask for by name."""
    value = (os.environ.get("CLAUDE_PLUGIN_OPTION_ROUTING_MODE") or "steer").strip().lower()
    return value if value in ROUTING_MODES else "steer"


# -------------------------------------------------------------- per-prompt state


def prompts_path() -> Path:
    return data_dir() / "prompts.json"


def load_prompt_state(session_id: str | None) -> dict:
    data = load_json(prompts_path(), {})
    entry = data.get(session_id) if isinstance(data, dict) and session_id else None
    return entry if isinstance(entry, dict) else {}


def save_prompt_state(session_id: str | None, entry: dict) -> None:
    if not session_id:
        return
    data = load_json(prompts_path(), {})
    if not isinstance(data, dict):
        data = {}
    data.pop(session_id, None)
    data[session_id] = entry
    while len(data) > 200:
        data.pop(next(iter(data)))
    save_json(prompts_path(), data)


def named_in_prompt(prompt: str, agent_type: str) -> bool:
    """Did the user ask for this built-in agent by name? Plain words like "explore the
    code" or "plan this" don't count; "the Explore agent" or "general-purpose" do."""
    text = (prompt or "").lower()
    name = agent_type.lower()
    if "-" in name or ":" in name:
        return name in text or name.replace("-", " ") in text
    return bool(re.search(r"\b%s\s+(?:sub-?)?agent\b|subagent_type\W+%s\b|built-?in\s+%s\b"
                          % (re.escape(name), re.escape(name), re.escape(name)), text))


def redirect_targets(config: dict, agent_type: str, cap: str) -> list[str]:
    ranks = tier_ranks(config)
    agents = config.get("agents") or {}
    names = (config.get("redirect") or {}).get(agent_type) or []
    return [n for n in names if n in agents and ranks.get(agents[n].get("model"), 9) <= ranks.get(cap, 4)]


def policy_line(config: dict, cap: str) -> str:
    ranks = tier_ranks(config)
    agents = [
        f"{agent_label(n)} ({a.get('model')}: {a.get('role', '').lower()})"
        for n, a in sorted((config.get("agents") or {}).items(), key=lambda kv: ranks.get(kv[1].get("model"), 9))
        if ranks.get(a.get("model"), 9) <= ranks.get(cap, 4)
    ]
    return (
        "Orchestrator routing policy (applies to every task, including work driven by any other skill, "
        "command or plugin): when you delegate, use these model-pinned agents instead of general-purpose, "
        "Explore or Plan: " + "; ".join(agents) + ". Pick the cheapest one that can do the job reliably, "
        "run independent pieces in parallel, and have non-trivial code changes verified and reviewed. "
        "Don't delegate work that takes you one or two tool calls. If the user names a specific agent or "
        "model, use what they asked for."
    )


# ------------------------------------------------------------------------- ledger


LEDGER_MAX_BYTES = 4 * 1024 * 1024


def append_ledger(entry: dict) -> None:
    entry = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), **entry}
    path = ledger_path()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    try:
        if path.stat().st_size > LEDGER_MAX_BYTES:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            tmp.write_text("".join(lines[len(lines) // 2:]), encoding="utf-8")
            tmp.replace(path)
    except OSError:
        pass


# ---------------------------------------------------------------- session models


def sessions_path() -> Path:
    return data_dir() / "sessions.json"


def record_session_model(session_id: str | None, model: str | None) -> None:
    if not session_id or not model:
        return
    sessions = load_json(sessions_path(), {})
    if not isinstance(sessions, dict):
        sessions = {}
    sessions.pop(session_id, None)
    sessions[session_id] = model
    while len(sessions) > 200:
        sessions.pop(next(iter(sessions)))
    save_json(sessions_path(), sessions)


def model_from_transcript(transcript_path: str | None) -> str | None:
    """The model of the newest assistant message in a transcript, read from its tail."""
    if not transcript_path:
        return None
    try:
        with open(os.path.expanduser(transcript_path), "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 512 * 1024))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        if '"assistant"' not in line or '"model"' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        message = row.get("message") if isinstance(row, dict) else None
        model = message.get("model") if isinstance(message, dict) else None
        if isinstance(model, str) and model_family(model):
            return model
    return None


def session_model(session_id: str | None, transcript_path: str | None = None) -> str | None:
    """Best effort: the model recorded by SessionStart or PostModelSwitch, else the one
    the transcript shows. SessionStart doesn't always carry `model`."""
    sessions = load_json(sessions_path(), {})
    recorded = sessions.get(session_id) if isinstance(sessions, dict) and session_id else None
    return model_from_transcript(transcript_path) or recorded


def read_ledger(days: int | None = None) -> list[dict]:
    rows: list[dict] = []
    cutoff = None
    if days:
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    try:
        lines = ledger_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if cutoff:
            try:
                if _dt.datetime.fromisoformat(row["ts"]) < cutoff:
                    continue
            except (KeyError, ValueError):
                continue
        rows.append(row)
    return rows


# ----------------------------------------------------------------------- upstream


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "orchestrator-plugin/0.1"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310 (fixed https URLs)
        return resp.read().decode("utf-8", "replace")


def parse_rss(xml_text: str, limit: int = 30) -> list[dict]:
    root = ET.fromstring(xml_text)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        content = ""
        for child in item:
            if child.tag.endswith("encoded"):
                content = child.text or ""
        text = re.sub(r"<[^>]+>", "", content)
        bullets = [b.strip() for b in text.splitlines() if b.strip()]
        items.append({
            "version": title,
            "date": (item.findtext("pubDate") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "highlights": bullets[:40],
        })
        if len(items) >= limit:
            break
    return items


def parse_llms_index(text: str) -> dict[str, str]:
    """Map docs page URL to its one-line summary from llms.txt."""
    pages = {}
    for match in re.finditer(r"^- \[[^\]]*\]\((https://[^)]+)\):?\s*(.*)$", text, re.M):
        pages[match.group(1)] = match.group(2).strip()
    return pages


def version_key(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-]", version))


def fetch_upstream() -> dict:
    releases = parse_rss(http_get(CHANGELOG_RSS))
    pages = parse_llms_index(http_get(DOCS_INDEX))
    return {
        "fetched_at": int(time.time()),
        "releases": releases,
        "pages": {url: hashlib.sha256(summary.encode()).hexdigest()[:16] for url, summary in pages.items()},
    }


def diff_upstream(reviewed: dict, live: dict) -> dict:
    reviewed_version = reviewed.get("claude_code_version") or "0"
    new_releases = [
        r for r in live.get("releases", [])
        if r.get("version") and version_key(r["version"]) > version_key(reviewed_version)
    ]
    old_pages = reviewed.get("pages") or {}
    new_pages = live.get("pages") or {}
    added = sorted(set(new_pages) - set(old_pages))
    removed = sorted(set(old_pages) - set(new_pages))
    changed = sorted(u for u in set(new_pages) & set(old_pages) if new_pages[u] != old_pages[u])

    def watched(url: str) -> bool:
        slug = url.rsplit("/docs/en/", 1)[-1].removesuffix(".md")
        return any(slug == p or slug.startswith(p + "/") or slug.startswith(p + "-") for p in WATCHED_PAGES)

    return {
        "reviewed_version": reviewed_version,
        "new_releases": new_releases,
        "pages_added": added,
        "pages_removed": removed,
        "pages_changed": changed,
        "watched_pages_touched": sorted(u for u in added + removed + changed if watched(u)),
    }


def refresh(force: bool = False) -> dict:
    state = load_json(state_path(), {})
    cache = state.get("cache") or {}
    if not force and time.time() - cache.get("fetched_at", 0) < CACHE_TTL_SECONDS:
        return state
    state["cache"] = fetch_upstream()
    if "reviewed" not in state:
        # First run: treat the present as reviewed so users aren't flooded with history.
        latest = state["cache"]["releases"][0]["version"] if state["cache"]["releases"] else "0"
        state["reviewed"] = {"claude_code_version": latest, "pages": state["cache"]["pages"]}
    save_json(state_path(), state)
    return state


def pending_changes(state: dict) -> dict | None:
    if not state.get("cache") or not state.get("reviewed"):
        return None
    return diff_upstream(state["reviewed"], state["cache"])


# -------------------------------------------------------------------- formatting


def routing_table(config: dict) -> str:
    cap = max_tier(from_state=True)
    ranks = tier_ranks(config)
    lines = [f"Model tier cap: **{cap}** (set with /config, option max_tier).", "",
             "| Agent | Model | Role |", "| :- | :- | :- |"]
    for name, agent in sorted((config.get("agents") or {}).items(), key=lambda kv: ranks.get(kv[1].get("model"), 9)):
        model = agent.get("model", "inherit")
        flag = " (over cap)" if ranks.get(model, 0) > ranks.get(cap, 4) else ""
        lines.append(f"| {agent_label(name)} | {model}{flag} | {agent.get('role', '')} |")
    lines += ["", "| Tier | Use for |", "| :- | :- |"]
    for name, tier in sorted((config.get("tiers") or {}).items(), key=lambda kv: kv[1].get("rank", 9)):
        lines.append(f"| {name} | {tier.get('use_for', '')} |")
    return "\n".join(lines)


def status_text(changes: dict | None) -> str:
    if changes is None:
        return "No upstream data cached yet. Run `/orchestrator:refresh`."
    out = [f"Last reviewed Claude Code version: {changes['reviewed_version']}"]
    if changes["new_releases"]:
        out.append(f"\nNew releases since then ({len(changes['new_releases'])}):")
        for rel in changes["new_releases"]:
            out.append(f"- {rel['version']} ({rel['date']}): {rel['link']}")
    else:
        out.append("No new releases.")
    for label, key in (("Docs pages added", "pages_added"), ("Docs pages removed", "pages_removed"),
                       ("Docs pages whose summary changed", "pages_changed")):
        if changes[key]:
            out.append(f"\n{label} ({len(changes[key])}):")
            out += [f"- {u}" for u in changes[key][:25]]
    if changes["watched_pages_touched"]:
        out.append("\nHigh impact (pages this plugin is built on):")
        out += [f"- {u}" for u in changes["watched_pages_touched"]]
    return "\n".join(out)


def has_pending(changes: dict | None) -> bool:
    return bool(changes and (changes["new_releases"] or changes["watched_pages_touched"]))


# -------------------------------------------------------------------- hook handlers


def read_hook_input() -> dict:
    try:
        data = json.load(sys.stdin)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def hook_session_start(hook_input: dict) -> None:
    config = load_config(hook_input)
    cap = max_tier()
    state = load_json(state_path(), {})
    if state.get("max_tier") != cap:
        state["max_tier"] = cap
        save_json(state_path(), state)
    record_session_model(hook_input.get("session_id"), hook_input.get("model"))
    agents = ", ".join(
        f"{agent_label(name)}={a.get('model')}" for name, a in sorted((config.get("agents") or {}).items())
    )
    mode = routing_mode()
    if mode == "off":
        parts = []
    else:
        parts = [
            policy_line(config, cap) + f" Model tier cap: {cap}. "
            "For a non-trivial task, the orchestrator:route skill has the scoring rubric; for a full "
            "plan-build-verify-review loop the user runs /orchestrator:ship."
        ]
    if os.environ.get("CLAUDE_PLUGIN_OPTION_UPDATE_CHECK", "true").lower() not in ("false", "0", "no"):
        changes = pending_changes(load_json(state_path(), {}))
        if has_pending(changes):
            n = len(changes["new_releases"])
            touched = len(changes["watched_pages_touched"])
            parts.append(
                f"Upstream: {n} Claude Code release(s) and {touched} plugin-relevant docs page change(s) "
                "since the last review. Mention once to the user that /orchestrator:refresh reviews them."
            )
    if parts:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": " ".join(parts)}}))


def hook_user_prompt(hook_input: dict) -> None:
    """Remember which built-in agents the user named, and restate the policy in one line
    so it holds deep into a long session and inside other plugins' skills."""
    mode = routing_mode()
    if mode == "off":
        return
    config = load_config(hook_input)
    prompt = hook_input.get("prompt") or ""
    named = [a for a in (config.get("redirect") or {}) if named_in_prompt(prompt, a)]
    save_prompt_state(hook_input.get("session_id"), {
        "prompt_id": hook_input.get("prompt_id"), "named": named, "redirected": []})
    reminder = (
        "Orchestrator routing is on: delegate through the orchestrator: agents (scout, verifier, implementer, "
        "architect, reviewer, deep-solver) rather than general-purpose, Explore or Plan, whichever skill or "
        "plugin is driving the work, unless the user named a specific agent."
    )
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                             "additionalContext": reminder}}))


def hook_pre_agent(hook_input: dict) -> int:
    tool_input = hook_input.get("tool_input") or {}
    subagent_type = tool_input.get("subagent_type") or "general-purpose"
    requested = tool_input.get("model")
    config = load_config(hook_input)
    ranks = tier_ranks(config)
    tier = resolve_agent_model(config, subagent_type, requested,
                               session_model(hook_input.get("session_id"), hook_input.get("transcript_path")),
                               project_dir(hook_input))
    cap = max_tier()
    decision = "allow"
    targets: list[str] = []
    if tier and ranks.get(tier, 0) > ranks.get(cap, 4):
        decision = "deny"
    else:
        mode = routing_mode()
        targets = redirect_targets(config, subagent_type, cap)
        if mode in ("steer", "strict") and targets and not (requested and mode == "steer"):
            session_id = hook_input.get("session_id")
            entry = load_prompt_state(session_id)
            if subagent_type in (entry.get("named") or []):
                pass  # the user asked for this agent by name
            elif mode == "strict":
                decision = "redirect"
            elif subagent_type not in (entry.get("redirected") or []):
                decision = "redirect"
                entry.setdefault("redirected", []).append(subagent_type)
                try:
                    save_prompt_state(session_id, entry)
                except OSError:
                    decision = "allow"  # can't remember the redirect, so don't risk a loop
    try:
        append_ledger({
            "event": "spawn",
            "session": hook_input.get("session_id"),
            "agent": subagent_type,
            "requested_model": requested,
            "expected_tier": tier or "unknown",
            "decision": decision,
            "description": (tool_input.get("description") or "")[:120],
        })
    except OSError:
        pass
    if decision == "redirect":
        agents = config.get("agents") or {}
        options = "; ".join(f"{agent_label(n)} ({agents[n].get('model')}: {agents[n].get('role', '').lower()})"
                            for n in targets)
        if routing_mode() == "strict":
            tail = ("Strict routing is on, so this agent type is only available when the user asks for it "
                    "by name.")
        else:
            tail = ("If none of them can do this particular job, call the same agent again and it will be "
                    "allowed.")
        reason = (f"Orchestrator routes '{subagent_type}' work to model-pinned agents. Re-issue this call "
                  f"with the same prompt and subagent_type set to one of: {options}. {tail}")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason}}))
        return 0
    if decision == "deny":
        allowed = sorted(
            (n for n, a in (config.get("agents") or {}).items() if ranks.get(a.get("model"), 0) <= ranks.get(cap, 4)),
        )
        reason = (
            f"Orchestrator tier cap is '{cap}', and this spawn would run on '{tier}'. "
            f"Retry with model: \"{cap}\" or one of these agents: "
            + ", ".join(agent_label(n) for n in allowed)
            + ". The user can raise the cap with /config (orchestrator max_tier)."
        )
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason}}))
    return 0


def hook_model_switch(hook_input: dict) -> None:
    record_session_model(hook_input.get("session_id"), hook_input.get("to_model"))


def hook_post_agent(hook_input: dict) -> None:
    tool_input = hook_input.get("tool_input") or {}
    response = hook_input.get("tool_response") or {}
    if not isinstance(response, dict):
        response = {}
    append_ledger({
        "event": "result",
        "session": hook_input.get("session_id"),
        "agent": tool_input.get("subagent_type") or "general-purpose",
        "resolved_model": response.get("resolvedModel"),
        "status": response.get("status"),
        "total_tokens": response.get("totalTokens"),
    })


# ------------------------------------------------------------------------- usage


def usage_report(days: int) -> str:
    rows = read_ledger(days)
    spawns = [r for r in rows if r.get("event") == "spawn"]
    results = [r for r in rows if r.get("event") == "result"]
    if not spawns and not results:
        return f"No subagent activity recorded in the last {days} days."
    by_agent: dict[str, dict] = {}
    for r in spawns:
        a = by_agent.setdefault(r.get("agent", "?"), {"spawns": 0, "denied": 0, "redirected": 0, "models": {}})
        a["spawns"] += 1
        if r.get("decision") == "deny":
            a["denied"] += 1
        if r.get("decision") == "redirect":
            a["redirected"] = a.get("redirected", 0) + 1
    for r in results:
        a = by_agent.setdefault(r.get("agent", "?"), {"spawns": 0, "denied": 0, "redirected": 0, "models": {}})
        model = r.get("resolved_model") or "background (not reported)"
        a["models"][model] = a["models"].get(model, 0) + 1
    lines = [f"Subagent usage, last {days} days", "", "| Agent | Calls | Redirected to ours | Denied by cap | Ran on |",
             "| :- | -: | -: | -: | :- |"]
    for agent, a in sorted(by_agent.items(), key=lambda kv: -kv[1]["spawns"]):
        models = ", ".join(f"{m} x{n}" for m, n in sorted(a["models"].items())) or "-"
        lines.append(f"| {agent} | {a['spawns']} | {a.get('redirected', 0)} | {a['denied']} | {models} |")
    lines.append("")
    lines.append("Token and dollar totals per model are in /usage and /cost; this ledger shows routing.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orchestrator")
    parser.add_argument("--data-dir", help="plugin data directory; skills pass ${CLAUDE_PLUGIN_DATA}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("session-start", "user-prompt", "pre-agent", "post-agent", "model-switch", "refresh-cache",
                 "routing-table", "ack"):
        sub.add_parser(name)
    p = sub.add_parser("refresh")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("status")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("usage")
    p.add_argument("days", nargs="?", default="7")
    p = sub.add_parser("upstream-check")
    p.add_argument("--baseline", required=True)
    p.add_argument("--write", action="store_true", help="overwrite the baseline with live data")
    p.add_argument("--json-out")
    args = parser.parse_args(argv)
    if args.data_dir and "${" not in args.data_dir:
        os.environ["CLAUDE_PLUGIN_DATA"] = args.data_dir

    hook_cmds = {"session-start", "user-prompt", "pre-agent", "post-agent", "model-switch", "refresh-cache"}
    if args.cmd in hook_cmds:
        try:
            hook_input = read_hook_input()
            if args.cmd == "session-start":
                hook_session_start(hook_input)
            elif args.cmd == "user-prompt":
                hook_user_prompt(hook_input)
            elif args.cmd == "pre-agent":
                return hook_pre_agent(hook_input)
            elif args.cmd == "post-agent":
                hook_post_agent(hook_input)
            elif args.cmd == "model-switch":
                hook_model_switch(hook_input)
            elif args.cmd == "refresh-cache":
                if os.environ.get("CLAUDE_PLUGIN_OPTION_UPDATE_CHECK", "true").lower() not in ("false", "0", "no"):
                    refresh(force=False)
        except Exception as exc:  # noqa: BLE001 - a hook must never break the session
            print(f"orchestrator {args.cmd}: {exc}", file=sys.stderr)
        return 0

    if args.cmd == "routing-table":
        print(routing_table(load_config()))
    elif args.cmd == "refresh":
        state = refresh(force=args.force)
        print(status_text(pending_changes(state)))
    elif args.cmd == "status":
        changes = pending_changes(load_json(state_path(), {}))
        print(json.dumps(changes, indent=2) if args.json else status_text(changes))
    elif args.cmd == "ack":
        state = load_json(state_path(), {})
        cache = state.get("cache")
        if not cache:
            print("Nothing cached yet; run refresh first.")
            return 1
        latest = cache["releases"][0]["version"] if cache.get("releases") else "0"
        state["reviewed"] = {"claude_code_version": latest, "pages": cache.get("pages", {}),
                             "reviewed_at": int(time.time())}
        save_json(state_path(), state)
        print(f"Marked Claude Code {latest} and the current docs index as reviewed.")
    elif args.cmd == "usage":
        days = int(args.days) if str(args.days).isdigit() else 7
        print(usage_report(days))
    elif args.cmd == "upstream-check":
        baseline_path = Path(args.baseline)
        baseline = load_json(baseline_path, {})
        live = fetch_upstream()
        changes = diff_upstream(baseline, live)
        if args.write:
            latest = live["releases"][0]["version"] if live["releases"] else "0"
            save_json(baseline_path, {"claude_code_version": latest, "pages": live["pages"]})
            print(f"Baseline written for Claude Code {latest} ({len(live['pages'])} docs pages).")
            return 0
        if args.json_out:
            Path(args.json_out).write_text(json.dumps(changes, indent=2), encoding="utf-8")
        print(status_text(changes))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
