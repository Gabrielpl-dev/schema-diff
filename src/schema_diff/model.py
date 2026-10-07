"""Normalized schema model."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Column:
    name: str
    type: str                 # canonical lower-case form (4.2)
    not_null: bool = False
    default: str | None = None  # normalized default expression
    # Inline PRIMARY KEY / UNIQUE flags collected while parsing, converted into
    # synthetic constraints once the table name is known.
    pending_constraints: list[str] = field(default_factory=list)

    def signature(self) -> dict:
        return {"type": self.type, "not_null": self.not_null, "default": self.default}


@dataclass
class Constraint:
    name: str
    kind: str                 # primary_key | unique | foreign_key
    table: str
    columns: list[str] = field(default_factory=list)
    references: dict | None = None  # {"table": str, "columns": [str]} for FK
    # True when the constraint was written as a column attribute (inline
    # PRIMARY KEY / UNIQUE) rather than as a table-level element.  This does not
    # affect identity (3.4) but controls reporting for added tables.
    inline: bool = False

    def signature(self) -> dict:
        return {
            "kind": self.kind,
            "columns": list(self.columns),
            "references": self.references,
        }


@dataclass
class Index:
    name: str
    table: str
    unique: bool
    columns: list[str] = field(default_factory=list)

    def signature(self) -> dict:
        return {"unique": self.unique, "columns": list(self.columns)}


@dataclass
class Table:
    name: str
    columns: dict[str, Column] = field(default_factory=dict)
    constraints: dict[str, Constraint] = field(default_factory=dict)


@dataclass
class Schema:
    tables: dict[str, Table] = field(default_factory=dict)
    indexes: dict[str, Index] = field(default_factory=dict)
