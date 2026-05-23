"""
PostgreSQL AST parsing для быстрых правил безопасности.

pglast здесь primary parser: он использует настоящий PostgreSQL parser,
поэтому сломанный SQL считаем сломанным, а не пытаемся угадать смысл.
sqlparse остается fallback в sql_guard для поверхностных payload checks.
Модуль маленький: дает statement type, identifiers и признаки, которые
нужны B2 guard без расширения таксономии до B3 STEP2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pglast import ast, parse_sql
from pglast.enums import SetOperation
from pglast.parser import ParseError
from pglast.visitors import Visitor


@dataclass
class ParsedSQL:
    """Результат разбора одной SQL-строки через pglast."""

    statement_type: str = "UNKNOWN"
    identifiers: dict[str, list[str]] = field(default_factory=dict)
    has_union: bool = False
    has_multi_statement: bool = False
    raw_ast: tuple[Any, ...] = ()
    broken: bool = False
    error: str | None = None


class _IdentifierVisitor(Visitor):
    """Собирает имена таблиц, колонок и функций из AST."""

    def __init__(self) -> None:
        self.tables: set[str] = set()
        self.columns: set[str] = set()
        self.functions: set[str] = set()

    def visit_RangeVar(self, ancestors, node: ast.RangeVar) -> None:
        """Таблица в FROM/JOIN/UPDATE/DELETE."""
        if node.relname:
            self.tables.add(_join_name(node.schemaname, node.relname))

    def visit_ColumnRef(self, ancestors, node: ast.ColumnRef) -> None:
        """Ссылка на колонку, включая alias.column."""
        name = _last_string(node.fields)
        if name:
            self.columns.add(name)

    def visit_FuncCall(self, ancestors, node: ast.FuncCall) -> None:
        """Вызов функции: pg_sleep, count, lower и так далее."""
        name = _join_string_parts(node.funcname)
        if name:
            self.functions.add(name)


def parse(sql: str) -> ParsedSQL:
    """
    Разобрать SQL через pglast и вернуть компактную структуру.

    broken=True означает, что PostgreSQL parser не принял строку.
    Это вызывается из sql_guard до правил. raw_ast оставляем для
    будущих B3 проверок, но в ТЗ-1 его не сериализуем наружу.
    """
    try:
        tree = parse_sql(sql)
    except ParseError as exc:
        return ParsedSQL(
            identifiers={"tables": [], "columns": [], "functions": []},
            broken=True,
            error=str(exc),
        )

    first = tree[0].stmt if tree else None
    visitor = _IdentifierVisitor()
    visitor(tree)

    return ParsedSQL(
        statement_type=_statement_type(first),
        identifiers={
            "tables": sorted(visitor.tables),
            "columns": sorted(visitor.columns),
            "functions": sorted(visitor.functions),
        },
        has_union=_has_union(first),
        has_multi_statement=len(tree) != 1,
        raw_ast=tree,
        broken=False,
        error=None,
    )


def is_single_statement(sql: str) -> bool:
    """
    Проверить, что строка содержит ровно один top-level statement.

    Невалидный SQL возвращает False. Это удобно для EXPLAIN sandbox:
    если parser не понял строку, в базу такой текст не отправляем.
    """
    parsed = parse(sql)
    return (not parsed.broken) and (not parsed.has_multi_statement)


def extract_identifiers(sql: str) -> dict[str, list[str]]:
    """
    Вернуть таблицы, колонки и функции из PostgreSQL AST.

    При parse error возвращаем пустые списки. Ошибка остается доступна
    через parse(sql).error, а эта функция нужна только для простого
    чтения identifiers в правилах и будущих отчетах.
    """
    return parse(sql).identifiers


def is_dml_without_where(sql: str) -> bool:
    """
    Найти опасный DML без WHERE.

    UPDATE и DELETE без whereClause считаются риском. TRUNCATE всегда
    считается риском, потому что удаляет всю таблицу без условия.
    """
    return is_dml_without_where_from_parsed(parse(sql))


def is_dml_without_where_from_parsed(parsed: ParsedSQL) -> bool:
    """
    Та же проверка, но для уже разобранного ParsedSQL.

    Нужна sql_guard, чтобы не парсить одну строку дважды. Публичная
    форма оставлена простой: на вход ParsedSQL, на выход bool.
    """
    if parsed.broken or len(parsed.raw_ast) != 1:
        return False

    stmt = parsed.raw_ast[0].stmt
    if isinstance(stmt, (ast.UpdateStmt, ast.DeleteStmt)):
        return getattr(stmt, "whereClause", None) is None
    return isinstance(stmt, ast.TruncateStmt)


def has_explain_analyze(sql: str) -> bool:
    """
    Проверить, содержит ли строка EXPLAIN ANALYZE.

    EXPLAIN без ANALYZE сам по себе безопасен для плана. ANALYZE уже
    исполняет запрос, поэтому этот признак нужен guard и sandbox.
    """
    parsed = parse(sql)
    if parsed.broken or len(parsed.raw_ast) != 1:
        return False

    stmt = parsed.raw_ast[0].stmt
    if not isinstance(stmt, ast.ExplainStmt):
        return False

    for item in stmt.options or ():
        if getattr(item, "defname", "").lower() == "analyze":
            return True
    return False


def _statement_type(stmt: Any) -> str:
    if stmt is None:
        return "UNKNOWN"
    name = stmt.__class__.__name__
    if name.endswith("Stmt"):
        name = name[:-4]
    return name.upper()


def _has_union(stmt: Any) -> bool:
    if not isinstance(stmt, ast.SelectStmt):
        return False
    if getattr(stmt, "op", SetOperation.SETOP_NONE) != SetOperation.SETOP_NONE:
        return True
    return _has_union(getattr(stmt, "larg", None)) or _has_union(getattr(stmt, "rarg", None))


def _join_name(schema: str | None, name: str | None) -> str:
    if schema:
        return schema + "." + (name or "")
    return name or ""


def _join_string_parts(parts: tuple[Any, ...] | None) -> str:
    values = []
    for item in parts or ():
        value = getattr(item, "sval", None)
        if value:
            values.append(value)
    return ".".join(values)


def _last_string(parts: tuple[Any, ...] | None) -> str:
    values = []
    for item in parts or ():
        value = getattr(item, "sval", None)
        if value:
            values.append(value)
    return values[-1] if values else ""
