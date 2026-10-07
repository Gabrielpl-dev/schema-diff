#!/bin/sh
# Lint the implementation and tests.
#
# Prefers ruff (configured in pyproject.toml).  When ruff is not installed the
# script falls back to a standard-library byte-compilation check so the lint
# step always performs a meaningful verification.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

if command -v ruff >/dev/null 2>&1; then
    exec ruff check src tests
fi

echo "ruff not found; running python byte-compilation fallback" >&2
exec python3 -m compileall -q src tests
