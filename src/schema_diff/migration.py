"""Migration SQL generation (spec section 5.3 / 5.4)."""

from __future__ import annotations

from .differ import Change
from .model import Column, Constraint, Schema, Table


def render_type(type_name: str) -> str:
    """Render a canonical type for SQL output (upper case, e.g. VARCHAR(100))."""
    if type_name.startswith("varchar("):
        return "VARCHAR" + type_name[len("varchar"):]
    if type_name.startswith("numeric("):
        return "NUMERIC" + type_name[len("numeric"):]
    return type_name.upper()


def render_default(expr: str) -> str:
    if expr == "nextval":
        return "nextval()"
    return expr


def render_column_fragment(column: Column) -> str:
    type_name = render_type(column.type)
    if column.serial:
        type_name = "BIGSERIAL" if column.type == "bigint" else "SERIAL"
    parts = [column.name, type_name]
    if column.not_null:
        parts.append("NOT NULL")
    if column.default is not None and not (column.serial and column.default == "nextval"):
        parts.extend(["DEFAULT", render_default(column.default)])
    return " ".join(parts)


def render_constraint_fragment(constraint: Constraint) -> str:
    if constraint.kind == "primary_key":
        return f"PRIMARY KEY ({', '.join(constraint.columns)})"
    if constraint.kind == "unique":
        return f"UNIQUE ({', '.join(constraint.columns)})"
    if constraint.kind == "foreign_key":
        ref = constraint.references or {}
        ref_cols = ", ".join(ref.get("columns", []))
        return (
            f"FOREIGN KEY ({', '.join(constraint.columns)}) "
            f"REFERENCES {ref.get('table', '')} ({ref_cols})"
        )
    raise ValueError(f"unknown constraint kind {constraint.kind}")


def _table_create_sql(table: Table) -> str:
    lines = [f"  {render_column_fragment(column)}" for column in table.columns.values()]
    for constraint in table.constraints.values():
        prefix = f"CONSTRAINT {constraint.name} " if constraint.named else ""
        lines.append("  " + prefix + render_constraint_fragment(constraint))
    body = ",\n".join(lines)
    return f"CREATE TABLE {table.name} (\n{body}\n);"


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _postgres_drop_constraint(constraint: Constraint) -> str:
    if constraint.named:
        return f"ALTER TABLE {constraint.table} DROP CONSTRAINT {constraint.name};"
    # Resolve the actual name at execution time: PostgreSQL can truncate names
    # and append collision suffixes, so guessing its default name is unsafe.
    table = _sql_literal(constraint.table)
    columns = ", ".join(_sql_literal(c) for c in constraint.columns)
    kind = {"primary_key": "p", "unique": "u", "foreign_key": "f"}[constraint.kind]
    reference = ""
    if constraint.references:
        ref = constraint.references
        ref_columns = ", ".join(_sql_literal(c) for c in ref["columns"])
        reference = (
            f" AND confrelid = {_sql_literal(ref['table'])}::regclass"
            " AND confkey = ARRAY(SELECT attnum FROM unnest("
            f"ARRAY[{ref_columns}]::text[]) WITH ORDINALITY AS cols(name, position) "
            "JOIN pg_attribute ON attrelid = confrelid AND attname = cols.name "
            "ORDER BY position)"
        )
    return (
        "DO $$\nDECLARE constraint_name text;\nBEGIN\n"
        "  SELECT conname INTO STRICT constraint_name FROM pg_constraint "
        f"WHERE conrelid = {table}::regclass AND contype = '{kind}' "
        "AND conkey = ARRAY(SELECT attnum FROM unnest("
        f"ARRAY[{columns}]::text[]) WITH ORDINALITY AS cols(name, position) "
        "JOIN pg_attribute ON attrelid = conrelid AND attname = cols.name "
        f"ORDER BY position){reference};\n"
        "  EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', "
        f"{table}::regclass, constraint_name);\nEND $$;"
    )


def _dependencies(names: set[str], get_constraints) -> dict[str, set[str]]:
    deps: dict[str, set[str]] = {name: set() for name in names}
    for name in names:
        for constraint in get_constraints(name):
            if constraint.kind != "foreign_key" or constraint.references is None:
                continue
            ref = constraint.references["table"]
            if ref in names and ref != name:
                deps[name].add(ref)
    return deps


def _topo_order(names: set[str], deps: dict[str, set[str]]) -> list[str]:
    """Referenced tables before referencing tables (alphabetical tie-break)."""
    remaining = dict(deps)
    order: list[str] = []
    while remaining:
        ready = sorted(n for n, d in remaining.items() if not (d & set(remaining)))
        if not ready:  # cycle: fall back to alphabetical to stay deterministic
            ready = sorted(remaining)
        for name in ready:
            order.append(name)
            del remaining[name]
    return order


class MigrationBuilder:
    def __init__(self, dialect: str, old: Schema, new: Schema, changes: list[Change]):
        self.dialect = dialect
        self.old = old
        self.new = new
        self.changes = changes

    def build(self) -> list[str]:
        if not self.changes:
            return []
        by_kind: dict[str, list[Change]] = {}
        for change in self.changes:
            by_kind.setdefault(change.kind, []).append(change)

        commands: list[str] = []
        commands.extend(self._phase_drop_foreign_keys(by_kind.get("constraint.removed", [])))
        commands.extend(
            self._phase_drop_keys_and_indexes(
                by_kind.get("constraint.removed", []), by_kind.get("index.removed", []),
                by_kind.get("index.changed", []),
            )
        )
        commands.extend(self._phase_drop_tables(by_kind.get("table.removed", [])))
        commands.extend(self._phase_create_tables(by_kind.get("table.added", [])))
        commands.extend(self._phase_add_columns(by_kind.get("column.added", [])))
        commands.extend(self._phase_alter_columns(by_kind))
        commands.extend(self._phase_drop_columns(by_kind.get("column.removed", [])))
        added_constraints = by_kind.get("constraint.added", [])
        for kind in ("primary_key", "unique", "foreign_key"):
            commands.extend(self._phase_add_constraints(added_constraints, kind))
        commands.extend(
            self._phase_create_indexes(
                by_kind.get("index.added", []), by_kind.get("index.changed", [])
            )
        )
        return commands

    # -- phases ------------------------------------------------------------

    def _phase_drop_foreign_keys(self, removed: list[Change]) -> list[str]:
        commands = []
        for change in removed:
            constraint = self.old.tables[change.table].constraints[change.object.split(".", 1)[1]]
            if constraint.kind != "foreign_key":
                continue
            if change.table not in self.new.tables:
                continue
            if self.dialect == "mysql":
                commands.append(f"ALTER TABLE {change.table} DROP FOREIGN KEY {constraint.name};")
            else:
                commands.append(_postgres_drop_constraint(constraint))
        return sorted(commands)

    def _phase_drop_keys_and_indexes(
        self, removed: list[Change], index_removed: list[Change], index_changed: list[Change]
    ) -> list[str]:
        commands = []
        for change in removed:
            name = change.object.split(".", 1)[1]
            constraint = self.old.tables[change.table].constraints[name]
            if constraint.kind not in ("primary_key", "unique"):
                continue
            if change.table not in self.new.tables:
                continue
            if self.dialect == "mysql":
                if constraint.kind == "primary_key":
                    commands.append(f"ALTER TABLE {change.table} DROP PRIMARY KEY;")
                else:
                    commands.append(f"ALTER TABLE {change.table} DROP INDEX {constraint.name};")
            else:
                commands.append(_postgres_drop_constraint(constraint))
        for change in list(index_removed) + list(index_changed):
            index = self.old.indexes[change.object]
            if index.table not in self.new.tables:
                continue
            if self.dialect == "mysql":
                commands.append(f"DROP INDEX {index.name} ON {index.table};")
            else:
                commands.append(f"DROP INDEX {index.name};")
        return sorted(commands)

    def _phase_drop_tables(self, removed: list[Change]) -> list[str]:
        names = {change.object for change in removed}
        deps = _dependencies(names, lambda n: self.old.tables[n].constraints.values())
        order = _topo_order(names, deps)
        return [f"DROP TABLE {name};" for name in reversed(order)]

    def _phase_create_tables(self, added: list[Change]) -> list[str]:
        names = {change.object for change in added}
        deps = _dependencies(names, lambda n: self.new.tables[n].constraints.values())
        order = _topo_order(names, deps)
        return [_table_create_sql(self.new.tables[name]) for name in order]

    def _phase_add_columns(self, added: list[Change]) -> list[str]:
        by_table: dict[str, list[Change]] = {}
        for change in added:
            by_table.setdefault(change.table, []).append(change)
        commands = []
        for table_name, table_changes in by_table.items():
            table = self.new.tables[table_name]
            ordered_columns = list(table.columns.keys())
            table_changes.sort(key=lambda c: ordered_columns.index(c.object.split(".", 1)[1]))
            for change in table_changes:
                column = table.columns[change.object.split(".", 1)[1]]
                commands.append(
                    f"ALTER TABLE {table_name} ADD COLUMN {render_column_fragment(column)};"
                )
        return commands

    def _altered_columns(self) -> dict[tuple[str, str], set[str]]:
        kinds = {
            "column.type_changed": "type",
            "column.nullability_changed": "nullability",
            "column.default_added": "default",
            "column.default_removed": "default",
            "column.default_changed": "default",
        }
        result: dict[tuple[str, str], set[str]] = {}
        for change in self.changes:
            if change.kind in kinds:
                key = (change.table, change.object.split(".", 1)[1])
                result.setdefault(key, set()).add(kinds[change.kind])
        return result

    def _phase_alter_columns(self, by_kind: dict[str, list[Change]]) -> list[str]:
        altered = self._altered_columns()
        commands = []
        for (table_name, column_name), aspects in sorted(altered.items()):
            column = self.new.tables[table_name].columns[column_name]
            if self.dialect == "mysql":
                commands.append(
                    f"ALTER TABLE {table_name} MODIFY COLUMN {render_column_fragment(column)};"
                )
                continue
            if "type" in aspects:
                commands.append(
                    f"ALTER TABLE {table_name} ALTER COLUMN {column_name} "
                    f"TYPE {render_type(column.type)};"
                )
            if "nullability" in aspects:
                action = "SET NOT NULL" if column.not_null else "DROP NOT NULL"
                commands.append(
                    f"ALTER TABLE {table_name} ALTER COLUMN {column_name} {action};"
                )
            if "default" in aspects:
                if column.default is None:
                    commands.append(
                        f"ALTER TABLE {table_name} ALTER COLUMN {column_name} DROP DEFAULT;"
                    )
                else:
                    commands.append(
                        f"ALTER TABLE {table_name} ALTER COLUMN {column_name} "
                        f"SET DEFAULT {render_default(column.default)};"
                    )
        return commands

    def _phase_drop_columns(self, removed: list[Change]) -> list[str]:
        commands = [
            f"ALTER TABLE {change.table} DROP COLUMN {change.object.split('.', 1)[1]};"
            for change in removed
        ]
        return sorted(commands)

    def _phase_add_constraints(self, added: list[Change], kind: str) -> list[str]:
        commands = []
        for change in added:
            if change.object.split(".", 1)[0] not in self.new.tables:
                continue
            if change.table in {c.object for c in self.changes if c.kind == "table.added"}:
                continue  # rendered inline in CREATE TABLE
            if change.table not in self.new.tables:
                continue
            constraint = self.new.tables[change.table].constraints[change.object.split(".", 1)[1]]
            if constraint.kind != kind:
                continue
            commands.append(
                f"ALTER TABLE {change.table} ADD CONSTRAINT {constraint.name} "
                f"{render_constraint_fragment(constraint)};"
            )
        return sorted(commands)

    def _phase_create_indexes(self, added: list[Change], changed: list[Change]) -> list[str]:
        commands = []
        for change in list(added) + list(changed):
            index = self.new.indexes[change.object]
            unique = "UNIQUE " if index.unique else ""
            commands.append(
                f"CREATE {unique}INDEX {index.name} ON {index.table} "
                f"({', '.join(index.columns)});"
            )
        return sorted(commands)
