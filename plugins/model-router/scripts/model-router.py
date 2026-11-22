#!/usr/bin/env python3
"""Entry point for the Model Router plugin's hooks and skills. See modelrouter/cli.py."""
import sys

# Too old: exit 87 before reading stdin or importing anything, so model-router.sh can try the
# next interpreter. Python compiles the whole file first, so keep all of it to syntax any
# Python can parse: no f-strings, annotations or walrus, or an old one fails with 1 instead.
if sys.version_info < (3, 9):
    sys.exit(87)

from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from modelrouter.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
