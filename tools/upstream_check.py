"""Compare Claude Code releases, the docs pages Model Router is built on, the model aliases and the
model retirements with compat/baseline.json, the last reviewed state. The daily upstream-watch
workflow runs it; the plugin itself makes no network call.

Run: python3 tools/upstream_check.py --baseline compat/baseline.json [--json-out FILE] [--write]
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "plugins" / "model-router" / "config" / "models.json"
CHANGELOG_PAGE = "https://code.claude.com/docs/en/changelog.md"
DOCS_INDEX = "https://code.claude.com/docs/llms.txt"
MODEL_CONFIG_PAGE = "https://code.claude.com/docs/en/model-config.md"
DEPRECATIONS_PAGE = "https://platform.claude.com/docs/en/about-claude/model-deprecations.md"
HTTP_TIMEOUT = 30  # per request: each socket wait, and the body's download as a whole
# Claude Code docs pages that describe the surfaces this plugin is built on, matched by slug prefix.
WATCHED_PAGES = ("plugins", "sub-agents", "hooks", "skills", "model-config", "workflows",
                 "plugin-evals", "mcp", "settings", "agent-teams", "costs", "permission-modes",
                 "env-vars", "prompt-caching", "fast-mode", "advisor", "headless", "best-practices")
# The pages the registry's prices, strengths and efforts come from.
PLATFORM_PAGES = ("https://platform.claude.com/docs/en/about-claude/pricing.md",
                  "https://platform.claude.com/docs/en/models/overview.md",
                  "https://platform.claude.com/docs/en/build-with-claude/effort.md",
                  "https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence.md")
# Aliases that select a mode or a context size rather than a model family of their own.
NON_FAMILY_ALIASES = {"default", "best", "opusplan"}


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "model-router-upstream-check"})
    deadline = time.monotonic() + HTTP_TIMEOUT
    # urllib's timeout covers each socket read, so a server that drips bytes never trips it.
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310 (fixed https URLs)
        chunks = []
        while time.monotonic() < deadline:
            chunk = resp.read1(65536)
            if not chunk:
                if resp.length:  # read1 ends a short Content-Length body quietly; read() would raise
                    raise http.client.IncompleteRead(b"".join(chunks), resp.length)
                return b"".join(chunks).decode("utf-8", "replace")
            chunks.append(chunk)
        raise TimeoutError(f"{url} took longer than {HTTP_TIMEOUT} s")


def parse_changelog(markdown: str) -> list[dict]:
    """Each release on the changelog page, newest first: a bold version line, a blank line, its date."""
    return [{"version": m.group(1), "date": m.group(2) or ""} for m in re.finditer(
        r"^\*\*(\d+(?:\.\d+)+)\*\*[ \t]*\n(?:[ \t]*\n([A-Z][a-z]+ \d{1,2}, \d{4})[ \t]*$)?", markdown, re.M)]


def parse_llms_index(text: str) -> list[str]:
    """The docs page URLs llms.txt lists."""
    return sorted(set(re.findall(r"^- \[[^\]]*\]\((https://[^)]+)\)", text, re.M)))


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


def watched(url: str) -> bool:
    if url in PLATFORM_PAGES:
        return True
    slug = url.rsplit("/docs/en/", 1)[-1]
    slug = slug[:-3] if slug.endswith(".md") else slug
    return any(slug == p or slug.startswith(p + "/") or slug.startswith(p + "-") for p in WATCHED_PAGES)


def model_families(aliases: object) -> list[str]:
    aliases = aliases if isinstance(aliases, list) else []
    return sorted({a for a in aliases if isinstance(a, str) and a not in NON_FAMILY_ALIASES and "[" not in a})


def version_key(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-]", str(version)))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def fetch() -> dict:
    releases = parse_changelog(http_get(CHANGELOG_PAGE))
    pages = parse_llms_index(http_get(DOCS_INDEX))
    texts = {url: http_get(url) for url in [*(u for u in pages if watched(u)), *PLATFORM_PAGES]}
    aliases = parse_model_aliases(texts.get(MODEL_CONFIG_PAGE) or http_get(MODEL_CONFIG_PAGE))
    lifecycle = parse_deprecations(http_get(DEPRECATIONS_PAGE))
    if not releases or not pages or not aliases or not lifecycle:
        # An outage page or a new page layout parses as empty; written as the baseline, it would read as
        # every page and model removed.
        raise ValueError("the changelog, the docs index, the alias table or the model status table came back empty")
    return {"releases": releases, "pages": pages, "watched": {url: _digest(t) for url, t in texts.items()},
            "aliases": aliases, "lifecycle": lifecycle}


def diff(reviewed: dict, live: dict, routed: list[str] | None = None) -> dict:
    """`routed` is the registry's model names; by default the ones the plugin ships."""
    if routed is None:
        routed = list(json.loads(REGISTRY.read_text(encoding="utf-8"))["models"])
    reviewed_version = reviewed.get("claude_code_version") or "0"
    old_pages, new_pages = set(reviewed.get("pages") or ()), set(live.get("pages") or ())
    old_text, new_text = reviewed.get("watched") or {}, live.get("watched") or {}
    live_families = model_families(live.get("aliases"))
    known = set(routed) | set(model_families(reviewed.get("aliases")))
    lifecycle, was = live.get("lifecycle") or {}, reviewed.get("lifecycle") or {}
    leaving = [m for m, info in lifecycle.items() if info.get("state", "").lower() in ("deprecated", "retired")]
    return {
        "reviewed_version": reviewed_version,
        "new_releases": [r for r in live.get("releases") or []
                         if r.get("version") and version_key(r["version"]) > version_key(reviewed_version)],
        "pages_added": sorted(new_pages - old_pages),
        "pages_removed": sorted(old_pages - new_pages),
        "watched_pages_touched": sorted(u for u in set(old_text) | set(new_text) if old_text.get(u) != new_text.get(u)),
        "new_model_families": sorted(f for f in live_families if f not in known),
        "removed_model_families": sorted(f for f in routed if live_families and f not in live_families),
        "newly_deprecated": [{"model": m, **lifecycle[m]} for m in sorted(leaving)
                             if was and (was.get(m) or {}).get("state") != lifecycle[m]["state"]],
    }


def snapshot(live: dict) -> dict:
    latest = live["releases"][0]["version"] if live.get("releases") else "0"
    return {"claude_code_version": latest, "pages": live.get("pages", []), "watched": live.get("watched", {}),
            "aliases": live.get("aliases", []), "lifecycle": live.get("lifecycle", {})}


def has_pending(changes: dict) -> bool:
    return bool(changes["new_releases"] or changes["watched_pages_touched"] or changes["new_model_families"]
                or changes["removed_model_families"] or changes["newly_deprecated"])


def report(changes: dict) -> str:
    out = [f"Last reviewed Claude Code version: {changes['reviewed_version']}"]
    if changes["new_releases"]:
        out.append(f"\nNew releases since then ({len(changes['new_releases'])}), in {CHANGELOG_PAGE}:")
        out += [f"- {r['version']}" + (f" ({r['date']})" if r.get("date") else "") for r in changes["new_releases"]]
    else:
        out.append("No new releases.")
    for label, key in (("Docs pages added", "pages_added"), ("Docs pages removed", "pages_removed")):
        if changes[key]:
            out.append(f"\n{label} ({len(changes[key])}):")
            out += [f"- {u}" for u in changes[key][:25]]
    if changes["watched_pages_touched"]:
        out.append("\nPages this plugin is built on whose text changed, or that were added or removed:")
        out += [f"- {u}" for u in changes["watched_pages_touched"]]
    if changes["new_model_families"]:
        out.append("\nNew model aliases in the docs, not in the registry yet: " + ", ".join(changes["new_model_families"]))
    if changes["removed_model_families"]:
        out.append("\nRegistry models the docs no longer list: " + ", ".join(changes["removed_model_families"]))
    if changes["newly_deprecated"]:
        out.append("\nModels deprecated or retired since the last review:")
        out += [f"- {m['model']}: {m['state']}, retires {m['retires']}"
                + (f", replacement {m['replacement']}" if m.get("replacement") else "") for m in changes["newly_deprecated"]]
    if not has_pending(changes):
        out.append("\nNothing to review.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="upstream_check", description=__doc__.split("\n\n")[0])
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--write", action="store_true", help="overwrite the baseline with live data")
    parser.add_argument("--json-out", type=Path, help="also write the changes, and whether any is pending, as JSON")
    args = parser.parse_args(argv)
    live = fetch()
    if args.write:
        baseline = snapshot(live)
        args.baseline.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Baseline written for Claude Code {baseline['claude_code_version']} "
              f"({len(live['pages'])} docs pages, {len(live['watched'])} watched).")
        return 0
    changes = diff(json.loads(args.baseline.read_text(encoding="utf-8")), live)
    if args.json_out:
        args.json_out.write_text(json.dumps({**changes, "pending": has_pending(changes)}, indent=2), encoding="utf-8")
    print(report(changes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
