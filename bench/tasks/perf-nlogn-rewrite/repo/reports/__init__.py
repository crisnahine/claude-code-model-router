"""The monthly sales report. `render(sales_path, regions_path)` returns it as text; `python3 -m reports` prints it."""
from .errors import ReportError
from .render import render

__all__ = ["ReportError", "render"]
