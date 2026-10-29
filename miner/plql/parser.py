"""PLQL (ProcessLens Query Language): tokenizer and recursive-descent parser.

The parser turns a formula into a small immutable AST. Nothing is ever passed to
``eval``; the engine (``miner.plql.engine``) interprets the AST over pandas data.

Grammar (keywords and function names are case-insensitive)::

    expr    := or
    or      := and (OR and)*
    and     := not (AND not)*
    not     := NOT not | cmp
    cmp     := add ( (= | == | != | <> | < | <= | > | >=) add | [NOT] IN '(' expr (',' expr)* ')' )?
    add     := mul ( (+ | -) mul )*
    mul     := unary ( (* | / | %) unary )*
    unary   := - unary | primary
    primary := NUMBER | STRING | TRUE | FALSE | NULL | '(' expr ')'
             | NAME '(' [expr (',' expr)*] ')'      -- function call
             | NAME '.' NAME                          -- column, e.g. case.duration_d
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_FORMULA_LENGTH = 4000
MAX_DEPTH = 60


class PlqlError(ValueError):
    """A formula that cannot be parsed or evaluated. ``position`` is a 0-based character offset."""

    def __init__(self, message: str, position: int | None = None):
        super().__init__(message)
        self.message = message
        self.position = position


# --------------------------------------------------------------------------- AST
@dataclass(frozen=True)
class Node:
    pos: int


@dataclass(frozen=True)
class Literal(Node):
    value: object  # float | int | str | bool | None


@dataclass(frozen=True)
class Column(Node):
    table: str  # "case" | "event"
    name: str


@dataclass(frozen=True)
class Call(Node):
    name: str  # upper-case
    args: tuple


@dataclass(frozen=True)
class Unary(Node):
    op: str  # "-" | "NOT"
    operand: Node


@dataclass(frozen=True)
class Binary(Node):
    op: str  # + - * / % = != < <= > >= AND OR
    left: Node
    right: Node


@dataclass(frozen=True)
class InList(Node):
    operand: Node
    items: tuple
    negated: bool


# --------------------------------------------------------------------------- tokens
_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<comment>--[^\n]*)
  | (?P<number>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)
  | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><=|>=|!=|<>|==|[-+*/%(),.=<>])
""", re.VERBOSE)

KEYWORDS = {"AND", "OR", "NOT", "IN", "TRUE", "FALSE", "NULL"}
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "/": "/"}


@dataclass(frozen=True)
class Token:
    kind: str  # number | string | name | keyword | op | end
    value: str
    pos: int


def _unescape(raw: str, pos: int) -> str:
    out, i = [], 1
    body_end = len(raw) - 1
    while i < body_end:
        ch = raw[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        nxt = raw[i + 1]
        if nxt == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", raw[i + 2:i + 6] or ""):
            out.append(chr(int(raw[i + 2:i + 6], 16)))
            i += 6
        elif nxt in _ESCAPES:
            out.append(_ESCAPES[nxt])
            i += 2
        else:
            raise PlqlError(f"Unknown escape sequence \\{nxt} in text", pos + i)
    return "".join(out)


def tokenize(text: str) -> list[Token]:
    if len(text) > MAX_FORMULA_LENGTH:
        raise PlqlError(f"Formula is longer than {MAX_FORMULA_LENGTH} characters", MAX_FORMULA_LENGTH)
    tokens: list[Token] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            ch = text[pos]
            if ch in "\"'":
                raise PlqlError("Text is not closed: add the closing quote", pos)
            raise PlqlError(f"Unexpected character {ch!r}", pos)
        kind = m.lastgroup
        value = m.group()
        if kind == "string":
            tokens.append(Token("string", _unescape(value, pos), pos))
        elif kind == "name":
            upper = value.upper()
            tokens.append(Token("keyword", upper, pos) if upper in KEYWORDS else Token("name", value, pos))
        elif kind in ("number", "op"):
            tokens.append(Token(kind, value, pos))
        pos = m.end()
    tokens.append(Token("end", "", len(text)))
    return tokens


# --------------------------------------------------------------------------- parser
_CMP = {"=": "=", "==": "=", "!=": "!=", "<>": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">="}


class _Parser:
    def __init__(self, text: str):
        self.tokens = tokenize(text)
        self.i = 0
        self.depth = 0

    @property
    def tok(self) -> Token:
        return self.tokens[self.i]

    def _advance(self) -> Token:
        t = self.tokens[self.i]
        self.i += 1
        return t

    def _is(self, kind: str, *values: str) -> bool:
        t = self.tok
        return t.kind == kind and (not values or t.value in values)

    def _expect_op(self, value: str) -> Token:
        if not self._is("op", value):
            got = self.tok.value or "the end of the formula"
            raise PlqlError(f"Expected {value!r} but found {got!r}", self.tok.pos)
        return self._advance()

    def parse(self) -> Node:
        if self._is("end"):
            raise PlqlError("The formula is empty", 0)
        node = self._or()
        if not self._is("end"):
            raise PlqlError(f"Unexpected {self.tok.value!r}: is an operator or comma missing?", self.tok.pos)
        return node

    def _nest(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise PlqlError("The formula is nested too deeply", self.tok.pos)

    def _or(self) -> Node:
        node = self._and()
        while self._is("keyword", "OR"):
            pos = self._advance().pos
            node = Binary(pos, "OR", node, self._and())
        return node

    def _and(self) -> Node:
        node = self._not()
        while self._is("keyword", "AND"):
            pos = self._advance().pos
            node = Binary(pos, "AND", node, self._not())
        return node

    def _not(self) -> Node:
        if self._is("keyword", "NOT"):
            pos = self._advance().pos
            self._nest()
            node = Unary(pos, "NOT", self._not())
            self.depth -= 1
            return node
        return self._cmp()

    def _cmp(self) -> Node:
        node = self._add()
        if self.tok.kind == "op" and self.tok.value in _CMP:
            t = self._advance()
            return Binary(t.pos, _CMP[t.value], node, self._add())
        negated = False
        if self._is("keyword", "NOT") and self.tokens[self.i + 1].kind == "keyword" and self.tokens[self.i + 1].value == "IN":
            self._advance()
            negated = True
        if self._is("keyword", "IN"):
            pos = self._advance().pos
            self._expect_op("(")
            items = [self._or()]
            while self._is("op", ","):
                self._advance()
                items.append(self._or())
            self._expect_op(")")
            return InList(pos, node, tuple(items), negated)
        if negated:
            raise PlqlError("Expected IN after NOT", self.tok.pos)
        return node

    def _add(self) -> Node:
        node = self._mul()
        while self._is("op", "+", "-"):
            t = self._advance()
            node = Binary(t.pos, t.value, node, self._mul())
        return node

    def _mul(self) -> Node:
        node = self._unary()
        while self._is("op", "*", "/", "%"):
            t = self._advance()
            node = Binary(t.pos, t.value, node, self._unary())
        return node

    def _unary(self) -> Node:
        if self._is("op", "-"):
            pos = self._advance().pos
            self._nest()
            node = Unary(pos, "-", self._unary())
            self.depth -= 1
            return node
        if self._is("op", "+"):
            self._advance()
            return self._unary()
        return self._primary()

    def _primary(self) -> Node:
        t = self.tok
        if t.kind == "number":
            self._advance()
            value = float(t.value)
            return Literal(t.pos, int(value) if value.is_integer() and re.fullmatch(r"\d+", t.value) else value)
        if t.kind == "string":
            self._advance()
            return Literal(t.pos, t.value)
        if t.kind == "keyword" and t.value in ("TRUE", "FALSE", "NULL"):
            self._advance()
            return Literal(t.pos, {"TRUE": True, "FALSE": False, "NULL": None}[t.value])
        if self._is("op", "("):
            self._advance()
            self._nest()
            node = self._or()
            self.depth -= 1
            self._expect_op(")")
            return node
        if t.kind == "name":
            self._advance()
            if self._is("op", "("):
                self._advance()
                self._nest()
                args = []
                if not self._is("op", ")"):
                    args.append(self._or())
                    while self._is("op", ","):
                        self._advance()
                        args.append(self._or())
                self.depth -= 1
                self._expect_op(")")
                return Call(t.pos, t.value.upper(), tuple(args))
            if self._is("op", "."):
                self._advance()
                if self.tok.kind not in ("name", "keyword"):
                    raise PlqlError("Expected a column name after the dot", self.tok.pos)
                col = self._advance()
                return Column(t.pos, t.value.lower(), col.value.lower())
            raise PlqlError(f"Unknown name {t.value!r}: write table.column (e.g. case.duration_d) "
                            "or call a function with parentheses", t.pos)
        if t.kind == "end":
            raise PlqlError("The formula ends too early", t.pos)
        raise PlqlError(f"Unexpected {t.value!r}", t.pos)


def parse(text: str) -> Node:
    return _Parser(text).parse()


def quote(value: str) -> str:
    """A PLQL text literal for ``value`` (used when building selection filters)."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
