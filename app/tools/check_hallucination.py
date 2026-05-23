"""
Tool: check_hallucination — детект упоминаний несуществующих таблиц/колонок
в SQL.

Использует pglast AST через app.sql_parsing и schema overlay через
app.rag_adapter. Никаких regex-эвристик: только то что реально парсится
парсером PostgreSQL.

Модель обязана вызвать этот tool после первой генерации SQL. Если
unknown_tables / unknown_columns не пустые — модель должна переписать
SQL, используя только known-объекты.
"""

from __future__ import annotations

from typing import Any


SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "check_hallucination",
        "description": (
            "Проверь сгенерированный SQL на упоминание несуществующих таблиц "
            "и колонок схемы GreenData. Возвращает unknown_tables и "
            "unknown_columns. Вызывай этот tool СРАЗУ после написания первого "
            "draft SQL. Если что-то найдено — перепиши SQL, используя только "
            "known-объекты, и вызови tool ещё раз для подтверждения."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "Sgenerированный SQL для проверки.",
                }
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
    },
}


def invoke(arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Выход:
        {
            "ok": bool,                       # true если ничего не галлюцинировано
            "unknown_tables": [...],          # таблицы которых нет в overlay
            "unknown_columns": {table: [...]},# колонки которых нет в schema
            "known_tables_referenced": [...], # таблицы которые SQL действительно упоминает
            "parse_error": str | null,        # ошибка pglast если SQL невалидный
        }
    """
    sql = str(arguments.get("sql") or "").strip()
    if not sql:
        return {"ok": False, "error": "empty_sql"}

    from app import sql_parsing, rag_adapter  # ленивый импорт чтобы избежать циклов

    parsed = sql_parsing.parse(sql)
    if parsed.broken:
        return {
            "ok": False,
            "parse_error": parsed.error or "unknown parse error",
            "unknown_tables": [],
            "unknown_columns": {},
            "known_tables_referenced": [],
        }
    parsed_tables = parsed.identifiers.get("tables") or []
    parsed_columns = parsed.identifiers.get("columns") or []

    schema_tables = (rag_adapter._load_schema() or {}).get("tables") or {}
    known_columns_by_table = {
        str(name): set((meta.get("columns") or {}).keys())
        for name, meta in schema_tables.items()
        if isinstance(meta, dict)
    }
    known_table_names = set(known_columns_by_table.keys())

    # pglast выдаёт идентификаторы с возможным quoting и schema-префиксом.
    # Нормализуем к base-name lowercased.
    def _base(name: str) -> str:
        return name.rsplit(".", 1)[-1].strip('"').lower()

    referenced_raw = parsed_tables
    referenced = sorted({_base(t) for t in referenced_raw if t})
    unknown_tables = sorted([t for t in referenced if t not in known_table_names])
    known_tables_referenced = sorted([t for t in referenced if t in known_table_names])

    # Колонки: pglast возвращает их без привязки к таблице. Используем простую
    # эвристику — все упомянутые column-names проверяем против объединения
    # known колонок только тех таблиц, что упомянуты в FROM/JOIN.
    referenced_cols = sorted({_base(c) for c in parsed_columns if c})
    union_known_cols: set[str] = set()
    for t in known_tables_referenced:
        union_known_cols.update(known_columns_by_table.get(t, set()))
    # Системные псевдо-колонки и звёздочки исключаем
    EXCLUDE_COLS = {"*", "count", "now", "current_date", "current_timestamp", "true", "false", "null"}
    unknown_columns_flat = sorted([
        c for c in referenced_cols
        if c not in union_known_cols and c not in EXCLUDE_COLS and not c.isdigit()
    ])
    unknown_columns: dict[str, list[str]] = {}
    if unknown_columns_flat:
        # Атрибутировать колонки к таблицам тяжело без полного AST-walker.
        # Возвращаем как special "_unattributed" — модель поймёт что нужно явно
        # указать table.column или удалить лишнее.
        unknown_columns["_unattributed"] = unknown_columns_flat

    ok = not unknown_tables and not unknown_columns
    return {
        "ok": ok,
        "unknown_tables": unknown_tables,
        "unknown_columns": unknown_columns,
        "known_tables_referenced": known_tables_referenced,
        "parse_error": None,
    }
