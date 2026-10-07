"""Parser for the v0.1 SQL DDL subset (section 3 of the spec)."""

from __future__ import annotations

from .errors import ParseError, ValidationError
from .lexer import EOF, IDENT, NUMBER, PUNCT, QUOTED_IDENT, STRING, Token, tokenize
from .model import Column, Constraint, Index, Schema, Table

# Simple type keywords -> canonical name. SERIAL types are handled separately.
_SIMPLE_TYPES = {
    "smallint": "smallint",
    "int2": "smallint",
    "int": "int",
    "integer": "int",
    "int4": "int",
    "bigint": "bigint",
    "int8": "bigint",
    "text": "text",
    "boolean": "boolean",
    "bool": "boolean",
    "date": "date",
    "timestamp": "timestamp",
    "timestamptz": "timestamptz",
    "uuid": "uuid",
}

_SERIAL_TYPES = {"serial": "int", "bigserial": "bigint"}

_CONSTRAINT_KEYWORDS = {"primary", "unique", "foreign"}

# Keywords that cannot be used as bare (unquoted) identifiers in the subset.
_RESERVED = {
    "create", "table", "index", "unique", "primary", "key", "foreign",
    "references", "default", "not", "null", "constraint", "if", "exists",
    "on", "check", "select", "view", "insert", "alter", "drop", "and", "or",
}


def synth_name(table: str, kind: str, columns: list[str]) -> str:
    if kind == "primary_key":
        return f"__inline__{table}__{'__'.join(columns)}__primary_key"
    if kind == "unique":
        return f"__inline__{table}__{'__'.join(columns)}__unique"
    if kind == "foreign_key":
        return f"__inline__{table}__fk__{columns[0]}"
    raise ValueError(f"unknown constraint kind: {kind}")


class _Stream:
    def __init__(self, tokens: list[Token]):
        if tokens and tokens[-1].kind == EOF:
            self.tokens = tokens
        else:
            line = tokens[-1].line if tokens else 1
            self.tokens = tokens + [Token(EOF, "", "", line, 0)]
        self.i = 0

    def peek(self, k: int = 0) -> Token:
        idx = self.i + k
        if idx >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[idx]

    def next(self) -> Token:
        tok = self.peek()
        if tok.kind != EOF:
            self.i += 1
        return tok

    def at_end(self) -> bool:
        return self.peek().kind == EOF

    def at_keyword(self, *words: str) -> bool:
        tok = self.peek()
        return tok.kind == IDENT and tok.value in words

    def accept_keyword(self, *words: str) -> bool:
        if self.at_keyword(*words):
            self.next()
            return True
        return False

    def expect_keyword(self, word: str) -> Token:
        if not self.at_keyword(word):
            self._fail(f"expected {word.upper()}")
        return self.next()

    def expect_ident(self) -> Token:
        tok = self.peek()
        if tok.kind == QUOTED_IDENT:
            return self.next()
        if tok.kind != IDENT:
            self._fail("expected identifier")
        if tok.value in _RESERVED:
            self._fail(f"unexpected keyword {tok.raw.upper()}")
        return self.next()

    def expect_punct(self, value: str) -> Token:
        tok = self.peek()
        if tok.kind != PUNCT or tok.value != value:
            self._fail(f"expected {value!r}")
        return self.next()

    def accept_punct(self, value: str) -> bool:
        tok = self.peek()
        if tok.kind == PUNCT and tok.value == value:
            self.next()
            return True
        return False

    def expect_number(self) -> str:
        tok = self.peek()
        if tok.kind != NUMBER:
            self._fail("expected a number")
        self.next()
        return tok.value

    def _fail(self, message: str) -> None:
        tok = self.peek()
        raise ParseError(
            f"{message} at line {tok.line}", line=tok.line, snippet=tok.raw or str(tok.value)
        )


def _split_statements(tokens: list[Token], text: str) -> list[tuple[list[Token], int, str]]:
    """Split a token list into statements at top-level ``;``."""
    statements: list[tuple[list[Token], int, str]] = []
    current: list[Token] = []
    depth = 0
    for tok in tokens:
        if tok.kind == EOF:
            break
        if tok.kind == PUNCT and tok.value == "(":
            depth += 1
        elif tok.kind == PUNCT and tok.value == ")":
            depth = max(0, depth - 1)
        elif tok.kind == PUNCT and tok.value == ";" and depth == 0:
            if current:
                statements.append((current, current[0].line, _snippet(text, current)))
            current = []
            continue
        current.append(tok)
    if current:
        statements.append((current, current[0].line, _snippet(text, current)))
    return statements


def _snippet(text: str, tokens: list[Token]) -> str:
    start = tokens[0].offset
    end = tokens[-1].offset + len(tokens[-1].raw)
    collapsed = " ".join(text[start:end].split())
    if len(collapsed) > 80:
        collapsed = collapsed[:77] + "..."
    return collapsed


def parse_schema(text: str) -> Schema:
    tokens = tokenize(text)
    schema = Schema()
    for stmt_tokens, line, snippet in _split_statements(tokens, text):
        try:
            _parse_statement(stmt_tokens, line, snippet, schema)
        except ParseError as exc:
            raise ParseError(exc.raw_message, line=line, snippet=snippet) from None
    _validate_schema(schema)
    return schema


def _parse_statement(tokens: list[Token], line: int, snippet: str, schema: Schema) -> None:
    stream = _Stream(tokens)
    if not stream.accept_keyword("create"):
        raise ParseError("unsupported statement", line=line, snippet=snippet)
    if stream.accept_keyword("table"):
        _parse_create_table(stream, schema)
    elif stream.accept_keyword("unique"):
        stream.expect_keyword("index")
        _parse_create_index(stream, schema, unique=True)
    elif stream.accept_keyword("index"):
        _parse_create_index(stream, schema, unique=False)
    else:
        raise ParseError("unsupported statement", line=line, snippet=snippet)
    if not stream.at_end():
        raise ParseError("unsupported statement", line=line, snippet=snippet)


def _parse_create_table(stream: _Stream, schema: Schema) -> None:
    if stream.accept_keyword("if"):
        stream.expect_keyword("not")
        stream.expect_keyword("exists")
    table_name = stream.expect_ident().value
    stream.expect_punct("(")
    elements = []
    while True:
        elements.append(_parse_element(stream, table_name))
        if stream.accept_punct(","):
            continue
        break
    stream.expect_punct(")")

    table = schema.tables.get(table_name)
    if table is None:
        table = Table(name=table_name)
        schema.tables[table_name] = table

    for kind, payload in elements:
        if kind == "column":
            column: Column = payload  # type: ignore[assignment]
            table.columns[column.name] = column
            for ckind in column.pending_constraints:
                cname = synth_name(table_name, ckind, [column.name])
                table.constraints[cname] = Constraint(
                    name=cname, kind=ckind, table=table_name, columns=[column.name],
                    inline=True, named=False
                )
            column.pending_constraints = []
        else:
            constraint: Constraint = payload  # type: ignore[assignment]
            table.constraints[constraint.name] = constraint

    # A PRIMARY KEY (inline or table constraint) implies NOT NULL on its columns.
    for constraint in table.constraints.values():
        if constraint.kind == "primary_key":
            for col_name in constraint.columns:
                col = table.columns.get(col_name)
                if col is not None:
                    col.not_null = True


def _parse_element(stream: _Stream, table_name: str) -> tuple[str, object]:
    if stream.at_keyword("constraint"):
        stream.next()
        cname = stream.expect_ident().value
        return "constraint", _parse_table_constraint(stream, table_name, cname)
    tok = stream.peek()
    if tok.kind == IDENT and tok.value in _CONSTRAINT_KEYWORDS:
        return "constraint", _parse_table_constraint(stream, table_name, None)
    return "column", _parse_column(stream)


def _parse_column(stream: _Stream) -> Column:
    name = stream.expect_ident().value
    type_name, is_serial = _parse_type(stream)
    column = Column(name=name, type=type_name, serial=is_serial)
    if is_serial:
        column.not_null = True
        column.default = "nextval"

    while not stream.at_end():
        tok = stream.peek()
        if tok.kind == PUNCT and tok.value in (",", ")"):
            break
        if stream.accept_keyword("not"):
            stream.expect_keyword("null")
            column.not_null = True
        elif stream.at_keyword("null"):
            stream.next()
            column.not_null = False
        elif stream.at_keyword("default"):
            stream.next()
            column.default = _parse_default(stream)
        elif stream.accept_keyword("primary"):
            stream.expect_keyword("key")
            column.not_null = True
            column.pending_constraints.append("primary_key")
        elif stream.accept_keyword("unique"):
            column.pending_constraints.append("unique")
        else:
            stream._fail("unsupported column definition")
    return column


def _parse_type(stream: _Stream) -> tuple[str, bool]:
    tok = stream.peek()
    if tok.kind != IDENT:
        stream._fail("expected a supported column type")
    word = tok.value
    if word in _SIMPLE_TYPES:
        stream.next()
        return _SIMPLE_TYPES[word], False
    if word in _SERIAL_TYPES:
        stream.next()
        return _SERIAL_TYPES[word], True
    if word == "varchar":
        stream.next()
        stream.expect_punct("(")
        size = stream.expect_number()
        stream.expect_punct(")")
        return f"varchar({size})", False
    if word == "character":
        stream.next()
        stream.expect_keyword("varying")
        stream.expect_punct("(")
        size = stream.expect_number()
        stream.expect_punct(")")
        return f"varchar({size})", False
    if word in ("numeric", "decimal"):
        stream.next()
        stream.expect_punct("(")
        precision = stream.expect_number()
        stream.expect_punct(",")
        scale = stream.expect_number()
        stream.expect_punct(")")
        return f"numeric({precision},{scale})", False
    stream._fail(f"unsupported type {tok.raw.upper()}")
    raise AssertionError("unreachable")  # pragma: no cover


def _parse_default(stream: _Stream) -> str:
    tok = stream.peek()
    if tok.kind == STRING:
        stream.next()
        return "'" + tok.value.replace("'", "''") + "'"
    if tok.kind == NUMBER:
        stream.next()
        return tok.value
    if tok.kind == PUNCT and tok.value in ("-", "+"):
        stream.next()
        num = stream.expect_number()
        return tok.value + num
    if tok.kind == IDENT:
        word = tok.value
        if word in ("true", "false"):
            stream.next()
            return word.upper()
        if word == "null":
            stream.next()
            return "NULL"
        if word == "current_timestamp":
            stream.next()
            return "CURRENT_TIMESTAMP"
        if word == "now":
            stream.next()
            stream.expect_punct("(")
            stream.expect_punct(")")
            return "NOW()"
        if word == "nextval":
            stream.next()
            if stream.accept_punct("("):
                while not stream.at_end() and not (
                    stream.peek().kind == PUNCT and stream.peek().value == ")"
                ):
                    stream.next()
                stream.expect_punct(")")
            return "nextval"
    stream._fail("unsupported DEFAULT expression")
    raise AssertionError("unreachable")  # pragma: no cover


def _parse_table_constraint(
    stream: _Stream, table_name: str, explicit_name: str | None
) -> Constraint:
    if stream.accept_keyword("primary"):
        stream.expect_keyword("key")
        columns = _parse_column_list(stream)
        if len(columns) > 2:
            stream._fail("composite PRIMARY KEY with more than 2 columns is not supported")
        name = explicit_name or synth_name(table_name, "primary_key", columns)
        return Constraint(
            name=name, kind="primary_key", table=table_name, columns=columns,
            named=explicit_name is not None,
        )
    if stream.accept_keyword("unique"):
        columns = _parse_column_list(stream)
        name = explicit_name or synth_name(table_name, "unique", columns)
        return Constraint(
            name=name, kind="unique", table=table_name, columns=columns,
            named=explicit_name is not None,
        )
    if stream.accept_keyword("foreign"):
        stream.expect_keyword("key")
        columns = _parse_column_list(stream)
        if len(columns) != 1:
            stream._fail("composite FOREIGN KEY is not supported")
        stream.expect_keyword("references")
        ref_table = stream.expect_ident().value
        ref_columns = _parse_column_list(stream)
        if stream.at_keyword("on"):
            stream._fail("REFERENCES ... ON DELETE/ON UPDATE is not supported")
        name = explicit_name or synth_name(table_name, "foreign_key", columns)
        return Constraint(
            name=name,
            kind="foreign_key",
            table=table_name,
            columns=columns,
            references={"table": ref_table, "columns": ref_columns},
            named=explicit_name is not None,
        )
    if stream.at_keyword("check"):
        stream._fail("CHECK is not supported")
    stream._fail("unsupported table constraint")
    raise AssertionError("unreachable")  # pragma: no cover


def _parse_column_list(stream: _Stream) -> list[str]:
    stream.expect_punct("(")
    columns = [stream.expect_ident().value]
    while stream.accept_punct(","):
        columns.append(stream.expect_ident().value)
    stream.expect_punct(")")
    return columns


def _parse_create_index(stream: _Stream, schema: Schema, unique: bool) -> None:
    name = stream.expect_ident().value
    stream.expect_keyword("on")
    table = stream.expect_ident().value
    columns = _parse_column_list(stream)
    if name in schema.indexes and schema.indexes[name].table != table:
        raise ValidationError(
            f"duplicate index name {name} on tables "
            f"{schema.indexes[name].table} and {table}"
        )
    schema.indexes[name] = Index(name=name, table=table, unique=unique, columns=columns)


def _validate_schema(schema: Schema) -> None:
    for table in schema.tables.values():
        for constraint in table.constraints.values():
            for col_name in constraint.columns:
                if col_name not in table.columns:
                    raise ValidationError(
                        f"constraint {constraint.name} references unknown column "
                        f"{table.name}.{col_name}"
                    )
    for index in schema.indexes.values():
        if index.table not in schema.tables:
            raise ValidationError(
                f"index {index.name} references unknown table {index.table}"
            )
        table = schema.tables[index.table]
        for col_name in index.columns:
            if col_name not in table.columns:
                raise ValidationError(
                    f"index {index.name} references unknown column {index.table}.{col_name}"
                )


def validate_references(schemas: list[Schema]) -> None:
    """Validate FK targets against the union of all provided schemas (EC-18)."""
    known_tables = set()
    for schema in schemas:
        known_tables.update(schema.tables.keys())
    for schema in schemas:
        for table in schema.tables.values():
            for constraint in table.constraints.values():
                if constraint.kind != "foreign_key" or constraint.references is None:
                    continue
                ref_table = constraint.references["table"]
                if ref_table not in known_tables:
                    raise ValidationError(
                        f"foreign key {constraint.name} references unknown table {ref_table}"
                    )


def validate_unique_index_names(schemas: list[Schema]) -> None:
    """Two indexes with the same name on different tables are invalid (EC-17)."""
    for schema in schemas:
        by_name: dict[str, Index] = {}
        for index in schema.indexes.values():
            previous = by_name.get(index.name)
            if previous is not None and previous.table != index.table:
                raise ValidationError(
                    f"duplicate index name {index.name} on tables "
                    f"{previous.table} and {index.table}"
                )
            by_name[index.name] = index
