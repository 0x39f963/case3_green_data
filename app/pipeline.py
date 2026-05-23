"""
Совместимая точка входа для заказчика.

Контракты заказчика в TASK-3/baseline1.py запрещено менять. Поэтому
точка входа run_sql_security_pipeline там продолжает создавать stub-
классы. В нашем коде мы держим зеркальную функцию с такой же
сигнатурой, но внутри она поднимает реальные реализации из app.

Заказчику и smoke-скрипту достаточно импортировать ее так:
    from app.pipeline import run_sql_security_pipeline
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_TASK3_ROOT = Path(__file__).resolve().parent.parent / "TASK-3"
if str(_TASK3_ROOT) not in sys.path:
    sys.path.insert(0, str(_TASK3_ROOT))

from baseline1 import SQLSecuritySystem as _BaseSystem, SystemResult  # noqa: E402

from app.auditor import SecurityAuditor  # noqa: E402
from app.generator import SQLGenerator  # noqa: E402
from app import llm_provider  # noqa: E402
from app.orchestrator import SQLSecuritySystem  # noqa: E402


def run_sql_security_pipeline(
    task_description: str,
    db_schema: dict[str, Any] | None = None,
    max_iterations: int = _BaseSystem.DEFAULT_MAX_ITERATIONS,
    generator_kwargs: dict[str, Any] | None = None,
    auditor_kwargs: dict[str, Any] | None = None,
    llm_mode: str | None = None,
    llm_generator_model: str | None = None,
) -> SystemResult:
    """
    Прогнать полный цикл генерации и проверки SQL.

    Сигнатура совпадает с baseline.run_sql_security_pipeline. Отличие
    одно: внутри собираются наши реальные SQLGenerator/SecurityAuditor/
    SQLSecuritySystem, а не stub-классы baseline. Возвращает
    SystemResult c заполненным audit_log и iterations_log.
    """
    with llm_provider.model_override(
        llm_mode=llm_mode,
        llm_generator_model=llm_generator_model,
    ):
        generator = SQLGenerator(db_schema=db_schema or {}, **(generator_kwargs or {}))
        auditor = SecurityAuditor(**(auditor_kwargs or {}))
        system = SQLSecuritySystem(
            generator=generator,
            auditor=auditor,
            max_iterations=max_iterations,
        )
        return system.run(task_description=task_description)
