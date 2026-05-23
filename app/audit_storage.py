"""
Запись истории прогонов в Postgres.

Дублирует JSON-трассы из app/trace.py: файлы остаются удобными для
trace_viewer, а Postgres дает агрегацию и SQL-запросы по истории.
Используется для будущего обновления датасета, метрик и дашбордов.
Если POSTGRES_DSN не задан или драйвер не установлен, модуль молча
пропускает запись, чтобы локальный smoke мог работать без Docker.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_TASK3_ROOT = Path(__file__).resolve().parent.parent / "TASK-3"
if str(_TASK3_ROOT) not in sys.path:
    sys.path.insert(0, str(_TASK3_ROOT))

from baseline1 import IterationLog, SystemResult, Vulnerability  # noqa: E402


def ensure_run(run_id: str, task: str, llm_mode: str) -> None:
    """
    Создать черновую строку audit_runs до сохранения iteration.

    У audit_iterations есть foreign key на audit_runs, поэтому перед
    первой итерацией нужен run-заголовок. Финальные поля будут обновлены
    в save_run в конце pipeline.
    """
    if not run_id:
        return

    _execute(
        """
        INSERT INTO audit_runs (run_id, task, llm_mode)
        VALUES (%s, %s, %s)
        ON CONFLICT (run_id) DO UPDATE SET
            task = EXCLUDED.task,
            llm_mode = EXCLUDED.llm_mode
        """,
        (run_id, task or "unknown", llm_mode),
    )


def save_run(system_result: SystemResult, run_id: str, llm_mode: str) -> None:
    """
    Сохранить финальную строку audit_runs.

    Функция делает upsert: если ensure_run уже создал черновик, поля
    обновятся финальным SQL, статусом approve и числом итераций.
    task берется из metadata, чтобы не менять контракт SystemResult.
    """
    if not run_id:
        return

    task = str(system_result.metadata.get("task", "") or "unknown")
    _execute(
        """
        INSERT INTO audit_runs (
            run_id, task, final_sql, approved, iterations_used, llm_mode
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id) DO UPDATE SET
            task = EXCLUDED.task,
            final_sql = EXCLUDED.final_sql,
            approved = EXCLUDED.approved,
            iterations_used = EXCLUDED.iterations_used,
            llm_mode = EXCLUDED.llm_mode
        """,
        (
            run_id,
            task,
            system_result.final_sql,
            system_result.approved,
            system_result.iterations_used,
            llm_mode,
        ),
    )


def save_iteration(run_id: str, iteration: IterationLog) -> None:
    """
    Сохранить одну итерацию audit_iterations.

    vulnerabilities кладутся в JSONB как список словарей с полями
    vuln_class, risk_score, description, recommendation и line_hint.
    Повторная запись той же итерации обновляет revision_notes.
    """
    if not run_id:
        return

    audit = iteration.audit_result
    vulnerabilities = [_vulnerability_to_dict(item) for item in audit.vulnerabilities]
    _execute_json(
        """
        INSERT INTO audit_iterations (
            run_id,
            iteration,
            sql_query,
            vulnerabilities,
            overall_risk_score,
            audit_summary,
            revision_notes
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id, iteration) DO UPDATE SET
            sql_query = EXCLUDED.sql_query,
            vulnerabilities = EXCLUDED.vulnerabilities,
            overall_risk_score = EXCLUDED.overall_risk_score,
            audit_summary = EXCLUDED.audit_summary,
            revision_notes = EXCLUDED.revision_notes
        """,
        (
            run_id,
            iteration.iteration,
            iteration.sql_query,
            vulnerabilities,
            audit.overall_risk_score,
            audit.summary,
            iteration.revision_notes,
        ),
    )


def _dsn() -> str:
    return os.environ.get("POSTGRES_DSN", "").strip()


def _execute(sql: str, params: tuple[Any, ...]) -> None:
    driver = _psycopg2()
    dsn = _dsn()
    if driver is None or not dsn:
        return

    try:
        with driver.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
    except driver.Error:
        return


def _execute_json(sql: str, params: tuple[Any, ...]) -> None:
    driver = _psycopg2()
    dsn = _dsn()
    if driver is None or not dsn:
        return

    json_params = list(params)
    json_params[3] = driver.extras.Json(json_params[3])

    try:
        with driver.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(json_params))
    except driver.Error:
        return


def _psycopg2():
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        return None
    return psycopg2


def _vulnerability_to_dict(item: Vulnerability) -> dict[str, Any]:
    return {
        "vuln_class": item.vuln_class,
        "risk_score": item.risk_score,
        "description": item.description,
        "recommendation": item.recommendation,
        "line_hint": item.line_hint,
    }
