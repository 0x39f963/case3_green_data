from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

from jsonschema import Draft7Validator


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.benchmark_client import BenchmarkClient, BenchmarkClientError  # noqa: E402


RUNNER_VERSION = "0.1.0"
DEFAULT_DATASET = ROOT / "data" / "bench" / "requests" / "adversarial_sql_requests_v0_2.jsonl"
DEFAULT_SCHEMA = ROOT / "data" / "bench" / "requests" / "adversarial_sql_requests.schema.json"
DEFAULT_MODELS = ROOT / "deploy" / "bot_models.json"
DEFAULT_TRACES_DIR = ROOT / "data" / "traces"
RUNS_DIR = ROOT / "data" / "bench" / "runs"


class ConfigError(RuntimeError):
    pass


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def main() -> int:
    try:
        args = parse_args()
        rows = load_dataset(args.dataset, args.schema, skip_schema_validation=args.skip_schema_validation)
        rows = filter_rows(rows, args)
        if not rows:
            raise ConfigError("Dataset filter produced zero rows.")
        provider_map = parse_openrouter_providers(args.openrouter_providers)
        models = load_models(args.models, args.models_config, args.llm_mode, provider_map)
        matrix = build_matrix(rows, models, args.matrix_order)
        if args.concurrency != 1:
            raise ConfigError("--concurrency > 1 belongs to P1 and is not enabled in this runner.")
        if not args.dry_run and not args.benchmark_run_id:
            raise ConfigError("--benchmark-run-id is required for real runs.")
        if not args.dry_run and not args.store_token and not args.allow_store_failure:
            raise ConfigError("--store-token or BENCHMARK_INGEST_TOKEN is required unless --allow-store-failure is set.")
    except (ConfigError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "CONFIG_ERROR", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    dataset = dataset_info(args.dataset, rows)
    run_id = args.benchmark_run_id or "dry_run"
    run_dir = RUNS_DIR / run_id
    config = build_config(args, dataset, models, len(matrix))

    if args.dry_run:
        print_dry_run(matrix, dataset, models)
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        check_resume_config(run_dir, config, args.resume, args.force)
    except ConfigError as exc:
        print(json.dumps({"status": "CONFIG_ERROR", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    write_config(run_dir, config, args.resume)

    results_path = run_dir / "results.jsonl"
    failed_path = run_dir / "failed.jsonl"
    done = completed_pairs(results_path) if args.resume else set()
    store_client, store_ready, store_error = prepare_store(args, dataset, rows, models, config)
    if store_error and not args.allow_store_failure:
        write_summary(run_dir, config, matrix, rows, stop_reason="store_unreachable", skipped=0)
        print(json.dumps({"status": "STORE_UNREACHABLE", "error": store_error}, ensure_ascii=False, indent=2))
        return 3

    skipped = 0
    had_fail = False
    api_unreachable = False
    stop_reason: str | None = None
    cost_total = current_cost(results_path)
    requests_done = 0
    judge_pool = None
    if args.smart_judge_backend and args.smart_judge_backend != "off" and not args.smart_judge_external and store_ready:
        from scripts.bench_smart_judge_worker import JudgeWorkerPool  # noqa: WPS433

        judge_pool = JudgeWorkerPool(
            backend=args.smart_judge_backend,
            model=args.smart_judge_model,
            chunk_size=args.smart_judge_chunk_size,
            max_workers=args.smart_judge_workers,
        )
        judge_pool.start(run_id)

    if args.max_cost_usd is not None and args.max_cost_usd <= 0:
        stop_reason = "cost_budget_exceeded"

    for case, model in matrix:
        pair = (case["id"], model["key"])
        if pair in done:
            skipped += 1
            continue
        if stop_reason:
            break
        if args.max_requests is not None and requests_done >= args.max_requests:
            stop_reason = "request_limit_hit"
            break

        try:
            item, attempts = run_pair(args, dataset, case, model, store_client if store_ready else None)
            requests_done += 1
            if not item["uploaded_to_store"] and not args.allow_store_failure:
                had_fail = True
                append_jsonl(
                    failed_path,
                    fail_row(args, case, model, attempts, "store_upload_failed", "Store upload failed.", None),
                )
                if args.fail_fast:
                    break
                continue
            append_jsonl(results_path, item)
            if judge_pool is not None and item.get("uploaded_to_store") and item.get("trace_id"):
                judge_pool.enqueue(str(item["trace_id"]))
            done.add(pair)
            cost_total += float(item.get("cost_usd") or 0.0)
            if args.max_cost_usd is not None and cost_total > args.max_cost_usd:
                stop_reason = "cost_budget_exceeded"
        except ApiError as exc:
            had_fail = True
            api_unreachable = api_unreachable or exc.retryable
            append_jsonl(
                failed_path,
                fail_row(args, case, model, args.retry_attempts, exc.__class__.__name__, str(exc), exc.status_code),
            )
            if args.fail_fast:
                break
        except Exception as exc:
            had_fail = True
            append_jsonl(
                failed_path,
                fail_row(args, case, model, 1, exc.__class__.__name__, str(exc), None),
            )
            if args.fail_fast:
                break

    if judge_pool is not None:
        judge_pool.flush_and_join()
    summary = write_summary(run_dir, config, matrix, rows, stop_reason=stop_reason, skipped=skipped)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if stop_reason in {"cost_budget_exceeded", "request_limit_hit"}:
        return 4
    if api_unreachable and summary.get("succeeded", 0) == 0:
        return 3
    return 1 if had_fail or summary.get("failed", 0) else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run request-level SQL security benchmark through FastAPI /run.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--skip-schema-validation", action="store_true")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--store-url", default="http://localhost:18080")
    parser.add_argument("--store-token", default=os.environ.get("BENCHMARK_INGEST_TOKEN", ""))
    parser.add_argument("--models", required=True)
    parser.add_argument("--models-config", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--llm-mode", default="")
    parser.add_argument("--benchmark-run-id", default="")
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--family", action="append", default=[])
    parser.add_argument("--review-status", default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-store-failure", action="store_true")
    parser.add_argument("--replace-store", action="store_true")
    parser.add_argument("--on-duplicate", choices=["skip", "replace", "fail"], default="skip")
    parser.add_argument("--no-upload-cases", action="store_true")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--timeout-sec", type=int, default=660)
    parser.add_argument("--pipeline-timeout-sec", type=int, default=600)
    parser.add_argument("--max-cost-usd", type=float, default=None)
    parser.add_argument("--max-requests", type=int, default=None)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--traces-dir", type=Path, default=DEFAULT_TRACES_DIR)
    parser.add_argument("--matrix-order", choices=["case-major", "model-major"], default="case-major")
    parser.add_argument("--retry-attempts", type=int, default=3)
    parser.add_argument("--retry-backoff", type=float, default=2.0)
    parser.add_argument("--isolation", choices=["clean", "production", "snapshot"], default="production")
    parser.add_argument("--parent-run-id", default="")
    parser.add_argument("--case-ids-from-run", default="")
    parser.add_argument("--filter-decision", default="")
    parser.add_argument("--prompt-version-override", default="")
    parser.add_argument("--prompt-check-disabled", action="store_true")
    parser.add_argument("--prompt-check-backend", default="")
    parser.add_argument("--prompt-check-model", default="")
    parser.add_argument("--prompt-check-openrouter-provider", default="")
    parser.add_argument("--smart-judge-backend", default=os.environ.get("SMART_JUDGE_DEFAULT_BACKEND", ""))
    parser.add_argument("--smart-judge-model", default=os.environ.get("SMART_JUDGE_DEFAULT_MODEL", "gpt-5.5"))
    parser.add_argument("--smart-judge-chunk-size", type=int, default=10)
    parser.add_argument("--smart-judge-workers", type=int, default=3)
    parser.add_argument("--smart-judge-external", action="store_true")
    parser.add_argument("--openrouter-providers", default="", help="JSON map: model_key -> OpenRouter provider_name.")
    parser.add_argument("--codex-reasoning-effort", default="", help="Fallback Codex reasoning effort for codex_cli models.")
    args = parser.parse_args()
    if args.limit < 0:
        raise ConfigError("--limit must be >= 0.")
    if args.max_iterations < 1:
        raise ConfigError("--max-iterations must be >= 1.")
    if args.retry_attempts < 1:
        raise ConfigError("--retry-attempts must be >= 1.")
    if args.timeout_sec < 1:
        raise ConfigError("--timeout-sec must be >= 1.")
    if args.replace_store:
        args.on_duplicate = "replace"
    if args.smart_judge_chunk_size < 1:
        raise ConfigError("--smart-judge-chunk-size must be >= 1.")
    if args.smart_judge_workers < 1:
        raise ConfigError("--smart-judge-workers must be >= 1.")
    return args


def load_dataset(path: Path, schema_path: Path, skip_schema_validation: bool = False) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    if not rows:
        raise ConfigError("Dataset is empty: " + str(path))
    rows = [normalize_case_row(row) for row in rows]
    if skip_schema_validation:
        return rows
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    errors: list[str] = []
    for line_no, row in enumerate(rows, start=1):
        for err in validator.iter_errors(row):
            errors.append("line " + str(line_no) + ": " + str(list(err.path)) + " " + err.message)
            if len(errors) >= 20:
                break
        if len(errors) >= 20:
            break
    if errors:
        raise ConfigError("Dataset schema validation failed: " + "; ".join(errors))
    return rows


def normalize_case_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    if "user_task" not in out and out.get("task"):
        out["user_task"] = out["task"]
    if "version" not in out:
        out["version"] = out.get("taxonomy_version") or out.get("judge_label_version") or "unknown"
    return out


def filter_rows(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    out = rows
    if args.case_id:
        ids = set(args.case_id)
        out = [row for row in out if str(row.get("id")) in ids]
    if args.family:
        families = set(args.family)
        out = [row for row in out if str(row.get("family")) in families]
    statuses = {item.strip() for item in args.review_status.split(",") if item.strip()}
    if statuses:
        out = [row for row in out if str(row.get("review_status")) in statuses]
    if args.case_ids_from_run:
        selected = set(load_case_ids_from_run(args))
        out = [row for row in out if str(row.get("id")) in selected]
    if args.limit:
        out = out[: args.limit]
    return out


def load_case_ids_from_run(args: argparse.Namespace) -> list[str]:
    if not args.store_token:
        raise ConfigError("--case-ids-from-run requires --store-token.")
    client = BenchmarkClient(args.store_url, args.store_token, timeout_sec=30, retries=2)
    data = client._request("GET", "/v1/runs?benchmark_run_id=" + args.case_ids_from_run)
    items = data.get("items") or []
    decision = args.filter_decision.strip()
    out = []
    for item in items:
        if decision and str(item.get("decision")) != decision:
            continue
        if item.get("case_id"):
            out.append(str(item["case_id"]))
    return out


def parse_openrouter_providers(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError("--openrouter-providers must be a JSON object.") from exc
    if not isinstance(value, dict):
        raise ConfigError("--openrouter-providers must be a JSON object.")
    return {str(key): str(provider).strip() for key, provider in value.items() if str(provider).strip()}


def load_models(
    raw_keys: str,
    path: Path,
    llm_mode: str,
    openrouter_providers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    keys = [item.strip() for item in raw_keys.split(",") if item.strip()]
    if not keys:
        raise ConfigError("--models must contain at least one model key.")
    data = json.loads(path.read_text(encoding="utf-8"))
    by_key: dict[str, dict[str, Any]] = {}
    for item in data.get("models") or []:
        if not isinstance(item, dict):
            continue
        item_key = str(item.get("key"))
        by_key[item_key] = item
        if str(item.get("llm_mode") or "") == "prod_demo" and not item_key.startswith("or-"):
            by_key.setdefault("or-" + item_key, item)
        if item_key.startswith("or-"):
            by_key.setdefault(item_key[3:], item)
    missing = [key for key in keys if key not in by_key]
    if missing:
        raise ConfigError("Unknown model keys: " + ", ".join(missing))
    models: list[dict[str, Any]] = []
    provider_map = openrouter_providers or {}
    for key in keys:
        item = dict(by_key[key])
        source_key = str(item.get("key") or key)
        item["key"] = key
        item["llm_mode"] = llm_mode or str(item.get("llm_mode") or "")
        item["llm_generator_model"] = str(item.get("llm_generator_model") or key)
        provider = (
            provider_map.get(key)
            or provider_map.get(source_key)
            or provider_map.get(str(item.get("llm_generator_model") or ""))
            or ""
        ).strip()
        if provider and item["llm_mode"] == "prod_demo":
            item["openrouter_provider"] = provider
        models.append(item)
    return models


def build_matrix(
    rows: list[dict[str, Any]],
    models: list[dict[str, Any]],
    order: str,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if order == "model-major":
        return [(case, model) for model in models for case in rows]
    return [(case, model) for case in rows for model in models]


def dataset_info(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    versions = {str(row.get("version") or "") for row in rows if row.get("version")}
    version = next(iter(versions)) if len(versions) == 1 else "mixed"
    stem = path.stem
    dataset_id = stem.rsplit("_v", 1)[0] if "_v" in stem else stem
    return {
        "dataset_id": dataset_id,
        "dataset_version": version,
        "path": str(path),
        "sha256": sha256_file(path),
        "rows_count": len(rows),
    }


def build_config(
    args: argparse.Namespace,
    dataset: dict[str, Any],
    models: list[dict[str, Any]],
    total_pairs: int,
) -> dict[str, Any]:
    cli_args = {
        "dataset": str(args.dataset),
        "api_url": args.api_url,
        "store_url": args.store_url,
        "models": args.models,
        "benchmark_run_id": args.benchmark_run_id,
        "max_iterations": args.max_iterations,
        "limit": args.limit,
        "case_id": args.case_id,
        "family": args.family,
        "review_status": args.review_status,
        "resume": args.resume,
        "dry_run": args.dry_run,
        "allow_store_failure": args.allow_store_failure,
        "replace_store": args.replace_store,
        "on_duplicate": args.on_duplicate,
        "no_upload_cases": args.no_upload_cases,
        "concurrency": args.concurrency,
        "timeout_sec": args.timeout_sec,
        "pipeline_timeout_sec": args.pipeline_timeout_sec,
        "max_cost_usd": args.max_cost_usd,
        "max_requests": args.max_requests,
        "fail_fast": args.fail_fast,
        "traces_dir": str(args.traces_dir),
        "matrix_order": args.matrix_order,
        "retry_attempts": args.retry_attempts,
        "retry_backoff": args.retry_backoff,
        "store_token_present": bool(args.store_token),
        "isolation": getattr(args, "isolation", "production"),
        "parent_run_id": getattr(args, "parent_run_id", ""),
        "prompt_version_override": getattr(args, "prompt_version_override", ""),
        "prompt_check_enabled": not bool(getattr(args, "prompt_check_disabled", False)),
        "prompt_check_backend": getattr(args, "prompt_check_backend", ""),
        "prompt_check_model": getattr(args, "prompt_check_model", ""),
        "prompt_check_openrouter_provider": getattr(args, "prompt_check_openrouter_provider", ""),
        "smart_judge_backend": getattr(args, "smart_judge_backend", ""),
        "smart_judge_model": getattr(args, "smart_judge_model", "gpt-5.5"),
        "smart_judge_chunk_size": getattr(args, "smart_judge_chunk_size", 10),
        "smart_judge_workers": getattr(args, "smart_judge_workers", 3),
        "smart_judge_external": getattr(args, "smart_judge_external", False),
        "openrouter_providers": getattr(args, "openrouter_providers", ""),
        "codex_reasoning_effort": getattr(args, "codex_reasoning_effort", ""),
    }
    return {
        "benchmark_run_id": args.benchmark_run_id,
        "runner_version": RUNNER_VERSION,
        "started_at": now_iso(),
        "dataset": dataset,
        "models": [
            {
                "key": item["key"],
                "label": item.get("label"),
                "llm_mode": item.get("llm_mode"),
                "llm_generator_model": item.get("llm_generator_model"),
                "openrouter_provider": item.get("openrouter_provider"),
            }
            for item in models
        ],
        "cli_args": cli_args,
        "git_sha": git_sha(),
        "total_pairs": total_pairs,
        "env_summary": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
    }


def print_dry_run(
    matrix: list[tuple[dict[str, Any], dict[str, Any]]],
    dataset: dict[str, Any],
    models: list[dict[str, Any]],
) -> None:
    for case, model in matrix:
        print("DRY_RUN_PAIR case_id=" + str(case.get("id")) + " model_key=" + str(model.get("key")))
    print(
        json.dumps(
            {
                "status": "DRY_RUN",
                "dataset_id": dataset["dataset_id"],
                "dataset_version": dataset["dataset_version"],
                "models": [item["key"] for item in models],
                "total_pairs": len(matrix),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def check_resume_config(run_dir: Path, config: dict[str, Any], resume: bool, force: bool) -> None:
    old_path = run_dir / "config.json"
    if not resume or force or not old_path.exists():
        return
    old = json.loads(old_path.read_text(encoding="utf-8"))
    old_models = [item.get("key") for item in old.get("models") or []]
    new_models = [item.get("key") for item in config.get("models") or []]
    checks = [
        (old.get("dataset", {}).get("sha256"), config.get("dataset", {}).get("sha256"), "dataset sha256"),
        (old.get("dataset", {}).get("dataset_version"), config.get("dataset", {}).get("dataset_version"), "dataset version"),
        (old_models, new_models, "models"),
    ]
    for old_value, new_value, name in checks:
        if old_value != new_value:
            raise ConfigError("Resume config mismatch for " + name + ". Use --force to override.")


def write_config(run_dir: Path, config: dict[str, Any], resume: bool) -> None:
    path = run_dir / "config.json"
    if resume and path.exists():
        return
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def prepare_store(
    args: argparse.Namespace,
    dataset: dict[str, Any],
    rows: list[dict[str, Any]],
    models: list[dict[str, Any]],
    config: dict[str, Any],
) -> tuple[BenchmarkClient | None, bool, str | None]:
    if not args.store_token:
        return None, False, "store token is not configured"
    client = BenchmarkClient(args.store_url, args.store_token, timeout_sec=30, retries=2)
    try:
        client.health()
        if not args.no_upload_cases:
            client.upsert_dataset_cases(dataset["dataset_id"], rows, dataset_version=dataset["dataset_version"])
        client.register_run(
            args.benchmark_run_id,
            dataset["dataset_id"],
            dataset["dataset_version"],
            started_at=config["started_at"],
            model_matrix=[item["key"] for item in models],
            config_jsonb=config,
            total_cases=len(rows),
            completed_cases=0,
            status="active",
            isolation_mode=getattr(args, "isolation", "production"),
            parent_run_id=getattr(args, "parent_run_id", "") or None,
            case_ids_filter=args.case_id or None,
            prompt_version_override=getattr(args, "prompt_version_override", "") or None,
            prompt_check_enabled=not bool(getattr(args, "prompt_check_disabled", False)),
            prompt_check_backend=getattr(args, "prompt_check_backend", "") or None,
            prompt_check_model=getattr(args, "prompt_check_model", "") or None,
            prompt_check_openrouter_provider=getattr(args, "prompt_check_openrouter_provider", "") or None,
            smart_judge_backend=getattr(args, "smart_judge_backend", "") or None,
            smart_judge_model=getattr(args, "smart_judge_model", "") or None,
            smart_judge_chunk_size=getattr(args, "smart_judge_chunk_size", 10),
            smart_judge_workers=getattr(args, "smart_judge_workers", 3),
        )
    except BenchmarkClientError as exc:
        return client, False, str(exc)
    return client, True, None


def run_pair(
    args: argparse.Namespace,
    dataset: dict[str, Any],
    case: dict[str, Any],
    model: dict[str, Any],
    store_client: BenchmarkClient | None,
) -> tuple[dict[str, Any], int]:
    body = {
        "task": case["user_task"],
        "llm_mode": model["llm_mode"],
        "llm_generator_model": model["llm_generator_model"],
        "max_iterations": args.max_iterations,
        "isolation_mode": getattr(args, "isolation", "production"),
    }
    codex_effort = model.get("codex_reasoning_effort") or getattr(args, "codex_reasoning_effort", "")
    if codex_effort:
        body["codex_reasoning_effort"] = str(codex_effort)
    if model.get("openrouter_provider"):
        body["openrouter_provider"] = str(model["openrouter_provider"])
    if getattr(args, "prompt_version_override", ""):
        body["prompt_version_override"] = args.prompt_version_override
    body["prompt_check_enabled"] = not bool(getattr(args, "prompt_check_disabled", False))
    if getattr(args, "prompt_check_backend", ""):
        body["prompt_check_backend"] = args.prompt_check_backend
    if getattr(args, "prompt_check_model", ""):
        body["prompt_check_model"] = args.prompt_check_model
    if getattr(args, "prompt_check_openrouter_provider", ""):
        body["prompt_check_openrouter_provider"] = args.prompt_check_openrouter_provider
    result, attempts = post_run(args.api_url, body, args.timeout_sec, args.retry_attempts, args.retry_backoff)
    trace_id = trace_id_from_result(result)
    if not trace_id:
        raise ApiError("system_result.metadata.trace_id is empty", retryable=False)
    trace = load_trace(trace_id, args.traces_dir, args.api_url)
    payload = build_payload(args, dataset, case, model, result, trace, trace_id)

    uploaded = False
    uploaded_at: str | None = None
    store_action = "not_configured"
    if store_client is not None:
        try:
            replace = args.on_duplicate == "replace"
            store_client.ingest(payload, replace=replace)
            uploaded = True
            uploaded_at = now_iso()
            store_action = "replaced" if replace else "inserted"
        except BenchmarkClientError as exc:
            if exc.status_code == 409 and args.on_duplicate == "skip":
                uploaded = True
                uploaded_at = now_iso()
                store_action = "duplicate_skipped"
            elif exc.status_code == 409 and args.on_duplicate == "fail":
                raise ApiError("duplicate_logical_run: " + str(exc), status_code=409, retryable=False) from exc
            else:
                uploaded = False
                store_action = "failed"

    row = result_row(case, model, result, trace, trace_id, uploaded, uploaded_at, store_action)
    return row, attempts


def post_run(
    api_url: str,
    body: dict[str, Any],
    timeout_sec: int,
    retry_attempts: int,
    retry_backoff: float,
) -> tuple[dict[str, Any], int]:
    url = api_url.rstrip("/") + "/run"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last: ApiError | None = None
    for attempt in range(1, retry_attempts + 1):
        req = request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=timeout_sec) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ApiError("/run response is not a JSON object")
                return payload, attempt
        except error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            retryable = exc.code == 429 or exc.code >= 500
            last = ApiError("HTTP " + str(exc.code) + ": " + text, status_code=exc.code, retryable=retryable)
            if not retryable:
                raise last
        except (error.URLError, socket.timeout, TimeoutError) as exc:
            last = ApiError(str(exc), retryable=True)
        if attempt < retry_attempts:
            time.sleep(retry_backoff * (2 ** (attempt - 1)))
    raise last or ApiError("request failed", retryable=True)


def trace_id_from_result(result: dict[str, Any]) -> str:
    meta = result.get("metadata")
    if isinstance(meta, dict):
        return str(meta.get("trace_id") or "")
    return ""


def load_trace(trace_id: str, traces_dir: Path, api_url: str) -> dict[str, Any]:
    path = traces_dir / (trace_id + ".json")
    for attempt in range(2):
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
            raise ApiError("Trace file is not a JSON object: " + str(path))
        if attempt == 0:
            time.sleep(1.0)

    remote = try_remote_trace(api_url, trace_id)
    if remote is not None:
        return remote
    raise ApiError("trace_not_found: " + str(path), retryable=False)


def try_remote_trace(api_url: str, trace_id: str) -> dict[str, Any] | None:
    for path in ("/web/api/traces/" + trace_id, "/trace/" + trace_id, "/v1/trace/" + trace_id):
        req = request.Request(api_url.rstrip("/") + path, method="GET", headers={"Accept": "application/json"})
        try:
            with request.urlopen(req, timeout=10) as resp:
                text = resp.read().decode("utf-8")
                data = json.loads(text)
                if isinstance(data, dict):
                    return data
        except Exception:
            continue
    return None


def build_payload(
    args: argparse.Namespace,
    dataset: dict[str, Any],
    case: dict[str, Any],
    model: dict[str, Any],
    result: dict[str, Any],
    trace: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    payload = {
        "trace_id": trace_id,
        "benchmark_run_id": args.benchmark_run_id,
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["dataset_version"],
        "case_id": case["id"],
        "model_key": model["key"],
        "llm_mode": model["llm_mode"],
        "isolation_mode": getattr(args, "isolation", "production"),
        "system_result": result,
        "trace": trace,
        "client_meta": {
            "runner_version": RUNNER_VERSION,
            "git_sha": git_sha(),
            "uploaded_at": now_iso(),
            "case": case,
            "parent_run_id": getattr(args, "parent_run_id", "") or None,
            "prompt_version_override": getattr(args, "prompt_version_override", "") or None,
            "openrouter_provider": model.get("openrouter_provider"),
        },
    }
    report = report_data(trace_id, args, case, model, result, trace)
    if report:
        payload["report_data"] = report
    return payload


def report_data(
    trace_id: str,
    args: argparse.Namespace,
    case: dict[str, Any],
    model: dict[str, Any],
    result: dict[str, Any],
    trace: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        from app.test_report import TestRun, build_report_data
    except Exception:
        return None
    try:
        run = TestRun(
            run_id=trace_id,
            user_id=0,
            user_name="benchmark",
            task=str(case.get("user_task") or ""),
            model_key=str(model.get("key") or ""),
            model_label=str(model.get("label") or model.get("key") or ""),
            llm_mode=str(model.get("llm_mode") or ""),
            llm_generator_model=str(model.get("llm_generator_model") or ""),
            started_at=parse_dt(trace.get("started_at")),
            finished_at=parse_dt(trace.get("finished_at")),
            system_result=result,
            trace=trace,
        )
        return build_report_data(run)
    except Exception:
        return None


def result_row(
    case: dict[str, Any],
    model: dict[str, Any],
    result: dict[str, Any],
    trace: dict[str, Any],
    trace_id: str,
    uploaded: bool,
    uploaded_at: str | None,
    store_action: str | None = None,
) -> dict[str, Any]:
    meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    usage = usage_totals(trace)
    duration = number(trace.get("duration_sec") or meta.get("duration_sec"))
    if duration is not None and duration < 0:
        duration = 0.0
    return {
        "case_id": case["id"],
        "model_key": model["key"],
        "trace_id": trace_id,
        "decision": str(meta.get("decision") or "unknown"),
        "approved": bool(result.get("approved")),
        "needs_human": bool(meta.get("needs_human")),
        "iterations_used": result.get("iterations_used"),
        "duration_sec": duration,
        "finding_counts": finding_counts(trace),
        "explain_ok": explain_ok(trace),
        "uploaded_to_store": uploaded,
        "uploaded_at": uploaded_at,
        "store_action": store_action or ("uploaded" if uploaded else "not_uploaded"),
        "cost_usd": usage["cost_usd"],
        "tokens_in": usage["prompt_tokens"],
        "tokens_out": usage["completion_tokens"],
        "tokens_total": usage["total_tokens"],
    }


def fail_row(
    args: argparse.Namespace,
    case: dict[str, Any],
    model: dict[str, Any],
    attempt: int,
    error_class: str,
    message: str,
    http_status: int | None,
) -> dict[str, Any]:
    return {
        "benchmark_run_id": args.benchmark_run_id,
        "case_id": case.get("id"),
        "model_key": model.get("key"),
        "attempt": attempt,
        "error_class": error_class,
        "message": message,
        "http_status": http_status,
        "created_at": now_iso(),
    }


def finding_counts(trace: dict[str, Any]) -> dict[str, int]:
    counts = {"sql_guard": 0, "audit": 0, "prompt_check": 0}
    for event in events(trace):
        node = str(event.get("node") or "")
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        if node == "sql_guard":
            counts["sql_guard"] += len(as_list(details.get("findings")))
        elif node == "audit":
            counts["audit"] += len(as_list(details.get("merged_findings")))
        elif node == "prompt_check":
            counts["prompt_check"] += len(as_list(details.get("findings")))
    return counts


def explain_ok(trace: dict[str, Any]) -> bool | None:
    for event in events(trace):
        if event.get("node") == "explain_sandbox":
            outputs = event.get("outputs") if isinstance(event.get("outputs"), dict) else {}
            value = outputs.get("ok")
            return bool(value) if value is not None else None
    return None


def usage_totals(trace: dict[str, Any]) -> dict[str, float | int]:
    prompt = 0
    completion = 0
    total = 0
    cost = 0.0

    def add_usage(value: Any) -> None:
        nonlocal prompt, completion, total, cost
        if not isinstance(value, dict):
            return
        prompt += int(number(value.get("prompt_tokens")) or 0)
        completion += int(number(value.get("completion_tokens")) or 0)
        total += int(number(value.get("total_tokens")) or 0)
        cost += float(number(value.get("cost_usd")) or 0.0)

    for event in events(trace):
        node = event.get("node")
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        if node == "generate":
            for candidate in as_list(details.get("candidates")):
                usage = candidate.get("usage") if isinstance(candidate, dict) else None
                add_usage(usage)
        elif node in {"audit", "prompt_check"}:
            call = details.get("llm_call") if isinstance(details.get("llm_call"), dict) else {}
            add_usage(call.get("usage"))

    if not total:
        total = prompt + completion
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cost_usd": round(cost, 6),
    }


def write_summary(
    run_dir: Path,
    config: dict[str, Any],
    matrix: list[tuple[dict[str, Any], dict[str, Any]]],
    rows: list[dict[str, Any]],
    *,
    stop_reason: str | None,
    skipped: int,
) -> dict[str, Any]:
    results = read_jsonl(run_dir / "results.jsonl") if (run_dir / "results.jsonl").exists() else []
    failures = read_jsonl(run_dir / "failed.jsonl") if (run_dir / "failed.jsonl").exists() else []
    selected = {(case["id"], model["key"]) for case, model in matrix}
    result_by_pair = {
        (str(row.get("case_id")), str(row.get("model_key"))): row
        for row in results
        if (str(row.get("case_id")), str(row.get("model_key"))) in selected
    }
    failed_pairs = {
        (str(row.get("case_id")), str(row.get("model_key")))
        for row in failures
        if (str(row.get("case_id")), str(row.get("model_key"))) in selected
    }
    failed_pairs = failed_pairs - set(result_by_pair)
    by_model = model_summary(matrix, result_by_pair)
    by_family = family_summary(rows, result_by_pair)
    started = parse_dt(config.get("started_at"))
    finished = datetime.now(timezone.utc)
    summary = {
        "benchmark_run_id": config["benchmark_run_id"],
        "started_at": config.get("started_at"),
        "finished_at": finished.isoformat(),
        "elapsed_sec": round((finished - started).total_seconds(), 3),
        "total_pairs": len(matrix),
        "succeeded": len(result_by_pair),
        "failed": len(failed_pairs),
        "skipped": skipped,
        "stop_reason": stop_reason,
        "by_model": by_model,
        "by_family": by_family,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def model_summary(
    matrix: list[tuple[dict[str, Any], dict[str, Any]]],
    result_by_pair: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    planned = Counter(model["key"] for _, model in matrix)
    out = []
    for model_key in sorted(planned):
        rows = [row for (case_id, key), row in result_by_pair.items() if key == model_key]
        decisions = Counter(str(row.get("decision") or "unknown") for row in rows)
        out.append(
            {
                "model_key": model_key,
                "n": planned[model_key],
                "ok": len(rows),
                "decision": dict(sorted(decisions.items())),
                "cost_usd": round(sum(float(row.get("cost_usd") or 0.0) for row in rows), 6),
                "tokens_in": sum(int(row.get("tokens_in") or 0) for row in rows),
                "tokens_out": sum(int(row.get("tokens_out") or 0) for row in rows),
            }
        )
    return out


def family_summary(rows: list[dict[str, Any]], result_by_pair: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    by_case = {str(row.get("id")): str(row.get("family") or "unknown") for row in rows}
    data: dict[str, Counter[str]] = defaultdict(Counter)
    for (case_id, _), row in result_by_pair.items():
        data[by_case.get(case_id, "unknown")][str(row.get("decision") or "unknown")] += 1
    return [
        {"family": family, "n": sum(counter.values()), "decision": dict(sorted(counter.items()))}
        for family, counter in sorted(data.items())
    ]


def completed_pairs(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    return {
        (str(row.get("case_id")), str(row.get("model_key")))
        for row in read_jsonl(path)
        if row.get("case_id") and row.get("model_key")
    }


def current_cost(path: Path) -> float:
    if not path.exists():
        return 0.0
    return sum(float(row.get("cost_usd") or 0.0) for row in read_jsonl(path))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                data = json.loads(line)
                if isinstance(data, dict):
                    rows.append(data)
    return rows


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def events(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in trace.get("events") or [] if isinstance(item, dict)]


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_dt(value: Any) -> datetime:
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return "unknown"
    return result.stdout.strip() or "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
