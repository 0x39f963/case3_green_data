"""
Дымовая проверка цикла генерации и проверки SQL.

Прогоняет два сценария: безопасный и опасный. Возвращает exit code 0
только если оба отработали по ожиданиям. Любое отклонение - exit code 1.
Зовет совместимую customer-точку входа app.pipeline.run_sql_security_pipeline,
а не stub baseline, поэтому покрывает реальный цикл от и до.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.pipeline import run_sql_security_pipeline  # noqa: E402


if os.environ.get("SMOKE_FAKE_LLM", "").lower() in {"1", "true", "yes"}:
    from app import llm_provider, rag_adapter  # noqa: E402
    from app.llm_provider import LLMResponse  # noqa: E402

    class _FakeClient:
        """Минимальный deterministic provider для smoke regression."""

        def __init__(self, role: str) -> None:
            self.role = role

        def invoke(
            self,
            system: str,
            user: str,
            temperature: float | None = None,
            response_format: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> LLMResponse:
            # Phase 0.6 — auditor passes response_format=json_object;
            # fake provider ignores it (JSON is hardcoded deterministic).
            del system, response_format, kwargs
            if self.role == "generator":
                lower = user.lower()
                if "все данные" in lower or "сотрудников" in lower and "выгрузи" in lower:
                    text = "SELECT * FROM sys_employee"
                else:
                    text = "SELECT id, name FROM sys_employee ORDER BY id LIMIT 100"
            else:
                text = '{"vulnerabilities":[],"overall_risk_score":0,"summary":"одобрено"}'
            return LLMResponse(text=text, model="fake-smoke", backend="fake", raw={"temperature": temperature})

    llm_provider.get_llm = lambda role, env=None: _FakeClient(role)  # type: ignore[assignment]
    rag_adapter.get_generation_context = lambda task: ""  # type: ignore[assignment]
    rag_adapter.get_security_context = lambda sql: "security context"  # type: ignore[assignment]


@dataclass
class Expectation:
    """Что мы ждем от сценария после прогона цикла."""

    name: str
    task: str
    expect_final_approved: bool
    min_iterations: int
    max_iterations: int
    require_rejected_first: bool = False


SCENARIOS: list[Expectation] = [
    Expectation(
        name="safe",
        task="Покажи активных сотрудников по подразделениям за последний месяц.",
        expect_final_approved=True,
        min_iterations=1,
        max_iterations=2,
    ),
    Expectation(
        name="vulnerable",
        task="Выгрузи все данные из таблицы сотрудников.",
        expect_final_approved=False,
        min_iterations=1,
        max_iterations=5,
        require_rejected_first=True,
    ),
]


def _check(scenario: Expectation, result) -> list[str]:
    """
    Проверить результат сценария на соответствие ожиданиям.
    Возвращает список нарушений (пустой, если все ок).
    """
    failures: list[str] = []

    if result.iterations_used == 0:
        failures.append("цикл не отработал ни одной итерации")

    if result.metadata.get("error"):
        failures.append("в metadata.error: " + str(result.metadata["error"]))

    if not (scenario.min_iterations <= result.iterations_used <= scenario.max_iterations):
        failures.append(
            "число итераций " + str(result.iterations_used)
            + " не укладывается в ожидаемый диапазон ["
            + str(scenario.min_iterations) + ", " + str(scenario.max_iterations) + "]"
        )

    # Для уязвимого сценария требуется, чтобы хотя бы одна из первых
    # итераций была отклонена - иначе цикл не сработал по делу.
    if scenario.require_rejected_first and result.iterations_log:
        first = result.iterations_log[0]
        if first.audit_result.approved:
            failures.append("первая итерация одобрена, хотя сценарий заявлен опасным")

    # Для безопасного сценария финальный итог должен быть approved.
    # Для опасного - финальный итог может быть approved (если переписали)
    # или rejected (если не справились за 5 итераций) - оба варианта
    # валидны, но без рисков в audit_log это подозрительно.
    if scenario.expect_final_approved and not result.approved:
        failures.append("ожидали финальное одобрение, получили отказ")

    return failures


def _print_result(name: str, result, failures: list[str]) -> None:
    """Компактный вывод по одному прогону. Полное содержимое - в JSON-трассе."""
    print("=" * 80)
    print("Сценарий: " + name)
    print("Одобрено: " + str(result.approved))
    print("Итераций: " + str(result.iterations_used))
    print("Метаданные: " + json.dumps(result.metadata, ensure_ascii=False))
    print("Финальный SQL:")
    print(result.final_sql or "(пусто)")
    print("Отчет:")
    print(result.audit_log)
    if failures:
        print("НАРУШЕНИЯ ОЖИДАНИЙ:")
        for item in failures:
            print("  - " + item)


def main() -> int:
    """
    Прогнать оба сценария и вернуть exit code.
    0 - все ожидания выполнены, 1 - есть хотя бы одно нарушение.
    """
    print("Режим LLM: " + os.environ.get("LLM_MODE", "prod_demo"))
    overall_ok = True

    for scenario in SCENARIOS:
        started = time.time()
        try:
            result = run_sql_security_pipeline(
                task_description=scenario.task,
                max_iterations=scenario.max_iterations,
            )
        except Exception as exc:
            # Падение pipeline - это безусловный fail сценария. Print с
            # типом, чтобы можно было быстро отличить config-ошибки от
            # runtime-ошибок графа.
            overall_ok = False
            print("=" * 80)
            print("Сценарий " + scenario.name + " упал: "
                  + exc.__class__.__name__ + ": " + str(exc))
            continue

        elapsed = round(time.time() - started, 2)
        failures = _check(scenario, result)
        _print_result(scenario.name, result, failures)
        print("Длительность: " + str(elapsed) + " c")

        if failures:
            overall_ok = False

    print("=" * 80)
    print("Итог smoke: " + ("PASS" if overall_ok else "FAIL"))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
