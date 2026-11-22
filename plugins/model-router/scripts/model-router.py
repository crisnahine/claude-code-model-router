#!/usr/bin/env python3
"""Entry point for the Model Router plugin's hooks and skills. See modelrouter/cli.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from modelrouter.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
