"""
Tool: get_sensitive_fields — список чувствительных колонок для таблиц.

Использует app.rag_adapter.get_sensitive_fields() (3-слойный union из
Phase 4.3: marina schema → overlay → regex-auto-detect). Модель видит
ровно тот же набор PII, что и rule-based sql_guard на этапе аудита.

Применение: модель вызывает после check_hallucination, проверяет что
SELECT-список не выдаёт PII напрямую, без маскирования.
"""

from __future__ import annotations

from typing import Any


SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_sensitive_fields",
        "description": (
            "Получи список чувствительных (PII) колонок для указанных таблиц. "
            "Источник — bundled overlay + auto-detected regex по схеме GreenData. "
            "Если колонка попала в список — её НЕЛЬЗЯ выдавать напрямую в SELECT "
            "без маскирования (LEFT(col, 3) || '...', или агрегат COUNT()). "
            "Вызывай tool после check_hallucination."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tables": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Список таблиц, которые используются в SQL. Пусто — "
                        "вернётся ВСЯ карта чувствительных полей (полезно если "
                        "ещё не решено какие таблицы брать)."
                    ),
                }
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}


def invoke(arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Выход:
        {
            "sensitive_fields": {table: [col1, col2, ...], ...},
            "tables_queried": [...],            # что было запрошено
            "tables_with_sensitive": [...],     # из запрошенных, у которых есть PII
        }
    """
    from app import rag_adapter

    requested = [str(t).strip().lower() for t in (arguments.get("tables") or []) if t]
    full_map = rag_adapter.get_sensitive_fields() or {}

    if not requested:
        return {
            "sensitive_fields": full_map,
            "tables_queried": [],
            "tables_with_sensitive": sorted(full_map.keys()),
            "tip": (
                "Передай tables=[...] чтобы получить только нужные таблицы. "
                "Сейчас возвращена полная карта."
            ),
        }

    out: dict[str, list[str]] = {}
    for t in requested:
        cols = full_map.get(t)
        if cols:
            out[t] = sorted(cols)

    return {
        "sensitive_fields": out,
        "tables_queried": requested,
        "tables_with_sensitive": sorted(out.keys()),
    }
