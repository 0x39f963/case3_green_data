"""
Stage 0 классификатора: канонизация SQL.

Перед rules и будущими ML-слоями SQL приводится к стабильной форме:
- комментарии удаляются, но их позиции сохраняются для evidence;
- whitespace схлопывается;
- top-level statements выделяются через PostgreSQL parser;
- SQLGlot форматирует выражения в PostgreSQL dialect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from pglast import parse_sql
from pglast.parser import ParseError


@dataclass
class NormalizedSQL:
    """Результат Stage 0 normalize."""

    canonical: str
    statements: list[str]
    comments: list[tuple[int, str]]
    dollar_quoted: list[tuple[int, str]]
    parse_error: str | None = None


def normalize(sql: str) -> NormalizedSQL:
    """Вернуть canonical SQL, statements, comments и parse_error."""
    comments = _comments(sql)
    dollar = _dollar_quoted(sql)
    clean = _strip_comments(sql)
    clean = _space(clean)

    try:
        parse_sql(clean)
    except ParseError as exc:
        return NormalizedSQL(
            canonical="",
            statements=[],
            comments=comments,
            dollar_quoted=dollar,
            parse_error=str(exc),
        )

    if clean.upper().startswith("DO "):
        canonical = _trim_semicolon(clean)
        if canonical and not canonical.endswith(";"):
            canonical += ";"
        return NormalizedSQL(
            canonical=canonical,
            statements=[_trim_semicolon(clean)],
            comments=comments,
            dollar_quoted=dollar,
            parse_error=None,
        )

    statements = _statements(clean)
    canonical = "; ".join(_format_statement(item) for item in statements)
    if canonical and not canonical.endswith(";"):
        canonical += ";"
    return NormalizedSQL(
        canonical=canonical,
        statements=statements,
        comments=comments,
        dollar_quoted=dollar,
        parse_error=None,
    )


def _comments(sql: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    pattern = r"/\*[\s\S]*?\*/|--[^\n]*"
    for match in re.finditer(pattern, sql):
        found.append((match.start(), match.group(0)))
    return found


def _dollar_quoted(sql: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    pattern = r"\$[A-Za-z_0-9]*\$[\s\S]*?\$[A-Za-z_0-9]*\$"
    for match in re.finditer(pattern, sql):
        found.append((match.start(), match.group(0)))
    return found


def _strip_comments(sql: str) -> str:
    no_block = re.sub(r"/\*[\s\S]*?\*/", " ", sql)
    return re.sub(r"--[^\n]*", " ", no_block)


def _space(sql: str) -> str:
    return " ".join(sql.strip().split())


def _statements(sql: str) -> list[str]:
    try:
        items = sqlglot.transpile(sql, read="postgres", write="postgres", pretty=False)
    except sqlglot.errors.SqlglotError:
        return [_trim_semicolon(sql)] if sql else []
    return [_trim_semicolon(item) for item in items if item.strip()]


def _format_statement(sql: str) -> str:
    try:
        items = sqlglot.transpile(sql, read="postgres", write="postgres", pretty=False)
    except sqlglot.errors.SqlglotError:
        return _trim_semicolon(_space(sql))
    if not items:
        return _trim_semicolon(_space(sql))
    return _trim_semicolon(items[0])


def _trim_semicolon(sql: str) -> str:
    return sql.strip().rstrip(";").strip()
