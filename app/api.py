"""
HTTP-эндпоинт нашей системы.

Дает /run для прогона цикла и /health для проверки состояния и
текущего режима LLM. /run держит общий latency budget из env
LATENCY_SOFT_SEC и LATENCY_HARD_SEC (Docker default - 300 sec soft
warning и 600 sec hard timeout). Ошибка конфигурации дает HTTP 400,
недоступность провайдера - HTTP 503, hard timeout - HTTP 504.
"""

from __future__ import annotations

from typing import Any, Literal

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

_logger = logging.getLogger("app.api")


@asynccontextmanager
async def _lifespan(app):  # type: ignore[no-untyped-def]
    """Startup: reconcile orphan analysis/judge/oracle jobs.

    Контейнер часто пересоздаётся через `docker compose up -d`, subprocess
    супервизоров умирает, а БД продолжает показывать running/partial. UI
    висит. Reconcile-функции каждого супервизора при первом импорте сейчас
    же помечают такие job-ы как `interrupted`.
    """
    try:
        from app import analysis_job_supervisor, judge_job_supervisor, oracle_job_supervisor
        totals = []
        for module, label in (
            (analysis_job_supervisor, "analysis"),
            (judge_job_supervisor, "judge"),
            (oracle_job_supervisor, "oracle"),
        ):
            try:
                n = module.reconcile_orphan_jobs()
            except Exception as exc:
                _logger.warning("reconcile_orphan_jobs(%s) failed: %s", label, exc)
                continue
            if n:
                _logger.info("reconcile_orphan_jobs(%s): reset %d", label, n)
            totals.append((label, n))
        _logger.info("startup reconcile summary: %s", totals)
    except Exception as exc:
        _logger.warning("startup reconcile bootstrap failed: %s", exc)
    yield

from app import llm_provider, rag_adapter
from app.llm_provider import LLMConfigError, ProviderUnavailable
from app.pipeline_service import (
    MAX_ITERATIONS_LIMIT,
    LatencyConfigError,
    PipelineTimeout,
    execute_run,
    provider_error_message,
)
from app.web_audits import router as audits_router
from app.web_chat import router as web_router


app = FastAPI(
    title="Case 3 SQL Security System",
    version="0.1.1",
    description="HTTP-обертка над оркестратором генерации и проверки SQL.",
    lifespan=_lifespan,
)
app.include_router(web_router)
app.include_router(audits_router)


class RunRequest(BaseModel):
    """Тело запроса на /run."""

    task: str = Field(..., min_length=1, description="Текстовая задача аналитика.")
    llm_mode: str | None = Field(
        default=None,
        description="Override LLM_MODE только для этого запроса.",
    )
    llm_generator_model: str | None = Field(
        default=None,
        description="Override LLM_GENERATOR_MODEL только для этого запроса.",
    )
    openrouter_provider: str | None = Field(
        default=None,
        description="OpenRouter provider routing: provider.only=[value], allow_fallbacks=false.",
    )
    judge_openrouter_provider: str | None = Field(
        default=None,
        description="OpenRouter provider routing for Stage 4 judge only.",
    )
    judge_backend: str | None = Field(
        default=None,
        description="Override Stage 4 judge backend только для этого запроса.",
    )
    prompt_check_enabled: bool | None = Field(
        default=None,
        description="Per-request switch for prompt-injection check.",
    )
    prompt_check_backend: str | None = Field(
        default=None,
        description="Prompt-check preset/backend key for this request.",
    )
    prompt_check_model: str | None = Field(
        default=None,
        description="Prompt-check provider model override for this request.",
    )
    prompt_check_openrouter_provider: str | None = Field(
        default=None,
        description="OpenRouter provider routing for prompt-check only.",
    )
    max_iterations: int = Field(
        default=MAX_ITERATIONS_LIMIT,
        ge=1,
        le=MAX_ITERATIONS_LIMIT,
        description="Сколько попыток дать циклу. Жесткий потолок - 5 по контракту заказчика.",
    )
    profile: bool = Field(
        default=False,
        description=(
            "Phase 0.8 — флаг диагностики 20-30с. При true в metadata "
            "прогона выставляется profile=true; внутренние слои могут "
            "включить расширенные тайминги (httpx phases в P1). Базовые "
            "тайминги (walltime/retry/RAG/EXPLAIN) собираются всегда."
        ),
    )
    isolation_mode: Literal["clean", "production", "snapshot"] | None = Field(
        default=None,
        description="Benchmark isolation mode. clean skips learned solutions context.",
    )
    isolation: Literal["clean", "production", "snapshot"] | None = Field(
        default=None,
        description="Alias for isolation_mode used by benchmark runner.",
    )


@app.get("/health")
def health() -> dict[str, Any]:
    """
    Жив ли сервис и какая модель сейчас сконфигурирована.
    Конфигурационные ошибки превращаются в HTTP 400 - так клиенту сразу
    понятно, что нужно поправить .env, а не дергать /run и ловить отказ.
    """
    try:
        info = llm_provider.describe_current_mode()
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", **info, "rag": rag_adapter.get_rag_diagnostics()}


@app.post("/run")
async def run(req: RunRequest) -> dict[str, Any]:
    """
    Запустить полный цикл по тексту задачи. Возвращает JSON, который
    точно повторяет SystemResult.__dict__ с вложенными iterations_log и
    vulnerabilities. Один HTTP-запрос - один прогон оркестратора с
    собственной JSON-трассой.
    """
    try:
        return await execute_run(
            task=req.task,
            llm_mode=req.llm_mode,
            llm_generator_model=req.llm_generator_model,
            openrouter_provider=req.openrouter_provider,
            judge_openrouter_provider=req.judge_openrouter_provider,
            judge_backend=req.judge_backend,
            prompt_check_enabled=req.prompt_check_enabled,
            prompt_check_backend=req.prompt_check_backend,
            prompt_check_model=req.prompt_check_model,
            prompt_check_openrouter_provider=req.prompt_check_openrouter_provider,
            max_iterations=req.max_iterations,
            profile=req.profile,
            isolation_mode=req.isolation_mode or req.isolation,
        )
    except (LLMConfigError, LatencyConfigError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail=provider_error_message(exc)) from exc
    except PipelineTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
