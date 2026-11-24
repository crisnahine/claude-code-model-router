"""The index of the docs pages and the head each one carries, shared by tools/docs_assets.py and bench/analyze.py."""
from __future__ import annotations

import html
import posixpath

# (file, title, question, group). Groups: 1 start here, 2 how it works, 3 evidence, 4 the project.
PAGES = [
    ("docs/FAQ.md", "Questions", "What is Model Router, and how does it pick?", 1),
    ("docs/GUIDE.md", "Guide", "How do I configure it for my setup?", 1),
    ("docs/README.md", "Documentation", "Every page, and the question each one answers", 1),
    ("docs/ARCHITECTURE.md", "Architecture", "How do two paths share one router?", 2),
    ("GLOSSARY.md", "Glossary", "What do call, level, pick and redo mean?", 2),
    ("docs/research/model-control.md", "Research", "What can a plugin control in Claude Code?", 2),
    ("docs/research/quality-first-routing.md", "Quality research",
     "What can the router save without losing quality?", 2),
    ("docs/research/caller-levels-and-redo.md", "Redo research",
     "Who names a call's level, and what marks a redo?", 2),
    ("docs/PERFORMANCE.md", "Performance", "What does the router cost in time and disk?", 2),
    ("docs/SECURITY.md", "Security", "What reaches the network, and who can steer it?", 2),
    ("docs/CLAUDE_CODE_DOCS.md", "Built on the docs", "Which Claude Code docs does it follow, and where?", 3),
    ("docs/BENCHMARKS.md", "Benchmarks", "What does a solved task cost, with and without it?", 3),
    ("docs/TEST_RESULTS.md", "Test results", "What ran last, and what passed?", 3),
    ("docs/TRACEABILITY.md", "Traceability", "Where is each requirement, and is it met?", 3),
    ("docs/RELEASE_READINESS.md", "Release readiness", "Is this release ready, and what is still open?", 3),
    ("docs/ROADMAP.md", "Roadmap", "What gets built next, and why first?", 4),
    ("CHANGELOG.md", "Changelog", "What changed in each release?", 4),
    ("CONTRIBUTING.md", "Contributing", "How does a change get merged?", 4),
    ("RELEASING.md", "Releasing", "How is a release cut, step by step?", 4),
    ("docs/assets/BRAND.md", "Brand", "Which colors and type make a page ours?", 4),
]

HEAD_START = "<!-- Page head"
HEAD_END = "<!-- End of page head. -->\n\n"


def attr(s: str) -> str:
    return html.escape(str(s), quote=True)


def rel(from_file: str, to_path: str) -> str:
    return posixpath.relpath(to_path, posixpath.dirname(from_file) or ".")


def page_head(path: str, pages: list = PAGES) -> str:
    i = [p[0] for p in pages].index(path)
    link = lambda p: f'<a href="{rel(path, p[0])}">{p[1]}</a>'  # noqa: E731
    nav = [f'<a href="{rel(path, "README.md")}">Model Router</a>']
    if path != "docs/README.md":
        nav.append(f'<a href="{rel(path, "docs/README.md")}">Documentation</a>')
    if i > 0:
        nav.append("Previous: " + link(pages[i - 1]))
    if i + 1 < len(pages):
        nav.append("Next: " + link(pages[i + 1]))
    return (f"{HEAD_START}, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->\n"
            f'<p align="center">{" &nbsp;&middot;&nbsp; ".join(nav)}</p>\n'
            f"{HEAD_END}")


def with_head(path: str, current: str, pages: list = PAGES) -> str:
    """`current` under the page's own head, whatever head it had."""
    if current.startswith(HEAD_START):
        _, end, current = current.partition(HEAD_END)
        if not end:
            raise SystemExit(f"{path}: the page head has no {HEAD_END.strip()!r} marker")
    return page_head(path, pages) + current
