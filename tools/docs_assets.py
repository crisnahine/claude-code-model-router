#!/usr/bin/env python3
"""Writes the docs' diagrams and badges, the docs' page heads and index, the benchmark figures, the site's
generated parts and CITATION.cff.

Run: python3 tools/docs_assets.py            write everything
     python3 tools/docs_assets.py --check    fail if anything on disk differs or breaks a rule
"""
from __future__ import annotations

import dataclasses
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from docs_pages import PAGES, attr, rel, with_head  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/crisnahine/claude-code-model-router"

SANS = '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif'
MONO = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace'

# The five rung colors run from the cheapest level (lookup) to the strongest (frontier).
LIGHT = {
    "bg": "#F4F7FB", "card": "#FFFFFF", "line": "#D2DBE7", "ink": "#0C1524", "muted": "#4A5871",
    "faint": "#E7EDF5", "route": "#A65200", "routesoft": "#FFF1DE", "tag": "#FFFFFF",
    "rungs": ["#0E8C6F", "#0A78B8", "#3B57D6", "#6D3FD1", "#A72C8A"],
}
DARK = {
    "bg": "#0A101B", "card": "#0F1828", "line": "#26344B", "ink": "#E7EDF6", "muted": "#9BA9BD",
    "faint": "#1A263A", "route": "#FFB23E", "routesoft": "#33260F", "tag": "#0C1524",
    "rungs": ["#38D3A8", "#45B4F2", "#8196FF", "#AA8BFF", "#F27BCD"],
}


def esc(s: str) -> str:
    return html.escape(str(s), quote=False)


def width_of(text: str, size: float, mono: bool = False, bold: bool = False) -> float:
    """A safe upper bound on rendered width: per-character widths at or above the widest font in the stacks
    (bold "standard" is 3.9em in SF Pro, 4.1em in Segoe UI and 4.2em in Arial; this gives it 4.6em)."""
    return len(text) * size * (0.61 if mono else (0.58 if bold else 0.54))


LAYOUT = []  # columns that can't hold their widest label, reported by --check


def room(where: str, end: float, limit: float):
    if end > limit:
        LAYOUT.append(f"{where}: text ends at {end:.0f}, past {limit:.0f}")


def palette_css(p: dict) -> str:
    rules = [
        f".card{{fill:{p['card']};stroke:{p['line']}}}", f".ink{{fill:{p['ink']}}}",
        f".mut{{fill:{p['muted']}}}", f".faint{{fill:{p['faint']}}}", f".ln{{stroke:{p['line']}}}",
        f".lnm{{stroke:{p['muted']}}}", f".rt{{fill:{p['route']}}}",
        f".rts{{stroke:{p['route']}}}", f".rtsoft{{fill:{p['routesoft']}}}", f".halo{{stroke:{p['card']}}}",
        f".tag{{fill:{p['tag']}}}",
    ]
    for i, c in enumerate(p["rungs"], 1):
        rules.append(f".r{i}{{fill:{c}}}.r{i}s{{stroke:{c}}}")
    return "".join(rules)


def style() -> str:
    return (f"<style>text{{font-family:{SANS}}}.mono{{font-family:{MONO}}}.b{{font-weight:600}}"
            + palette_css(LIGHT) + "@media (prefers-color-scheme: dark){" + palette_css(DARK) + "}</style>")


def svg(w: int, h: int, title: str, label: str, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="{attr(label)}">\n<title>{esc(title)}</title>\n'
            f"{style()}\n{body}\n</svg>\n")


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


# ---------------------------------------------------------------- the documentation index

# Cards on the index for pages that carry no page head of their own.
EXTRA_CARDS = [("README.md", "Model Router", "What does it do, and how do I install it?", 1)]
GROUPS = {1: ("Start here", "What it is, and the questions people ask first."),
          2: ("How it works", "The design, its words and sources, and what it costs to run."),
          3: ("Evidence", "What was measured and tested, and what is still open."),
          4: ("The project", "What comes next, what changed, and how to take part.")}
CARDS_START, CARDS_END = "<!-- Cards, written by tools/docs_assets.py from PAGES in tools/docs_pages.py. -->", "<!-- End of cards. -->"


def cards(pages: list = PAGES) -> str:
    out = [CARDS_START, "<table>"]
    for group, (name, blurb) in GROUPS.items():
        shown = [p for p in EXTRA_CARDS + pages if p[3] == group and p[0] != "docs/README.md"]
        out.append(f'<tr><th colspan="2" align="left">{name}<br><sub>{blurb}</sub></th></tr>')
        for k in range(0, len(shown), 2):
            out.append("<tr>")
            for path, title, question, _ in shown[k:k + 2]:
                out.append(f'<td width="50%" valign="top"><a href="{rel("docs/README.md", path)}"><b>{title}</b></a>'
                           f'<br>{question}</td>')
            if len(shown[k:k + 2]) == 1:
                out.append('<td width="50%"></td>')
            out.append("</tr>")
    out += ["</table>", CARDS_END]
    return "\n".join(out)


def with_cards(current: str, pages: list = PAGES) -> str:
    if CARDS_START in current:
        return between(current, (CARDS_START, CARDS_END), cards(pages), "docs/README.md")
    return current


# ---------------------------------------------------------------- badges

BADGES = [
    ("version", "version", None),
    ("claude-code", "claude code", None),
    ("python", "python", "3.9 or later"),
    ("dependencies", "dependencies", "none"),
    ("license", "license", "Apache-2.0"),
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


def badge_assets(version: str, floor: str) -> dict:
    filled = {"version": version, "claude-code": f"{floor} or later"}
    return {f"badge-{slug}.svg": badge(label, value or filled[slug]) for slug, label, value in BADGES}


# ---------------------------------------------------------------- the ladder

LADDER = [  # level, model, effort, needs, subtask
    ("lookup", "Haiku", "medium", 1, "Find, list, summarize, run a command"),
    ("standard", "Sonnet", "medium", 2, "Implement to a clear spec, write tests"),
    ("complex", "Opus", "high", 3, "Fix a bug, design, review, risky change"),
    ("hardest", "Opus", "xhigh", 3, "Ambiguous, multi-system and risky at once"),
    ("frontier", "Fable", "high", 4, "No known approach: research"),
]


def check_ladder_against_registry(config: dict):
    by_strength = {m["strength"]: name for name, m in config["models"].items()}
    shipped = [(lv, by_strength[spec["needs"]], spec["effort"], spec["needs"]) for lv, spec in config["levels"].items()]
    mine = [(lv, model.lower(), effort, needs) for lv, model, effort, needs, _ in LADDER]
    if shipped != mine:
        raise SystemExit(f"LADDER differs from config/models.json: {shipped}")


# ---------------------------------------------------------------- diagrams

def diagram_ladder() -> str:
    w, h = 920, 492
    out = [frame(w, h), text(32, 42, "The ladder", "ink b", 17),
           text(32, 66, "Each level runs on the cheapest usable model strong enough for it.", "mut", 13.5)]
    for x, label in ((180, "Level"), (300, "Model"), (392, "Effort"), (482, "Model strength"), (590, "Typical subtask")):
        out.append(text(x, 104, label, "mut b", 12))
    out.append('<path d="M24 114H896" class="ln" stroke-width="1"/>')
    row = 56
    ys = {lv: 142 + (4 - i) * row for i, (lv, *_r) in enumerate(LADDER)}
    out.append(f'<rect x="24" y="{ys["complex"] - 22}" width="872" height="44" rx="10" class="rtsoft"/>')
    out.append(f'<path d="M96 122V{ys["lookup"] + 24}M152 122V{ys["lookup"] + 24}" class="lnm" stroke-width="5" '
               f'stroke-linecap="round" opacity="0.4"/>')
    for i, (lv, model, effort, needs, sub) in enumerate(LADDER, 1):
        y = ys[lv]
        out.append(f'<path d="M96 {y}H152" class="r{i}s" stroke-width="6" stroke-linecap="round"/>')
        out.append(text(180, y + 5, lv, "ink b", 15))
        out.append(f'<circle cx="290" cy="{y}" r="5" class="r{i}"/>' + text(300, y + 5, model, "ink", 15))
        p, _ = pill(392, y - 12, effort, size=13)
        out.append(p)
        out.append("".join(f'<rect x="{482 + k * 24}" y="{y - 4}" width="18" height="8" rx="4" '
                           f'class="{f"r{i}" if k < needs else "faint"}"/>' for k in range(4)))
        out.append(text(590, y + 5, sub, "mut", 12.5))
        room("ladder.svg: subtask", 590 + width_of(sub, 12.5), 900)
        room("ladder.svg: level", 180 + width_of(lv, 15, bold=True), 290 - 5 - 6)
        room("ladder.svg: model", 300 + width_of(model, 15), 392 - 8)
    # a call redone from standard, more effort on Sonnet first, now on complex
    cy = ys["complex"]
    out.append(f'<circle cx="124" cy="{cy}" r="13" class="halo" fill="none" stroke-width="5"/>'
               f'<circle cx="124" cy="{cy}" r="11" class="rt"/>'
               + pill(30, cy - 12, "redo", cls="rt", tcls="tag", size=12)[0])
    out.append(f'<path class="rts" d="M124 {ys["standard"] - 4}V{cy + 18}" stroke-width="2.5" '
               f'stroke-dasharray="4 4" fill="none" stroke-linecap="round"/>')
    out.append(text(32, 432, "A model named on a call stands for its lowest level. When the cap, "
                    "availableModels, deniedModels or failures", "mut", 13))
    out.append(text(32, 452, "rule a model out, the pick moves to the nearest usable model above it, or to the "
                    "strongest one left. A redo adds effort first.", "mut", 13))
    out.append(text(32, 472, "A pick weaker than its level needs gets one more effort step, up to xhigh (high on "
                    "Haiku), which makes up part of the loss.", "mut", 13))
    title = "The ladder: five levels to a model and an effort"
    label = ("Lookup runs on Haiku at medium effort, standard on Sonnet at medium, complex on Opus at high, hardest "
             "on Opus at xhigh, and frontier on Fable at high. A model named on a call stands for its lowest level. "
             "A call Claude marks as a redo runs one effort step higher on its model while it ran below high, and "
             "after that on the next stronger model: Haiku, Sonnet, Opus, then Fable. When the cap, the availableModels or "
             "deniedModels settings, or failures rule a model out, the pick moves to the nearest usable model above "
             "it, or to the strongest one left, and a pick weaker than its level needs gets one more effort step, up "
             "to xhigh, or high on Haiku.")
    return svg(w, h, title, label, "".join(out))


def diagram_redo() -> str:
    w, h = 920, 336
    out = [frame(w, h), text(32, 42, "Redo escalation", "ink b", 17),
           text(32, 66, "A call Claude marks as a redo climbs from the run it redoes.", "mut", 13.5)]
    steps = [
        ("1. Agent call", ["Runs at its level:"], "standard, Sonnet, medium", 2),
        ("2. Claude marks it", ["The result fell short:", 'its description gets "Redo:"'], None, -1),
        ("3. Effort first", ["Below high, the same", "model, one step up:"], "Sonnet, high", 2),
        ("4. Then the model", ["From high, the next", "stronger model:"], "complex, Opus, high", 3),
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
    out.append(f'<circle cx="{last}" cy="{track}" r="11" class="halo" fill="none" stroke-width="5"/>'
               f'<circle cx="{last}" cy="{track}" r="9" class="rt"/>')
    note = ["The next model runs at its own level's effort; with no stronger usable model, one more effort step, at",
            "most max. Not a redo: the same call sent again unmarked. Run as named: a redo that names its own model or",
            "effort. Never redone: a workflow stage. A background agent's call counts as finished when the agent stops",
            "with an answer."]
    out.append(lines(32, 264, note, "mut", 13, 20))
    title = "Redo escalation: effort first, then the model"
    label = ("A call runs at its level, for example standard on Sonnet at medium effort. When its result falls short, "
             "Claude marks a redo by starting the call's description with Redo. Below high effort, the redo runs the "
             "same model one effort step up, Sonnet at high. From high, the next redo runs the next stronger model at "
             "its own level's effort, complex on Opus at high.")
    return svg(w, h, title, label, "".join(out))


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
    out.append(lines(52, 276, ["Primary path. Sets each spawn's model and its effort",
                               "on each model request. Reaches teammates and",
                               "workflow stages too."], "mut", 13, 19))
    out.append(card(478, 92, 410, 238, rx=12, cls="faint", sw=0))
    out.append(text(498, 120, "Settings hooks: hooks/hooks.json", "ink b", 14.5))
    out.append(chip_row(498, 138, ["SessionStart", "UserPromptSubmit", "PreToolUse"], 11.5))
    out.append(chip_row(498, 172, ["PostToolUse", "PostToolUseFailure", "SubagentStop"], 11.5))
    out.append(chip_row(498, 206, ["PostModelSwitch", "Stop"], 11.5))
    out.append(card(498, 244, 370, 72, rx=10))
    out.append('<rect x="499" y="256" width="4" height="48" rx="2" class="r4"/>')
    out.append(lines(516, 266, ["Fallback: PreToolUse rewrites the Agent call where",
                                "mods don't load, and stands down for each call",
                                "the mod's tool.call hook marked."], "mut", 12.5, 18))
    arrow = 'class="lnm" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"'
    out.append(f'<path d="M237 188V216M232 211L237 216L242 211" {arrow}/>')
    out.append(f'<path d="M237 330V366H410V398M405 393L410 398L415 393" {arrow}/>')
    out.append(f'<path d="M683 330V366H510V398M505 393L510 398L515 393" {arrow}/>')
    out.append(text(250, 356, "sh model-router.sh mod-*", "mono mut", 11.5))
    out.append(text(696, 356, "sh model-router.sh <event>", "mono mut", 11.5))
    out.append(card(132, 404, 656, 110, rx=12))
    out.append('<rect x="133" y="420" width="4" height="78" rx="2" class="rt"/>')
    out.append(text(156, 434, "scripts/modelrouter: one Python runtime, one router", "ink b", 15))
    out.append(chip_row(156, 450, ["spawn", "router.decide", "ladder", "registry", "calls", "gates"], 11.5))
    out.append(text(156, 500, "One decision procedure and one state store for both paths.", "mut", 13))
    title = "Two control paths, one router"
    label = ("Claude Code engine events (agent.spawn, turn.start, turn.step, turn.complete) reach the mod, "
             "hooks/router.ts. Settings hooks in hooks/hooks.json (SessionStart, UserPromptSubmit, PreToolUse, "
             "PostToolUse, PostToolUseFailure, SubagentStop, PostModelSwitch, Stop) do the bookkeeping and are the "
             "fallback. Both run sh model-router.sh, which calls one Python runtime that decides every spawn.")
    return svg(w, h, title, label, "".join(out))


PRECEDENCE = [
    ("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "pins every subagent's model; the router sets effort only", True),
    ("A model named on the call", "by Claude, a skill's instructions or a workflow script", False),
    ("A per-agent rule", "in ~/.claude/model-router.json or .claude/model-router.json", False),
    ("The agent's own model field", "in its definition's frontmatter", False),
    ("CLAUDE_CODE_SUBAGENT_MODEL", "your default for agents that name no model", True),
    ("The parent's model", "Claude Code's own default when nothing names one", False),
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
            out.append(f'<rect x="32.5" y="{y + 0.5}" width="559" height="51" rx="10" class="rtsoft"/>')
        out.append(f'<circle cx="62" cy="{y + 26}" r="14" class="faint"/>')
        out.append(text(62, y + 31, str(k + 1), "ink b", 14, "middle"))
        out.append(text(88, y + 22, head, ("mono ink b" if is_env else "ink b"), 13 if is_env else 14))
        out.append(text(88, y + 41, detail, "mut", 12.5))
    out.append(f'<rect x="22" y="{top + 5 * rh + 8}" width="5" height="36" rx="2.5" class="rt"/>')
    arrow = 'class="lnm" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"'
    out.append(f'<path d="M592 {top + 5 * rh + 26}H612V{top + 26}H624M619 {top + 21}L624 {top + 26}L619 {top + 31}" '
               f'{arrow}/>')
    cap = ["For 2, 3, 4 and 6, the winner has to", "be usable: inside max_tier, allowed", "by your availableModels and",
           "deniedModels settings, and known to", "work. If it isn't, the next stronger", "usable model runs, else the strongest",
           "usable one left."]
    out.append(card(632, 92, 256, 66 + 19 * len(cap), rx=12))
    out.append(f'<rect x="633" y="108" width="4" height="{34 + 19 * len(cap)}" rx="2" class="rt"/>')
    out.append(text(652, 120, "Then the cap", "ink b", 14.5))
    out.append(lines(652, 146, cap, "mut", 12.5, 19))
    ey = 92 + 66 + 19 * len(cap) + 16
    out.append(card(632, ey, 256, 138, rx=12))
    out.append(f'<rect x="633" y="{ey + 16}" width="4" height="106" rx="2" class="r3"/>')
    out.append(text(652, ey + 28, "And the effort", "ink b", 14.5))
    out.append(lines(652, ey + 54, ["Set from the model's level, unless the", "call, the agent or one of these sets it:"],
                     "mut", 13, 19))
    out.append(lines(652, ey + 98, ["CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_SUBAGENT_EFFORT"], "mono ink", 11.5, 18))
    out.append(text(32, 476, "Example: a call that names sonnet and no effort runs on Sonnet at medium effort, the",
                    "mut", 13))
    out.append(text(32, 496, "standard level's, which is inside the cap. A rule's level raises a model below it.", "mut", 13))
    title = "Who decides the model: the order of authority"
    label = ("Strongest first: CLAUDE_CODE_SUBAGENT_MODEL_FORCE, then a model named on the call, then a per-agent "
             "rule in model-router.json, then the agent's own model field, then CLAUDE_CODE_SUBAGENT_MODEL, then "
             "the parent's model. For all but the two environment variables, the winner must be usable under the "
             "cap and your settings, else the next stronger usable model runs, or the strongest usable one when "
             "nothing stronger is left. Effort follows the model's level unless something already sets it.")
    return svg(w, h, title, label, "".join(out))


def diagram_stop_checks() -> str:
    w, h = 920, 486
    out = [frame(w, h), text(32, 42, "The check before a turn ends", "ink b", 17),
           text(32, 66, "The Stop hook runs one check. It asks once, then lets the turn end.", "mut", 13.5)]
    out.append(card(320, 88, 280, 50, rx=25))
    out.append(text(460, 118, "Claude is about to end the turn", "ink b", 13.5, "middle"))
    arrow = 'class="lnm" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"'
    out.append(f'<path d="M460 138V176M455 171L460 176L465 171" {arrow}/>')
    x, name = 255, "verify_gate"
    when = ["a file in the project was edited, and no", "check command ran after the last edit, and",
            "the transcript can be read."]
    then = ["Asks once for the work's own check: tests", "for code, a recompute for figures, a reread",
            "for a document. Turn it off in /config."]
    out.append(card(x, 182, 410, 252, rx=12))
    out.append(text(x + 20, 212, name, "mono ink b", 14))
    p, _ = pill(x + 26 + width_of(name, 14, True), 194, "on by default", cls="r1", tcls="tag", size=11.5, h=24, pad=10)
    out.append(p)
    out.append(text(x + 20, 244, "Asks when", "mut b", 12))
    out.append(lines(x + 20, 266, when, "ink", 13, 20))
    out.append(f'<path d="M{x + 20} 336H{x + 390}" class="ln" stroke-width="1"/>')
    out.append(text(x + 20, 360, "Then", "mut b", 12))
    out.append(lines(x + 62, 360, then, "ink", 13, 19))
    out.append(text(32, 464, "A workflow, subagent, shell or teammate still running holds the check, since the "
                    "turn is paused, not done.", "mut", 13))
    title = "The check before a turn ends: verify_gate"
    label = ("When Claude is about to end a turn, the Stop hook runs one check. verify_gate, on by default, asks "
             "once for the work's own check when project files changed and nothing checked them.")
    return svg(w, h, title, label, "".join(out))


# ---------------------------------------------------------------- the headline figures in the docs

FIGURE_PAGES = ("README.md", "docs/ARCHITECTURE.md", "docs/FAQ.md", "docs/RELEASE_READINESS.md", "docs/ROADMAP.md",
                "docs/TRACEABILITY.md", "site/llms.txt")
BENCH_SPAN = re.compile(r"<!-- bench:([\w.-]+) -->(.*?)<!-- /bench -->")
SESSION_NAMES = {"claude-opus-5-5": "opus", "sonnet": "sonnet"}
CLASS_WORDS = {"security-review": "security review", "root-cause": "root cause"}
VERDICT_WORDS = {"WIN": "WIN", "LOSE": "LOSE", "NONE": "no difference shown", "REFUSED": "no verdict"}


def class_word(k: str) -> str:
    return CLASS_WORDS.get(k, k)


def words(items: list) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def pct(x: float) -> str:
    return f"{100 * x:.0f}"


def session_figures(name: str, session: dict) -> dict:
    f = {}
    router = session["router"]
    classes = session["classes"]
    roles = {role: cfg for role, cfg in session["roles"].items() if cfg}
    if session.get("plugin_ref"):
        f["commit"] = session["plugin_ref"]
        f["commit_code"] = f"`{session['plugin_ref']}`"  # the span must sit outside the code span
    f["router_code"] = f"`{router}`"  # the span must sit outside the code span
    f["classes"] = str(len(classes))
    f["saturated"] = str(session["saturated"])
    for status in ("WIN", "LOSE", "NONE", "REFUSED"):
        f[f"status.{status}"] = str(sum(c["status"] == status for c in classes))
    # The router is spelled by its role, never by its id; every other configuration by its id, and some by a role too.
    key = lambda cfg: "router" if cfg == router else cfg  # noqa: E731
    for c in classes:
        for cfg, cell in c["configs"].items():
            v = cell["cost_per_solved"]
            f[f"cell.{c['class']}.{key(cfg)}"] = "n/a" if v is None else f"${v:.4f}"
            f[f"cell_solved.{c['class']}.{key(cfg)}"] = f"{cell['solved']} of {cell['runs']}"
    pooled = session["pooled"]
    spelled = {**{key(cfg): cfg for cfg in pooled}, **roles}
    for k, cfg in spelled.items():
        p = pooled[cfg]
        f[f"runs.{k}"] = str(p["runs"])
        f[f"solved.{k}"] = f"{p['solved']} of {p['runs']}"
        if p["solved"]:
            f[f"pooled.{k}"] = f"${p['cost_usd'] / p['solved']:.3f}"
    against = {**{cfg: cfg for cfg in session["against"]}, **{r: roles[r] for r in ("defaults", "best", "strongest")
                                                              if r in roles and roles[r] in session["against"]}}
    for k, cfg in against.items():
        a = session["against"][cfg]
        if cfg in session["pooled_less"]:
            f[f"pooled_less.{k}"] = pct(session["pooled_less"][cfg])
        f[f"below.{k}"] = str(len(a["below"]))
        if a["level"]:
            f[f"level.{k}"] = words([class_word(c) for c in a["level"]])
        if a["less"]:
            f[f"less.{k}.min"], f[f"less.{k}.max"] = pct(a["less"][0]), pct(a["less"][1])
    for cfg, a in session["against"].items():
        if a["best_in"]:
            f[f"best.{cfg}"], f[f"best_count.{cfg}"] = words([class_word(c) for c in a["best_in"]]), str(len(a["best_in"]))
    if session["behind"]:
        (lo, lo_class), (hi, hi_class) = session["behind"]["min"], session["behind"]["max"]
        f["behind.min"], f["behind.min_class"] = pct(lo), class_word(lo_class)
        f["behind.max"], f["behind.max_class"] = pct(hi), class_word(hi_class)
    f["trace"] = "; ".join(class_trace(c, router) for c in classes)
    return {f"{name}.{k}": v for k, v in f.items()}


def class_trace(c: dict, router: str) -> str:
    def usd(v):
        return "n/a" if v is None else f"${v:.3f}"

    def missed(cell):
        return f", {cell['solved']} of {cell['runs']} solved" if cell["solved"] < cell["runs"] else ""

    if router not in c["configs"]:
        raise SystemExit(f"bench results have no {router} runs for {c['class']}")
    cell = c["configs"][router]
    key = lambda cfg: (c["configs"][cfg]["cost_per_solved"] is None, c["configs"][cfg]["cost_per_solved"] or 0, cfg)  # noqa: E731
    def gap(cfg):
        lo, hi = c["differences"][cfg] or (None, None)
        return "" if lo is None or hi is None else f"{lo:+.3f} to {hi:+.3f}"

    base = ", ".join(
        f"`{cfg}` {usd(c['configs'][cfg]['cost_per_solved'])} "
        f"({VERDICT_WORDS[c['verdicts'][cfg]['verdict']]}{', ' + gap(cfg) if gap(cfg) else ''}{missed(c['configs'][cfg])})"
        for cfg in sorted(c["verdicts"], key=key))
    return (f"{class_word(c['class'])} {VERDICT_WORDS[c['status']]} (router {usd(cell['cost_per_solved'])}{missed(cell)}; "
            f"{base})")


def bench_figures(data: dict) -> dict:
    f = {"runs": f"{data['runs']:,}", "usd": f"${data['usd']:,.2f}",
         "dates": " to ".join(dict.fromkeys(data["dates"])),
         "claude": ", ".join(v.replace(" (Claude Code)", "") for v in data["claude_versions"]),
         "per_cell": " or ".join(str(n) for n in data["per_cell"]),
         "tasks": str(data["tasks"]), "tasks_total": str(data["tasks_total"])}
    for s in data["sessions"]:
        if s["model"] in SESSION_NAMES:
            f.update(session_figures(SESSION_NAMES[s["model"]], s))
    return f


def with_figures(path: str, text: str, figures: dict) -> str:
    def one(m):
        if m.group(1) not in figures:
            raise SystemExit(f"{path}: unknown benchmark figure {m.group(1)}")
        return f"<!-- bench:{m.group(1)} -->{figures[m.group(1)]}<!-- /bench -->"
    return BENCH_SPAN.sub(one, text)


# ---------------------------------------------------------------- the Claude Code floor

# The pages that state the current floor; the changelog and research pages keep the floor of their day.
FLOOR_PAGES = ("README.md", "plugins/model-router/README.md", "docs/FAQ.md", "docs/GUIDE.md", "site/llms.txt")
FLOOR = re.compile(r"Claude Code \d+(?:\.\d+)+ or later")


def with_floor(text: str, floor: str) -> str:
    return FLOOR.sub(f"Claude Code {floor} or later", text)


# ---------------------------------------------------------------- the site

FAQ_HTML = ("<!-- FAQ, written by tools/docs_assets.py from docs/FAQ.md. -->", "<!-- End of FAQ. -->")
SITE_URL = re.compile(r"<!-- SITE_URL: (\S+) -->")


def faq_entries(text: str) -> list:
    out = []
    for block in BENCH_SPAN.sub(lambda m: m.group(2), text).split("\n## ")[1:]:
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


def site_faq_html(faq: str) -> str:
    items = [f'<details{" open" if i == 0 else ""}><summary><h3>{esc(q)}</h3></summary><p>{md_html(a)}</p></details>'
             for i, (q, a) in enumerate(faq_entries(faq))]
    return FAQ_HTML[0] + "\n" + "\n".join(items) + "\n" + FAQ_HTML[1]


def between(current: str, marks: tuple, body: str, where: str) -> str:
    before, start, rest = current.partition(marks[0])
    end, after = rest.partition(marks[1])[1:]
    if not (start and end):
        raise SystemExit(f"{where}: no {(marks[1] if start else marks[0])!r} marker")
    return before + body + after


def site_files(faq: str, figures: dict, texts: dict, v: str, day: str, floor: str) -> dict:
    if "site/index.html" not in texts:
        return {}
    page = texts["site/index.html"]
    found = SITE_URL.search(page)
    if not found:
        raise SystemExit("site/index.html: no <!-- SITE_URL: ... --> comment")
    url = found.group(1)
    old = None
    if "site/sitemap.xml" in texts:
        m = re.search(r"<loc>(\S+?)</loc>", texts["site/sitemap.xml"])
        old = m.group(1) if m else None
    if old and old != url:
        head, sep, tail = page.partition("-->")
        page = head + sep + tail.replace(old, url)
    page = between(page, FAQ_HTML, site_faq_html(faq), "site/index.html")
    page = with_floor(badge_alt(page, v), floor)
    page = with_figures("site/index.html", page, figures)
    ladder = "Five levels: " + ", ".join(f"{lv} on {model} at {effort}" for lv, model, effort, *_ in LADDER)
    for pattern, value in ((r'"softwareVersion": "[^"]*"', f'"softwareVersion": "{v}"'),
                           (r'"Five levels: [^"]*"', f'"{ladder}"'),
                           (r'Version [^,]+, released <time datetime="[^"]*">[^<]*</time>',
                            f'Version {v}, released <time datetime="{day}">{day}</time>')):
        page, found = re.subn(pattern, lambda m: value, page)
        if found != 1:
            raise SystemExit(f"site/index.html: expected one match for {pattern}, found {found}")
    return {
        "site/index.html": page,
        "site/sitemap.xml": ('<?xml version="1.0" encoding="UTF-8"?>\n'
                             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                             f"  <url>\n    <loc>{url}</loc>\n  </url>\n</urlset>\n"),
    }


def site_asset_problems(page: str, root: Path = ROOT) -> list:
    """The site's assets are copied from docs/assets when Pages deploys, so each one the page names must be there."""
    return [f"site/index.html: assets/{name} is not in docs/assets"
            for name in sorted(set(re.findall(r"\bassets/([\w.-]+\.\w+)", page)))
            if not (root / "docs" / "assets" / name).exists()]


# ---------------------------------------------------------------- write and check

def badge_alt(page: str, v: str) -> str:
    return re.sub(r'(badge-version\.svg"[^>]*? alt=")version [^"]*"', rf'\g<1>version {v}"', page)


def manifest() -> dict:
    return json.loads((ROOT / "plugins/model-router/.claude-plugin/plugin.json").read_text(encoding="utf-8"))


def version() -> str:
    return manifest()["version"]


def release_date(v: str) -> str:
    m = re.search(rf"^## {re.escape(v)} - (\d{{4}}-\d{{2}}-\d{{2}})$", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
                  re.M)
    if not m:
        raise SystemExit(f"CHANGELOG.md has no '## {v} - <date>' section")
    return m.group(1)


def citation(v: str, day: str) -> str:
    return f"""cff-version: 1.2.0
message: "If you use Model Router in your work, please cite it with these details."
type: software
title: "Model Router"
abstract: >-
  Model Router (model-router) is a Claude Code plugin that holds the Claude
  model and reasoning effort Claude names for every agent Claude Code starts
  (subagents, agent-team teammates and workflow stages) to the user's limits,
  sets the effort nobody named, and runs a call Claude marks as a redo with
  more effort or on a stronger model.
authors:
  - given-names: Cris
    family-names: Nahine
version: "{v}"
date-released: "{day}"
license: Apache-2.0
repository-code: "{REPO}"
url: "{REPO}"
keywords:
  - Claude Code
  - Claude Code plugin
  - model routing
  - subagents
  - cost per solved task
"""


@dataclasses.dataclass(frozen=True)
class Snapshot:
    """Everything `build` reads, taken once: the plugin's version, its release date and its Claude Code floor, the
    shipped ladder, the text of every page it rewrites, the page index and the benchmark figures from
    `analyze.figures`."""
    version: str
    released: str
    claude_code: str
    ladder: dict
    texts: dict
    bench: dict
    pages: list = dataclasses.field(default_factory=lambda: list(PAGES))


def snapshot() -> Snapshot:
    sys.path.insert(0, str(ROOT / "bench"))
    import analyze
    v = version()
    wanted = [*(p[0] for p in PAGES), "README.md", "plugins/model-router/README.md", "site/index.html",
              "site/sitemap.xml", "site/llms.txt"]
    return Snapshot(
        version=v, released=release_date(v), claude_code=manifest()["engines"]["claude-code"].removeprefix(">="),
        ladder=json.loads((ROOT / "plugins/model-router/config/models.json").read_text(encoding="utf-8")),
        texts={path: (ROOT / path).read_text(encoding="utf-8") for path in wanted if (ROOT / path).exists()},
        bench=analyze.figures(analyze.load(analyze.default_paths()), legacy=True))


def build(snap: Snapshot) -> dict:
    """The files to write, by path, for a snapshot of the inputs. Reads no file."""
    LAYOUT.clear()
    check_ladder_against_registry(snap.ladder)
    figures = bench_figures(snap.bench)
    v = snap.version
    files = {}
    assets = badge_assets(v, snap.claude_code)
    assets["ladder.svg"] = diagram_ladder()
    assets["redo-escalation.svg"] = diagram_redo()
    assets["two-paths.svg"] = diagram_two_paths()
    assets["precedence.svg"] = diagram_precedence()
    assets["stop-checks.svg"] = diagram_stop_checks()
    for name, body in assets.items():
        files[f"docs/assets/{name}"] = body
    for path, *_ in snap.pages:
        if path in snap.texts:
            files[path] = with_head(path, snap.texts[path], snap.pages)
            if path == "docs/README.md":
                files[path] = with_cards(files[path], snap.pages)
    files["README.md"] = badge_alt(snap.texts["README.md"], v)
    for path in FLOOR_PAGES:
        if path in files or path in snap.texts:
            files[path] = with_floor(files.get(path, snap.texts[path]), snap.claude_code)
    for path in FIGURE_PAGES:
        if path in files:
            files[path] = with_figures(path, files[path], figures)
    files["CITATION.cff"] = citation(v, snap.released)
    files.update(site_files(files.get("docs/FAQ.md", ""), figures, snap.texts, v, snap.released, snap.claude_code))
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


def sync(files: dict, check: bool, root: Path = ROOT) -> list:
    """Write `files` under `root`, or with `check` report each one that differs."""
    problems = []
    for path, body in files.items():
        if path.endswith(".svg"):
            problems += problems_in_svg(path, body)
        target = root / path
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current != body:
            if check:
                problems.append(f"{path}: out of date, run python3 tools/docs_assets.py")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body.encode("utf-8"))
    return problems


def main(argv: list) -> int:
    check = "--check" in argv
    files = build(snapshot())
    problems = palette_problems() + LAYOUT + sync(files, check)
    problems += site_asset_problems(files.get("site/index.html", ""))
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
