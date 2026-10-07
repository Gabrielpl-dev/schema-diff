"""A small SQL tokenizer for the supported DDL subset.

Handles line comments (``--``), block comments (``/* ... */``), single quoted
strings, double quoted identifiers and MySQL-style backtick identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ParseError

# Token kinds
IDENT = "ident"
QUOTED_IDENT = "quoted_ident"
STRING = "string"
NUMBER = "number"
PUNCT = "punct"
EOF = "eof"


@dataclass
class Token:
    kind: str
    value: str          # normalized value (see identifiers.py rules)
    raw: str            # source text of the token
    line: int
    offset: int
    quoted: bool = False


_PUNCT_CHARS = set("(),;=<>+-*/.")


class Lexer:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.line = 1
        self.n = len(text)
        self.tokens: list[Token] = []

    def _advance(self, count: int = 1) -> None:
        for _ in range(count):
            if self.pos < self.n:
                if self.text[self.pos] == "\n":
                    self.line += 1
                self.pos += 1

    def tokenize(self) -> list[Token]:
        while self.pos < self.n:
            ch = self.text[self.pos]
            if ch in " \t\r\n":
                self._advance()
                continue
            if self.text.startswith("--", self.pos):
                self._skip_line_comment()
                continue
            if self.text.startswith("/*", self.pos):
                self._skip_block_comment()
                continue
            if ch == "'":
                self.tokens.append(self._read_string())
                continue
            if ch == '"':
                self.tokens.append(self._read_quoted(QUOTED_IDENT, '"'))
                continue
            if ch == "`":
                self.tokens.append(self._read_quoted(QUOTED_IDENT, "`"))
                continue
            if ch.isdigit():
                self.tokens.append(self._read_number())
                continue
            if ch.isalpha() or ch == "_":
                self.tokens.append(self._read_ident())
                continue
            if ch in _PUNCT_CHARS:
                self.tokens.append(
                    Token(PUNCT, ch, ch, self.line, self.pos)
                )
                self._advance()
                continue
            raise ParseError(f"unexpected character {ch!r} at line {self.line}")
        self.tokens.append(Token(EOF, "", "", self.line, self.pos))
        return self.tokens

    def _skip_line_comment(self) -> None:
        while self.pos < self.n and self.text[self.pos] != "\n":
            self._advance()

    def _skip_block_comment(self) -> None:
        start_line = self.line
        self._advance(2)
        while self.pos < self.n and not self.text.startswith("*/", self.pos):
            self._advance()
        if self.pos >= self.n:
            raise ParseError(f"unterminated block comment starting at line {start_line}")
        self._advance(2)

    def _read_string(self) -> Token:
        start_offset = self.pos
        start_line = self.line
        self._advance()  # opening quote
        chars: list[str] = []
        while self.pos < self.n:
            ch = self.text[self.pos]
            if ch == "'":
                if self.pos + 1 < self.n and self.text[self.pos + 1] == "'":
                    chars.append("'")
                    self._advance(2)
                    continue
                self._advance()
                raw = self.text[start_offset:self.pos]
                return Token(STRING, "".join(chars), raw, start_line, start_offset, quoted=True)
            chars.append(ch)
            self._advance()
        raise ParseError(f"unterminated string literal starting at line {start_line}")

    def _read_quoted(self, kind: str, quote: str) -> Token:
        start_offset = self.pos
        start_line = self.line
        self._advance()
        chars: list[str] = []
        while self.pos < self.n:
            ch = self.text[self.pos]
            if ch == quote:
                if self.pos + 1 < self.n and self.text[self.pos + 1] == quote:
                    chars.append(quote)
                    self._advance(2)
                    continue
                self._advance()
                raw = self.text[start_offset:self.pos]
                # Quoted identifiers preserve case literally.
                return Token(kind, "".join(chars), raw, start_line, start_offset, quoted=True)
            chars.append(ch)
            self._advance()
        raise ParseError(f"unterminated quoted identifier starting at line {start_line}")

    def _read_number(self) -> Token:
        start_offset = self.pos
        start_line = self.line
        while self.pos < self.n and (self.text[self.pos].isdigit() or self.text[self.pos] == "."):
            self._advance()
        raw = self.text[start_offset:self.pos]
        return Token(NUMBER, raw, raw, start_line, start_offset)

    def _read_ident(self) -> Token:
        start_offset = self.pos
        start_line = self.line
        while self.pos < self.n and (self.text[self.pos].isalnum() or self.text[self.pos] == "_"):
            self._advance()
        raw = self.text[start_offset:self.pos]
        # Unquoted identifiers are normalized to lower case.
        return Token(IDENT, raw.lower(), raw, start_line, start_offset)


def tokenize(text: str) -> list[Token]:
    return Lexer(text).tokenize()
