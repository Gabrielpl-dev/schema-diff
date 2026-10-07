#!/usr/bin/env python3
"""Entry point invoked by the ``./app`` wrapper."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from schema_diff.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
