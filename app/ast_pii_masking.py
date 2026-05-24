"""Проверка чувствительных полей и класса маскировки PII через AST.

marina-02. Сгенерированный SQL не должен возвращать персональные данные в сыром
виде. Если есть эталон из Golden Dataset (``safe_rewrite``) — кандидат должен
маскировать PII тем же классом, что и эталон (хэш ≠ частичное скрытие ≠ агрегат).

Проверка детерминированная (AST через pglast), без LLM. Особенно ловит случай,
когда колонка переименована через ``AS``, но осталась сырой (фикс 22 мая):
``SELECT email AS contact`` — это всё ещё сырая PII.

Модуль самодостаточный (зависит только от ``pglast`` и ``app.sql_parsing``), не
импортирует тяжёлый ``sql_guard`` → тесты гоняются на лёгком окружении.
Словарь :data:`_PII_MASK_CLASS` расширяет ``sql_guard._PII_MASK_FUNCS`` /
``_PII_AGGREGATE_FUNCS`` различением классов и согласован с таблицей маскировки
из marina-04 (``data/eval/golden_safe_rewrite_rules.html``).

Контракт findings: :class:`Finding` (label/severity/evidence/column). При
интеграции (ivan-02) маппится в ``sql_guard.make_vulnerability(label, …,
severity=…, evidence_span=evidence)``.

Пример использования::

    from app.ast_pii_masking import check_pii_masking

    sensitive = {"sys_employee": ["email", "phone"]}
    # без эталона: сырой email в проекции → DIRECT_SENSITIVE
    check_pii_masking("SELECT email FROM sys_employee", sensitive)
    # с эталоном: эталон хэширует, кандидат отдаёт сырое → MASKING_DOWNGRADED
    check_pii_masking(
        "SELECT email FROM sys_employee",
        sensitive,
        oracle_sql="SELECT md5(email) AS e FROM sys_employee",
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pglast import ast, parse_sql
from pglast.parser import ParseError

from app.sql_parsing import _last_string

__all__ = ["Finding", "check_pii_masking", "_PII_MASK_CLASS"]


# Канонические классы маскировки: имя функции → класс. Согласовано с marina-04.
# format/concat и left/right/substring обрабатываются с учётом позиции аргумента
# (см. _func_mask_class) — здесь зафиксирован их базовый класс.
_PII_MASK_CLASS: dict[str, str] = {
    # hash — необратимое хэширование (высокий уровень защиты)
    "md5": "hash",
    "sha256": "hash",
    "sha512": "hash",
    "digest": "hash",
    "encode": "hash",
    # partial — частичное скрытие (средний уровень)
    "left": "partial",
    "right": "partial",
    "substring": "partial",
    "substr": "partial",
    "date_trunc": "partial",
    "split_part": "partial",
    # replace — замена символов (средний уровень)
    "regexp_replace": "replace",
    "translate": "replace",
    "replace": "replace",
    # format — зависит от шаблона (низкий уровень)
    "format": "format",
    "concat": "format",
    # aggregate — агрегация (высокий уровень)
    "count": "aggregate",
    "sum": "aggregate",
    "avg": "aggregate",
    "min": "aggregate",
    "max": "aggregate",
    "stddev": "aggregate",
    "variance": "aggregate",
}

_RAW = "raw"

_SEVERITY = {
    "DIRECT_SENSITIVE": 6,
    "MASKING_DOWNGRADED": 8,
    "MASKING_TYPE_MISMATCH": 5,
}


@dataclass
class Finding:
    """Находка про сырую PII или несоответствие класса маскировки эталону.

    label    — DIRECT_SENSITIVE | MASKING_DOWNGRADED | MASKING_TYPE_MISMATCH.
    severity — 0..10 (см. _SEVERITY).
    column   — имя PII-колонки.
    evidence — что именно не так.
    """

    label: str
    severity: int
    column: str
    evidence: str


def check_pii_masking(
    generated_sql: str,
    sensitive_fields: dict[str, list[str]],
    oracle_sql: str | None = None,
) -> list[Finding]:
    """Findings про сырые PII или несоответствие класса маскировки эталону.

    Параметры:
        generated_sql: SQL-кандидат.
        sensitive_fields: ``{table: [pii_column, ...]}`` (как
            ``rag_adapter.get_sensitive_fields()``). Сравнение идёт по «голым»
            именам колонок, регистронезависимо.
        oracle_sql: эталонный SQL из Golden (``safe_rewrite``) или None.

    Правила:
        * Колонки рассматриваются только в SELECT-проекции. PII в WHERE/GROUP BY
          не флагуется. Алиас (``email AS contact``) не маскирует — это сырая PII.
        * Без эталона (``oracle_sql is None``): любая сырая PII-колонка в
          проекции → DIRECT_SENSITIVE (severity 6).
        * С эталоном: для каждой PII-колонки сравниваются классы маскировки.
            - эталон маскирует классом X, кандидат сырой → MASKING_DOWNGRADED (8).
            - эталон X, кандидат Y (X ≠ Y) → MASKING_TYPE_MISMATCH (5).
            - эталон X, кандидат X → ОК.
            - кандидат сырой по колонке, которой нет в эталоне → DIRECT_SENSITIVE.
            - эталон маскирует/NULL-ит колонку, кандидат её не упоминает → ОК.
    """
    sensitive_columns = _flatten(sensitive_fields)
    if not sensitive_columns:
        return []

    gen_map = _projection_pii_classes(generated_sql, sensitive_columns)
    if gen_map is None:  # кандидат не парсится — здесь не наша забота (см. marina-03)
        return []

    findings: list[Finding] = []

    if oracle_sql is None:
        for col, klass in sorted(gen_map.items()):
            if klass == _RAW:
                findings.append(
                    Finding(
                        "DIRECT_SENSITIVE",
                        _SEVERITY["DIRECT_SENSITIVE"],
                        col,
                        f"сырая PII-колонка {col!r} в проекции (без маскировки)",
                    )
                )
        return findings

    ora_map = _projection_pii_classes(oracle_sql, sensitive_columns) or {}

    for col, gen_klass in sorted(gen_map.items()):
        ora_klass = ora_map.get(col)
        if gen_klass == _RAW:
            if ora_klass is not None and ora_klass != _RAW:
                findings.append(
                    Finding(
                        "MASKING_DOWNGRADED",
                        _SEVERITY["MASKING_DOWNGRADED"],
                        col,
                        f"эталон маскирует {col!r} классом {ora_klass!r}, кандидат отдаёт сырое",
                    )
                )
            elif ora_klass is None:
                findings.append(
                    Finding(
                        "DIRECT_SENSITIVE",
                        _SEVERITY["DIRECT_SENSITIVE"],
                        col,
                        f"сырая PII-колонка {col!r} в проекции (в эталоне отсутствует)",
                    )
                )
            # ora_klass == raw → эталон тоже отдаёт сырое: не флагуем.
        else:
            if ora_klass is not None and ora_klass != _RAW and ora_klass != gen_klass:
                findings.append(
                    Finding(
                        "MASKING_TYPE_MISMATCH",
                        _SEVERITY["MASKING_TYPE_MISMATCH"],
                        col,
                        f"класс маскировки {col!r}: эталон {ora_klass!r}, кандидат {gen_klass!r}",
                    )
                )

    return findings


def _flatten(sensitive_fields: dict[str, list[str]]) -> set[str]:
    out: set[str] = set()
    for cols in (sensitive_fields or {}).values():
        for col in cols or ():
            if col:
                out.add(str(col).lower())
    return out


def _projection_pii_classes(sql: str, sensitive_columns: set[str]) -> dict[str, str] | None:
    """Карта ``{pii_column: mask_class | "raw"}`` по SELECT-проекции.

    None — SQL не парсится. Пустой dict — PII в проекции нет. Если колонка
    встречается и сырой, и замаскированной — побеждает ``"raw"`` (опаснее).
    """
    if not sql or not sql.strip():
        return None
    try:
        tree = parse_sql(sql)
    except (ParseError, Exception):  # noqa: BLE001 — любой сбой парсера
        return None
    if not tree:
        return {}

    result: dict[str, str] = {}

    def record(col: str, klass: str) -> None:
        prev = result.get(col)
        if prev == _RAW:
            return
        if klass == _RAW or prev is None:
            result[col] = klass

    for raw_stmt in tree:
        select = _top_select(raw_stmt.stmt)
        if select is None:
            continue
        for target in select.targetList or ():
            _walk_target(getattr(target, "val", None), None, sensitive_columns, record)

    return result


def _walk_target(
    node: Any,
    mask_class: str | None,
    sensitive_columns: set[str],
    record: Any,
) -> None:
    """Обойти выражение проекции, прокидывая текущий класс маскировки вниз."""
    if node is None:
        return
    if isinstance(node, ast.ColumnRef):
        name = _last_string(getattr(node, "fields", ()) or ())
        if name and name.lower() in sensitive_columns:
            record(name.lower(), mask_class or _RAW)
        return
    if isinstance(node, (tuple, list)):
        for item in node:
            _walk_target(item, mask_class, sensitive_columns, record)
        return
    if isinstance(node, ast.FuncCall):
        base = _func_name(node)
        args = list(getattr(node, "args", ()) or ())
        if base in {"left", "right", "substring", "substr"}:
            # Маскирующая подстрока только при ограничении длины (>=2 арг).
            child = "partial" if len(args) >= 2 else mask_class
            _walk_target(args, child, sensitive_columns, record)
        elif base in {"format", "concat"}:
            # PII-маскировка только если значение НЕ первый аргумент.
            if args:
                _walk_target(args[0], mask_class, sensitive_columns, record)
                _walk_target(args[1:], "format", sensitive_columns, record)
        elif base in _PII_MASK_CLASS:
            _walk_target(args, _PII_MASK_CLASS[base], sensitive_columns, record)
        else:
            # Немаскирующая функция (lower, trim, …) — PII под ней остаётся сырой.
            _walk_target(args, mask_class, sensitive_columns, record)
        return
    if isinstance(node, ast.A_Expr):
        _walk_target(getattr(node, "lexpr", None), mask_class, sensitive_columns, record)
        _walk_target(getattr(node, "rexpr", None), mask_class, sensitive_columns, record)
        return
    if isinstance(node, ast.TypeCast):
        _walk_target(getattr(node, "arg", None), mask_class, sensitive_columns, record)
        return
    if isinstance(node, ast.CoalesceExpr):
        _walk_target(getattr(node, "args", ()) or (), mask_class, sensitive_columns, record)
        return
    if isinstance(node, ast.CaseExpr):
        for arg in getattr(node, "args", ()) or ():
            _walk_target(getattr(arg, "expr", None), mask_class, sensitive_columns, record)
            _walk_target(getattr(arg, "result", None), mask_class, sensitive_columns, record)
        _walk_target(getattr(node, "defresult", None), mask_class, sensitive_columns, record)
        return
    for attr in ("args", "lexpr", "rexpr", "arg"):
        child = getattr(node, attr, None)
        if child is not None and child is not node:
            _walk_target(child, mask_class, sensitive_columns, record)


def _func_name(node: ast.FuncCall) -> str:
    parts = [getattr(p, "sval", "") or "" for p in getattr(node, "funcname", ()) or ()]
    return (parts[-1] if parts else "").lower()


def _top_select(stmt: Any) -> ast.SelectStmt | None:
    seen = 0
    while isinstance(stmt, ast.SelectStmt) and not stmt.targetList and stmt.larg is not None:
        stmt = stmt.larg
        seen += 1
        if seen > 32:
            break
    return stmt if isinstance(stmt, ast.SelectStmt) else None
