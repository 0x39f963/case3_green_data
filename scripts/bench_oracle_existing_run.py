from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark_service import db  # noqa: E402
from scripts._oracle.dispatchers import dispatch  # noqa: E402
from scripts._oracle.loaders import load_golden_v1_1  # noqa: E402
from scripts._oracle.types import OracleCase, OracleVerdict  # noqa: E402


DEFAULT_GOLDEN = ROOT / "data" / "eval" / "golden_dataset_v1_1.csv"


def normalize_oracle_test_id(case_id: str) -> str:
    match = re.search(r"tc[-_]?(\d+)", str(case_id or ""), re.IGNORECASE)
    if not match:
        return str(case_id or "").upper()
    raw = match.group(1)
    return "TC-" + raw.zfill(max(4, len(raw)))


def build_pipeline_response(row: dict[str, Any]) -> dict[str, Any]:
    decision = str(row.get("decision") or "").lower()
    human_reason = row.get("human_reason")
    return {
        "final_sql": row.get("final_sql_text") or "",
        "decision": decision,
        "approved": row.get("approved"),
        "needs_human": row.get("needs_human"),
        "needs_clarification": decision == "clarify",
        "human_reason": human_reason,
        "rationale": human_reason,
        "metadata": {
            "decision": decision,
            "approved": row.get("approved"),
            "human_reason": human_reason,
            "trace_id": row.get("trace_id"),
            "case_id": row.get("case_id"),
            "model_key": row.get("model_key"),
            "generator_backend": row.get("generator_backend"),
            "generator_model": row.get("generator_model"),
            "generator_provider": row.get("generator_provider"),
            "auditor_backend": row.get("auditor_backend"),
            "auditor_model": row.get("auditor_model"),
        },
    }


def run_oracle(args: argparse.Namespace) -> dict[str, Any]:
    _load_benchmark_env()
    cases = {case.test_id: case for case in load_golden_v1_1(args.golden)}
    type_filter = {item.strip() for item in args.oracle_types.split(",") if item.strip()}
    case_filter = {str(item) for item in args.case_id or []}
    case_filter_norm = {normalize_oracle_test_id(item) for item in case_filter}
    existing = db.existing_oracle_keys(args.benchmark_run_id) if args.missing_only else {}
    case_keys = set(existing.get("case_keys", set())) if isinstance(existing, dict) else set(existing)
    trace_keys = set(existing.get("trace_keys", set())) if isinstance(existing, dict) else set()

    rows = db.list_oracle_pipeline_rows(args.benchmark_run_id)
    verdicts: list[dict[str, Any]] = []
    by_type: dict[str, dict[str, int]] = {}
    seen_total = 0
    enqueued = 0
    inserted = 0
    skipped = 0

    counts = db.oracle_counts(args.benchmark_run_id)
    db.update_oracle_run_status(
        args.benchmark_run_id,
        "running",
        running_workers=1,
        total_missing=counts["missing_cases"],
    )
    print_event("START", benchmark_run_id=args.benchmark_run_id, pipeline_rows=len(rows), missing=counts["missing_cases"])

    for row in rows:
        oracle_test_id = normalize_oracle_test_id(str(row.get("case_id") or ""))
        if case_filter and str(row.get("case_id")) not in case_filter and oracle_test_id not in case_filter_norm:
            continue
        case = cases.get(oracle_test_id)
        if not case:
            skipped += 1
            print_event("SKIP_NO_ORACLE_CASE", trace_id=row.get("trace_id"), case_id=row.get("case_id"), oracle_test_id=oracle_test_id)
            continue
        if type_filter and case.oracle_type not in type_filter:
            continue
        seen_total += 1
        case_key = (str(row.get("case_id")), str(case.oracle_type))
        trace_key = (str(row.get("trace_id")), str(case.oracle_type))
        if args.missing_only and (case_key in case_keys or trace_key in trace_keys):
            skipped += 1
            continue
        if args.limit and enqueued >= args.limit:
            break

        started = time.perf_counter()
        verdict = _dispatch_one(case, row, args.status_on_error)
        verdict.elapsed_sec = round(time.perf_counter() - started, 3)
        item = asdict(verdict)
        verdicts.append(item)
        _count(by_type, str(case.oracle_type), verdict.verdict)
        enqueued += 1
        if args.ingest_store:
            saved = db.insert_oracle_eval_result(
                args.benchmark_run_id,
                row,
                item,
                dataset_version=args.dataset_version,
                missing_only=args.missing_only,
            )
            inserted += 1 if saved.get("inserted") else 0
            if saved.get("inserted"):
                case_keys.add(case_key)
                trace_keys.add(trace_key)
        print_event("CASE", idx=enqueued, trace_id=row.get("trace_id"), case_id=row.get("case_id"), oracle_test_id=oracle_test_id, oracle_type=case.oracle_type, verdict=verdict.verdict)

    final_counts = db.oracle_counts(args.benchmark_run_id)
    final_status = "completed" if final_counts["missing_cases"] == 0 else ("partial" if final_counts["completed_cases"] else "not_started")
    db.update_oracle_run_status(
        args.benchmark_run_id,
        final_status,
        running_workers=0,
        total_missing=final_counts["missing_cases"],
    )
    report = {
        "run_id": args.benchmark_run_id,
        "dataset_version": args.dataset_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(verdicts),
        "seen_total": seen_total,
        "inserted": inserted,
        "skipped": skipped,
        "by_oracle_type": by_type,
        "cases": verdicts,
    }
    if args.report:
        write_report(report, args.report)
    print_event("DONE", status=final_status, seen_total=seen_total, evaluated=len(verdicts), inserted=inserted, skipped=skipped, **final_counts)
    return report


def _dispatch_one(case: OracleCase, row: dict[str, Any], status_on_error: str) -> OracleVerdict:
    try:
        return dispatch(case, build_pipeline_response(row))
    except Exception as exc:
        verdict = "error" if status_on_error == "error" else "fail"
        return OracleVerdict(
            test_id=case.test_id,
            oracle_type=str(case.oracle_type),
            verdict=verdict,  # type: ignore[arg-type]
            ast_semantic_ok=None,
            reasons=["oracle_dispatch_error: " + str(exc)],
            error_message=str(exc),
            category_id=case.category_id,
            severity_if_failed=case.severity_if_failed,
            pipeline_decision=str(row.get("decision") or ""),
            pipeline_final_sql=row.get("final_sql_text"),
        )


def write_report(report: dict[str, Any], path: str) -> Path:
    out = Path(path)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return out


def _count(target: dict[str, dict[str, int]], key: str, verdict: str) -> None:
    target.setdefault(key, {"pass": 0, "fail": 0, "error": 0})[verdict] += 1


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
    parser = argparse.ArgumentParser(description="Run Oracle against stored benchmark.pipeline_runs.")
    parser.add_argument("--benchmark-run-id", required=True)
    parser.add_argument("--dataset", default="")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--dataset-version", default="1.1")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--missing-only", action="store_true")
    parser.add_argument("--oracle-types", default="")
    parser.add_argument("--status-on-error", choices=["error", "fail"], default="error")
    parser.add_argument("--report", default="")
    parser.add_argument("--ingest-store", action="store_true")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be >= 0")
    if args.dataset and not args.golden:
        args.golden = Path(args.dataset)
    return args


def print_event(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True), flush=True)


def main() -> int:
    args = parse_args()
    try:
        run_oracle(args)
        return 0
    except KeyboardInterrupt:
        db.update_oracle_run_status(args.benchmark_run_id, "aborted", running_workers=0)
        print_event("ABORTED")
        return 130
    except Exception as exc:
        db.update_oracle_run_status(args.benchmark_run_id, "failed", running_workers=0, error_text=str(exc))
        print_event("FAILED", error=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
