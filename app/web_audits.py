from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from app import analysis_job_supervisor
from app import judge_job_supervisor
from app import oracle_job_supervisor


router = APIRouter(tags=["web-audits"])

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "app" / "templates"
ASSET_DIR = ROOT / "app" / "static" / "audit_reviews"
DATASET_DIR = ROOT / "data" / "eval"
ALLOWED_ASSETS = {
    "shared.css",
    "audit_run_compare.css",
    "audit_run_compare.js",
    "run_detail_insights.js",
    "run_detail_per_stage.js",
    "labels_dict.json",
    "metric_labels_ru.json",
    "sql_event_specs.js",
    "settings_tariffs.js",
    "batch_cases.css",
    "batch_cases.js",
}
ALLOWED_DATASET_ASSETS = {
    "golden_v2.jsonl",
    "golden_v2_bucket_overrides.jsonl",
}

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/audits/runs", response_class=HTMLResponse)
def audit_runs_screen(request: Request) -> HTMLResponse:
    return _screen(request, "audit_runs.html", "audit-runs")


@router.get("/audits/runs/compare", response_class=HTMLResponse)
def audit_runs_compare_screen(request: Request) -> HTMLResponse:
    return _screen(request, "audit_run_compare.html", "audit-runs")


@router.get("/audits/batch-cases", response_class=HTMLResponse)
def batch_cases_screen(request: Request) -> HTMLResponse:
    return _screen(request, "batch_cases.html", "batch-cases")


@router.get("/audits/runs/{benchmark_run_id}", response_class=HTMLResponse)
def audit_run_detail_screen(benchmark_run_id: str, request: Request) -> HTMLResponse:
    ctx = _base_context("audit-runs")
    ctx["benchmark_run_id"] = benchmark_run_id
    return templates.TemplateResponse(request, "audit_run_detail.html", ctx)


@router.get("/settings/tariffs", response_class=HTMLResponse)
def settings_tariffs_screen(request: Request) -> HTMLResponse:
    return _screen(request, "settings_tariffs.html", "tariffs")


@router.post("/web/api/benchmarks/runs/{benchmark_run_id}/judge/start")
async def start_judge_job(benchmark_run_id: str, request: Request) -> dict[str, Any]:
    _require_benchmark_token(request)
    payload = await _json_body(request)
    try:
        return judge_job_supervisor.start_judge_job(benchmark_run_id, payload)
    except judge_job_supervisor.JudgeStartError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/web/api/benchmarks/runs/{benchmark_run_id}/judge/status")
def judge_job_status(benchmark_run_id: str, request: Request) -> dict[str, Any]:
    _require_benchmark_token(request)
    return judge_job_supervisor.get_judge_job_status(benchmark_run_id)


@router.post("/web/api/benchmarks/runs/{benchmark_run_id}/judge/abort")
def abort_judge_job(benchmark_run_id: str, request: Request) -> dict[str, Any]:
    _require_benchmark_token(request)
    return judge_job_supervisor.abort_judge_job(benchmark_run_id)


@router.post("/web/api/benchmarks/runs/{benchmark_run_id}/oracle/start")
async def start_oracle_job(benchmark_run_id: str, request: Request) -> dict[str, Any]:
    _require_benchmark_token(request)
    payload = await _json_body(request)
    try:
        return oracle_job_supervisor.start_oracle_job(benchmark_run_id, payload)
    except oracle_job_supervisor.OracleStartError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/web/api/benchmarks/runs/{benchmark_run_id}/oracle/status")
def oracle_job_status(benchmark_run_id: str, request: Request) -> dict[str, Any]:
    _require_benchmark_token(request)
    return oracle_job_supervisor.get_oracle_job_status(benchmark_run_id)


@router.post("/web/api/benchmarks/runs/{benchmark_run_id}/oracle/abort")
def abort_oracle_job(benchmark_run_id: str, request: Request) -> dict[str, Any]:
    _require_benchmark_token(request)
    return oracle_job_supervisor.abort_oracle_job(benchmark_run_id)


@router.post("/web/api/benchmarks/runs/{benchmark_run_id}/analysis/start")
async def start_analysis_job(benchmark_run_id: str, request: Request) -> dict[str, Any]:
    _require_benchmark_token(request)
    payload = await _json_body(request)
    try:
        return analysis_job_supervisor.start_analysis_job(benchmark_run_id, payload)
    except analysis_job_supervisor.AnalysisStartError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/web/api/benchmarks/runs/{benchmark_run_id}/analysis/status")
def analysis_job_status(benchmark_run_id: str, request: Request) -> dict[str, Any]:
    _require_benchmark_token(request)
    return analysis_job_supervisor.get_analysis_job_status(benchmark_run_id)


@router.post("/web/api/benchmarks/runs/{benchmark_run_id}/analysis/abort")
def abort_analysis_job(benchmark_run_id: str, request: Request) -> dict[str, Any]:
    _require_benchmark_token(request)
    return analysis_job_supervisor.abort_analysis_job(benchmark_run_id)


def _screen(request: Request, template: str, active: str) -> HTMLResponse:
    return templates.TemplateResponse(request, template, _base_context(active))


def _base_context(active: str) -> dict[str, object]:
    return {
        "api_token": os.environ.get("BENCHMARK_API_TOKEN") or os.environ.get("BENCHMARK_INGEST_TOKEN", ""),
        "api_base_url": os.environ.get("BENCHMARK_API_URL", "http://localhost:18081"),
        "active_section": active,
        "audits_enabled": True,
    }


@router.get("/web/audits/static/{name}")
def audit_asset(name: str) -> FileResponse:
    if name in ALLOWED_DATASET_ASSETS:
        path = DATASET_DIR / name
        if not path.exists():
            raise HTTPException(status_code=404, detail="asset not found")
        return FileResponse(path, media_type="application/x-ndjson")
    if name not in ALLOWED_ASSETS:
        raise HTTPException(status_code=404, detail="asset not found")
    path = ASSET_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(path)


async def _json_body(request: Request) -> dict[str, Any]:
    if not request.headers.get("content-length") and request.method != "GET":
        return {}
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _require_benchmark_token(request: Request) -> None:
    expected = os.environ.get("BENCHMARK_API_TOKEN") or os.environ.get("BENCHMARK_INGEST_TOKEN") or ""
    if not expected:
        return
    auth = request.headers.get("Authorization", "")
    if auth != "Bearer " + expected:
        raise HTTPException(status_code=401, detail="invalid benchmark token")
