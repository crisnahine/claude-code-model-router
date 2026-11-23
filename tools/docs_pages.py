"""The index of the docs pages and the head each one carries, shared by tools/docs_assets.py and bench/analyze.py."""
from __future__ import annotations

import html
import posixpath

# (file, slug, title, question, icon, group). Groups: 1 start here, 2 how it works, 3 evidence, 4 the project.
PAGES = [
    ("docs/FAQ.md", "faq", "Questions", "What is Model Router, and how does it pick?", "faq", 1),
    ("docs/GUIDE.md", "guide", "Guide", "How do I configure it for my setup?", "plug", 1),
    ("docs/README.md", "docs", "Documentation", "Every page, and the question each one answers", "index", 1),
    ("docs/ARCHITECTURE.md", "architecture", "Architecture", "How do two paths share one router?", "architecture", 2),
    ("GLOSSARY.md", "glossary", "Glossary", "What do call, level, pick and redo mean?", "glossary", 2),
    ("docs/research/model-control.md", "research", "Research", "What can a plugin control in Claude Code?",
     "research", 2),
    ("docs/research/quality-first-routing.md", "research-quality", "Quality research",
     "What can the router save without losing quality?", "research", 2),
    ("docs/PERFORMANCE.md", "performance", "Performance", "What does the router cost in time and disk?",
     "performance", 2),
    ("docs/SECURITY.md", "security", "Security", "What reaches the network, and who can steer it?", "security", 2),
    ("docs/BENCHMARKS.md", "benchmarks", "Benchmarks", "What does a solved task cost, with and without it?",
     "benchmarks", 3),
    ("docs/TEST_RESULTS.md", "test-results", "Test results", "What ran last, and what passed?", "tests", 3),
    ("docs/TRACEABILITY.md", "traceability", "Traceability", "Where is each requirement, and is it met?",
     "trace", 3),
    ("docs/RELEASE_READINESS.md", "release-readiness", "Release readiness",
     "Is this release ready, and what is still open?", "flag", 3),
    ("docs/ROADMAP.md", "roadmap", "Roadmap", "What gets built next, and why first?", "roadmap", 4),
    ("CHANGELOG.md", "changelog", "Changelog", "What changed in each release?", "changelog", 4),
    ("CONTRIBUTING.md", "contributing", "Contributing", "How does a change get merged?", "branch", 4),
    ("RELEASING.md", "releasing", "Releasing", "How is a release cut, step by step?", "tag", 4),
    ("docs/assets/BRAND.md", "brand", "Brand", "Which colors, type and motion make a page ours?", "brand", 4),
]

HEAD_START = "<!-- Page head"
HEAD_END = "<!-- End of page head. -->\n\n"


def attr(s: str) -> str:
    return html.escape(str(s), quote=True)


def rel(from_file: str, to_path: str) -> str:
    return posixpath.relpath(to_path, posixpath.dirname(from_file) or ".")


def page_head(path: str, pages: list = PAGES) -> str:
    i = [p[0] for p in pages].index(path)
    _, slug, title, question, _, _ = pages[i]
    link = lambda p: f'<a href="{rel(path, p[0])}">{p[2]}</a>'  # noqa: E731
    nav = [f'<a href="{rel(path, "README.md")}">Model Router</a>']
    if path != "docs/README.md":
        nav.append(f'<a href="{rel(path, "docs/README.md")}">Documentation</a>')
    if i > 0:
        nav.append("Previous: " + link(pages[i - 1]))
    if i + 1 < len(pages):
        nav.append("Next: " + link(pages[i + 1]))
    return (f"{HEAD_START}, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->\n"
            f'<p align="center"><img src="{rel(path, f"docs/assets/banner-{slug}.svg")}" width="880" '
            f'alt="{attr(title)}: {attr(question)}"></p>\n\n'
            f'<p align="center">{" &nbsp;&middot;&nbsp; ".join(nav)}</p>\n'
            f"{HEAD_END}")


def with_head(path: str, current: str, pages: list = PAGES) -> str:
    """`current` under the page's own head, whatever head it had."""
    if current.startswith(HEAD_START):
        current = current.split(HEAD_END, 1)[1]
    return page_head(path, pages) + current
