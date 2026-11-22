#!/usr/bin/env python3
"""Writes the docs' SVG assets, the docs' page heads, the site's copies and CITATION.cff.

Run: python3 tools/docs_assets.py            write everything
     python3 tools/docs_assets.py --check    fail if anything on disk differs or breaks a rule
"""
from __future__ import annotations

import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"
SITE = ROOT / "site"
REPO = "https://github.com/crisnahine/claude-code-model-router"

SANS = '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif'
MONO = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace'

# The five rung colors run from the cheapest level (lookup) to the strongest (frontier).
LIGHT = {
    "bg": "#F4F7FB", "card": "#FFFFFF", "line": "#D2DBE7", "ink": "#0C1524", "muted": "#4A5871",
    "faint": "#E7EDF5", "route": "#A65200", "routesoft": "#FFF1DE", "tile": "#0C1524", "tileline": "#0C1524",
    "tag": "#FFFFFF", "rungs": ["#0E8C6F", "#0A78B8", "#3B57D6", "#6D3FD1", "#A72C8A"],
}
DARK = {
    "bg": "#0A101B", "card": "#0F1828", "line": "#26344B", "ink": "#E7EDF6", "muted": "#9BA9BD",
    "faint": "#1A263A", "route": "#FFB23E", "routesoft": "#33260F", "tile": "#13203A", "tileline": "#2C3E60",
    "tag": "#0C1524", "rungs": ["#38D3A8", "#45B4F2", "#8196FF", "#AA8BFF", "#F27BCD"],
}
TILE_INK = "#0C1524"  # the mark's tile is ink in both themes, so its rails and rungs keep one color

LEVELS = ["lookup", "standard", "complex", "hardest", "frontier"]


def esc(s: str) -> str:
    return html.escape(str(s), quote=False)


def attr(s: str) -> str:
    return html.escape(str(s), quote=True)


def width_of(text: str, size: float, mono: bool = False, bold: bool = False) -> float:
    return len(text) * size * (0.61 if mono else (0.58 if bold else 0.54))


def wrap(text: str, size: float, width: float, mono: bool = False) -> list:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and width_of(trial, size, mono) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + ([line] if line else [])


def palette_css(p: dict) -> str:
    rules = [
        f".bg{{fill:{p['bg']}}}", f".card{{fill:{p['card']};stroke:{p['line']}}}", f".ink{{fill:{p['ink']}}}",
        f".mut{{fill:{p['muted']}}}", f".faint{{fill:{p['faint']}}}", f".ln{{stroke:{p['line']}}}",
        f".lnm{{stroke:{p['muted']}}}", f".inks{{stroke:{p['ink']}}}", f".rt{{fill:{p['route']}}}",
        f".rts{{stroke:{p['route']}}}", f".rtsoft{{fill:{p['routesoft']}}}", f".halo{{stroke:{p['card']}}}",
        f".haloc{{stroke:{p['bg']}}}", f".tile{{fill:{p['tile']};stroke:{p['tileline']}}}",
        f".stop0{{stop-color:{p['card']}}}", f".stop1{{stop-color:{p['bg']}}}", f".tag{{fill:{p['tag']}}}",
    ]
    for i, c in enumerate(p["rungs"], 1):
        rules.append(f".r{i}{{fill:{c}}}.r{i}s{{stroke:{c}}}")
    return "".join(rules)


def style(extra: str = "", theme: str = "auto") -> str:
    base = (f"text{{font-family:{SANS}}}.mono{{font-family:{MONO}}}.b{{font-weight:600}}"
            ".rail{stroke:#C9D4E4}.tilehalo{stroke:" + TILE_INK + "}")
    if theme == "auto":
        colors = palette_css(LIGHT) + "@media (prefers-color-scheme: dark){" + palette_css(DARK) + "}"
    else:
        colors = palette_css(DARK if theme == "dark" else LIGHT)
    motion = "@media (prefers-reduced-motion: reduce){*{animation:none!important}}" if "@keyframes" in extra else ""
    return f"<style>{base}{colors}{extra}{motion}</style>"


def svg(w: int, h: int, title: str, label: str, body: str, extra_css: str = "", theme: str = "auto",
        width=None) -> str:
    size = f'width="{width or w}" height="{round(h * (width or w) / w)}"'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" {size} role="img" '
            f'aria-label="{attr(label)}">\n<title>{esc(title)}</title>\n'
            f"{style(extra_css, theme)}\n{body}\n</svg>\n")


def text(x, y, s, cls="ink", size=13, anchor=None, extra=""):
    a = f' text-anchor="{anchor}"' if anchor else ""
    return f'<text x="{x:g}" y="{y:g}" class="{cls}" font-size="{size}"{a}{extra}>{esc(s)}</text>'


def lines(x, y, rows, cls="mut", size=13, step=None):
    step = step or round(size * 1.5)
    return "".join(text(x, y + i * step, row, cls, size) for i, row in enumerate(rows))


def card(x, y, w, h, rx=12, cls="card", sw=1):
    return f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{rx}" class="{cls}" stroke-width="{sw}"/>'


def pill(x, y, label, cls="faint", tcls="ink", size=12, mono=True, h=24, pad=10):
    w = width_of(label, size, mono) + 2 * pad
    t = "mono " + tcls if mono else tcls
    return (f'<rect x="{x:g}" y="{y:g}" width="{w:.0f}" height="{h}" rx="{h / 2:g}" class="{cls}"/>'
            + text(x + w / 2, y + h / 2 + size * 0.36, label, t, size, "middle")), w


def frame(w, h, rx=16):
    return f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="{rx}" class="card"/>'


def keyframes(name: str, stops: list) -> str:
    return f"@keyframes {name}{{" + "".join(f"{p}%{{{v}}}" for p, v in stops) + "}"


# ---------------------------------------------------------------- brand

def mark_body(x=0, y=0, s=1.0, halo="tilehalo"):
    rungs = "".join(f'<path d="M21 {14 + 9 * k}H43" class="r{5 - k}s" stroke-width="3.4" stroke-linecap="round"/>'
                    for k in range(5))
    route = "M7 41H27A4.5 4.5 0 0 0 31.5 36.5A4.5 4.5 0 0 1 36 32H51"
    return (f'<g transform="translate({x:g} {y:g}) scale({s:g})">'
            f'<rect x="1" y="1" width="62" height="62" rx="15" class="tile" stroke-width="1.5"/>'
            f'<path d="M21 9V55M43 9V55" class="rail" stroke-width="4.4" stroke-linecap="round" fill="none"/>'
            f"{rungs}"
            f'<path d="{route}" class="{halo}" stroke-width="10.5" fill="none" stroke-linecap="round"/>'
            f'<path d="{route}" class="rts" stroke-width="5.4" fill="none" stroke-linecap="round" '
            f'stroke-linejoin="round"/>'
            f'<circle cx="54" cy="32" r="4.9" class="rt"/></g>')


def glyphs(word: str):
    """Monoline lowercase letters on a 36-unit x-height: (ink path, accent path, width)."""
    top, base, asc, mid = 22, 58, 2, 40
    x, ink, accent = 0.0, [], []
    for ch in word:
        if ch == " ":
            x += 24
            continue
        if ch == "m":
            ink.append(f"M{x:g} {top}V{base}M{x:g} {top + 11}A11 11 0 0 1 {x + 22:g} {top + 11}V{base}"
                       f"M{x + 22:g} {top + 11}A11 11 0 0 1 {x + 44:g} {top + 11}V{base}")
            w = 44
        elif ch in "od":
            ink.append(f"M{x:g} {mid}A18 18 0 1 0 {x + 36:g} {mid}A18 18 0 1 0 {x:g} {mid}")
            if ch == "d":
                ink.append(f"M{x + 36:g} {asc}V{base}")
            w = 36
        elif ch == "e":
            ink.append(f"M{x:g} {mid}H{x + 36:g}A18 18 0 1 0 {x + 30.73:g} {mid + 12.73}")
            w = 36
        elif ch == "l":
            ink.append(f"M{x:g} {asc}V{base}")
            w = 0
        elif ch == "r":
            ink.append(f"M{x:g} {top}V{base}M{x:g} {top + 16}A16 16 0 0 1 {x + 16:g} {top}H{x + 19:g}")
            w = 19
        elif ch == "u":
            ink.append(f"M{x:g} {top}V{mid}A18 18 0 0 0 {x + 36:g} {mid}M{x + 36:g} {top}V{base}")
            w = 36
        elif ch == "t":
            ink.append(f"M{x + 7:g} {asc + 8}V{base - 9}A9 9 0 0 0 {x + 16:g} {base}H{x + 20:g}")
            accent.append(f"M{x - 1:g} {top}H{x + 19:g}")
            w = 20
        else:
            raise ValueError(ch)
        x += w + 15
    return "".join(ink), "".join(accent), x - 15 + 3.5


WORD = "model router"
WORD_SCALE = 0.84


def wordmark_body(x=0, y=0, s=1.0, ink_cls="inks"):
    ink, accent, _ = glyphs(WORD)
    common = 'fill="none" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"'
    return (f'<g transform="translate({x:g} {y:g}) scale({s:g})">{mark_body()}'
            f'<g transform="translate(86 6) scale({WORD_SCALE})">'
            f'<path d="{ink}" class="{ink_cls}" {common}/><path d="{accent}" class="rts" {common}/></g></g>')


def wordmark_width():
    return 86 + glyphs(WORD)[2] * WORD_SCALE + 4


def brand_assets() -> dict:
    out = {}
    label = "Model Router mark: a ladder of five colored rungs, with an amber route that climbs one rung."
    out["logo-mark.svg"] = svg(64, 64, "Model Router", label, mark_body())
    w = round(wordmark_width())
    wl = "Model Router logo: the ladder mark and the words model router."
    out["logo.svg"] = svg(w, 64, "Model Router", wl, wordmark_body())
    out["logo-light.svg"] = svg(w, 64, "Model Router", wl, wordmark_body(), theme="light")
    out["logo-dark.svg"] = svg(w, 64, "Model Router", wl, wordmark_body(), theme="dark")
    return out


# ---------------------------------------------------------------- icons

ICONS = {
    "faq": '<path d="M5 4.5h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-7l-4.5 3.5V16.5H5a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z"/>'
           '<path d="M9.6 8.9a2.4 2.4 0 1 1 3.4 2.2c-.6.3-1 .8-1 1.4v.3"/><path d="M12 14.6v.1"/>',
    "index": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.6"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.6"/>'
             '<rect x="3.5" y="13.5" width="7" height="7" rx="1.6"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.6"/>',
    "architecture": '<rect x="3.5" y="3.5" width="7" height="6" rx="1.4"/><rect x="13.5" y="3.5" width="7" height="6" rx="1.4"/>'
                    '<rect x="7.5" y="15" width="9" height="5.5" rx="1.4"/><path d="M7 9.5v2.5h10V9.5M12 12v3"/>',
    "glossary": '<path d="M5 18.5V6a2.5 2.5 0 0 1 2.5-2.5H19v13H7.5A2.5 2.5 0 0 0 5 19a2.5 2.5 0 0 0 2.5 2H19"/>'
                '<path d="M9 8h6M9 11.5h4"/>',
    "research": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.4 15.4L20.5 20.5M8 10.5h5M10.5 8v5"/>',
    "performance": '<circle cx="12" cy="13.5" r="7"/><path d="M12 13.5L15 10.5M10 2.5h4M12 2.5v4M18.5 6l1.5-1.5"/>',
    "security": '<path d="M12 3l7 2.8v5.4c0 4.4-2.9 8.1-7 9.8-4.1-1.7-7-5.4-7-9.8V5.8z"/><path d="M8.8 12l2.2 2.2 4.2-4.4"/>',
    "benchmarks": '<path d="M3.5 20.5h17"/><path d="M6.5 20.5v-6M11 20.5V9.5M15.5 20.5v-4M20 20.5V5"/>',
    "tests": '<rect x="4.5" y="4.5" width="15" height="16.5" rx="2.2"/><path d="M9 3h6v3H9z"/><path d="M8.5 13l2.4 2.4 4.6-4.8"/>',
    "trace": '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="12" r="2.5"/>'
             '<path d="M8.5 6h2a3 3 0 0 1 3 3v0a3 3 0 0 0 2 2.8M8.5 18h2a3 3 0 0 0 3-3v0a3 3 0 0 1 2-2.8"/>',
    "flag": '<path d="M6 21V3.5"/><path d="M6 4.5h11.5l-2.5 4 2.5 4H6"/>',
    "roadmap": '<circle cx="5.5" cy="18.5" r="2"/><circle cx="18.5" cy="5.5" r="2"/>'
               '<path d="M7.5 18.5H15a3.25 3.25 0 0 0 0-6.5H9a3.25 3.25 0 0 1 0-6.5h7.5"/>',
    "changelog": '<path d="M4 6h10M4 11h7M4 16h6"/><circle cx="16.5" cy="15.5" r="4.5"/><path d="M16.5 13.3v2.4l1.6 1"/>',
    "branch": '<circle cx="6.5" cy="5.5" r="2.2"/><circle cx="6.5" cy="18.5" r="2.2"/><circle cx="17.5" cy="7.5" r="2.2"/>'
              '<path d="M6.5 7.7v8.6M17.5 9.7c0 4.3-5.5 3.6-9.5 7.2"/>',
    "plug": '<path d="M9 3.5v4.5M15 3.5v4.5"/><path d="M6 8h12v3.5a6 6 0 0 1-12 0z"/><path d="M12 17.5v3"/>',
    "brand": '<circle cx="8" cy="9" r="4.5"/><circle cx="16" cy="9" r="4.5"/><circle cx="12" cy="15.5" r="4.5"/>',
    "tag": '<path d="M3.5 12.2V4.5a1 1 0 0 1 1-1h7.7l8.6 8.6a1.4 1.4 0 0 1 0 2l-6.7 6.7a1.4 1.4 0 0 1-2 0z"/>'
           '<circle cx="8.2" cy="8.2" r="1.6"/>',
}


def icon_body(name, x=0, y=0, s=1.0, cls="inks", sw=1.8):
    return (f'<g transform="translate({x:g} {y:g}) scale({s:g})" fill="none" class="{cls}" stroke-width="{sw}" '
            f'stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</g>')


# ---------------------------------------------------------------- pages and banners

# (file, slug, title, question, icon, group). Groups: 1 start here, 2 how it works, 3 evidence, 4 the project.
PAGES = [
    ("docs/FAQ.md", "faq", "Questions", "What is Model Router, and how does it pick?", "faq", 1),
    ("docs/README.md", "docs", "Documentation", "Every page, and the question each one answers", "index", 1),
    ("docs/ARCHITECTURE.md", "architecture", "Architecture", "How do two paths share one router?", "architecture", 2),
    ("GLOSSARY.md", "glossary", "Glossary", "What do call, level, pick and redo mean?", "glossary", 2),
    ("docs/research/model-control.md", "research", "Research", "What can a plugin control in Claude Code?",
     "research", 2),
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
# Cards on the index for pages that carry no page head of their own.
EXTRA_CARDS = [("README.md", "readme", "Model Router", "What does it do, and how do I install it?", "plug", 1)]
GROUPS = {1: ("Start here", "What it is, and the questions people ask first."),
          2: ("How it works", "The design, its words and sources, and what it costs to run."),
          3: ("Evidence", "What was measured and tested, and what is still open."),
          4: ("The project", "What comes next, what changed, and how to take part.")}
CARDS_START, CARDS_END = "<!-- Cards, written by tools/docs_assets.py from its PAGES table. -->", "<!-- End of cards. -->"


def cards() -> str:
    out = [CARDS_START, "<table>"]
    for group, (name, blurb) in GROUPS.items():
        pages = [p for p in EXTRA_CARDS + PAGES if p[5] == group and p[0] != "docs/README.md"]
        out.append(f'<tr><th colspan="2" align="left">{name}<br><sub>{blurb}</sub></th></tr>')
        for k in range(0, len(pages), 2):
            out.append("<tr>")
            for path, slug, title, question, _, _ in pages[k:k + 2]:
                out.append(f'<td width="50%" valign="top"><img src="assets/icon-{slug}.svg" width="20" height="20" '
                           f'alt=""> <a href="{rel("docs/README.md", path)}"><b>{title}</b></a><br>{question}</td>')
            if len(pages[k:k + 2]) == 1:
                out.append('<td width="50%"></td>')
            out.append("</tr>")
    out += ["</table>", CARDS_END]
    return "\n".join(out)


def with_cards(current: str) -> str:
    if CARDS_START in current:
        before, rest = current.split(CARDS_START, 1)
        return before + cards() + rest.split(CARDS_END, 1)[1]
    return current
# Written by bench/analyze.py instead, so this script only checks that page's head.
GENERATED_ELSEWHERE = {"docs/BENCHMARKS.md"}


def banner(slug, title, question, icon, group) -> str:
    w, h = 880, 168
    lo = group  # the route climbs from this rung to the next one up
    y_of = lambda rung: 140 - (rung - 1) * 24  # noqa: E731
    yf, yt, cx, r = y_of(lo), y_of(lo + 1), 726, 6
    route = (f"M626 {yf}H{cx - r}A{r} {r} 0 0 0 {cx} {yf - r}V{yt + r}A{r} {r} 0 0 1 {cx + r} {yt}H806")
    rungs = "".join(
        f'<path d="M696 {y_of(i)}H{756}" class="r{i}s" stroke-width="6" stroke-linecap="round" '
        f'opacity="{1 if i in (lo, lo + 1) else 0.45}"/>' for i in range(1, 6))
    css = (".draw{stroke-dasharray:1;animation:draw 1.6s ease-in-out 0.2s both}"
           + keyframes("draw", [(0, "stroke-dashoffset:1"), (100, "stroke-dashoffset:0")])
           + ".dot{animation:dot 0.4s ease-out 1.7s both}"
           + keyframes("dot", [(0, "opacity:0"), (100, "opacity:1")]))
    body = (
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="0"><stop offset="0.45" class="stop0"/>'
        '<stop offset="1" class="stop1"/></linearGradient></defs>'
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="18" fill="url(#g)" class="ln" stroke-width="1"/>'
        + card(40, 52, 64, 64, rx=16)
        + icon_body(icon, 54, 66, 1.5, sw=1.7)
        + text(128, 66, "MODEL ROUTER DOCS", "mono rt b", 12, extra=' letter-spacing="1.6"')
        + text(127, 103, title, "ink b", 32)
        + text(128, 132, question, "mut", 16)
        + '<path d="M696 28V152M756 28V152" class="lnm" stroke-width="5" stroke-linecap="round" opacity="0.35"/>'
        + rungs
        + f'<path d="{route}" class="halo" stroke-width="13" fill="none" stroke-linecap="round"/>'
        + f'<path d="{route}" class="rts draw" pathLength="1" stroke-width="6" fill="none" stroke-linecap="round" '
          'stroke-linejoin="round"/>'
        + f'<circle cx="814" cy="{yt}" r="7" class="rt dot"/>'
    )
    label = f"{title}: {question}"
    return svg(w, h, f"{title}: Model Router documentation", label, body, css)


def banner_assets() -> dict:
    out = {}
    for _, slug, title, question, icon, group in PAGES:
        out[f"banner-{slug}.svg"] = banner(slug, title, question, icon, group)
    for _, slug, title, question, icon, group in PAGES + EXTRA_CARDS:
        out[f"icon-{slug}.svg"] = svg(24, 24, title, f"{title} icon", icon_body(icon))
    return out


def rel(from_file: str, to_path: str) -> str:
    import os
    return os.path.relpath(ROOT / to_path, (ROOT / from_file).parent).replace(os.sep, "/")


def page_head(path: str) -> str:
    i = [p[0] for p in PAGES].index(path)
    _, slug, title, question, _, _ = PAGES[i]
    link = lambda p: f'<a href="{rel(path, p[0])}">{p[2]}</a>'  # noqa: E731
    nav = [f'<a href="{rel(path, "README.md")}">Model Router</a>']
    if path != "docs/README.md":
        nav.append(f'<a href="{rel(path, "docs/README.md")}">Documentation</a>')
    if i > 0:
        nav.append("Previous: " + link(PAGES[i - 1]))
    if i + 1 < len(PAGES):
        nav.append("Next: " + link(PAGES[i + 1]))
    return ("<!-- Page head, written by tools/docs_assets.py: edit its PAGES table, then run it. -->\n"
            f'<p align="center"><img src="{rel(path, f"docs/assets/banner-{slug}.svg")}" width="880" '
            f'alt="{attr(title)}: {attr(question)}"></p>\n\n'
            f'<p align="center">{" &nbsp;&middot;&nbsp; ".join(nav)}</p>\n'
            "<!-- End of page head. -->\n\n")


HEAD_END = "<!-- End of page head. -->\n\n"


def with_head(path: str, current: str) -> str:
    if current.startswith("<!-- Page head"):
        current = current.split(HEAD_END, 1)[1]
    return page_head(path) + current


# ---------------------------------------------------------------- badges

BADGES = [
    ("version", "version", None),
    ("claude-code", "claude code", "2.1.292 or later"),
    ("python", "python", "3.9 or later"),
    ("dependencies", "dependencies", "none"),
    ("license", "license", "MIT"),
]


def badge(label: str, value: str) -> str:
    size, h = 12, 28
    lw = 34 + width_of(label, size, True)
    vw = 22 + width_of(value, size, True)
    w = round(lw + vw)
    rungs = "".join(f'<path d="M11 {9 + 5 * k}H19" class="r{3 - k}s" stroke-width="2.4" stroke-linecap="round"/>'
                    for k in range(3))
    body = (f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="{h / 2 - 0.5}" class="card" stroke-width="1"/>'
            f'<path d="M{lw:.1f} 1V{h - 1}" class="ln" stroke-width="1"/>'
            f'<rect x="{lw + 0.5:.1f}" y="1" width="{vw - 1.5:.1f}" height="{h - 2}" rx="{h / 2 - 1}" class="faint"/>'
            f'<rect x="{lw + 0.5:.1f}" y="1" width="14" height="{h - 2}" class="faint"/>'
            + rungs
            + text(27, 18.5, label, "mono mut", size)
            + text(lw + vw / 2, 18.5, value, "mono ink b", size, "middle"))
    return svg(w, h, f"{label}: {value}", f"{label}: {value}", body)


def badge_assets(version: str) -> dict:
    return {f"badge-{slug}.svg": badge(label, value or version) for slug, label, value in BADGES}


# ---------------------------------------------------------------- the ladder panel (hero, social preview)

LADDER = [  # level, model, effort, needs, subtask
    ("lookup", "Haiku", "low", 1, "Find, list, summarize, run a command"),
    ("standard", "Sonnet", "medium", 2, "Implement to a clear spec, write tests"),
    ("complex", "Sonnet", "high", 2, "Design, review, risky or cross-module change"),
    ("hardest", "Opus", "high", 3, "Ambiguous, multi-system and risky at once"),
    ("frontier", "Fable", "high", 4, "No known approach: research"),
]


def check_ladder_against_registry():
    config = json.loads((ROOT / "plugins/model-router/config/models.json").read_text(encoding="utf-8"))
    by_strength = {m["strength"]: name for name, m in config["models"].items()}
    shipped = [(lv, by_strength[spec["needs"]], spec["effort"], spec["needs"]) for lv, spec in config["levels"].items()]
    mine = [(lv, model.lower(), effort, needs) for lv, model, effort, needs, _ in LADDER]
    if shipped != mine:
        raise SystemExit(f"LADDER differs from config/models.json: {shipped}")


BRIEF = '"Implement parse_duration exactly as its docstring specifies"'


def ladder_panel(x, y, w, s=1.0, animate=True, prefix="h", brief_lines=(BRIEF,), model_at=0.47,
                 tag_gap=76, effort_right=104):
    """Five rungs, a call routed at standard that comes back and climbs to complex."""
    row_h = 60 * s
    chip_h = (16 + 20 * len(brief_lines)) * s
    top = y + 52 * s + chip_h + 44 * s
    ys = {lv: top + (4 - i) * row_h for i, (lv, *_rest) in enumerate(LADDER)}
    rail_l, rail_r = x + 34 * s, x + 74 * s
    col_level, col_model, col_effort = x + 96 * s, x + w * model_at, x + w - effort_right * s
    panel_h = top - y + 4 * row_h + 42 * s
    out = [card(x, y, w, panel_h, rx=round(18 * s), cls="card")]
    out.append(text(x + 24 * s, y + 38 * s, "One agent call", "ink b", round(15 * s)))
    out.append(f'<g class="{prefix}brief">' + card(x + 24 * s, y + 52 * s, w - 48 * s, chip_h, rx=round(10 * s), cls="faint", sw=0)
               + lines(x + 38 * s, y + 52 * s + 28 * s, brief_lines, "mono ink", round(12.5 * s), round(20 * s)) + "</g>")
    band_y = ys["complex"] - 22 * s
    out.append(f'<rect x="{x + 12 * s:g}" y="{band_y:g}" width="{w - 24 * s:g}" height="{44 * s:g}" rx="{10 * s:g}" '
               f'class="rtsoft {prefix}band"/>')
    out.append(f'<path d="M{rail_l:g} {top - 22 * s:g}V{ys["lookup"] + 24 * s:g}M{rail_r:g} {top - 22 * s:g}'
               f'V{ys["lookup"] + 24 * s:g}" class="lnm" stroke-width="{4.5 * s:g}" stroke-linecap="round" opacity="0.4"/>')
    for i, (lv, model, effort, _needs, _sub) in enumerate(LADDER, 1):
        yy = ys[lv]
        out.append(f'<path d="M{rail_l:g} {yy:g}H{rail_r:g}" class="r{i}s" stroke-width="{6 * s:g}" stroke-linecap="round"/>')
        out.append(text(col_level, yy + 5 * s, lv, "ink b", round(15 * s)))
        out.append(f'<circle cx="{col_model - 12 * s:g}" cy="{yy:g}" r="{5 * s:g}" class="r{i}"/>')
        out.append(text(col_model, yy + 5 * s, model, "ink", round(15 * s)))
        p, _ = pill(col_effort, yy - 12 * s, effort, size=round(12.5 * s), h=round(24 * s), pad=10 * s)
        out.append(p)
    # the redo tag on the standard row and the solved tag on the complex row
    sy, cy = ys["standard"], ys["complex"]
    tag_x = col_model + tag_gap * s
    redo, _ = pill(tag_x, sy - 12 * s, "redo", cls="rt", tcls="tag", size=round(12 * s), h=round(24 * s), pad=10 * s)
    out.append(f'<g class="{prefix}redo">{redo}</g>')
    solved, _ = pill(tag_x, cy - 12 * s, "solved", cls="r1", tcls="tag", size=round(12 * s), h=round(24 * s), pad=10 * s)
    out.append(f'<g class="{prefix}solved">{solved}</g>')
    # the call itself
    tx = (rail_l + rail_r) / 2
    out.append(f'<g class="{prefix}token"><circle cx="{tx:g}" cy="{cy:g}" r="{13 * s:g}" class="halo" fill="none" '
               f'stroke-width="{5 * s:g}"/><circle cx="{tx:g}" cy="{cy:g}" r="{11 * s:g}" class="rt"/></g>')
    css = ""
    if animate:
        up = lambda d: f"transform:translate(0px,{d * row_h:g}px)"  # noqa: E731
        d_top = (y + 52 * s + chip_h / 2) - cy
        css += (f".{prefix}token{{animation:{prefix}t 10s ease-in-out infinite}}"
                + keyframes(f"{prefix}t", [(0, f"transform:translate(0px,{d_top:g}px);opacity:0"),
                                           (6, f"transform:translate(0px,{d_top:g}px);opacity:1"),
                                           (18, up(1)), (44, up(1)), (54, up(0)), (92, up(0) + ";opacity:1"),
                                           (100, up(0) + ";opacity:0")])
                + f".{prefix}band{{animation:{prefix}b 10s ease-in-out infinite}}"
                + keyframes(f"{prefix}b", [(0, up(1) + ";opacity:0"), (14, up(1) + ";opacity:0"),
                                           (20, up(1) + ";opacity:1"), (44, up(1)), (54, up(0)),
                                           (92, up(0) + ";opacity:1"), (100, up(0) + ";opacity:0")])
                + f".{prefix}brief{{animation:{prefix}f 10s ease-in-out infinite}}"
                + keyframes(f"{prefix}f", [(0, "opacity:0"), (5, "opacity:1"), (92, "opacity:1"), (100, "opacity:0")])
                + f".{prefix}redo{{animation:{prefix}r 10s ease-in-out infinite}}"
                + keyframes(f"{prefix}r", [(0, "opacity:0"), (30, "opacity:0"), (34, "opacity:1"),
                                           (92, "opacity:1"), (100, "opacity:0")])
                + f".{prefix}solved{{animation:{prefix}s 10s ease-in-out infinite}}"
                + keyframes(f"{prefix}s", [(0, "opacity:0"), (58, "opacity:0"), (62, "opacity:1"),
                                           (92, "opacity:1"), (100, "opacity:0")]))
    return "".join(out), css, panel_h


HERO_TITLE = "Model Router: every Claude Code agent on the cheapest model that should do the job"
HERO_LABEL = ("Model Router, a Claude Code plugin. An agent call to implement a function is profiled as standard and "
              "runs on Sonnet at medium effort. Its result comes back for a redo, so the same call climbs one level "
              "to complex, Sonnet at high effort, and is solved. The ladder runs lookup on Haiku at low, standard on "
              "Sonnet at medium, complex on Sonnet at high, hardest on Opus at high and frontier on Fable at high.")


def hero(phone=False) -> str:
    if phone:
        w = 600
        panel, css, ph = ladder_panel(24, 470, w - 48, 1.3, prefix="p",
                                      brief_lines=('"Implement parse_duration', 'exactly as its docstring', 'specifies"'),
                                      model_at=0.465, tag_gap=62, effort_right=80)
        h = round(470 + ph + 40)
        body = [frame(w, h, 24), wordmark_body(32, 44, 0.86),
                text(34, 156, "CLAUDE CODE PLUGIN", "mono rt b", 15, extra=' letter-spacing="2"')]
        body += [text(32, 206 + i * 44, s, "ink b", 35) for i, s in
                 enumerate(["Every agent on the cheapest", "model and effort that should", "do the job."])]
        body.append(lines(32, 372, ["When the work comes back for a redo,", "the same call runs one level higher."],
                          "mut", 22, 32))
        body.append(panel)
        return svg(w, h, HERO_TITLE, HERO_LABEL, "".join(body), css)
    w, h = 1200, 500
    panel, css, _ = ladder_panel(640, 40, 520, 1.0)
    body = [frame(w, h, 24), wordmark_body(56, 70, 0.92),
            text(58, 186, "CLAUDE CODE PLUGIN", "mono rt b", 14, extra=' letter-spacing="2"')]
    body += [text(56, 236 + i * 46, s, "ink b", 37) for i, s in
             enumerate(["Every agent on the cheapest", "model and effort that", "should do the job."])]
    body.append(lines(57, 384, ["When the work comes back for a redo, the", "same call runs one level higher."], "mut", 19, 28))
    chips_x = 57
    for label in ("subagents", "agent-team teammates", "workflow stages"):
        p, pw = pill(chips_x, 432, label, size=13, h=28, pad=12)
        body.append(p)
        chips_x += pw + 10
    body.append(panel)
    return svg(w, h, HERO_TITLE, HERO_LABEL, "".join(body), css)


# ---------------------------------------------------------------- diagrams

def diagram_ladder() -> str:
    w, h = 920, 470
    out = [frame(w, h), text(32, 42, "The ladder", "ink b", 17),
           text(32, 66, "Balanced priority: the cheapest usable model strong enough for each level.", "mut", 13.5)]
    for x, label in ((180, "Level"), (300, "Model"), (392, "Effort"), (490, "Model strength"), (612, "Typical subtask")):
        out.append(text(x, 104, label, "mut b", 12))
    out.append('<path d="M24 114H896" class="ln" stroke-width="1"/>')
    row = 56
    ys = {lv: 142 + (4 - i) * row for i, (lv, *_r) in enumerate(LADDER)}
    out.append(f'<rect x="24" y="{ys["complex"] - 22}" width="872" height="44" rx="10" class="rtsoft lband"/>')
    out.append(f'<path d="M96 122V{ys["lookup"] + 24}M152 122V{ys["lookup"] + 24}" class="lnm" stroke-width="5" '
               f'stroke-linecap="round" opacity="0.4"/>')
    for i, (lv, model, effort, needs, sub) in enumerate(LADDER, 1):
        y = ys[lv]
        out.append(f'<path d="M96 {y}H152" class="r{i}s" stroke-width="6" stroke-linecap="round"/>')
        out.append(text(180, y + 5, lv, "ink b", 15))
        out.append(f'<circle cx="290" cy="{y}" r="5" class="r{i}"/>' + text(300, y + 5, model, "ink", 15))
        p, _ = pill(392, y - 12, effort, size=13)
        out.append(p)
        out.append("".join(f'<rect x="{490 + k * 24}" y="{y - 4}" width="18" height="8" rx="4" '
                           f'class="{f"r{i}" if k < needs else "faint"}"/>' for k in range(4)))
        out.append(text(612, y + 5, sub, "mut", 13))
    # the call and its redo tag, which climb one rung on each redo
    cy = ys["complex"]
    out.append(f'<g class="lcall"><circle cx="124" cy="{cy}" r="13" class="halo" fill="none" stroke-width="5"/>'
               f'<circle cx="124" cy="{cy}" r="11" class="rt"/>'
               f'<g class="lredo">' + pill(30, cy - 12, "redo", cls="rt", tcls="tag", size=12)[0] + "</g></g>")
    out.append(f'<path class="lstatic rts" d="M124 {ys["standard"] - 4}V{cy + 18}" stroke-width="2.5" '
               f'stroke-dasharray="4 4" fill="none" stroke-linecap="round"/>')
    out.append(text(32, 432, "Priority quality goes one step stronger, fast one step lighter. When the cap, "
                    "availableModels, deniedModels or failures", "mut", 13))
    out.append(text(32, 452, "rule a model out, the pick moves to the nearest usable model above it, or to the "
                    "strongest one left. A redo runs one level higher.", "mut", 13))
    at = lambda lv: f"transform:translate(0px,{ys[lv] - cy}px)"  # noqa: E731
    stops = [(0, at("lookup") + ";opacity:0"), (4, at("lookup") + ";opacity:1"), (18, at("lookup")),
             (24, at("standard")), (40, at("standard")), (46, at("complex")), (62, at("complex")),
             (68, at("hardest")), (84, at("hardest")), (90, at("frontier")), (96, at("frontier") + ";opacity:1"),
             (100, at("frontier") + ";opacity:0")]
    pulse = [(0, "opacity:0")]
    for a, b in ((16, 26), (38, 48), (60, 70), (82, 92)):
        pulse += [(a, "opacity:0"), (a + 2, "opacity:1"), (b, "opacity:1"), (b + 2, "opacity:0")]
    css = (".lcall,.lband{animation:lc 12s ease-in-out infinite}" + keyframes("lc", stops)
           + ".lredo{animation:lr 12s linear infinite}" + keyframes("lr", pulse + [(100, "opacity:0")])
           + ".lstatic{animation:hide 1s infinite}" + keyframes("hide", [(0, "opacity:0"), (100, "opacity:0")]))
    title = "The ladder: five levels to a model and an effort"
    label = ("With balanced priority, lookup runs on Haiku at low effort, standard on Sonnet at medium, complex on "
             "Sonnet at high, hardest on Opus at high, and frontier on Fable at high. A call climbs one rung each "
             "time it comes back for a redo. Quality priority goes one step stronger, fast one step lighter. When "
             "the cap, the availableModels or deniedModels settings, or failures rule a model out, the pick moves "
             "to the nearest usable model above it, or to the strongest one left.")
    return svg(w, h, title, label, "".join(out), css)


def diagram_redo() -> str:
    w, h = 920, 450
    out = [frame(w, h), text(32, 42, "Redo escalation", "ink b", 17),
           text(32, 66, "The same call, issued again in the same request after its result came back.", "mut", 13.5)]
    steps = [
        ("1. Agent call", ["Routed at its level:"], "standard, Sonnet, medium", 2),
        ("2. Result comes back", ["Recorded as finished,", "with the level it ran at."], None, 0),
        ("3. Same call again", ["In the same request:", "the answer fell short."], None, -1),
        ("4. One level higher", ["Up a model, or up effort:"], "complex, Sonnet, high", 3),
    ]
    cw, gap, top = 196, 28, 92
    for k, (head, body, code, accent) in enumerate(steps):
        x = 32 + k * (cw + gap)
        out.append(card(x, top, cw, 104, rx=12))
        stripe = "rt" if accent == -1 else (f"r{accent}" if accent else "faint")
        out.append(f'<rect x="{x + 1}" y="{top + 14}" width="4" height="76" rx="2" class="{stripe}"/>')
        out.append(text(x + 18, top + 30, head, "ink b", 14.5))
        out.append(lines(x + 18, top + 54, body, "mut", 13, 19))
        if code:
            out.append(text(x + 18, top + 54 + 19 * len(body), code, "mono ink", 12))
        if k < 3:
            ax = x + cw
            out.append(f'<path d="M{ax + 6} {top + 52}H{ax + gap - 6}M{ax + gap - 11} {top + 47}L{ax + gap - 6} '
                       f'{top + 52}L{ax + gap - 11} {top + 57}" class="lnm" stroke-width="1.8" fill="none" '
                       'stroke-linecap="round" stroke-linejoin="round"/>')
    track = top + 126
    out.append(f'<path d="M{32 + cw / 2} {track}H{32 + 3 * (cw + gap) + cw / 2}" class="ln" stroke-width="3" '
               'stroke-linecap="round"/>')
    for k in range(4):
        out.append(f'<circle cx="{32 + k * (cw + gap) + cw / 2}" cy="{track}" r="5" class="faint"/>')
    last = 32 + 3 * (cw + gap) + cw / 2
    out.append(f'<g class="rtok"><circle cx="{last}" cy="{track}" r="11" class="halo" fill="none" stroke-width="5"/>'
               f'<circle cx="{last}" cy="{track}" r="9" class="rt"/></g>')
    vy = 256
    out.append(card(32, vy, 856, 92, rx=12))
    out.append(text(56, vy + 32, "Each redo is a vote that the first level was routed too low", "ink b", 14.5))
    for k in range(6):
        out.append(f'<circle cx="{66 + k * 30}" cy="{vy + 62}" r="10" class="faint"/>'
                   f'<circle cx="{66 + k * 30}" cy="{vy + 62}" r="10" class="r2 v{k}"/>')
    out.append(text(262, vy + 58, "At least 6 votes that are also at least 30% of the level's calls raise it one step.",
                    "ink", 13))
    out.append(text(262, vy + 78, "A raise that keeps being overruled with a weaker model is undone the same way.",
                    "mut", 13))
    note = ["One level up the ladder: lookup, complex and hardest move to a stronger model; standard stays on Sonnet",
            "at more effort, and a frontier redo runs at one step more effort. Not a redo: copies of one brief started",
            "together. Never escalated: a call that names its own model. A background agent's call counts as finished",
            "when the agent stops with an answer."]
    out.append(lines(32, 378, note, "mut", 13, 20))
    xs = [32 + k * (cw + gap) + cw / 2 - last for k in range(4)]
    at = lambda k: f"transform:translate({xs[k]:g}px,0px)"  # noqa: E731
    css = (".rtok{animation:rt 9s ease-in-out infinite}"
           + keyframes("rt", [(0, at(0) + ";opacity:0"), (4, at(0) + ";opacity:1"), (14, at(0)), (22, at(1)),
                              (32, at(1)), (40, at(2)), (50, at(2)), (58, at(3)), (92, at(3) + ";opacity:1"),
                              (100, at(3) + ";opacity:0")]))
    for k in range(6):
        on = 58 + k * 5
        css += (f".v{k}{{animation:v{k} 9s linear infinite}}"
                + keyframes(f"v{k}", [(0, "opacity:0"), (on, "opacity:0"), (on + 1, "opacity:1"), (96, "opacity:1"),
                                      (100, "opacity:0")]))
    title = "Redo escalation: the same call again runs one level higher"
    label = ("A call is routed at its level, for example standard on Sonnet at medium effort. Its result comes back "
             "and the call is recorded as finished. If Claude issues the same call again in the same request, it "
             "runs one level higher, complex on Sonnet at high, and counts as a vote that standard was routed too "
             "low. At least 6 votes that are also at least 30 percent of a level's calls raise that level one step.")
    return svg(w, h, title, label, "".join(out), css)


def chip(x, y, label, size=12, h=26):
    w = width_of(label, size, True) + 16
    return (f'<rect x="{x:g}" y="{y:g}" width="{w:.0f}" height="{h}" rx="6" class="card" stroke-width="1"/>'
            + text(x + 9, y + h / 2 + size * 0.36, label, "mono ink", size)), w


def chip_row(x, y, labels, size=12, gap=8, h=26):
    out, cx = [], x
    for label in labels:
        c, cw = chip(cx, y, label, size, h)
        out.append(c)
        cx += cw + gap
    return "".join(out)


def diagram_two_paths() -> str:
    w, h = 920, 540
    out = [frame(w, h), text(32, 42, "Two paths, one router", "ink b", 17),
           text(32, 66, "Both paths run the same launcher and the same Python runtime, so one router decides "
                "every spawn.", "mut", 13.5)]
    out.append(card(32, 92, 410, 96, rx=12, cls="faint", sw=0))
    out.append(text(52, 120, "Claude Code engine events", "ink b", 14.5))
    out.append(chip_row(52, 140, ["agent.spawn", "turn.start", "turn.step", "turn.complete"], 11.5, 6))
    out.append(card(32, 222, 410, 108, rx=12))
    out.append('<rect x="33" y="238" width="4" height="76" rx="2" class="r2"/>')
    out.append(text(52, 252, "The mod: hooks/router.ts", "ink b", 15))
    out.append(lines(52, 276, ["Primary path. Sets each spawn's model, its effort on",
                               "each model request, and a hard turn's effort. Reaches",
                               "teammates and workflow stages too."], "mut", 13, 19))
    out.append(card(478, 92, 410, 238, rx=12, cls="faint", sw=0))
    out.append(text(498, 120, "Settings hooks: hooks/hooks.json", "ink b", 14.5))
    out.append(chip_row(498, 138, ["SessionStart", "UserPromptSubmit", "PreToolUse"], 11.5))
    out.append(chip_row(498, 172, ["PostToolUse", "PostToolUseFailure", "SubagentStop"], 11.5))
    out.append(chip_row(498, 206, ["PostModelSwitch", "Stop"], 11.5))
    out.append(card(498, 244, 370, 72, rx=10))
    out.append('<rect x="499" y="256" width="4" height="48" rx="2" class="r4"/>')
    out.append(lines(516, 266, ["Fallback: PreToolUse rewrites the Agent call where",
                                "mods don't load, and stands down once the mod has",
                                "announced itself for the session (mod-start)."], "mut", 12.5, 18))
    arrow = 'class="lnm" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"'
    out.append(f'<path d="M237 188V216M232 211L237 216L242 211" {arrow}/>')
    out.append(f'<path d="M237 330V366H410V398M405 393L410 398L415 393" {arrow}/>')
    out.append(f'<path d="M683 330V366H510V398M505 393L510 398L515 393" {arrow}/>')
    out.append(text(250, 356, "sh model-router.sh mod-*", "mono mut", 11.5))
    out.append(text(696, 356, "sh model-router.sh <event>", "mono mut", 11.5))
    out.append(card(132, 404, 656, 110, rx=12))
    out.append('<rect x="133" y="420" width="4" height="78" rx="2" class="rt"/>')
    out.append(text(156, 434, "scripts/modelrouter: one Python runtime, one router", "ink b", 15))
    out.append(chip_row(156, 450, ["spawn", "router.decide", "ladder", "registry", "profile", "calls", "learn",
                                   "gates"], 11.5))
    out.append(text(156, 500, "One decision procedure and one state store for both paths.", "mut", 13))
    for name in ("pa", "pb", "pc"):
        out.append(f'<circle cx="0" cy="0" r="6" class="rt {name}"/>')

    def path_frames(points, start, end):
        stops = [(0, f"transform:translate({points[0][0]}px,{points[0][1]}px);opacity:0"),
                 (start, f"transform:translate({points[0][0]}px,{points[0][1]}px);opacity:0")]
        seg = [abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(points, points[1:])]
        t = start
        for (px, py), length in zip(points[1:], seg):
            t += (end - start) * length / sum(seg)
            stops.append((round(t, 2), f"transform:translate({px}px,{py}px);opacity:1"))
        stops.insert(2, (start + 1, f"transform:translate({points[0][0]}px,{points[0][1]}px);opacity:1"))
        stops.append((min(end + 2, 100), f"transform:translate({points[-1][0]}px,{points[-1][1]}px);opacity:0"))
        stops.append((100, f"transform:translate({points[-1][0]}px,{points[-1][1]}px);opacity:0"))
        return stops

    css = ".pa,.pb,.pc{opacity:0}"
    css += ".pa{animation:pa 6s linear infinite}" + keyframes("pa", path_frames([(237, 188), (237, 222)], 0, 12))
    css += ".pb{animation:pb 6s linear infinite}" + keyframes(
        "pb", path_frames([(237, 330), (237, 366), (410, 366), (410, 404)], 22, 52))
    css += ".pc{animation:pc 6s linear infinite}" + keyframes(
        "pc", path_frames([(683, 330), (683, 366), (510, 366), (510, 404)], 58, 88))
    title = "Two control paths, one router"
    label = ("Claude Code engine events (agent.spawn, turn.start, turn.step, turn.complete) reach the mod, "
             "hooks/router.ts. Settings hooks in hooks/hooks.json (SessionStart, UserPromptSubmit, PreToolUse, "
             "PostToolUse, PostToolUseFailure, SubagentStop, PostModelSwitch, Stop) do the bookkeeping and are the "
             "fallback. Both run sh model-router.sh, which calls one Python runtime that decides every spawn.")
    return svg(w, h, title, label, "".join(out), css)


PRECEDENCE = [
    ("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "pins every subagent's model; the router sets effort only", True),
    ("A model named on the call", "by Claude, a skill's instructions or a workflow script", False),
    ("A per-agent rule", "in ~/.claude/model-router.json or .claude/model-router.json", False),
    ("The agent's own model field", "kept unless agent_pins is override", False),
    ("CLAUDE_CODE_SUBAGENT_MODEL", "your default for agents that name no model", True),
    ("The router's pick", "the ladder's model and effort for the brief's level", False),
]


def diagram_precedence() -> str:
    w, h = 920, 520
    out = [frame(w, h), text(32, 42, "Who decides the model", "ink b", 17),
           text(32, 66, "Strongest first: the first source that names a model wins.",
                "mut", 13.5)]
    top, rh = 92, 60
    for k, (head, detail, is_env) in enumerate(PRECEDENCE):
        y = top + k * rh
        out.append(card(32, y, 560, 52, rx=10))
        if k == 5:
            out.append(f'<rect x="32.5" y="{y + 0.5}" width="559" height="51" rx="10" class="rtsoft qband"/>')
        out.append(f'<circle cx="62" cy="{y + 26}" r="14" class="faint"/>')
        out.append(text(62, y + 31, str(k + 1), "ink b", 14, "middle"))
        out.append(text(88, y + 22, head, ("mono ink b" if is_env else "ink b"), 13 if is_env else 14))
        out.append(text(88, y + 41, detail, "mut", 12.5))
    out.append(f'<g class="qmark"><rect x="22" y="{top + 5 * rh + 8}" width="5" height="36" rx="2.5" class="rt"/></g>')
    arrow = 'class="lnm" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"'
    out.append(f'<path d="M592 {top + 5 * rh + 26}H612V{top + 26}H624M619 {top + 21}L624 {top + 26}L619 {top + 31}" '
               f'{arrow}/>')
    cap = ["For 2, 3, 4 and 6, the winner has to", "be usable: inside max_tier, allowed", "by your availableModels and",
           "deniedModels settings, and known to", "work. If it isn't, the strongest usable", "model below it runs, or the weakest",
           "usable one when none is below it."]
    out.append(card(632, 92, 256, 66 + 19 * len(cap), rx=12))
    out.append(f'<rect x="633" y="108" width="4" height="{34 + 19 * len(cap)}" rx="2" class="rt"/>')
    out.append(text(652, 120, "Then the cap", "ink b", 14.5))
    out.append(lines(652, 146, cap, "mut", 12.5, 19))
    ey = 92 + 66 + 19 * len(cap) + 16
    out.append(card(632, ey, 256, 138, rx=12))
    out.append(f'<rect x="633" y="{ey + 16}" width="4" height="106" rx="2" class="r3"/>')
    out.append(text(652, ey + 28, "And the effort", "ink b", 14.5))
    out.append(lines(652, ey + 54, ["Set from the level, unless the call,", "the agent or one of these sets it:"],
                     "mut", 13, 19))
    out.append(lines(652, ey + 98, ["CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_SUBAGENT_EFFORT"], "mono ink", 11.5, 18))
    out.append(text(32, 476, "Example: a standard brief with nothing above it set falls through to the router's "
                    "pick,", "mut", 13))
    out.append(text(32, 496, "Sonnet at medium effort, which is inside the cap and runs.", "mut", 13))
    marks = [(0, "transform:translate(0px,-300px);opacity:0"), (3, "transform:translate(0px,-300px);opacity:1")]
    t = 3
    for k in range(1, 6):
        marks += [(t + 9, f"transform:translate(0px,{-300 + (k - 1) * rh}px)"),
                  (t + 13, f"transform:translate(0px,{-300 + k * rh}px)")]
        t += 13
    marks += [(92, "transform:translate(0px,0px);opacity:1"), (100, "transform:translate(0px,0px);opacity:0")]
    css = (".qmark{animation:qm 9s ease-in-out infinite}" + keyframes("qm", marks)
           + ".qband{animation:qb 9s ease-in-out infinite}"
           + keyframes("qb", [(0, "opacity:0"), (66, "opacity:0"), (70, "opacity:1"), (92, "opacity:1"),
                              (100, "opacity:0")]))
    title = "Who decides the model: the order of authority"
    label = ("Strongest first: CLAUDE_CODE_SUBAGENT_MODEL_FORCE, then a model named on the call, then a per-agent "
             "rule in model-router.json, then the agent's own model field unless agent_pins is override, then "
             "CLAUDE_CODE_SUBAGENT_MODEL, then the router's pick. For all but the two environment variables, the "
             "winner must be usable under the cap and your settings, else the strongest usable model below it runs. Effort follows the level unless something "
             "already sets it.")
    return svg(w, h, title, label, "".join(out), css)


def diagram_stop_checks() -> str:
    w, h = 920, 486
    out = [frame(w, h), text(32, 42, "The checks before a turn ends", "ink b", 17),
           text(32, 66, "The Stop hook runs two checks. Each asks once, then lets the turn end.", "mut", 13.5)]
    out.append(card(320, 88, 280, 50, rx=25))
    out.append(text(460, 118, "Claude is about to end the turn", "ink b", 13.5, "middle"))
    arrow = 'class="lnm" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"'
    out.append(f'<path d="M400 138V156H237V176M232 171L237 176L242 171" {arrow}/>')
    out.append(f'<path d="M520 138V156H683V176M678 171L683 176L688 171" {arrow}/>')
    lanes = [
        (32, "strong_agent_gate", "on by default", "r1", "tag",
         ["the request is complex or harder,", "the session model is weaker than it needs,",
          "and no agent of the needed strength started."],
         ["Blocks the stop once and asks for a stronger", "agent's check: the architect, or the deep-solver",
          "for a frontier request, started no lower than", "the request's level."], 1.0),
        (478, "verify_gate", "off by default", "faint", "mut",
         ["code in the project changed, and no test, lint,", "type-check or build ran after the last edit,",
          "and no verifier or reviewer started."],
         ["Asks once for a test run (the verifier)", "before the turn ends. Turn it on in /config", "under the plugin."],
         0.62),
    ]
    for x, name, state, cls, tcls, when, then, op in lanes:
        out.append(f'<g opacity="{op}">')
        out.append(card(x, 182, 410, 252, rx=12))
        out.append(text(x + 20, 212, name, "mono ink b", 14))
        p, _ = pill(x + 26 + width_of(name, 14, True), 194, state, cls=cls, tcls=tcls, size=11.5, h=24, pad=10)
        out.append(p)
        out.append(text(x + 20, 244, "Asks when", "mut b", 12))
        out.append(lines(x + 20, 266, when, "ink", 13, 20))
        out.append(f'<path d="M{x + 20} 336H{x + 390}" class="ln" stroke-width="1"/>')
        out.append(text(x + 20, 360, "Then", "mut b", 12))
        out.append(lines(x + 62, 360, then, "ink", 13, 19))
        out.append("</g>")
    out.append(text(32, 464, "Both are options in /config. The stronger-agent hand-off starts at the request's "
                    "level, so a short hand-off brief can't route it lower.", "mut", 13))
    out.append('<circle cx="0" cy="0" r="6" class="rt sa"/>')
    pts = [(400, 138), (400, 156), (237, 156), (237, 182)]
    stops = [(0, f"transform:translate({pts[0][0]}px,{pts[0][1]}px);opacity:0"),
             (8, f"transform:translate({pts[0][0]}px,{pts[0][1]}px);opacity:1")]
    for k, (px, py) in enumerate(pts[1:], 1):
        stops.append((8 + k * 10, f"transform:translate({px}px,{py}px);opacity:1"))
    stops += [(44, f"transform:translate(237px,182px);opacity:0"), (100, "transform:translate(237px,182px);opacity:0")]
    css = ".sa{opacity:0;animation:sa 5s linear infinite}" + keyframes("sa", stops)
    title = "The checks before a turn ends: strong_agent_gate and verify_gate"
    label = ("When Claude is about to end a turn, the Stop hook runs two checks. strong_agent_gate, on by default, "
             "blocks the stop once when a complex or harder request was answered by a weaker session model with no "
             "agent of the needed strength started, and asks for a stronger agent's check, started no lower than "
             "the request's level. verify_gate, off by default, asks once for a test run when code changed and "
             "nothing checked it.")
    return svg(w, h, title, label, "".join(out), css)


# ---------------------------------------------------------------- the benchmark chart

BENCH_SESSION = "claude-opus-5-5"
BENCH_SERIES = [  # config id in docs/BENCHMARKS.md, legend label, rung class
    ("defaults", "Claude Code defaults", "faintbar"),
    ("plugin-m6-08-b-nogate", "Model Router, test-run check off", "r2"),
    ("plugin-m6-10", "Model Router defaults at a06e5d4", "r4"),
]
CELL = re.compile(r"(\d+) of (\d+) solved .*?\$([\d.]+) per solved")


def bench_data():
    page = (ROOT / "docs" / "BENCHMARKS.md").read_text(encoding="utf-8")
    head = re.search(r"(\d[\d,]*) runs, \$[\d.]+ at list price, on ([\d-]+)", page)
    section = page.split(f"## Results: session model `{BENCH_SESSION}`", 1)
    if len(section) < 2:
        raise SystemExit(f"docs/BENCHMARKS.md has no results table for {BENCH_SESSION}")
    table = [ln for ln in section[1].split("\n## ")[0].split("### ")[0].splitlines() if ln.startswith("|")]
    cols = [c.strip() for c in table[0].strip("|").split("|")]
    rows = []
    for line in table[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        values = []
        for config, _, _ in BENCH_SERIES:
            if config not in cols:
                raise SystemExit(f"docs/BENCHMARKS.md has no {config} column; update BENCH_SERIES")
            m = CELL.search(cells[cols.index(config)])
            values.append((int(m.group(1)), int(m.group(2)), float(m.group(3))) if m else None)
        rows.append((cells[0], values, cells[-1]))
    return rows, head.group(1), head.group(2)


def bench_chart(phone=False) -> str:
    rows, runs, date = bench_data()
    peak = max(v[2] for _, vals, _ in rows for v in vals if v)
    axis = max(0.1, -(-peak // 0.1) * 0.1)
    w = 520 if phone else 920
    label_w = 0 if phone else 150
    bar_x = 32 + label_w
    bar_w = w - bar_x - (150 if phone else 160)
    bh, gap = (12, 4) if phone else (11, 3)
    row_h = (3 * bh + 2 * gap) + (40 if phone else 22)
    top = 190 if phone else 132
    h = top + len(rows) * row_h + (150 if phone else 84)
    out = [frame(w, h), text(32, 42, "Cost per solved task", "ink b", 18 if phone else 17),
           text(32, 66, "Opus 5.5 session. Lower is cheaper. Every row is PARTIAL.", "mut", 14 if phone else 13.5)]
    lx, ly = 32, 96
    for k, (_, name, cls) in enumerate(BENCH_SERIES):
        if phone:
            ly = 100 + k * 24
            lx = 32
        out.append(f'<rect x="{lx}" y="{ly - 10}" width="14" height="10" rx="3" class="{cls}"/>')
        out.append(text(lx + 20, ly, name, "ink", 14 if phone else 13))
        if not phone:
            lx += 40 + width_of(name, 13)
    for k in range(0, int(round(axis / 0.1)) + 1):
        gx = bar_x + bar_w * (k * 0.1) / axis
        out.append(f'<path d="M{gx:.1f} {top - 6}V{top + len(rows) * row_h - 10}" class="ln" stroke-width="1"/>')
        out.append(text(gx, top + len(rows) * row_h + 8, f"${k * 0.1:.2f}", "mono mut", 11.5, "middle"))
    css = ".faintbar{fill:#9AA6B8}@media (prefers-color-scheme: dark){.faintbar{fill:#5B6880}}"
    css += ".bar{transform-box:fill-box;transform-origin:0% 50%;animation:grow 1.1s cubic-bezier(.2,.7,.2,1) both}"
    css += keyframes("grow", [(0, "transform:scaleX(0)"), (100, "transform:scaleX(1)")])
    for r, (cls_name, vals, status) in enumerate(rows):
        y = top + r * row_h
        if phone:
            out.append(text(32, y + 4, cls_name, "ink b", 15))
            y += 14
        else:
            out.append(text(32, y + 22, cls_name, "ink b", 13.5))
        for k, v in enumerate(vals):
            by = y + k * (bh + gap)
            if not v:
                out.append(text(bar_x, by + bh - 1, "not run", "mono mut", 11))
                continue
            solved, n, cost = v
            bw = max(2.0, bar_w * cost / axis)
            out.append(f'<rect x="{bar_x}" y="{by}" width="{bw:.1f}" height="{bh}" rx="3" '
                       f'class="{BENCH_SERIES[k][2]} bar" style="animation-delay:{r * 0.06 + k * 0.05:.2f}s"/>')
            out.append(text(bar_x + bw + 8, by + bh - 1.5, f"${cost:.4f} ({solved} of {n})", "mono mut",
                            12.5 if phone else 11))
    note_y = top + len(rows) * row_h + 40
    note = (f"Runs per class: 4 for the defaults and the router with the check off, 2 for a06e5d4. A class needs "
            f"10 to count as a win. Source: docs/BENCHMARKS.md, {runs} runs on {date}.")
    size = 14 if phone else 12.5
    out.append(lines(32, note_y, wrap(note, size, w - 64), "mut", size, round(size * 1.45)))
    title = "Cost per solved task on an Opus 5.5 session, by task class"
    parts = []
    for cls_name, vals, status in rows:
        bits = [f"{BENCH_SERIES[k][1]} ${v[2]:.4f} ({v[0]} of {v[1]} solved)" for k, v in enumerate(vals) if v]
        parts.append(f"{cls_name}: " + ", ".join(bits))
    label = ("Cost per solved task on an Opus 5.5 session, from docs/BENCHMARKS.md. " + "; ".join(parts)
             + ". Every row is PARTIAL: too few runs to call a winner.")
    return svg(w, h, title, label, "".join(out), css)


# ---------------------------------------------------------------- social preview

def social_preview() -> str:
    w, h = 1280, 640
    panel, _, _ = ladder_panel(700, 92, 520, 1.0, animate=False, prefix="s")
    body = (f'<rect width="{w}" height="{h}" class="bg"/>'
            + wordmark_body(72, 96, 1.05)
            + text(74, 232, "CLAUDE CODE PLUGIN", "mono rt b", 18, extra=' letter-spacing="2.4"')
            + "".join(text(72, 300 + i * 56, s, "ink b", 46) for i, s in
                      enumerate(["Every agent on the", "cheapest model that", "should do the job."]))
            + lines(73, 488, ["A redo climbs one level. Subagents,", "teammates and workflow stages."], "mut", 24, 36)
            + text(74, 590, "github.com/crisnahine/claude-code-model-router", "mono mut", 18)
            + panel)
    label = ("Model Router, a Claude Code plugin: every agent on the cheapest model that should do the job, and a "
             "redo climbs one level.")
    return svg(w, h, "Model Router social preview", label, body, theme="dark")


# ---------------------------------------------------------------- the site

FAQ_HTML = ("<!-- FAQ, written by tools/docs_assets.py from docs/FAQ.md. -->", "<!-- End of FAQ. -->")
FAQ_LD = ("<!-- FAQ data, written by tools/docs_assets.py from docs/FAQ.md. -->", "<!-- End of FAQ data. -->")
SITE_URL = re.compile(r"<!-- SITE_URL: (\S+) -->")


def faq_entries() -> list:
    text_ = (ROOT / "docs" / "FAQ.md").read_text(encoding="utf-8")
    out = []
    for block in text_.split("\n## ")[1:]:
        question, _, answer = block.partition("\n")
        out.append((question.strip(), " ".join(answer.split())))
    return out


def md_link(target: str) -> str:
    if target.startswith("http"):
        return target
    path, _, anchor = target.partition("#")
    resolved = (ROOT / "docs" / path).resolve().relative_to(ROOT).as_posix()
    return f"{REPO}/blob/main/{resolved}" + (f"#{anchor}" if anchor else "")


def md_html(s: str) -> str:
    s = esc(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: f'<a href="{attr(md_link(m.group(2)))}">{m.group(1)}</a>', s)


def md_plain(s: str) -> str:
    s = re.sub(r"\s*\(\[[^\]]+\]\([^)]+\)(, \[[^\]]+\]\([^)]+\)[^)]*)?\)", "", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    return s.replace("`", "")


def site_faq_html() -> str:
    items = [f'<details{" open" if i == 0 else ""}><summary><h3>{esc(q)}</h3></summary><p>{md_html(a)}</p></details>'
             for i, (q, a) in enumerate(faq_entries())]
    return FAQ_HTML[0] + "\n" + "\n".join(items) + "\n" + FAQ_HTML[1]


def site_faq_ld() -> str:
    data = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": md_plain(a)}}
        for q, a in faq_entries()]}
    return (FAQ_LD[0] + '\n<script type="application/ld+json">\n' + json.dumps(data, indent=2, ensure_ascii=False)
            + "\n</script>\n" + FAQ_LD[1])


def between(current: str, marks: tuple, body: str) -> str:
    before, rest = current.split(marks[0], 1)
    return before + body + rest.split(marks[1], 1)[1]


def site_files() -> dict:
    index = SITE / "index.html"
    if not index.exists():
        return {}
    page = index.read_text(encoding="utf-8")
    url = SITE_URL.search(page).group(1)
    old = None
    sitemap = SITE / "sitemap.xml"
    if sitemap.exists():
        m = re.search(r"<loc>(\S+?)</loc>", sitemap.read_text(encoding="utf-8"))
        old = m.group(1) if m else None
    if old and old != url:
        head, sep, tail = page.partition("-->")
        page = head + sep + tail.replace(old, url)
    page = between(page, FAQ_HTML, site_faq_html())
    page = between(page, FAQ_LD, site_faq_ld())
    v, day = version(), release_date(version())
    page = re.sub(r'"softwareVersion": "[^"]*"', f'"softwareVersion": "{v}"', page)
    page = re.sub(r'"datePublished": "[^"]*"', f'"datePublished": "{day}"', page)
    page = re.sub(r'Version [^,]+, released <time datetime="[^"]*">[^<]*</time>',
                  f'Version {v}, released <time datetime="{day}">{day}</time>', page)
    return {
        "site/index.html": page,
        "site/sitemap.xml": ('<?xml version="1.0" encoding="UTF-8"?>\n'
                             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                             f"  <url>\n    <loc>{url}</loc>\n    <lastmod>{release_date(version())}</lastmod>\n"
                             "  </url>\n</urlset>\n"),
        "site/robots.txt": f"User-agent: *\nAllow: /\n\nSitemap: {url}sitemap.xml\n",
    }


# ---------------------------------------------------------------- write and check

def version() -> str:
    return json.loads((ROOT / "plugins/model-router/.claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]


def release_date(v: str) -> str:
    m = re.search(rf"^## {re.escape(v)} - (\d{{4}}-\d{{2}}-\d{{2}})$", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
                  re.M)
    if not m:
        raise SystemExit(f"CHANGELOG.md has no '## {v} - <date>' section")
    return m.group(1)


def citation(v: str) -> str:
    return f"""cff-version: 1.2.0
message: "If you use Model Router in your work, please cite it with these details."
type: software
title: "Model Router"
abstract: >-
  Model Router (model-router) is a Claude Code plugin that routes every agent
  Claude Code starts (subagents, agent-team teammates and workflow stages) to
  the cheapest Claude model and reasoning effort that should do the job, and
  runs a call one level higher when its work comes back for a redo.
authors:
  - given-names: Cris
    family-names: Nahine
version: "{v}"
date-released: "{release_date(v)}"
license: MIT
repository-code: "{REPO}"
url: "{REPO}"
keywords:
  - Claude Code
  - Claude Code plugin
  - model routing
  - subagents
  - cost per solved task
"""


SITE_ASSETS = ["logo-mark.svg", "logo.svg", "routing-call.svg", "badge-version.svg", "badge-claude-code.svg",
               "badge-python.svg", "badge-license.svg", "ladder.svg", "redo-escalation.svg",
               "two-paths.svg", "precedence.svg", "stop-checks.svg", "benchmark.svg", "benchmark-phone.svg",
               "social-preview.png"]


def build() -> dict:
    check_ladder_against_registry()
    v = version()
    files = {}
    assets = {}
    assets.update(brand_assets())
    assets.update(banner_assets())
    assets.update(badge_assets(v))
    assets["hero.svg"] = hero()
    assets["hero-phone.svg"] = hero(phone=True)
    assets["ladder.svg"] = diagram_ladder()
    assets["redo-escalation.svg"] = diagram_redo()
    assets["two-paths.svg"] = diagram_two_paths()
    assets["precedence.svg"] = diagram_precedence()
    assets["stop-checks.svg"] = diagram_stop_checks()
    assets["benchmark.svg"] = bench_chart()
    assets["benchmark-phone.svg"] = bench_chart(phone=True)
    assets["social-preview.svg"] = social_preview()
    panel, css, ph = ladder_panel(1, 1, 518, prefix="c")
    assets["routing-call.svg"] = svg(520, round(ph) + 2, "One agent call, routed and redone", HERO_LABEL, panel, css)
    for name, body in assets.items():
        files[f"docs/assets/{name}"] = body
    for path, *_ in PAGES:
        if path in GENERATED_ELSEWHERE:
            continue
        p = ROOT / path
        if p.exists():
            files[path] = with_head(path, p.read_text(encoding="utf-8"))
            if path == "docs/README.md":
                files[path] = with_cards(files[path])
    files["CITATION.cff"] = citation(v)
    files.update(site_files())
    return files


def problems_in_svg(name: str, source: str) -> list:
    found = []
    try:
        root = ET.fromstring(source)
    except ET.ParseError as e:
        return [f"{name}: not valid XML ({e})"]
    ns = "{http://www.w3.org/2000/svg}"
    if root.get("role") != "img" or not root.get("aria-label"):
        found.append(f"{name}: needs role=img and an aria-label")
    if root.find(f"{ns}title") is None:
        found.append(f"{name}: needs a <title>")
    if "@keyframes" in source and "prefers-reduced-motion: reduce" not in source:
        found.append(f"{name}: animates without a reduced-motion rule")
    if re.search(r"<script|href=\"http|url\(http|@import", source):
        found.append(f"{name}: loads something from outside the file")
    if len(source.encode()) > 40_000:
        found.append(f"{name}: {len(source.encode())} bytes, over 40 KB")
    if "var(" in source:
        found.append(f"{name}: CSS variables render black in some viewers")
    return found


def contrast(a: str, b: str) -> float:
    def lum(hexcolor):
        c = [int(hexcolor[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def palette_problems() -> list:
    found = []
    for name, p in (("light", LIGHT), ("dark", DARK)):
        for surface in ("card", "bg", "faint"):
            for token, need in (("ink", 7), ("muted", 4.5), ("route", 4.5)):
                if contrast(p[token], p[surface]) < need:
                    found.append(f"{name}: {token} on {surface} is {contrast(p[token], p[surface]):.2f}:1, under {need}")
            for i, c in enumerate(p["rungs"], 1):
                if surface != "faint" and contrast(c, p[surface]) < 3:
                    found.append(f"{name}: rung {i} on {surface} is {contrast(c, p[surface]):.2f}:1, under 3")
    return found


def main(argv: list) -> int:
    check = "--check" in argv
    files = build()
    problems = palette_problems()
    for path, body in files.items():
        if path.endswith(".svg"):
            problems += problems_in_svg(path, body)
        target = ROOT / path
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current != body:
            if check:
                problems.append(f"{path}: out of date, run python3 tools/docs_assets.py")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body.encode("utf-8"))
    for path in GENERATED_ELSEWHERE:
        text_now = (ROOT / path).read_text(encoding="utf-8")
        if not text_now.startswith(page_head(path)):
            problems.append(f"{path}: its head differs from page_head(); fix the header in bench/analyze.py")
    for name in SITE_ASSETS:
        src, dst = ASSETS / name, SITE / "assets" / name
        if src.exists() and (not dst.exists() or dst.read_bytes() != src.read_bytes()):
            if check:
                problems.append(f"site/assets/{name}: differs from docs/assets, run python3 tools/docs_assets.py")
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
