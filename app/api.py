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

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

_logger = logging.getLogger("app.api")
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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
    except ImportError:
        yield
        return

    try:
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


app = FastAPI(
    title="Case 3 SQL Security System",
    version="0.1.1",
    description="HTTP-обертка над оркестратором генерации и проверки SQL.",
    lifespan=_lifespan,
)
try:
    from app.web_chat import router as web_router
except ImportError:
    web_router = None

try:
    from app.web_audits import router as audits_router
except ImportError:
    audits_router = None

if web_router is not None:
    app.include_router(web_router)
if audits_router is not None:
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
    codex_reasoning_effort: str | None = Field(
        default=None,
        description="Codex CLI generator reasoning effort for this request.",
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


@app.get("/prompts/candidates", response_class=HTMLResponse)
def prompt_candidates_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "prompt_candidates.html",
        {
            "active_section": "prompt-candidates",
            "audits_enabled": True,
        },
    )


@app.get("/web/api/prompt-candidates")
def prompt_candidates_api(limit: int = 500) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 500), 2000))
    rows = _prompt_candidate_rows(safe_limit)
    series: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get("prompt_key") or "unknown")
        series.setdefault(key, []).append(
            {
                "time": row.get("time"),
                "quality_score": row.get("quality_score"),
                "selected": row.get("selected"),
                "trace_id": row.get("trace_id"),
                "temperature": row.get("temperature"),
                "model": row.get("model"),
                "prompt_key": row.get("prompt_key"),
                "prompt_id": row.get("prompt_id"),
                "prompt_version": row.get("prompt_version"),
                "prompt_sha256": row.get("prompt_sha256"),
                "business_alignment_labels": row.get("business_alignment_labels") or [],
            }
        )
    for values in series.values():
        values.sort(key=lambda item: str(item.get("time") or ""))
    return {"rows": rows, "prompt_series": series, "total": len(rows)}


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
            codex_reasoning_effort=req.codex_reasoning_effort,
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


def _prompt_candidate_rows(limit: int) -> list[dict[str, Any]]:
    trace_dir = Path(os.environ.get("TRACES_DIR", "data/traces"))
    if not trace_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(trace_dir.glob("*.json"), key=_mtime, reverse=True):
        payload = _read_trace(path)
        if not isinstance(payload, dict):
            continue
        trace_id = str(payload.get("request_id") or path.stem)
        task = str(payload.get("task") or "")
        source = _candidate_source(payload)
        approved = bool((payload.get("result") or {}).get("approved"))
        for event in payload.get("events", []) or []:
            if not isinstance(event, dict) or event.get("node") != "generate":
                continue
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            outputs = event.get("outputs") if isinstance(event.get("outputs"), dict) else {}
            iteration = int(details.get("iteration") or outputs.get("iteration") or 1)
            selected_index = _int_or_none(details.get("selected_index", outputs.get("selected_index")))
            candidates = _event_candidates(details)
            for idx, cand in enumerate(candidates):
                meta = _candidate_prompt_meta(cand, details)
                selected = _candidate_selected(cand, idx, selected_index)
                score = _candidate_quality(cand, selected, approved)
                prompt_key = _prompt_key(meta)
                business_labels = _candidate_business_labels(cand)
                rows.append(
                    {
                        "row_id": trace_id + ":" + str(iteration) + ":" + str(idx),
                        "trace_id": trace_id,
                        "trace_url": "/runs/" + trace_id,
                        "time": str(event.get("started_at") or payload.get("started_at") or ""),
                        "task": task,
                        "source_type": source["type"],
                        "source_id": source["id"],
                        "case_id": source["case_id"],
                        "iteration": iteration,
                        "candidate_index": cand.get("candidate_index", idx),
                        "selected": selected,
                        "temperature": cand.get("temperature"),
                        "model": cand.get("model") or details.get("model") or "",
                        "backend": cand.get("backend") or details.get("backend") or "",
                        "prompt_key": prompt_key,
                        "prompt_id": meta.get("prompt_id") or "",
                        "prompt_type": meta.get("prompt_type") or "",
                        "prompt_version": meta.get("prompt_version"),
                        "prompt_sha256": meta.get("prompt_sha256") or "",
                        "prompt_source": meta.get("prompt_source") or "",
                        "prompt_system": cand.get("prompt_system") or details.get("prompt_system") or "",
                        "prompt_user": cand.get("prompt_user") or details.get("prompt_user") or "",
                        "sql": cand.get("sql") or cand.get("response") or "",
                        "quality_score": score,
                        "business_alignment_labels": business_labels,
                        "business_alignment_findings": _candidate_business_findings(cand),
                        "selector_reason": _candidate_selector_reason(cand),
                    }
                )
                if len(rows) >= limit:
                    return rows
    return rows


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _read_trace(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _event_candidates(details: dict[str, Any]) -> list[dict[str, Any]]:
    raw = details.get("candidates")
    if isinstance(raw, list) and raw:
        return [item for item in raw if isinstance(item, dict)]
    sql_items = details.get("response_sql")
    if not isinstance(sql_items, list):
        return []
    temperatures = details.get("temperature_schedule") if isinstance(details.get("temperature_schedule"), list) else []
    out: list[dict[str, Any]] = []
    for idx, sql in enumerate(sql_items):
        out.append(
            {
                "candidate_index": idx,
                "sql": sql,
                "temperature": temperatures[idx] if idx < len(temperatures) else details.get("temperature"),
                "model": details.get("model"),
                "backend": details.get("backend"),
            }
        )
    return out


def _candidate_prompt_meta(cand: dict[str, Any], details: dict[str, Any]) -> dict[str, Any]:
    meta = cand.get("prompt_meta") if isinstance(cand.get("prompt_meta"), dict) else {}
    if not meta:
        meta = details.get("prompt_meta") if isinstance(details.get("prompt_meta"), dict) else {}
    return {
        "prompt_id": meta.get("prompt_id") or cand.get("prompt_id") or details.get("prompt_id"),
        "prompt_type": meta.get("prompt_type") or cand.get("prompt_type") or details.get("prompt_type"),
        "prompt_version": meta.get("prompt_version") or cand.get("prompt_version") or details.get("prompt_version"),
        "prompt_sha256": meta.get("prompt_sha256") or cand.get("prompt_sha256") or details.get("prompt_sha256"),
        "prompt_source": meta.get("prompt_source") or cand.get("prompt_source") or details.get("prompt_source"),
    }


def _candidate_selected(cand: dict[str, Any], idx: int, selected_index: int | None) -> bool:
    selected = cand.get("selected_by_selector", cand.get("selected"))
    if selected is not None:
        return bool(selected)
    return selected_index is not None and idx == selected_index


def _candidate_quality(cand: dict[str, Any], selected: bool, approved: bool) -> int:
    score_data = cand.get("selector_score") if isinstance(cand.get("selector_score"), dict) else {}
    labels = score_data.get("labels") if isinstance(score_data.get("labels"), list) else []
    business_labels = _candidate_business_labels(cand)
    score = 65
    if selected:
        score += 18
    if approved:
        score += 12
    if score_data.get("broken"):
        score = min(score, 25)
    if business_labels:
        score = min(score, 45)
    score -= min(len(labels) * 8, 32)
    score -= min(len(business_labels) * 12, 30)
    return max(0, min(100, int(score)))


def _candidate_business_findings(cand: dict[str, Any]) -> list[dict[str, Any]]:
    score_data = cand.get("selector_score") if isinstance(cand.get("selector_score"), dict) else {}
    raw = cand.get("business_alignment_findings") or score_data.get("business_alignment_findings") or []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _candidate_business_labels(cand: dict[str, Any]) -> list[str]:
    score_data = cand.get("selector_score") if isinstance(cand.get("selector_score"), dict) else {}
    raw = score_data.get("business_alignment_labels")
    if isinstance(raw, list):
        return [str(item) for item in raw if item]
    labels: list[str] = []
    for item in _candidate_business_findings(cand):
        label = str(item.get("vuln_class") or item.get("label") or "").strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def _candidate_selector_reason(cand: dict[str, Any]) -> str:
    score_data = cand.get("selector_score") if isinstance(cand.get("selector_score"), dict) else {}
    return str(cand.get("selector_reason") or score_data.get("selector_reason") or "")


def _prompt_key(meta: dict[str, Any]) -> str:
    prompt_id = str(meta.get("prompt_id") or meta.get("prompt_type") or "unknown")
    version = meta.get("prompt_version")
    if version is not None and version != "":
        return prompt_id + "@v" + str(version)
    sha = str(meta.get("prompt_sha256") or "")
    return prompt_id + "@" + (sha[:10] if sha else "legacy")


def _candidate_source(payload: dict[str, Any]) -> dict[str, str]:
    meta = payload.get("result")
    meta = meta.get("metadata") if isinstance(meta, dict) and isinstance(meta.get("metadata"), dict) else {}
    batch_id = str(
        meta.get("benchmark_run_id")
        or meta.get("batch_run_id")
        or payload.get("benchmark_run_id")
        or payload.get("batch_run_id")
        or ""
    )
    case_id = str(meta.get("case_id") or payload.get("case_id") or "")
    if batch_id or case_id:
        return {"type": "batch", "id": batch_id or "batch", "case_id": case_id}
    return {"type": "single request", "id": str(payload.get("request_id") or ""), "case_id": ""}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
