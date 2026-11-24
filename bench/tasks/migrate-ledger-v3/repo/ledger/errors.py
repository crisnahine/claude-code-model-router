class FormatError(ValueError):
    """A file that doesn't follow its format."""

    def __init__(self, message, line=None):
        super().__init__(f"line {line}: {message}" if line is not None else message)
        self.line = line


class MigrationError(ValueError):
    """A v2 line the migration can't turn into a v3 record."""

    def __init__(self, message, line=None):
        super().__init__(f"line {line}: {message}" if line is not None else message)
        self.line = line
