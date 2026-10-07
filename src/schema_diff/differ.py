"""Schema comparison, change detection and breaking-change classification."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .model import Column, Constraint, Schema, Table

_CATEGORY_RANK = {"table": 0, "column": 1, "constraint": 2, "index": 3}


@dataclass
class Change:
    kind: str
    table: str
    object: str
    breaking: bool
    before: dict | None = None
    after: dict | None = None

    @property
    def category(self) -> str:
        return self.kind.split(".", 1)[0]

    @property
    def action(self) -> str:
        return self.kind.split(".", 1)[1]

    @property
    def sigil(self) -> str:
        return {"added": "+", "removed": "-", "changed": "~"}.get(self.action, "~")

    @property
    def bucket(self) -> str:
        """Summary bucket of the change (``added`` / ``removed`` / ``changed``).

        Kinds may carry a compound action (``column.default_added``,
        ``column.type_changed``), so the bucket is decided by the kind suffix
        rather than by the whole action after the first dot.
        """
        for suffix in ("added", "removed", "changed"):
            if self.kind.endswith(suffix):
                return suffix
        return "changed"

    def to_json(self) -> dict:
        return {
            "kind": self.kind,
            "table": self.table,
            "object": self.object,
            "breaking": self.breaking,
            "before": self.before,
            "after": self.after,
        }


def diff_schemas(old: Schema, new: Schema) -> list[Change]:
    changes: list[Change] = []
    old_tables = old.tables
    new_tables = new.tables

    for name in sorted(set(old_tables) | set(new_tables)):
        old_table = old_tables.get(name)
        new_table = new_tables.get(name)
        if old_table is None:
            changes.append(_table_added(new_table))
            for constraint in new_table.constraints.values():
                # Inline constraints are column attributes, not separate
                # objects, for added tables (AC-3 / AC-14).
                if not constraint.inline:
                    changes.append(_constraint_added(constraint))
            continue
        if new_table is None:
            changes.append(_table_removed(old_table))
            continue
        changes.extend(_diff_common_table(old_table, new_table))

    changes.extend(_diff_indexes(old.indexes, new.indexes))

    changes.sort(key=lambda c: (c.table, _CATEGORY_RANK.get(c.category, 9), c.object, c.kind))
    return changes


def _table_added(table: Table) -> Change:
    return Change(
        kind="table.added",
        table=table.name,
        object=table.name,
        breaking=False,
        before=None,
        after={"columns": list(table.columns.keys())},
    )


def _table_removed(table: Table) -> Change:
    return Change(
        kind="table.removed",
        table=table.name,
        object=table.name,
        breaking=True,
        before={"columns": list(table.columns.keys())},
        after=None,
    )


def _constraint_added(constraint: Constraint) -> Change:
    return Change(
        kind="constraint.added",
        table=constraint.table,
        object=f"{constraint.table}.{constraint.name}",
        breaking=False,
        before=None,
        after=constraint.signature(),
    )


def _diff_common_table(old_table: Table, new_table: Table) -> list[Change]:
    changes: list[Change] = []
    changes.extend(_diff_columns(old_table, new_table))
    changes.extend(_diff_constraints(old_table, new_table))
    return changes


def _diff_columns(old_table: Table, new_table: Table) -> list[Change]:
    changes: list[Change] = []
    for name in sorted(set(old_table.columns) | set(new_table.columns)):
        old_col = old_table.columns.get(name)
        new_col = new_table.columns.get(name)
        obj = f"{old_table.name}.{name}"
        if old_col is None:
            breaking = new_col.not_null and new_col.default is None
            changes.append(
                Change(
                    "column.added", old_table.name, obj, breaking,
                    before=None, after=new_col.signature(),
                )
            )
            continue
        if new_col is None:
            changes.append(
                Change(
                    "column.removed", old_table.name, obj, True,
                    before=old_col.signature(), after=None,
                )
            )
            continue
        changes.extend(_diff_column(old_table.name, obj, old_col, new_col))
    return changes


def _diff_column(table: str, obj: str, old_col: Column, new_col: Column) -> list[Change]:
    changes: list[Change] = []
    if old_col.type != new_col.type:
        breaking = not is_widening(old_col.type, new_col.type)
        changes.append(
            Change(
                "column.type_changed", table, obj, breaking,
                before={"type": old_col.type}, after={"type": new_col.type},
            )
        )
    if old_col.not_null != new_col.not_null:
        breaking = False
        if new_col.not_null and not old_col.not_null:
            breaking = new_col.default is None  # B7 / B8
        changes.append(
            Change(
                "column.nullability_changed", table, obj, breaking,
                before={"not_null": old_col.not_null},
                after={"not_null": new_col.not_null},
            )
        )
    if old_col.default != new_col.default:
        if old_col.default is None:
            changes.append(
                Change(
                    "column.default_added", table, obj, False,
                    before={"default": None}, after={"default": new_col.default},
                )
            )
        elif new_col.default is None:
            changes.append(
                Change(
                    "column.default_removed", table, obj, True,
                    before={"default": old_col.default}, after={"default": None},
                )
            )
        else:
            changes.append(
                Change(
                    "column.default_changed", table, obj, False,
                    before={"default": old_col.default}, after={"default": new_col.default},
                )
            )
    return changes


def _diff_constraints(old_table: Table, new_table: Table) -> list[Change]:
    changes: list[Change] = []
    old_names = set(old_table.constraints)
    new_names = set(new_table.constraints)
    for name in sorted(old_names | new_names):
        old_c = old_table.constraints.get(name)
        new_c = new_table.constraints.get(name)
        if old_c is None:
            changes.append(_constraint_added(new_c))
        elif new_c is None:
            changes.append(_constraint_removed(old_c))
        elif old_c.signature() != new_c.signature():
            changes.append(_constraint_removed(old_c))
            changes.append(_constraint_added(new_c))
    return changes


def _constraint_removed(constraint: Constraint) -> Change:
    breaking = constraint.kind in ("primary_key", "unique")  # B13 / B14
    return Change(
        kind="constraint.removed",
        table=constraint.table,
        object=f"{constraint.table}.{constraint.name}",
        breaking=breaking,
        before=constraint.signature(),
        after=None,
    )


def _diff_indexes(old_indexes, new_indexes) -> list[Change]:
    changes: list[Change] = []
    for name in sorted(set(old_indexes) | set(new_indexes)):
        old_i = old_indexes.get(name)
        new_i = new_indexes.get(name)
        if old_i is None:
            changes.append(
                Change(
                    "index.added", new_i.table, new_i.name, False,
                    before=None, after=new_i.signature(),
                )
            )
        elif new_i is None:
            changes.append(
                Change(
                    "index.removed", old_i.table, old_i.name, False,
                    before=old_i.signature(), after=None,
                )
            )
        elif old_i.signature() != new_i.signature():
            changes.append(
                Change(
                    "index.changed", new_i.table, new_i.name, False,
                    before=old_i.signature(), after=new_i.signature(),
                )
            )
    return changes


# ---------------------------------------------------------------------------
# Type widening / narrowing (spec 6.1)
# ---------------------------------------------------------------------------

_INT_RANK = {"smallint": 1, "int": 2, "bigint": 3}
_TEMPORAL_RANK = {"date": 1, "timestamp": 2, "timestamptz": 3}
_VARCHAR_RE = re.compile(r"^varchar\((\d+)\)$")
_NUMERIC_RE = re.compile(r"^numeric\((\d+),(\d+)\)$")


def _describe(type_name: str):
    if type_name in _INT_RANK:
        return ("int", _INT_RANK[type_name])
    match = _VARCHAR_RE.match(type_name)
    if match:
        return ("varchar", int(match.group(1)))
    if type_name == "text":
        return ("text",)
    match = _NUMERIC_RE.match(type_name)
    if match:
        return ("numeric", int(match.group(1)), int(match.group(2)))
    if type_name in _TEMPORAL_RANK:
        return ("temporal", _TEMPORAL_RANK[type_name])
    return (type_name,)


def is_widening(old_type: str, new_type: str) -> bool:
    """True when changing ``old_type`` to ``new_type`` is a safe widening."""
    old = _describe(old_type)
    new = _describe(new_type)
    if old[0] != new[0]:
        # varchar <-> text is a cross-family pair with a defined direction.
        if old[0] == "varchar" and new[0] == "text":
            return True
        if old[0] == "text" and new[0] == "varchar":
            return False
        return False
    family = old[0]
    if family == "int" or family == "temporal":
        return new[1] > old[1]
    if family == "varchar":
        return new[1] > old[1]
    if family == "numeric":
        return new[1] > old[1] and new[2] >= old[2]
    return False
