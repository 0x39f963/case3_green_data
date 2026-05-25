from __future__ import annotations

from contextlib import asynccontextmanager
import json
import logging
import os
from json import JSONDecodeError
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from benchmark_service import __version__
from benchmark_service import db
from benchmark_service import runner_supervisor
from benchmark_service.auth import api_error, max_body_bytes, require_token, validate_startup_auth
from benchmark_service.ingest import normalize_payload
from benchmark_service.models import (
    AuditReviewPayload,
    BenchmarkAnalysisStart,
    BenchmarkJudgeStart,
    BenchmarkOracleStart,
    BenchmarkRunRegister,
    BenchmarkRunStart,
    RunPayload,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=os.environ.get("BENCHMARK_LOG_LEVEL", "INFO").upper())
    validate_startup_auth()
    yield


app = FastAPI(
    title="Case 3 Benchmark Store",
    version=__version__,
    description="HTTP store for SQL security benchmark traces.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        body = detail
    else:
        body = {"code": "http_error", "message": str(detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": body})


@app.exception_handler(RequestValidationError)
async def request_validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "schema_validation", "message": "Request validation failed.", "details": exc.errors()}},
    )


@app.get("/health")
def health() -> dict[str, Any]:
    info = db.health()
    return {
        "status": "ok" if info.get("db_ok") else "error",
        "db_ok": bool(info.get("db_ok")),
    }


@app.get("/v1/benchmarks/datasets", dependencies=[Depends(require_token)])
def list_benchmark_datasets() -> dict[str, Any]:
    try:
        return {"items": runner_supervisor.list_datasets()}
    except Exception as exc:
        api_error(500, "dataset_list_failed", str(exc))


@app.post("/v1/benchmarks/datasets/upload", dependencies=[Depends(require_token)])
async def upload_benchmark_dataset(request: Request) -> dict[str, Any]:
    _, data = await _json_body(request)
    try:
        filename = str(data.get("filename") or "dataset.jsonl")
        content = str(data.get("content") or "")
        dataset_id = data.get("dataset_id")
        item = runner_supervisor.save_uploaded_dataset(
            filename,
            content,
            str(dataset_id) if dataset_id else None,
        )
        return {"item": item}
    except (ValueError, json.JSONDecodeError) as exc:
        api_error(400, "bad_dataset", str(exc))
    except Exception as exc:
        api_error(500, "dataset_upload_failed", str(exc))


@app.post("/v1/ingest/run", dependencies=[Depends(require_token)])
async def ingest_run(request: Request, replace: bool = False) -> dict[str, Any]:
    raw_body, data = await _json_body(request)
    try:
        payload = RunPayload.model_validate(data)
        normalized = normalize_payload(payload, raw_body)
    except ValidationError as exc:
        api_error(422, "schema_validation", "Payload validation failed.", exc.errors())
    except ValueError as exc:
        api_error(400, "bad_trace", str(exc))

    try:
        action, counts = db.ingest_run(normalized, replace=replace)
    except db.DuplicateLogicalRun as exc:
        api_error(
            409,
            "duplicate_logical_run",
            "Logical benchmark run already exists for benchmark_run_id, case_id and model_key.",
            {"existing_trace_id": exc.existing_trace_id, "hint": "Use ?replace=true to replace the existing trace."},
        )
    except Exception as exc:
        api_error(500, "db_error", str(exc))
    return {"trace_id": payload.trace_id, "action": action, "normalized_counts": counts}


@app.post("/v1/ingest/batch", dependencies=[Depends(require_token)])
async def ingest_batch(request: Request) -> dict[str, Any]:
    _, data = await _json_body(request)
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        api_error(422, "schema_validation", "Batch body must be a list or {items: [...]} object.")
    if len(items) > 50:
        api_error(422, "batch_too_large", "Batch size must be <= 50.")

    inserted = 0
    updated = 0
    failed: list[dict[str, Any]] = []
    for item in items:
        trace_id = item.get("trace_id") if isinstance(item, dict) else None
        try:
            item_body = _canonical_body(item)
            payload = RunPayload.model_validate(item)
            normalized = normalize_payload(payload, item_body)
            action, _ = db.ingest_run(normalized)
            if action == "inserted":
                inserted += 1
            else:
                updated += 1
        except Exception as exc:
            failed.append({"trace_id": trace_id or "unknown", "error": str(exc)})
    return {
        "total": len(items),
        "inserted": inserted,
        "updated": updated,
        "failed": failed,
        "sha256_mode": "canonical_sha256",
    }


@app.post("/v1/datasets/{dataset_id}/cases", dependencies=[Depends(require_token)])
async def upsert_dataset_cases(dataset_id: str, request: Request, dataset_version: str | None = None) -> dict[str, Any]:
    body = await request.body()
    if len(body) > max_body_bytes():
        api_error(413, "body_too_large", "Request body exceeds configured size limit.")
    try:
        items = _case_items(body)
        result = db.upsert_dataset_cases(dataset_id, items, version=dataset_version)
    except ValueError as exc:
        api_error(400, "bad_json", str(exc))
    except Exception as exc:
        api_error(500, "db_error", str(exc))
    return {"dataset_id": dataset_id, **result}


@app.post("/v1/runs/register", dependencies=[Depends(require_token)])
async def register_run(request: Request) -> dict[str, Any]:
    _, data = await _json_body(request)
    try:
        payload = BenchmarkRunRegister.model_validate(data)
    except ValidationError as exc:
        api_error(422, "schema_validation", "Run registration validation failed.", exc.errors())
    try:
        run_id = db.register_benchmark_run(payload.model_dump(mode="python"))
    except Exception as exc:
        api_error(500, "db_error", str(exc))
    return {"benchmark_run_id": run_id}


@app.post("/v1/benchmarks/runs", dependencies=[Depends(require_token)])
async def start_benchmark_run(request: Request) -> dict[str, Any]:
    _, data = await _json_body(request)
    try:
        payload = BenchmarkRunStart.model_validate(data)
        item = payload.model_dump(mode="python")
        item["isolation_mode"] = item.get("isolation_mode") or item.get("isolation") or (
            "clean" if str(item.get("dataset_id") or "").startswith("golden_") else "production"
        )
        if item.get("limit") == 0 and not item.get("case_ids_filter"):
            item["total_cases"] = runner_supervisor.dataset_case_count(
                str(item.get("dataset_id") or ""),
                item.get("dataset_path"),
            )
        registered = db.start_benchmark_run(item)
        item["benchmark_run_id"] = registered["benchmark_run_id"]
        token = os.environ.get("BENCHMARK_INGEST_TOKEN", "")
        runner = runner_supervisor.start_subprocess(item, token)
        judge: dict[str, Any] = {"status": "disabled"}
        backend = str(item.get("smart_judge_backend") or "")
        if backend and backend != "off":
            try:
                judge = runner_supervisor.start_judge_subprocess(registered["benchmark_run_id"], item, token, watch=True)
            except runner_supervisor.JudgeStartError as exc:
                judge = {"status": "start_failed", "error": str(exc)}
        posthoc: dict[str, Any] = {"status": "disabled"}
        if item.get("oracle_enabled", True) or item.get("analysis_enabled", True):
            posthoc = runner_supervisor.start_posthoc_chain(registered["benchmark_run_id"], item, token)
        return {**registered, "status": runner["status"], "runner": runner, "judge": judge, "posthoc": posthoc}
    except ValidationError as exc:
        api_error(422, "schema_validation", "Run start validation failed.", exc.errors())
    except db.BatchStartBlocked as exc:
        api_error(409, "batch_locked", str(exc))
    except runner_supervisor.RunnerStartError as exc:
        api_error(500, "runner_start_failed", str(exc))
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.post("/v1/benchmarks/runs/{benchmark_run_id}/abort", dependencies=[Depends(require_token)])
def abort_benchmark_run(benchmark_run_id: str) -> dict[str, Any]:
    try:
        return runner_supervisor.abort_subprocess(benchmark_run_id)
    except Exception as exc:
        api_error(500, "runner_abort_failed", str(exc))


@app.post("/v1/benchmarks/runs/{benchmark_run_id}/judge/start", dependencies=[Depends(require_token)])
async def start_benchmark_judge(benchmark_run_id: str, request: Request) -> dict[str, Any]:
    _, data = await _json_body(request)
    try:
        payload = BenchmarkJudgeStart.model_validate(data)
        item = payload.model_dump(mode="python")
        result = runner_supervisor.start_judge_subprocess(
            benchmark_run_id,
            item,
            os.environ.get("BENCHMARK_INGEST_TOKEN", ""),
            watch=bool(item.get("watch")),
        )
        return {"benchmark_run_id": benchmark_run_id, **result}
    except ValidationError as exc:
        api_error(422, "schema_validation", "Judge start validation failed.", exc.errors())
    except runner_supervisor.JudgeStartError as exc:
        api_error(500, "judge_start_failed", str(exc))
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.post("/v1/benchmarks/runs/{benchmark_run_id}/oracle/start", dependencies=[Depends(require_token)])
async def start_benchmark_oracle(benchmark_run_id: str, request: Request) -> dict[str, Any]:
    _, data = await _json_body(request)
    try:
        payload = BenchmarkOracleStart.model_validate(data)
        result = runner_supervisor.start_oracle_subprocess(
            benchmark_run_id,
            payload.model_dump(mode="python"),
            os.environ.get("BENCHMARK_INGEST_TOKEN", ""),
        )
        return {"benchmark_run_id": benchmark_run_id, **result}
    except ValidationError as exc:
        api_error(422, "schema_validation", "Oracle start validation failed.", exc.errors())
    except runner_supervisor.OracleStartError as exc:
        api_error(500, "oracle_start_failed", str(exc))
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.post("/v1/benchmarks/runs/{benchmark_run_id}/analysis/start", dependencies=[Depends(require_token)])
async def start_benchmark_analysis(benchmark_run_id: str, request: Request) -> dict[str, Any]:
    _, data = await _json_body(request)
    try:
        payload = BenchmarkAnalysisStart.model_validate(data)
        result = runner_supervisor.start_analysis_subprocess(
            benchmark_run_id,
            payload.model_dump(mode="python"),
            os.environ.get("BENCHMARK_INGEST_TOKEN", ""),
        )
        return {"benchmark_run_id": benchmark_run_id, **result}
    except ValidationError as exc:
        api_error(422, "schema_validation", "Analysis start validation failed.", exc.errors())
    except runner_supervisor.AnalysisStartError as exc:
        api_error(500, "analysis_start_failed", str(exc))
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.get("/v1/benchmarks/runs")
def list_benchmark_runs(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    try:
        return db.list_benchmark_runs(limit=limit, offset=offset)
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.get("/v1/benchmarks/cases")
def list_benchmark_cases(
    run_id: str | None = None,
    model_key: str | None = None,
    generator_backend: str | None = None,
    generator_provider: str | None = None,
    case_id: str | None = None,
    q: str | None = None,
    decision: str | None = None,
    approved: str | None = None,
    smart_score_min: float | None = None,
    smart_score_max: float | None = None,
    smart_judge_status: str | None = None,
    patch_target_area: str | None = None,
    patch_severity: str | None = None,
    oracle_verdict: str | None = None,
    analysis_status: str | None = None,
    latency_min: float | None = None,
    latency_max: float | None = None,
    tokens_min: float | None = None,
    tokens_max: float | None = None,
    cost_min: float | None = None,
    cost_max: float | None = None,
    limit: int = 100,
    offset: int = 0,
    sort: str = "created_desc",
) -> dict[str, Any]:
    try:
        return db.list_benchmark_cases(locals())
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.get("/v1/benchmarks/cases/export.csv")
def export_benchmark_cases_csv(
    run_id: str | None = None,
    model_key: str | None = None,
    generator_backend: str | None = None,
    generator_provider: str | None = None,
    case_id: str | None = None,
    q: str | None = None,
    decision: str | None = None,
    approved: str | None = None,
    smart_score_min: float | None = None,
    smart_score_max: float | None = None,
    smart_judge_status: str | None = None,
    patch_target_area: str | None = None,
    patch_severity: str | None = None,
    oracle_verdict: str | None = None,
    analysis_status: str | None = None,
    latency_min: float | None = None,
    latency_max: float | None = None,
    tokens_min: float | None = None,
    tokens_max: float | None = None,
    cost_min: float | None = None,
    cost_max: float | None = None,
    sort: str = "created_desc",
) -> Response:
    try:
        text = db.export_benchmark_cases_csv(locals())
    except Exception as exc:
        api_error(500, "db_error", str(exc))
    return Response(
        content=text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=\"benchmark_cases.csv\""},
    )


@app.get("/v1/benchmarks/cases/{trace_id}")
def get_benchmark_case_detail(trace_id: str) -> dict[str, Any]:
    try:
        result = db.get_benchmark_case_detail(trace_id)
    except Exception as exc:
        api_error(500, "db_error", str(exc))
    if not result:
        api_error(404, "not_found", "Benchmark case not found.")
    return result


@app.get("/v1/benchmarks/runs/{benchmark_run_id}")
def get_benchmark_run_detail(benchmark_run_id: str) -> dict[str, Any]:
    try:
        result = db.benchmark_run_detail(benchmark_run_id)
    except Exception as exc:
        api_error(500, "db_error", str(exc))
    if not result:
        api_error(404, "not_found", "Benchmark run not found.")
    return result


@app.get("/v1/benchmarks/runs/{benchmark_run_id}/progress")
def benchmark_run_progress(benchmark_run_id: str) -> dict[str, Any]:
    try:
        runner_supervisor.reap_finished()
        result = db.benchmark_progress(benchmark_run_id)
        if result:
            result["runner"] = {
                **(result.get("runner") or {}),
                **runner_supervisor.get_subprocess_status(benchmark_run_id),
            }
    except Exception as exc:
        api_error(500, "db_error", str(exc))
    if not result:
        api_error(404, "not_found", "Benchmark run not found.")
    return result


@app.get("/v1/benchmarks/runs/{benchmark_run_id}/hypotheses")
def benchmark_run_hypotheses(benchmark_run_id: str, limit: int = 50) -> dict[str, Any]:
    try:
        result = db.list_run_hypotheses(benchmark_run_id, limit=limit)
    except Exception as exc:
        api_error(500, "db_error", str(exc))
    return result


@app.get("/v1/benchmarks/runs/{benchmark_run_id}/audit-report")
def benchmark_run_audit_report(benchmark_run_id: str) -> dict[str, Any]:
    try:
        result = db.benchmark_run_audit_report(benchmark_run_id)
    except Exception as exc:
        api_error(500, "db_error", str(exc))
    if not result:
        api_error(404, "not_found", "Benchmark run not found.")
    return result


@app.post("/v1/benchmarks/runs/{benchmark_run_id}/audit-report/summary/start", dependencies=[Depends(require_token)])
async def benchmark_run_audit_summary_start(benchmark_run_id: str, request: Request) -> dict[str, Any]:
    _, data = await _json_body(request)
    source = str(data.get("source") or "deterministic")
    try:
        result = db.start_run_audit_summary(benchmark_run_id, source=source)
    except Exception as exc:
        api_error(500, "db_error", str(exc))
    if not result:
        api_error(404, "not_found", "Benchmark run not found.")
    return {"benchmark_run_id": benchmark_run_id, "status": result.get("status") or "completed", "item": result}


@app.get("/v1/benchmarks/runs/{benchmark_run_id}/audit-report/summary/status")
def benchmark_run_audit_summary_status(benchmark_run_id: str) -> dict[str, Any]:
    try:
        return db.run_audit_summary_status(benchmark_run_id)
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.get("/v1/benchmarks/runs/{benchmark_run_id}/insights")
def benchmark_run_insights(benchmark_run_id: str) -> dict[str, Any]:
    try:
        return db.get_run_insights(benchmark_run_id)
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.get("/v1/benchmarks/runs/{benchmark_run_id}/failed-cases")
def benchmark_run_failed_cases(benchmark_run_id: str) -> dict[str, Any]:
    try:
        result = db.failed_cases_for_rerun(benchmark_run_id)
    except Exception as exc:
        api_error(500, "db_error", str(exc))
    if not result:
        api_error(404, "not_found", "Benchmark run not found.")
    return result


@app.post("/v1/benchmarks/runs/compare")
async def compare_benchmark_runs(request: Request) -> dict[str, Any]:
    _, data = await _json_body(request)
    run_ids = data.get("run_ids") if isinstance(data, dict) else None
    if not isinstance(run_ids, list) or not run_ids:
        api_error(422, "schema_validation", "Body must contain non-empty run_ids list.")
    try:
        return db.get_run_comparison([str(item) for item in run_ids])
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.get("/v1/benchmarks/runs/{benchmark_run_id}/export.csv")
def export_benchmark_run_csv(benchmark_run_id: str) -> Response:
    try:
        text = db.export_run_csv(benchmark_run_id)
    except Exception as exc:
        api_error(500, "db_error", str(exc))
    return Response(
        content=text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=\"" + benchmark_run_id + ".csv\""},
    )


@app.get("/v1/tariffs")
def list_tariffs() -> dict[str, Any]:
    try:
        return db.list_tariffs()
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.post("/v1/tariffs", dependencies=[Depends(require_token)])
async def upsert_tariff(request: Request) -> dict[str, Any]:
    _, data = await _json_body(request)
    try:
        return {"item": db.upsert_tariff(data)}
    except ValueError as exc:
        api_error(400, "bad_tariff", str(exc))
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.delete("/v1/tariffs/{preset_key}", dependencies=[Depends(require_token)])
def delete_tariff(preset_key: str) -> dict[str, Any]:
    try:
        return {"deleted": db.delete_tariff(preset_key)}
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.get("/v1/runs", dependencies=[Depends(require_token)])
def list_runs(benchmark_run_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    try:
        return db.list_runs(benchmark_run_id=benchmark_run_id, limit=limit)
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.get("/v1/runs/{trace_id}", dependencies=[Depends(require_token)])
def get_run(trace_id: str) -> dict[str, Any]:
    try:
        result = db.get_run(trace_id)
    except Exception as exc:
        api_error(500, "db_error", str(exc))
    if result is None:
        api_error(404, "not_found", "Trace not found.")
    return result


@app.get("/v1/audit/targets", dependencies=[Depends(require_token)])
def audit_targets(
    benchmark_run_id: str,
    trace_id: list[str] | None = Query(default=None),
    case_id: list[str] | None = Query(default=None),
    family: list[str] | None = Query(default=None),
    limit: int = 100,
) -> dict[str, Any]:
    try:
        return db.list_audit_targets(
            benchmark_run_id=benchmark_run_id,
            trace_ids=trace_id or [],
            case_ids=case_id or [],
            families=family or [],
            limit=limit,
        )
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.post("/v1/audit/reviews", dependencies=[Depends(require_token)])
async def upsert_audit_review(request: Request) -> dict[str, Any]:
    _, data = await _json_body(request)
    try:
        payload = AuditReviewPayload.model_validate(data)
        counts = db.upsert_audit_review(payload.model_dump(mode="python"))
    except ValidationError as exc:
        api_error(422, "schema_validation", "Audit review validation failed.", exc.errors())
    except ValueError as exc:
        api_error(400, "bad_review", str(exc))
    except Exception as exc:
        api_error(500, "db_error", str(exc))
    return {"review_id": data.get("review", {}).get("review_id"), "normalized_counts": counts}




@app.get("/v1/audit/reviews/{review_id}", dependencies=[Depends(require_token)])
def get_audit_review_detail(review_id: str) -> dict[str, Any]:
    try:
        result = db.get_audit_review_detail(review_id)
    except Exception as exc:
        api_error(500, "db_error", str(exc))
    if not result:
        api_error(404, "not_found", "Audit review not found.")
    return result


@app.get("/v1/audit/suggestions", dependencies=[Depends(require_token)])
def list_audit_suggestions(benchmark_run_id: str | None = None, top: int = 20) -> dict[str, Any]:
    try:
        return db.list_audit_suggestions(benchmark_run_id=benchmark_run_id, top=top)
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.get("/v1/metrics/summary", dependencies=[Depends(require_token)])
def metrics_summary(benchmark_run_id: str | None = None) -> dict[str, Any]:
    try:
        return db.metrics_summary(benchmark_run_id=benchmark_run_id)
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.get("/v1/datasets", dependencies=[Depends(require_token)])
def list_datasets() -> dict[str, Any]:
    try:
        return db.list_datasets()
    except Exception as exc:
        api_error(500, "db_error", str(exc))


@app.get("/v1/admin/version", dependencies=[Depends(require_token)])
def admin_version() -> dict[str, Any]:
    info = db.admin_version()
    return {"service_version": __version__, "git_sha": _git_sha(), **info}


async def _json_body(request: Request) -> tuple[bytes, Any]:
    body = await request.body()
    if len(body) > max_body_bytes():
        api_error(413, "body_too_large", "Request body exceeds configured size limit.")
    try:
        return body, json.loads(body.decode("utf-8"))
    except UnicodeDecodeError:
        api_error(400, "bad_json", "Request body must be UTF-8 JSON.")
    except JSONDecodeError as exc:
        api_error(400, "bad_json", "Request body is not valid JSON.", str(exc))
    raise AssertionError("unreachable")


def _canonical_body(item: Any) -> bytes:
    return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _case_items(body: bytes) -> list[dict[str, Any]]:
    text = body.decode("utf-8")
    try:
        data = json.loads(text)
    except JSONDecodeError:
        items = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("Cases body must be a JSON array, {items: [...]} object, or JSONL.")
    if not all(isinstance(item, dict) for item in items):
        raise ValueError("Each dataset case must be a JSON object.")
    return items


def _git_sha() -> str:
    return os.environ.get("BENCHMARK_GIT_SHA") or os.environ.get("GIT_SHA") or "unknown"
