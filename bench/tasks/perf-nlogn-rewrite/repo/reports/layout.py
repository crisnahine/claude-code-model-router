"""Plain-text tables: each column as wide as its widest cell, two spaces between columns, indented two spaces."""

INDENT = "  "
GAP = "  "


def table(rows, align):
    """rows of cells (anything str() can show); align is one "l" or "r" per column. No trailing spaces."""
    cells = [[str(c) for c in row] for row in rows]
    if not cells:
        return []
    widths = [max(len(row[n]) for row in cells) for n in range(len(align))]
    lines = []
    for row in cells:
        parts = [c.ljust(w) if a == "l" else c.rjust(w) for c, w, a in zip(row, widths, align)]
        lines.append((INDENT + GAP.join(parts)).rstrip())
    return lines


def heading(text):
    return f"== {text} =="
