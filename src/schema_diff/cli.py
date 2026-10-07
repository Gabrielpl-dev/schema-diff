"""Command line interface for the schema diff tool."""

from __future__ import annotations

import json
import os
import re
import sys

from . import __version__
from .differ import Change, diff_schemas
from .errors import CLIError, SchemaDiffError
from .migration import MigrationBuilder
from .parser import (
    parse_schema,
    validate_references,
    validate_unique_index_names,
)

VALID_DIALECTS = ("postgres", "mysql")

_USAGE = f"""schema-diff — SQL schema diff (v{__version__})

Usage:
  ./app diff ANTIGO.sql NOVO.sql [--dialect postgres|mysql] [--json]
  ./app --version
  ./app --help

Options:
  --dialect postgres|mysql  dialect of the generated migration (default: postgres)
  --json                    emit pure JSON on stdout
"""

# Tokens that only make sense in MySQL and therefore signal an ambiguous input
# when no dialect was requested.
_AMBIGUOUS_PATTERN = re.compile(
    r"`"
    r"|\bauto_increment\b"
    r"|\bunsigned\b"
    r"|\bengine\s*="
    r"|\btinyint\b"
    r"|\bmediumint\b"
    r"|\bmediumtext\b"
    r"|\blongtext\b"
    r"|\bdatetime\b",
    re.IGNORECASE,
)

_AMBIGUOUS_WARNING = (
    "warning: dialect not specified; ambiguous syntax detected, defaulting to postgres"
)


class Options:
    def __init__(self):
        self.old_path = ""
        self.new_path = ""
        self.dialect = "postgres"
        self.dialect_explicit = False
        self.json = False
        self.help_requested = False


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return _run(argv)
    except SchemaDiffError as exc:
        print(f"error: {exc.message()}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - defensive: never crash
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _run(argv: list[str]) -> int:
    if not argv:
        raise CLIError("missing subcommand")
    first = argv[0]
    if first == "--version":
        print(f"schema-diff {__version__}")
        return 0
    if first in ("--help", "-h"):
        sys.stdout.write(_USAGE)
        return 0
    if first != "diff":
        raise CLIError(f"unknown subcommand: {first}")
    options = _parse_diff_args(argv[1:])
    if options.help_requested:
        return 0
    return _run_diff(options)


def _parse_diff_args(rest: list[str]) -> Options:
    options = Options()
    files: list[str] = []
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--json":
            options.json = True
        elif arg == "--dialect":
            i += 1
            if i >= len(rest):
                raise CLIError("missing value for --dialect")
            _set_dialect(options, rest[i])
        elif arg.startswith("--dialect="):
            _set_dialect(options, arg.split("=", 1)[1])
        elif arg in ("--help", "-h"):
            sys.stdout.write(_USAGE)
            options.help_requested = True
            return options
        elif arg.startswith("-") and arg != "-":
            raise CLIError(f"unknown option: {arg}")
        else:
            files.append(arg)
        i += 1
    if len(files) < 2:
        raise CLIError("missing schema file arguments")
    if len(files) > 2:
        raise CLIError(f"unexpected argument: {files[2]}")
    options.old_path, options.new_path = files[0], files[1]
    return options


def _set_dialect(options: Options, value: str) -> None:
    if value not in VALID_DIALECTS:
        raise CLIError(f"invalid dialect: {value}")
    options.dialect = value
    options.dialect_explicit = True


def _read_file(path: str) -> str:
    if not os.path.exists(path):
        raise CLIError(f"file not found: {path}")
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise CLIError(f"cannot read file {path}: {exc}") from exc


def _run_diff(options: Options) -> int:
    old_text = _read_file(options.old_path)
    new_text = _read_file(options.new_path)

    warnings: list[str] = []
    if not options.dialect_explicit:
        if _AMBIGUOUS_PATTERN.search(old_text) or _AMBIGUOUS_PATTERN.search(new_text):
            warnings.append(_AMBIGUOUS_WARNING)

    old_schema = parse_schema(old_text)
    new_schema = parse_schema(new_text)
    validate_unique_index_names([old_schema, new_schema])
    validate_references([old_schema, new_schema])

    changes = diff_schemas(old_schema, new_schema)
    migration = MigrationBuilder(options.dialect, old_schema, new_schema, changes).build()

    breaking = any(change.breaking for change in changes)
    if not changes:
        exit_code = 0
    elif breaking:
        exit_code = 3
    else:
        exit_code = 1

    if options.json:
        _emit_json(options.dialect, changes, migration, warnings)
    else:
        _emit_text(options.dialect, changes, migration, warnings)

    return exit_code


def _emit_text(
    dialect: str, changes: list[Change], migration: list[str], warnings: list[str]
) -> None:
    for warning in warnings:
        print(warning, file=sys.stderr)
    if not changes:
        print("no differences")
        return
    for change in changes:
        print(f"{change.sigil} {change.kind} {change.object}")
    breaking_count = sum(1 for change in changes if change.breaking)
    if breaking_count:
        print(f"breaking: {breaking_count} change(s)")
    print()
    print(f"-- migration ({dialect})")
    for command in migration:
        print(command)


def _emit_json(
    dialect: str, changes: list[Change], migration: list[str], warnings: list[str]
) -> None:
    for warning in warnings:
        print(warning, file=sys.stderr)
    added = sum(1 for change in changes if change.bucket == "added")
    removed = sum(1 for change in changes if change.bucket == "removed")
    changed = sum(1 for change in changes if change.bucket == "changed")
    payload = {
        "version": __version__,
        "dialect": dialect,
        "breaking": any(change.breaking for change in changes),
        "summary": {"added": added, "removed": removed, "changed": changed},
        "changes": [change.to_json() for change in changes],
        "migration": migration,
        "warnings": warnings,
    }
    print(json.dumps(payload, indent=2))
