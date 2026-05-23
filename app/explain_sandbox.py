"""
Безопасный EXPLAIN на тестовой базе через audit_ro.

Финальный SQL никогда не выполняется на боевых данных. Здесь мы только
показываем планировщику запрос, читаем план и откатываем транзакцию.
Основной DSN - POSTGRES_AUDIT_DSN с read-only ролью audit_ro. Запрос
разрешается ровно один statement через PostgreSQL parser, транзакция
стартует READ ONLY - двойная защита от случайных побочных эффектов.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from app import sql_parsing


@dataclass
class ExplainResult:
    """Результат проверки плана запроса для одной попытки."""

    ok: bool
    plan: str | None
    error: str | None
    skipped: bool = False
    # Phase 0.5 — sub-timing EXPLAIN-сэндбокса. Каждая фаза показывает
    # сколько секунд ушло: psycopg2.connect, SET TRANSACTION + statement_timeout,
    # сам EXPLAIN, fetchall. total_sec — сумма (без rollback/close).
    # Помогает диагностировать когда узел explain_sandbox растягивается:
    # медленный DSN-handshake / медленный план / отдача больших планов.
    sub_timings: dict[str, float] = field(default_factory=dict)


def _target_dsn(explicit_dsn: str | None) -> str:
    """Выбрать DSN для EXPLAIN: явный аргумент, audit_ro, затем старый demo."""
    if explicit_dsn:
        return explicit_dsn
    return os.environ.get("POSTGRES_AUDIT_DSN", "") or os.environ.get("POSTGRES_DSN", "")


def run_explain(sql: str, dsn: str | None = None, timeout_sec: int = 5) -> ExplainResult:
    """
    Прогнать EXPLAIN над одним statement и откатить транзакцию.

    Возвращает ExplainResult. Если в строке несколько statement или SQL
    пустой - возвращаем ошибку без обращения в базу. Если psycopg2 нет
    или DSN не задан - помечаем skipped=True и не падаем.
    """

    if not sql or not sql.strip():
        return ExplainResult(ok=False, plan=None, error="Пустой SQL-запрос.")

    if not sql_parsing.is_single_statement(sql):
        return ExplainResult(
            ok=False,
            plan=None,
            error="EXPLAIN допускает только один валидный SQL-оператор за раз.",
        )

    target_dsn = _target_dsn(dsn)
    if not target_dsn:
        return ExplainResult(ok=True, plan=None, error=None, skipped=True)

    try:
        import psycopg2
    except ImportError:
        return ExplainResult(
            ok=True,
            plan=None,
            error="psycopg2 не установлен, шаг EXPLAIN пропущен.",
            skipped=True,
        )

    conn = None
    sub_timings: dict[str, float] = {}
    try:
        t0 = time.perf_counter()
        conn = psycopg2.connect(target_dsn, connect_timeout=timeout_sec)
        sub_timings["connect_sec"] = round(time.perf_counter() - t0, 4)
        conn.autocommit = False
        with conn.cursor() as cur:
            t1 = time.perf_counter()
            # READ ONLY гарантирует, что даже если в SQL ошибочно попал
            # DML, планировщик отклонит его до построения плана.
            cur.execute("SET TRANSACTION READ ONLY;")
            cur.execute("SET statement_timeout TO %s;", (timeout_sec * 1000,))
            sub_timings["setup_sec"] = round(time.perf_counter() - t1, 4)

            t2 = time.perf_counter()
            cur.execute("EXPLAIN " + sql)
            sub_timings["execute_sec"] = round(time.perf_counter() - t2, 4)

            t3 = time.perf_counter()
            plan_rows = cur.fetchall()
            sub_timings["fetch_sec"] = round(time.perf_counter() - t3, 4)
        plan_text = "\n".join(row[0] for row in plan_rows if row and row[0])
        sub_timings["total_sec"] = round(
            sub_timings.get("connect_sec", 0.0)
            + sub_timings.get("setup_sec", 0.0)
            + sub_timings.get("execute_sec", 0.0)
            + sub_timings.get("fetch_sec", 0.0),
            4,
        )
        return ExplainResult(ok=True, plan=plan_text, error=None, sub_timings=sub_timings)
    except psycopg2.Error as exc:
        sub_timings["total_sec"] = round(
            sub_timings.get("connect_sec", 0.0)
            + sub_timings.get("setup_sec", 0.0)
            + sub_timings.get("execute_sec", 0.0)
            + sub_timings.get("fetch_sec", 0.0),
            4,
        )
        return ExplainResult(
            ok=False,
            plan=None,
            error=str(exc).strip(),
            sub_timings=sub_timings,
        )
    finally:
        if conn is not None:
            try:
                conn.rollback()
            finally:
                conn.close()
