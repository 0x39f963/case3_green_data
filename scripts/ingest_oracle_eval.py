"""Ingest oracle-aware eval JSON reports into benchmark.oracle_eval_runs."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg2
from psycopg2.extras import execute_values

from app.rag_adapter import _bench_dsn_for_solutions as _dsn


def ingest_report(report_path: Path | str) -> int:
    """Read one report JSON and insert per-case rows into benchmark Postgres."""
    _load_benchmark_env()
    path = Path(report_path)
    report = json.loads(path.read_text(encoding="utf-8"))
    cases = report.get("cases", [])
    if not cases:
        return 0

    run_id = report.get("run_id")
    llm_mode = report.get("llm_mode")
    llm_model = report.get("llm_generator_model")
    dataset_version = report.get("dataset_version", "1.1")

    rows = []
    for case in cases:
        rows.append(
            (
                run_id,
                case.get("test_id"),
                case.get("category_id"),
                case.get("oracle_type"),
                case.get("verdict"),
                case.get("severity_if_failed"),
                case.get("ast_semantic_ok"),
                json.dumps(case.get("assertions", []), ensure_ascii=False),
                json.dumps(case.get("reasons", []), ensure_ascii=False),
                case.get("pipeline_decision"),
                case.get("pipeline_final_sql"),
                case.get("elapsed_sec"),
                case.get("error_message"),
                llm_mode,
                llm_model,
                dataset_version,
            )
        )

    with psycopg2.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO benchmark.oracle_eval_runs
                    (run_id, case_id, category_id, oracle_type, verdict, severity,
                     ast_semantic_ok, assertions_jsonb, reasons_jsonb, pipeline_decision,
                     pipeline_final_sql, elapsed_sec, error_message, llm_mode,
                     llm_generator_model, dataset_version)
                VALUES %s
                """,
                rows,
                template="(%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)",
            )
        conn.commit()
    return len(rows)


def _load_benchmark_env() -> None:
    """Load deploy/benchmark.env defaults when shell env is not populated."""
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


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/ingest_oracle_eval.py <report.json>", file=sys.stderr)
        return 2
    count = ingest_report(sys.argv[1])
    print(f"ingested {count} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
