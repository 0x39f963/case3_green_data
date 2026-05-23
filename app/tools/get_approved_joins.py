"""
Tool: get_approved_joins — список одобренных join-ключей между парами таблиц.

Использует deploy/schema_overlay.json.approved_join_keys (211 ключей,
бизнес-подтверждённые). Модель видит ровно те же ключи, что sql_guard
проверяет на WRONG_JOIN_PATH.

Применение: при написании JOIN-запросов модель проверяет что используемый
ON-ключ есть в approved-списке. Иначе аудитор поднимет WRONG_JOIN_PATH.
"""

from __future__ import annotations

from typing import Any


SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_approved_joins",
        "description": (
            "Получи бизнес-одобренные JOIN-ключи между двумя таблицами. Если "
            "JOIN использует не из этого списка — sql_guard поднимет "
            "WRONG_JOIN_PATH. Вызывай tool перед написанием каждого JOIN."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_a": {
                    "type": "string",
                    "description": "Первая таблица (обычно слева в JOIN).",
                },
                "table_b": {
                    "type": "string",
                    "description": "Вторая таблица (справа в JOIN).",
                },
            },
            "required": ["table_a", "table_b"],
            "additionalProperties": False,
        },
    },
}


def _norm(name: str) -> str:
    return str(name).rsplit(".", 1)[-1].strip('"').lower()


def invoke(arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Выход:
        {
            "table_a": str,
            "table_b": str,
            "approved_keys": [{"left": "a.col", "right": "b.col"}, ...],
            "has_approved": bool,
            "hint": str | null,    # подсказка если ключей нет
        }
    """
    from app import rag_adapter

    a = _norm(arguments.get("table_a") or "")
    b = _norm(arguments.get("table_b") or "")
    if not a or not b:
        return {"error": "table_a и table_b обязательны"}

    overlay = rag_adapter._load_overlay() or {}
    # Реальный формат deploy/schema_overlay.json (211 ключей):
    #   tables[<src>].approved_join_keys: [
    #       {"to": "<dst>", "on": "<src_col>", "to_column": "<dst_col>"},
    #       ...
    #   ]
    # То есть запись означает SRC.on -> DST.to_column. Top-level
    # approved_join_keys в overlay не существует — раньше код искал
    # его и всегда возвращал пустой список.
    tables = overlay.get("tables") or {}
    if not isinstance(tables, dict):
        return {
            "table_a": a, "table_b": b,
            "approved_keys": [],
            "has_approved": False,
            "hint": "overlay.tables отсутствует или повреждён",
        }

    matches: list[dict[str, str]] = []

    def _scan(src: str, dst: str) -> None:
        meta = tables.get(src) or {}
        if not isinstance(meta, dict):
            return
        joins = meta.get("approved_join_keys") or []
        if not isinstance(joins, list):
            return
        for j in joins:
            if not isinstance(j, dict):
                continue
            to_table = _norm(j.get("to") or "")
            if to_table != dst:
                continue
            on_col = str(j.get("on") or "").strip()
            to_col = str(j.get("to_column") or "").strip()
            if not on_col or not to_col:
                continue
            matches.append({
                "left": src + "." + on_col,
                "right": dst + "." + to_col,
                "source": src,
            })

    def _scan_legacy() -> None:
        joins = overlay.get("approved_join_keys") or []
        if not isinstance(joins, list):
            return
        for item in joins:
            if not isinstance(item, dict):
                continue
            left_table = _norm(item.get("left_table") or "")
            right_table = _norm(item.get("right_table") or "")
            left_col = str(item.get("left_column") or "").strip()
            right_col = str(item.get("right_column") or "").strip()
            if not left_table or not right_table or not left_col or not right_col:
                continue
            if left_table == a and right_table == b:
                matches.append({
                    "left": a + "." + left_col,
                    "right": b + "." + right_col,
                    "source": "legacy",
                })
            elif left_table == b and right_table == a:
                matches.append({
                    "left": b + "." + left_col,
                    "right": a + "." + right_col,
                    "source": "legacy",
                })

    _scan(a, b)
    if a != b:
        _scan(b, a)
    _scan_legacy()

    has_approved = bool(matches)
    hint = None
    if not has_approved:
        hint = (
            "Между " + a + " и " + b + " нет approved JOIN-ключей. "
            "Подумай: правильные ли это таблицы для задачи? Если да — "
            "используй явно описанный FK из schema (но это может быть "
            "помечено как WRONG_JOIN_PATH в аудите). Лучше переформулировать "
            "через промежуточную справочную таблицу."
        )

    return {
        "table_a": a,
        "table_b": b,
        "approved_keys": matches,
        "has_approved": has_approved,
        "hint": hint,
    }
