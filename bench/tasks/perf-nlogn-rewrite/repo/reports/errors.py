class ReportError(Exception):
    """An input the report can't be built from at all (as opposed to a bad row, which is counted and skipped)."""
