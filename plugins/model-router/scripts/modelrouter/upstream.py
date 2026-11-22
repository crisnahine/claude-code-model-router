"""Claude Code releases, docs pages and model aliases, compared with what was last reviewed."""
from __future__ import annotations

import hashlib
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config as cfg
from . import store

CHANGELOG_RSS = "https://code.claude.com/docs/en/changelog/rss.xml"
DOCS_INDEX = "https://code.claude.com/docs/llms.txt"
MODEL_CONFIG_PAGE = "https://code.claude.com/docs/en/model-config.md"
CACHE_TTL_SECONDS = 24 * 3600
HTTP_TIMEOUT = 10
# Docs pages that describe the surfaces this plugin is built on.
WATCHED_PAGES = ("plugins", "sub-agents", "hooks", "skills", "model-config", "workflows",
                 "plugin-evals", "mcp", "settings", "agent-teams", "costs", "permission-modes")
# Aliases that select a mode or a context size rather than a model family of their own.
NON_FAMILY_ALIASES = {"default", "best", "opusplan"}


def path() -> Path:
    return store.data_dir() / "upstream.json"


def load() -> dict:
    return store.load_dict(path())


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "model-router-plugin/0.1"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310 (fixed https URLs)
        return resp.read().decode("utf-8", "replace")


def parse_rss(xml_text: str, limit: int = 30) -> list[dict]:
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


def model_families(aliases: object) -> list[str]:
    aliases = aliases if isinstance(aliases, list) else []
    return sorted({a for a in aliases if isinstance(a, str) and a not in NON_FAMILY_ALIASES and "[" not in a})


def version_key(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-]", str(version)))


def fetch() -> dict:
    releases = parse_rss(http_get(CHANGELOG_RSS))
    pages = parse_llms_index(http_get(DOCS_INDEX))
    try:
        aliases = parse_model_aliases(http_get(MODEL_CONFIG_PAGE))
    except Exception:  # noqa: BLE001 - the alias table is a bonus; never fail the refresh for it
        aliases = []
    return {"aliases": aliases, "fetched_at": int(time.time()), "releases": releases,
            "pages": {url: hashlib.sha256(s.encode()).hexdigest()[:16] for url, s in pages.items()}}


def diff(reviewed: dict, live: dict, routed: list[str] | None = None) -> dict:
    """`routed` is the registry's model names; by default the ones this plugin ships."""
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
    known = set(routed) | set(model_families(reviewed.get("aliases")))
    return {
        "reviewed_version": reviewed_version,
        "new_releases": [r for r in live.get("releases") or []
                         if r.get("version") and version_key(r["version"]) > version_key(reviewed_version)],
        "pages_added": added,
        "pages_removed": removed,
        "pages_changed": changed,
        "watched_pages_touched": sorted(u for u in added + removed + changed if watched(u)),
        "new_model_families": sorted(f for f in live_families if f not in known),
        "removed_model_families": sorted(f for f in routed if live_families and f not in live_families),
    }


def snapshot(cache: dict) -> dict:
    latest = cache["releases"][0]["version"] if cache.get("releases") else "0"
    return {"claude_code_version": latest, "pages": cache.get("pages", {}), "aliases": cache.get("aliases", [])}


def refresh(force: bool = False) -> dict:
    cache = load().get("cache") or {}
    if not force and time.time() - (cache.get("fetched_at") or 0) < CACHE_TTL_SECONDS:
        return load()
    fresh = fetch()  # slow, so outside the lock

    def apply(data: dict) -> None:
        data["cache"] = fresh
        if "reviewed" not in data:
            # First run: treat the present as reviewed so users aren't flooded with history.
            data["reviewed"] = snapshot(fresh)

    return store.update_dict(path(), apply)


def ack() -> str | None:
    if not load().get("cache"):
        return None

    def apply(data: dict) -> None:
        data["reviewed"] = {**snapshot(data.get("cache") or {}), "reviewed_at": int(time.time())}

    return store.update_dict(path(), apply)["reviewed"]["claude_code_version"]


def pending(data: dict | None = None) -> dict | None:
    data = load() if data is None else data
    if not data.get("cache") or not data.get("reviewed"):
        return None
    return diff(data["reviewed"], data["cache"])


def has_pending(changes: dict | None) -> bool:
    return bool(changes and (changes["new_releases"] or changes["watched_pages_touched"]
                             or changes["new_model_families"] or changes["removed_model_families"]))
