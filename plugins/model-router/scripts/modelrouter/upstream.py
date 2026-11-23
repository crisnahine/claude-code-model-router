"""Claude Code releases, docs pages, model aliases and model retirements, compared with
what was last reviewed."""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path

from . import config as cfg
from . import health, store
from .settings import Settings

CHANGELOG_RSS = "https://code.claude.com/docs/en/changelog/rss.xml"
DOCS_INDEX = "https://code.claude.com/docs/llms.txt"
MODEL_CONFIG_PAGE = "https://code.claude.com/docs/en/model-config.md"
DEPRECATIONS_PAGE = "https://platform.claude.com/docs/en/about-claude/model-deprecations.md"
MODELS_API = "https://api.anthropic.com/v1/models?limit=1000"
CACHE_TTL_SECONDS = 24 * 3600
FAILED_RETRY_SECONDS = 3600  # after a failed fetch; doubles with each failure in a row, to a day
HTTP_TIMEOUT = 10  # per request: each socket wait, and the body's download as a whole
# The whole fetch, every phase of every request. Claude Code doesn't time out an async hook, so
# this is what ends a refresh stuck on a slow server.
FETCH_BUDGET = 25
# Docs pages that describe the surfaces this plugin is built on.
WATCHED_PAGES = ("plugins", "sub-agents", "hooks", "skills", "model-config", "workflows",
                 "plugin-evals", "mcp", "settings", "agent-teams", "costs", "permission-modes")
# Aliases that select a mode or a context size rather than a model family of their own.
NON_FAMILY_ALIASES = {"default", "best", "opusplan"}


def path() -> Path:
    return store.data_dir() / "upstream.json"


def load() -> dict:
    return store.load_state_file(path())


def http_get(url: str, headers: dict | None = None) -> str:
    import http.client
    import urllib.request  # about 20 ms, so only the daily fetch pays it

    class RefuseRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None  # urllib copies Authorization onto the next host

    req = urllib.request.Request(url, headers={"User-Agent": "model-router-plugin/0.1", **(headers or {})})
    handlers = [RefuseRedirect] if "Authorization" in (headers or {}) else []
    deadline = time.monotonic() + HTTP_TIMEOUT
    # urllib's timeout covers each socket read, so a server that drips bytes never trips it.
    with urllib.request.build_opener(*handlers).open(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310 (fixed https URLs)
        chunks = []
        while time.monotonic() < deadline:
            chunk = resp.read1(65536)
            if not chunk:
                if resp.length:  # read1 ends a short Content-Length body quietly; read() would raise
                    raise http.client.IncompleteRead(b"".join(chunks), resp.length)
                return b"".join(chunks).decode("utf-8", "replace")
            chunks.append(chunk)
        raise TimeoutError(f"{url} took longer than {HTTP_TIMEOUT} s")


def parse_rss(xml_text: str, limit: int = 30) -> list[dict]:
    import xml.etree.ElementTree as ET
    items = []
    for item in ET.fromstring(xml_text).iter("item"):
        content = ""
        for child in item:
            if child.tag.endswith("encoded"):
                content = child.text or ""
        bullets = [b.strip() for b in re.sub(r"<[^>]+>", "", content).splitlines() if b.strip()]
        items.append({"version": (item.findtext("title") or "").strip(),
                      "date": (item.findtext("pubDate") or "").strip(),
                      "link": (item.findtext("link") or "").strip(),
                      "highlights": bullets[:40]})
        if len(items) >= limit:
            break
    return items


def parse_llms_index(text: str) -> dict[str, str]:
    """Map docs page URL to its one-line summary from llms.txt."""
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r"^- \[[^\]]*\]\((https://[^)]+)\):?\s*(.*)$", text, re.M)}


def parse_model_aliases(markdown: str) -> list[str]:
    """Model aliases from the docs' alias table, e.g. haiku, sonnet, opus, fable."""
    return sorted(set(re.findall(r"^\|\s*\*\*`([a-z0-9.\-\[\]]+)`\*\*\s*\|", markdown, re.M)))


def parse_deprecations(markdown: str) -> dict[str, dict]:
    """The "Model status" table: model ID to its state, deprecation date and retirement."""
    out: dict[str, dict] = {}
    for row in re.finditer(r"^\|\s*`?(claude-[a-z0-9.\-]+)`?\s*\|([^|\n]*)\|([^|\n]*)\|([^|\n]*)\|\s*$", markdown, re.M):
        state = row.group(2).strip()
        if state.lower() in ("active", "deprecated", "retired", "legacy"):
            out[row.group(1)] = {"state": state, "deprecated": row.group(3).strip(), "retires": row.group(4).strip()}
    for row in re.finditer(r"^\|[^|\n]*\|\s*`(claude-[a-z0-9.\-]+)`\s*\|\s*`(claude-[a-z0-9.\-]+)`\s*\|", markdown, re.M):
        if row.group(1) in out:
            out[row.group(1)]["replacement"] = row.group(2)
    return out


def api_models(settings: Settings) -> dict[str, dict]:
    """The models this account can call, from the Models API. Only with an Anthropic API key
    and no gateway or cloud provider in between: the key goes nowhere but api.anthropic.com."""
    if not settings.api_key or settings.base_url or settings.cloud:
        return {}
    data = json.loads(http_get(MODELS_API, {"Authorization": f"Bearer {settings.api_key}",
                                            "anthropic-version": "2026-10-21"}))
    out = {}
    for model in data.get("data") or []:
        if isinstance(model, dict) and isinstance(model.get("id"), str):
            out[model["id"]] = {"line": model.get("line"), "lifecycle": model.get("lifecycle"),
                                "retires_at": str(model.get("retires_at") or "")[:10]}
    return out


def merged_lifecycle(cache: dict) -> dict[str, dict]:
    """The deprecations page, with this account's own Models API answer over it."""
    lifecycle = {k: dict(v) for k, v in (cache.get("lifecycle") or {}).items() if isinstance(v, dict)}
    for model_id, info in (cache.get("api_models") or {}).items():
        if isinstance(info, dict) and info.get("lifecycle"):
            entry = lifecycle.setdefault(model_id, {})
            entry["state"] = str(info["lifecycle"]).capitalize()
            if info.get("retires_at"):
                entry["retires"] = info["retires_at"]
            entry.setdefault("retires", "to be announced")
    return lifecycle


_VERSIONED_ID = re.compile(r"^claude-([a-z]+)-(\d+(?:-\d{1,2})?)(?:-\d{8})?$")


def version_of(model_id: object) -> tuple[str, tuple[int, ...]] | None:
    """A model ID's family and version: claude-opus-5-5 is ("opus", (5, 5))."""
    match = _VERSIONED_ID.match(str(model_id or "").lower().split("[")[0])
    if not match:
        return None
    return match.group(1), tuple(int(part) for part in match.group(2).split("-"))


def newest_versions(data: dict | None = None) -> dict[str, str]:
    """The newest active model ID of each family, from the last fetch."""
    lifecycle = merged_lifecycle((load() if data is None else data).get("cache") or {})
    newest: dict[str, tuple[tuple[int, ...], str]] = {}
    for model_id, info in lifecycle.items():
        found = version_of(model_id)
        if found and str((info or {}).get("state", "")).lower() == "active":
            line, version = found
            if line not in newest or version > newest[line][0]:
                newest[line] = (version, model_id)
    return {line: model_id for line, (_, model_id) in newest.items()}


def behind(model_ids: list, data: dict | None = None) -> list[tuple[str, str]]:
    """(running, newest) for each model ID older than its family's newest active version."""
    newest = newest_versions(data)
    out = []
    for model_id in dict.fromkeys(m for m in model_ids if isinstance(m, str)):
        found = version_of(model_id)
        latest = newest.get(found[0]) if found else None
        if found and latest and version_of(latest)[1] > found[1]:
            out.append((model_id.split("[")[0], latest))
    return out


def model_families(aliases: object) -> list[str]:
    aliases = aliases if isinstance(aliases, list) else []
    return sorted({a for a in aliases if isinstance(a, str) and a not in NON_FAMILY_ALIASES and "[" not in a})


def version_key(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-]", str(version)))


def _api_lines(cache: dict) -> list[str]:
    return sorted({i.get("line") for i in (cache.get("api_models") or {}).values()
                   if isinstance(i, dict) and isinstance(i.get("line"), str)})


def fetch(settings: Settings) -> dict:
    """All of it within FETCH_BUDGET or nothing: a worker thread bounds DNS, connecting and
    dripped headers too, and a fetch cut off mid-way never stores a half-empty cache."""
    box: dict = {}

    def run() -> None:
        try:
            box["value"] = _fetch(settings)
        except Exception as exc:  # noqa: BLE001 - re-raised below, in the caller's thread
            box["error"] = exc

    worker = threading.Thread(target=run, daemon=True)  # the hook process exits without it
    worker.start()
    worker.join(FETCH_BUDGET)
    if worker.is_alive():
        raise TimeoutError(f"the upstream fetch took longer than {FETCH_BUDGET} s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _fetch(settings: Settings) -> dict:
    releases = parse_rss(http_get(CHANGELOG_RSS))
    pages = parse_llms_index(http_get(DOCS_INDEX))
    if not releases or not pages:
        # An outage page parses as empty; stored, it would read as every page removed.
        raise ValueError("the changelog or the docs index came back empty")
    try:
        aliases = parse_model_aliases(http_get(MODEL_CONFIG_PAGE))
    except Exception:  # noqa: BLE001 - the alias table is a bonus; never fail the refresh for it
        aliases = []
    try:
        lifecycle = parse_deprecations(http_get(DEPRECATIONS_PAGE))
    except Exception:  # noqa: BLE001 - a bonus too
        lifecycle = {}
    try:
        account = api_models(settings)
    except Exception:  # noqa: BLE001 - and this
        account = {}
    return {"aliases": aliases, "lifecycle": lifecycle, "api_models": account, "fetched_at": int(store.now()),
            "releases": releases,
            "pages": {url: hashlib.sha256(s.encode()).hexdigest()[:16] for url, s in pages.items()}}


def _base_id(model_id: str) -> str:
    return re.sub(r"-\d{8}$", "", model_id.lower().split("[")[0])


def _same_model(model_id: str, seen: str) -> bool:
    return _base_id(model_id) == _base_id(seen)


def diff(reviewed: dict, live: dict, routed: list[str] | None = None, in_use: list[str] | None = None) -> dict:
    """`routed` is the registry's model names; by default the ones this plugin ships.
    `in_use` is the full model IDs routed calls have really run as here."""
    routed = list(routed if routed is not None else (cfg.base_config().get("models") or {}))
    reviewed_version = reviewed.get("claude_code_version") or "0"
    old_pages, new_pages = reviewed.get("pages") or {}, live.get("pages") or {}
    added = sorted(set(new_pages) - set(old_pages))
    removed = sorted(set(old_pages) - set(new_pages))
    changed = sorted(u for u in set(new_pages) & set(old_pages) if new_pages[u] != old_pages[u])

    def watched(url: str) -> bool:
        slug = url.rsplit("/docs/en/", 1)[-1]
        slug = slug[:-3] if slug.endswith(".md") else slug
        return any(slug == p or slug.startswith(p + "/") or slug.startswith(p + "-") for p in WATCHED_PAGES)

    live_families = model_families(live.get("aliases"))
    known = set(routed) | set(model_families(reviewed.get("aliases"))) | set(reviewed.get("api_lines") or [])
    lines = _api_lines(live)
    lifecycle, was = merged_lifecycle(live), reviewed.get("lifecycle") or {}

    def entry(model_id: str) -> dict:
        return {"model": model_id, **lifecycle[model_id]}

    leaving = [m for m, info in lifecycle.items() if info.get("state", "").lower() in ("deprecated", "retired")]
    return {
        "reviewed_version": reviewed_version,
        "new_releases": [r for r in live.get("releases") or []
                         if r.get("version") and version_key(r["version"]) > version_key(reviewed_version)],
        "pages_added": added,
        "pages_removed": removed,
        "pages_changed": changed,
        "watched_pages_touched": sorted(u for u in added + removed + changed if watched(u)),
        "new_model_families": sorted(f for f in set(live_families) | set(lines) if f not in known),
        "removed_model_families": sorted(f for f in routed if live_families and f not in live_families),
        "newly_deprecated": [entry(m) for m in sorted(leaving)
                             if was and (was.get(m) or {}).get("state") != lifecycle[m]["state"]],
        "deprecated_in_use": [entry(m) for m in sorted(leaving)
                              if any(_same_model(m, seen) for seen in in_use or [])],
    }


def snapshot(cache: dict) -> dict:
    latest = cache["releases"][0]["version"] if cache.get("releases") else "0"
    return {"claude_code_version": latest, "pages": cache.get("pages", {}), "aliases": cache.get("aliases", []),
            "lifecycle": merged_lifecycle(cache), "api_lines": _api_lines(cache)}


def _failures(failed: object) -> int:
    """Failed fetches in a row, or 0 for no record or one that doesn't make sense."""
    if not isinstance(failed, dict) or not all(
            cfg._number(failed.get(key)) and math.isfinite(failed[key]) for key in ("at", "count")):
        return 0
    return max(0, int(failed["count"]))


def _retry_wait(failed: object, now: float) -> float:
    """Seconds left before a failed fetch may be tried again: an hour, doubling with each
    failure in a row to a day. A record from the future, after a clock change, holds nothing."""
    count = _failures(failed)
    if not count or float(failed["at"]) > now:
        return 0
    return float(failed["at"]) + min(FAILED_RETRY_SECONDS * 2 ** min(count - 1, 10), CACHE_TTL_SECONDS) - now


def refresh(settings: Settings, force: bool = False) -> dict:
    data = load()
    now = store.now()
    if not force and (now - ((data.get("cache") or {}).get("fetched_at") or 0) < CACHE_TTL_SECONDS
                      or _retry_wait(data.get("failed"), now) > 0):
        return data
    try:
        fresh = fetch(settings)  # slow, so outside the lock
    except Exception:
        def failed(data: dict) -> None:
            data["failed"] = {"at": int(now), "count": _failures(data.get("failed")) + 1}
        store.update_dict(path(), failed)
        raise

    def apply(data: dict) -> None:
        data.pop("failed", None)
        data["cache"] = fresh
        if "reviewed" not in data:
            # First run: treat the present as reviewed so users aren't flooded with history.
            data["reviewed"] = snapshot(fresh)

    return store.update_dict(path(), apply)


def ack() -> str | None:
    if not load().get("cache"):
        return None

    def apply(data: dict) -> None:
        data["reviewed"] = {**snapshot(data.get("cache") or {}), "reviewed_at": int(store.now())}

    return store.update_dict(path(), apply)["reviewed"]["claude_code_version"]


def pending(data: dict | None = None) -> dict | None:
    data = load() if data is None else data
    if not data.get("cache") or not data.get("reviewed"):
        return None
    return diff(data["reviewed"], data["cache"], in_use=health.load().seen_running())


def has_pending(changes: dict | None) -> bool:
    return bool(changes and (changes["new_releases"] or changes["watched_pages_touched"]
                             or changes["new_model_families"] or changes["removed_model_families"]
                             or changes.get("newly_deprecated")))
