#!/bin/sh
# Entry point for the Database Schema Diff tool.
# Delegates to the Python implementation under src/.
set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$DIR/src/main.py" "$@"
