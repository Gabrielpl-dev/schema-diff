"""Error types for the schema diff tool."""


class SchemaDiffError(Exception):
    """Base class for controlled errors (all map to exit code 2)."""

    exit_code = 2

    def message(self) -> str:
        return str(self)


class CLIError(SchemaDiffError):
    """Invalid command line usage."""


class IOErrorError(SchemaDiffError):
    """A schema file could not be read."""


class ParseError(SchemaDiffError):
    """Unsupported or malformed SQL construction."""

    def __init__(self, message: str, line: int | None = None, snippet: str | None = None):
        super().__init__(message)
        self.raw_message = message
        self.line = line
        self.snippet = snippet

    def message(self) -> str:
        if self.line is not None and self.snippet is not None:
            return f"unsupported statement at line {self.line}: {self.snippet}"
        return self.raw_message


class ValidationError(SchemaDiffError):
    """A schema violates a structural rule of the subset."""
