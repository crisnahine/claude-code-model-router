"""The index of the docs pages and the head each one carries, shared by tools/docs_assets.py and bench/analyze.py."""
from __future__ import annotations

import html
import posixpath

# (file, title, question, group). Groups: 1 start here, 2 how it works, 3 evidence, 4 the project.
PAGES = [
    ("docs/README.md", "Documentation", "Every page, and the question each one answers", 1),
    ("docs/GUIDE.md", "Guide", "How do I configure it for my setup?", 1),
    ("docs/FAQ.md", "Questions", "What is Model Router, and who picks the model?", 1),
    ("docs/ARCHITECTURE.md", "Architecture", "How is it built, and how does it decide a call?", 2),
    ("docs/GLOSSARY.md", "Glossary", "What do call, level, pick and redo mean?", 2),
    ("docs/SECURITY.md", "Security", "What does it store, and who can steer it?", 2),
    ("docs/PERFORMANCE.md", "Performance", "What does it cost in time, disk and context?", 2),
    ("docs/CLAUDE_CODE_DOCS.md", "Built on the docs", "Which Claude Code docs does it follow, and where?", 3),
    ("docs/BENCHMARKS.md", "Benchmarks", "How is it measured against Claude Code without it?", 3),
    ("docs/TESTING.md", "Testing", "Which checks gate a change, and how do I run them?", 3),
    ("docs/ROADMAP.md", "Roadmap", "What is not done yet?", 4),
    ("CHANGELOG.md", "Changelog", "What is in each release?", 4),
    ("CONTRIBUTING.md", "Contributing", "How does a change get merged?", 4),
    ("docs/RELEASING.md", "Releasing", "How is a release cut, step by step?", 4),
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
