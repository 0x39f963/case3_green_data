from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import llm_provider, prompt_registry  # noqa: E402
from benchmark_service import db  # noqa: E402


def run_analysis(args: argparse.Namespace) -> dict[str, Any]:
    _load_benchmark_env()
    job_id = db.create_analysis_job(
        args.benchmark_run_id,
        backend=args.backend,
        model=args.model,
        missing_only=args.missing_only,
        config={
            "limit": args.limit,
            "oracle_required": args.oracle_required,
            "status_on_error": args.status_on_error,
        },
        log_path=args.log_path or None,
        job_id=args.job_id or None,
    )
    db.update_analysis_run_status(args.benchmark_run_id, "running", job_id=job_id, log_path=args.log_path or None)
    trace_ids = db.list_analysis_trace_ids(
        args.benchmark_run_id,
        args.backend,
        args.model,
        missing_only=args.missing_only,
        oracle_required=args.oracle_required,
        limit=args.limit,
        trace_ids=args.trace_id,
    )
    print_event("START", benchmark_run_id=args.benchmark_run_id, job_id=job_id, targets=len(trace_ids))
    ok = 0
    failed = 0
    for index, trace_id in enumerate(trace_ids, start=1):
        payload = db.get_case_analysis_input(trace_id)
        if not payload:
            failed += 1
            print_event("SKIP_MISSING_TRACE", trace_id=trace_id)
            continue
        try:
            parsed, raw = analyze_one(payload, args.backend, args.model)
            status = "ok"
            error_text = None
        except Exception as exc:
            parsed = _error_payload(str(exc))
            raw = {"error": str(exc)}
            status = _status_for_error(str(exc), args.status_on_error)
            error_text = str(exc)
        report_id = _save_report(job_id, payload, args.backend, args.model, status, parsed, raw)
        _save_hypotheses(report_id, payload, parsed)
        ok += 1 if status == "ok" else 0
        failed += 0 if status == "ok" else 1
        print_event("CASE", idx=index, trace_id=trace_id, status=status, error=error_text)

    final_status = "completed" if failed == 0 else ("partial" if ok else "failed")
    db.update_analysis_job_status(job_id, final_status, error_text=None if failed == 0 else str(failed) + " cases failed")
    db.update_analysis_run_status(args.benchmark_run_id, final_status, job_id=job_id, log_path=args.log_path or None)
    print_event("DONE", status=final_status, ok=ok, failed=failed)
    return {"job_id": job_id, "status": final_status, "ok": ok, "failed": failed}


def analyze_one(payload: dict[str, Any], backend: str, model: str) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt = prompt_registry.get_default("judge_audit_hypothesis_system")
    client = _quality_client(backend, model)
    response = client.invoke(
        str(prompt.get("text") or ""),
        json.dumps(_compact_payload(payload), ensure_ascii=False, indent=2, default=str),
        temperature=0,
        response_format={"type": "json_object"},
    )
    parsed = _parse_json(response.text)
    parsed.setdefault("summary", "")
    parsed.setdefault("root_causes", [])
    parsed.setdefault("hypotheses", [])
    parsed.setdefault("evidence", [])
    return parsed, {"response": response.text, "usage": response.usage_norm or {}}


def _quality_client(backend: str, model: str) -> llm_provider.LLMClient:
    key = backend.replace("-", "_")
    if key == "claude_cli":
        key = "anthropic_cli"
    role = "generator" if key == "codex_cli" else "direct"
    return llm_provider._build_direct_client(key, model, role=role)  # type: ignore[attr-defined]


def _compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    run = dict(payload.get("run") or {})
    quality = dict(payload.get("quality") or {})
    oracle = dict(payload.get("oracle") or {})
    raw = payload.get("raw_payload") if isinstance(payload.get("raw_payload"), dict) else {}
    case = ((raw.get("client_meta") or {}).get("case") or {}) if isinstance(raw, dict) else {}
    return {
        "pipeline_run": {
            key: run.get(key)
            for key in (
                "trace_id", "benchmark_run_id", "case_id", "model_key", "decision",
                "approved", "needs_human", "human_reason", "iterations_used",
                "duration_sec", "generator_backend", "generator_model",
                "generator_provider", "auditor_backend", "auditor_model",
                "final_sql_text",
            )
        },
        "task": case.get("user_task") or case.get("task") or raw.get("task") or (raw.get("trace") or {}).get("task"),
        "smart_judge": {
            key: quality.get(key)
            for key in (
                "score_id", "reviewer_status", "overall_score", "sql_correctness",
                "security", "intent_fidelity", "schema_usage", "rag_facts_used",
                "decision_rationale", "performance", "robustness",
                "retry_efficiency", "patch_target_area", "patch_severity",
                "patch_title", "patch_details", "patch_hint",
                "reviewer_raw_jsonb",
            )
        },
        "oracle": {
            key: oracle.get(key)
            for key in (
                "id", "oracle_type", "oracle_test_id", "verdict",
                "ast_semantic_ok", "assertions_jsonb", "reasons_jsonb",
                "error_message",
            )
        },
        "findings": _take(payload.get("findings"), 20),
        "pipeline_steps": _take(payload.get("steps"), 20),
        "faiss_hits": _take(payload.get("faiss_hits"), 20),
        "generator_candidate_metrics": _take(payload.get("generator_candidate_metrics"), 12),
        "llm_calls": _take(payload.get("llm_calls"), 20),
    }


def _take(value: Any, limit: int) -> list[Any]:
    return list(value or [])[:limit]


def _parse_json(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("analysis response must be a JSON object")
    for key in ("root_causes", "hypotheses", "evidence"):
        if key in data and not isinstance(data[key], list):
            data[key] = []
    return data


def _save_report(
    job_id: str,
    payload: dict[str, Any],
    backend: str,
    model: str,
    status: str,
    parsed: dict[str, Any],
    raw: dict[str, Any],
) -> str:
    run = payload.get("run") or {}
    quality = payload.get("quality") or {}
    oracle = payload.get("oracle") or {}
    return db.insert_case_analysis_report(
        job_id=job_id,
        trace_id=str(run.get("trace_id") or ""),
        benchmark_run_id=str(run.get("benchmark_run_id") or ""),
        case_id=str(run.get("case_id") or ""),
        score_id=quality.get("score_id") if quality else None,
        oracle_eval_id=oracle.get("id") if oracle else None,
        backend=backend,
        model=model,
        status=status,
        summary=str(parsed.get("summary") or ""),
        root_causes=[item for item in parsed.get("root_causes") or [] if isinstance(item, dict)],
        hypotheses=[item for item in parsed.get("hypotheses") or [] if isinstance(item, dict)],
        evidence=[item for item in parsed.get("evidence") or [] if isinstance(item, dict)],
        raw_response=raw,
    )


def _save_hypotheses(report_id: str, payload: dict[str, Any], parsed: dict[str, Any]) -> None:
    run = payload.get("run") or {}
    quality = payload.get("quality") or {}
    oracle = payload.get("oracle") or {}
    evidence = parsed.get("evidence") or []
    evidence_text = "; ".join(str(item.get("text") or "") for item in evidence if isinstance(item, dict))[:2000]
    for item in parsed.get("hypotheses") or []:
        if not isinstance(item, dict):
            continue
        db.upsert_hypothesis_with_evidence(
            report_id,
            str(run.get("trace_id") or ""),
            quality.get("score_id") if quality else None,
            oracle.get("id") if oracle else None,
            item,
            evidence_text=evidence_text,
            similarity_score=1.0,
        )


def _error_payload(error_text: str) -> dict[str, Any]:
    return {
        "summary": "Analysis failed: " + error_text[:180],
        "root_causes": [],
        "hypotheses": [],
        "evidence": [{"kind": "runtime", "text": error_text[:500]}],
    }


def _status_for_error(error_text: str, fallback: str) -> str:
    text = error_text.lower()
    if "quota" in text or "rate limit" in text:
        return "quota_exhausted"
    if "timeout" in text:
        return "timeout"
    if fallback in {"parse_error", "runtime_error", "quota_exhausted", "timeout"}:
        return fallback
    return "runtime_error"


def _load_benchmark_env() -> None:
    if os.environ.get("BENCHMARK_DSN") or os.environ.get("BENCH_PG_PORT"):
        return
    env_path = ROOT / "deploy" / "benchmark.env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze smart-judge + Oracle reports into improvement hypotheses.")
    parser.add_argument("--benchmark-run-id", required=True)
    parser.add_argument("--backend", default="codex_cli")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--missing-only", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--oracle-required", action="store_true")
    parser.add_argument("--trace-id", action="append", default=[])
    parser.add_argument("--status-on-error", default="runtime_error")
    parser.add_argument("--codex-reasoning-effort", default="")
    parser.add_argument("--job-id", default="")
    parser.add_argument("--log-path", default="")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be >= 0")
    if args.codex_reasoning_effort:
        os.environ["CODEX_GENERATOR_REASONING_EFFORT"] = args.codex_reasoning_effort
    return args


def print_event(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True), flush=True)


def main() -> int:
    args = parse_args()
    try:
        result = run_analysis(args)
        return 0 if result["status"] in {"completed", "partial"} else 1
    except KeyboardInterrupt:
        if args.job_id:
            db.update_analysis_job_status(args.job_id, "aborted")
        db.update_analysis_run_status(args.benchmark_run_id, "aborted")
        print_event("ABORTED")
        return 130
    except Exception as exc:
        if args.job_id:
            db.update_analysis_job_status(args.job_id, "failed", error_text=str(exc))
        db.update_analysis_run_status(args.benchmark_run_id, "failed", error_text=str(exc))
        print_event("FAILED", error=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
