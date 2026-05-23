"""Структурное сравнение сгенерированного SQL с эталоном из Golden Dataset.

marina-01. Эталон (поле ``safe_rewrite`` в Golden Dataset) — это «учительский
ответ». Здесь мы сравниваем структуру кандидата с эталоном детерминированно
через AST (pglast), без LLM: те же таблицы, те же колонки проекции, та же
структура ``WHERE / GROUP BY / ORDER BY / LIMIT``.

Проверка вызывается только в eval-режиме (когда прогоняем Golden и эталон есть).
В live ``/run`` эталона нет — функция не используется.

Опирается на правила формата эталона из marina-04
(``data/eval/golden_safe_rewrite_rules.html``): чтобы сравнение было осмысленным,
эталоны пишутся по единым правилам (явные колонки, LIMIT кратный 10 и т.д.).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pglast import ast, parse_sql
from pglast.parser import ParseError
from pglast.visitors import Visitor

from app.sql_parsing import _last_string

__all__ = ["OracleFinding", "check_oracle_compatibility"]


@dataclass
class OracleFinding:
    """Одно расхождение между кандидатом и эталоном.

    label    — стабильный код правила (``ORACLE_*``).
    severity — 0..10, как в sql_guard; severity ≤ 4 трактуется как
               «структурно совместимо» (косметика), ≥ 5 — блокирующее.
    evidence — что именно не совпало (для отчёта и ревью).
    """

    label: str
    severity: int
    evidence: str


@dataclass
class _Summary:
    """Структурный «слепок» одного SQL для сравнения."""

    tables: set[str]
    projection: set[str]
    aliases: set[str]
    has_where: bool
    has_group_by: bool
    has_order_by: bool
    has_limit: bool
    limit_value: int | None


class _TableVisitor(Visitor):
    """Собирает имена таблиц (RangeVar.relname) по всему дереву FROM/JOIN."""

    def __init__(self) -> None:
        self.tables: set[str] = set()

    def visit_RangeVar(self, ancestors: Any, node: ast.RangeVar) -> None:  # noqa: N802
        if node.relname:
            self.tables.add(node.relname.lower())


def check_oracle_compatibility(
    generated_sql: str,
    oracle_sql: str,
    *,
    strict_columns: bool = True,
) -> list[OracleFinding]:
    """Сравнивает структуру сгенерированного SQL с эталоном.

    Возвращает пустой список, если сгенерированный SQL покрывает эталон.
    Каждое нарушенное правило — отдельный :class:`OracleFinding`.

    Параметры:
        generated_sql: SQL-кандидат (что сгенерировала система).
        oracle_sql:    эталонный SQL (поле ``safe_rewrite`` из Golden).
        strict_columns: если True (по умолчанию), отсутствие колонок эталона
            в кандидате даёт ``ORACLE_COLUMNS_MISSING``. Если False — проверка
            недостающих колонок пропускается (лояльный режим).

    Правила (label / severity):
        ORACLE_PARSE_ERROR (7)     — один из SQL не парсится.
        ORACLE_TABLES_MISMATCH (8) — множества таблиц не совпадают.
        ORACLE_COLUMNS_MISSING (7) — колонки эталона отсутствуют в кандидате.
        ORACLE_COLUMNS_EXTRA (3)   — лишние колонки в кандидате (warning).
        ORACLE_LIMIT_MISSING (6)   — в эталоне есть LIMIT, в кандидате нет.
        ORACLE_LIMIT_TOO_WIDE (5)  — LIMIT кандидата шире эталонного.
        ORACLE_WHERE_MISSING (6)   — в эталоне есть WHERE, в кандидате нет.
        ORACLE_GROUP_BY_MISSING (6)— в эталоне есть GROUP BY, в кандидате нет.
        ORACLE_ORDER_BY_MISSING (3)— в эталоне есть ORDER BY, в кандидате нет.

    Пример::

        check_oracle_compatibility(
            "SELECT id, name FROM emp WHERE status = 1",
            "SELECT id, name FROM emp WHERE status = 1 LIMIT 100",
        )
        # → [OracleFinding('ORACLE_LIMIT_MISSING', 6, ...)]
    """
    gen = _parse(generated_sql)
    ora = _parse(oracle_sql)
    if gen is None or ora is None:
        which = "generated" if gen is None else "oracle"
        return [OracleFinding("ORACLE_PARSE_ERROR", 7, f"{which} SQL не парсится через pglast")]

    g = _summarize(gen)
    o = _summarize(ora)
    findings: list[OracleFinding] = []

    def add(label: str, severity: int, evidence: str) -> None:
        findings.append(OracleFinding(label, severity, evidence))

    # Таблицы должны полностью совпадать.
    if g.tables != o.tables:
        add("ORACLE_TABLES_MISMATCH", 8, f"oracle={_fmt(o.tables)} candidate={_fmt(g.tables)}")

    # Колонки эталона должны быть в кандидате (projection_oracle ⊆ projection_generated).
    missing = o.projection - g.projection
    if strict_columns and missing:
        add("ORACLE_COLUMNS_MISSING", 7, f"в кандидате нет колонок эталона: {_fmt(missing)}")

    # Лишние колонки в кандидате — предупреждение, не блокер.
    extra = g.projection - o.projection
    if extra:
        add("ORACLE_COLUMNS_EXTRA", 3, f"лишние колонки в кандидате: {_fmt(extra)}")

    # LIMIT: если в эталоне есть — в кандидате тоже должен быть и не шире.
    if o.has_limit and not g.has_limit:
        add("ORACLE_LIMIT_MISSING", 6, "в эталоне есть LIMIT, в кандидате нет")
    elif (
        o.has_limit
        and g.has_limit
        and o.limit_value is not None
        and g.limit_value is not None
        and g.limit_value > o.limit_value
    ):
        add("ORACLE_LIMIT_TOO_WIDE", 5, f"LIMIT кандидата {g.limit_value} > эталона {o.limit_value}")

    # WHERE / GROUP BY обязательны, если есть в эталоне.
    if o.has_where and not g.has_where:
        add("ORACLE_WHERE_MISSING", 6, "в эталоне есть WHERE, в кандидате нет")
    if o.has_group_by and not g.has_group_by:
        add("ORACLE_GROUP_BY_MISSING", 6, "в эталоне есть GROUP BY, в кандидате нет")

    # ORDER BY — предупреждение.
    if o.has_order_by and not g.has_order_by:
        add("ORACLE_ORDER_BY_MISSING", 3, "в эталоне есть ORDER BY, в кандидате нет")

    return findings


def _parse(sql: str) -> tuple[Any, ...] | None:
    """Разобрать SQL через pglast. None — если строка не парсится."""
    if not sql or not sql.strip():
        return None
    try:
        tree = parse_sql(sql)
    except (ParseError, Exception):  # noqa: BLE001 — любой сбой парсера = «не парсится»
        return None
    return tree if tree else None


def _summarize(tree: tuple[Any, ...]) -> _Summary:
    """Собрать структурный слепок из распарсенного AST."""
    table_visitor = _TableVisitor()
    table_visitor(tree)

    select = _top_select(tree[0].stmt)
    projection: set[str] = set()
    aliases: set[str] = set()
    if select is not None:
        for target in select.targetList or ():
            if getattr(target, "name", None):
                aliases.add(str(target.name).lower())
            projection.update(_collect_column_refs(getattr(target, "val", None)))

    has_where = bool(select and select.whereClause is not None)
    has_group_by = bool(select and select.groupClause)
    has_order_by = bool(select and select.sortClause)
    limit_value = _limit_value(select)
    has_limit = bool(select and select.limitCount is not None)

    return _Summary(
        tables=table_visitor.tables,
        projection=projection,
        aliases=aliases,
        has_where=has_where,
        has_group_by=has_group_by,
        has_order_by=has_order_by,
        has_limit=has_limit,
        limit_value=limit_value,
    )


def _top_select(stmt: Any) -> ast.SelectStmt | None:
    """Вернуть top-level SELECT (для UNION спускаемся к левому с targetList)."""
    seen = 0
    while isinstance(stmt, ast.SelectStmt) and not stmt.targetList and stmt.larg is not None:
        stmt = stmt.larg
        seen += 1
        if seen > 32:  # защита от патологических деревьев
            break
    return stmt if isinstance(stmt, ast.SelectStmt) else None


def _collect_column_refs(node: Any) -> set[str]:
    """Собрать «голые» имена колонок из выражения проекции.

    Префикс таблицы нормализуется через ``_last_string`` (``e.first_name`` и
    ``first_name`` — одна колонка). ``*`` (A_Star) игнорируется. Колонки внутри
    функций (``SUM(lim_sum)``) тоже учитываются как участники проекции.
    """
    out: set[str] = set()

    def _walk(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, ast.ColumnRef):
            name = _last_string(getattr(value, "fields", ()) or ())
            if name:
                out.add(name.lower())
            return
        if isinstance(value, (tuple, list)):
            for item in value:
                _walk(item)
            return
        if isinstance(value, ast.FuncCall):
            _walk(getattr(value, "args", ()) or ())
            return
        if isinstance(value, ast.A_Expr):
            _walk(getattr(value, "lexpr", None))
            _walk(getattr(value, "rexpr", None))
            return
        if isinstance(value, ast.CaseExpr):
            for arg in getattr(value, "args", ()) or ():
                _walk(getattr(arg, "expr", None))
                _walk(getattr(arg, "result", None))
            _walk(getattr(value, "defresult", None))
            return
        if isinstance(value, ast.CoalesceExpr):
            _walk(getattr(value, "args", ()) or ())
            return
        if isinstance(value, ast.TypeCast):
            _walk(getattr(value, "arg", None))
            return
        # Прочие узлы: пробуем обойти типовые поля выражений.
        for attr in ("args", "lexpr", "rexpr", "arg"):
            child = getattr(value, attr, None)
            if child is not None and child is not value:
                _walk(child)

    _walk(node)
    return out


def _limit_value(select: ast.SelectStmt | None) -> int | None:
    """Числовое значение LIMIT, если это целочисленная константа."""
    if select is None or select.limitCount is None:
        return None
    count = select.limitCount
    val = getattr(count, "val", None)  # A_Const.val → Integer
    ival = getattr(val, "ival", None)
    if isinstance(ival, int):
        return ival
    return None


def _fmt(values: set[str]) -> str:
    return "{" + ", ".join(sorted(values)) + "}"
