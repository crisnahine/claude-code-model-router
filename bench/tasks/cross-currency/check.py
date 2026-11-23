"""Pass when every formatter takes the currency through: symbol, decimals, and USD unchanged, and when it reads
them from pricing/symbols.py: on a copy of the work dir with other tables the output follows them."""
import ast
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

WORK = Path(sys.argv[1])
passcheck.use_work(WORK)

with passcheck.agent_code():
    fmt = importlib.import_module("pricing.money").format_price
    Invoice = importlib.import_module("billing.invoice").Invoice
    summary_line = importlib.import_module("reports.summary").summary_line
    got = [fmt(1234), fmt(5), fmt(1234, "EUR"), fmt(1234, currency="GBP"), fmt(1234, "JPY"),
           Invoice([("a", 1000), ("b", 234)], "EUR").total_line(), Invoice([("a", 1234)]).total_line(),
           Invoice([("a", 500)], "JPY").total_line(), summary_line("Q1", 1234, "GBP"), summary_line("Q1", 1234)]
WANT = ["$12.34", "$0.05", "€12.34", "£12.34", "¥1234", "Total: €12.34", "Total: $12.34", "Total: ¥500",
        "Q1: £12.34", "Q1: $12.34"]
if got != WANT:
    passcheck.finish(False, passcheck.differs(got, WANT))
TABLES = {"SYMBOLS": {"USD": "$", "CHF": "Fr", "XAF": "F", "SEK": "kr"}, "DECIMALS": {"CHF": 0, "XAF": 0}}
# Also appended and swapped in place, so a table the run's symbols.py builds some other way follows too.
OTHER_TABLES = "".join(f"\n{name}.clear()\n{name}.update({table!r})" for name, table in TABLES.items()) + "\n"


def with_tables(source: str) -> str:
    """symbols.py with each top-level SYMBOLS or DECIMALS assignment given the other table, so whatever the run's
    code derives from the tables at import, a set of zero-decimal codes say, derives from the other ones."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    def at(row: int, col: int) -> int:  # ast columns count UTF-8 bytes, and the shipped symbols are multibyte
        return starts[row - 1] + len(lines[row - 1].encode("utf-8")[:col].decode("utf-8"))

    spans = []
    for node in ast.parse(source).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        names = [t.id for t in targets if isinstance(t, ast.Name) and t.id in TABLES]
        if names and node.value is not None:
            value = node.value
            spans.append((at(value.lineno, value.col_offset), at(value.end_lineno, value.end_col_offset), names[0]))
    for begin, end, name in reversed(spans):
        source = source[:begin] + repr(TABLES[name]) + source[end:]
    return source


FOLLOWS = ["Fr1234", "F1234", "kr12.34", "Total: kr12.34", "Q: F1234", "$12.34"]
with tempfile.TemporaryDirectory() as tmp:
    copy = Path(tmp) / "work"
    shutil.copytree(WORK, copy, ignore=shutil.ignore_patterns("__pycache__"))
    symbols = copy / "pricing" / "symbols.py"
    with passcheck.agent_code():  # the run's own file: one it deleted or left unparsable fails the task
        source = with_tables(symbols.read_text(encoding="utf-8"))
    symbols.write_text(source + OTHER_TABLES, encoding="utf-8")
    seen = passcheck.probe(copy, Path(__file__).resolve().parent / "probe.py", None)
passcheck.finish(seen == FOLLOWS, "with other tables in pricing/symbols.py the output is "
                 f"{seen!r}, expected {FOLLOWS!r}: the symbols and decimals must come from SYMBOLS and DECIMALS")
