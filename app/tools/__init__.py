"""
Phase 5 — Tool-calling agentic mode.

Набор детерминистских tools, которые LLM-генератор обязан вызвать
в ходе написания SQL. Это сдвигает архитектуру с post-hoc guard
("сначала сгенерируй SQL, потом мы проверим") на agentic-loop
("сгенерируй → проверь tool-ом → исправь если нужно → отдай SQL").

Безопасность: tools работают ТОЛЬКО на чтение (read-only) поверх
существующих модулей. Никаких сетевых вызовов, никаких прямых
INSERT/UPDATE. sql_guard + EXPLAIN остаются safety net на случай
если модель проигнорировала tool_call.

Контракт OpenAI/OpenRouter tool spec:
    {
        "type": "function",
        "function": {
            "name": "<tool_name>",
            "description": "<...>",
            "parameters": {<JSON Schema>}
        }
    }

Каждый tool в этом пакете:
- экспортирует `SPEC: dict` — OpenAI tool spec для регистрации в provider
- экспортирует `invoke(arguments: dict) -> dict` — выполнение

ToolDispatcher (см. dispatcher.py) маршрутизирует tool_calls из ответа
LLM к нужному invoke().
"""

from __future__ import annotations

from app.tools.check_hallucination import SPEC as CHECK_HALLUCINATION_SPEC, invoke as check_hallucination_invoke
from app.tools.get_sensitive_fields import SPEC as GET_SENSITIVE_FIELDS_SPEC, invoke as get_sensitive_fields_invoke
from app.tools.explain_query import SPEC as EXPLAIN_QUERY_SPEC, invoke as explain_query_invoke
from app.tools.get_approved_joins import SPEC as GET_APPROVED_JOINS_SPEC, invoke as get_approved_joins_invoke


ALL_SPECS: list[dict] = [
    CHECK_HALLUCINATION_SPEC,
    GET_SENSITIVE_FIELDS_SPEC,
    EXPLAIN_QUERY_SPEC,
    GET_APPROVED_JOINS_SPEC,
]


_REGISTRY = {
    "check_hallucination": check_hallucination_invoke,
    "get_sensitive_fields": get_sensitive_fields_invoke,
    "explain_query": explain_query_invoke,
    "get_approved_joins": get_approved_joins_invoke,
}


class ToolError(RuntimeError):
    """Tool вернул семантическую ошибку (не падение Python). Возвращается
    модели как часть tool_result чтобы она могла адаптироваться."""


def dispatch(name: str, arguments: dict) -> dict:
    """
    Выполнить tool по имени. Возвращает JSON-сериализуемый dict.

    Если tool не зарегистрирован — возвращает {"error": "unknown_tool: ..."}
    вместо raise чтобы LLM могла получить нормальный feedback и попробовать
    другое имя.

    Любые исключения внутри tool ловятся и заворачиваются в
    {"error": str, "exception_class": ...} — модель в tool_result увидит
    обычный JSON и сможет адаптироваться.
    """
    invoke_fn = _REGISTRY.get(name)
    if invoke_fn is None:
        return {
            "error": "unknown_tool: " + name,
            "available_tools": sorted(_REGISTRY.keys()),
        }
    try:
        result = invoke_fn(arguments or {})
        if not isinstance(result, dict):
            return {"error": "tool_invalid_return", "got": type(result).__name__}
        return result
    except Exception as exc:  # noqa: BLE001 — tool isolation
        return {
            "error": str(exc)[:240],
            "exception_class": exc.__class__.__name__,
            "tool": name,
        }


__all__ = [
    "ALL_SPECS",
    "ToolError",
    "dispatch",
]
