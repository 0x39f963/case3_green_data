"""
Tool: explain_query — реальный EXPLAIN на тестовой PG read-only.

Использует app.explain_sandbox.run_explain — тот же модуль, что
запускает узел explain_sandbox в pipeline. То есть модель видит ТОТ ЖЕ
вердикт планировщика, что и safety net на этапе аудита.

Применение: модель вызывает в конце цикла, перед финальной отдачей SQL,
чтобы убедиться что план строится. Если ok=false — переписать.
"""

from __future__ import annotations

from typing import Any


SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "explain_query",
        "description": (
            "Запусти EXPLAIN sgenerированного SQL на тестовой PostgreSQL под "
            "read-only ролью audit_ro. Возвращает план или ошибку. Это финальная "
            "проверка перед отдачей SQL — если ok=false, перепиши SQL. "
            "EXPLAIN использует statement_timeout 5 секунд."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "Финальный SQL для проверки.",
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
            "ok": bool,                # успешно ли построен план
            "skipped": bool,           # был ли тест пропущен (нет psycopg2 / нет DSN)
            "plan": str | null,        # текст EXPLAIN (первые 4000 chars)
            "error": str | null,       # текст ошибки planner если ok=false
            "sub_timings": {            # тайминги фаз (Phase 0.5)
                "connect_sec": float,
                "setup_sec": float,
                "execute_sec": float,
                "fetch_sec": float,
                "total_sec": float,
            }
        }
    """
    sql = str(arguments.get("sql") or "").strip()
    if not sql:
        return {"ok": False, "error": "empty_sql", "skipped": False, "sub_timings": {}}

    from app import explain_sandbox

    result = explain_sandbox.run_explain(sql)
    plan = result.plan or ""
    if len(plan) > 3000:
        plan_text = plan[:2000] + "\n\n[truncated]\n\n" + plan[-1000:]
    else:
        plan_text = plan
    return {
        "ok": bool(result.ok),
        "skipped": bool(result.skipped),
        "plan": plan_text if plan_text else None,
        "error": result.error,
        "sub_timings": result.sub_timings or {},
    }
