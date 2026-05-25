"""Oracle-aware eval runner for golden v1.1.

Usage:
    python scripts/eval_oracle_aware.py --limit 30
    python scripts/eval_oracle_aware.py --limit 600 --report data/eval/reports/oracle_v1_1_full.json
    python scripts/eval_oracle_aware.py --limit 50 --oracle-types reference_sql,refusal_only
    python scripts/eval_oracle_aware.py --limit 30 --ingest-store
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._oracle.dispatchers import dispatch
from scripts._oracle.loaders import load_golden_v1_1
from scripts._oracle.types import EvalReport, OracleVerdict


def post_run(
    base_url: str,
    task: str,
    *,
    llm_mode: str | None,
    llm_generator_model: str | None,
    timeout: float,
) -> dict[str, Any]:
    """Call POST /run."""
    payload: dict[str, Any] = {"task": task, "max_iterations": 5}
    if llm_mode:
        payload["llm_mode"] = llm_mode
    if llm_generator_model:
        payload["llm_generator_model"] = llm_generator_model
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(base_url.rstrip("/") + "/run", json=payload)
        resp.raise_for_status()
        return resp.json()


def run_eval(args: argparse.Namespace) -> EvalReport:
    """Run oracle-aware eval and return the in-memory report."""
    cases = load_golden_v1_1(args.golden)
    type_filter = {item.strip() for item in args.oracle_types.split(",") if item.strip()}
    if type_filter:
        cases = [case for case in cases if case.oracle_type in type_filter]
    if args.limit and args.limit > 0:
        cases = cases[: args.limit]

    run_id = f"oracle_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
    print(f"[info] run_id={run_id}", flush=True)
    print(f"[info] running {len(cases)} cases ({args.oracle_types or 'all types'})", flush=True)

    verdicts: list[OracleVerdict] = []
    by_type: dict[str, dict[str, int]] = {}
    by_severity: dict[str, dict[str, int]] = {}

    for idx, case in enumerate(cases, start=1):
        started = time.perf_counter()
        try:
            response = post_run(
                args.base_url,
                case.nl_prompt,
                llm_mode=args.llm_mode,
                llm_generator_model=args.llm_generator_model,
                timeout=args.timeout,
            )
            verdict = dispatch(case, response)
        except Exception as exc:
            verdict = OracleVerdict(
                test_id=case.test_id,
                oracle_type=str(case.oracle_type),
                verdict="error",
                ast_semantic_ok=None,
                reasons=[f"pipeline_error: {exc}"],
                error_message=str(exc),
                category_id=case.category_id,
                severity_if_failed=case.severity_if_failed,
            )
        verdict.elapsed_sec = round(time.perf_counter() - started, 2)
        verdicts.append(verdict)
        _count(by_type, str(case.oracle_type), verdict.verdict)
        _count(by_severity, case.severity_if_failed, verdict.verdict)
        print(f"[{idx}/{len(cases)}] {case.test_id} [{case.oracle_type}] -> {verdict.verdict} ({verdict.elapsed_sec}s)", flush=True)

    pass_count = sum(1 for item in verdicts if item.verdict == "pass")
    rate = round(pass_count / len(verdicts), 4) if verdicts else 0.0
    return EvalReport(
        dataset_version="1.1",
        total_cases=len(verdicts),
        by_oracle_type=by_type,
        by_severity=by_severity,
        aggregate_pass_rate=rate,
        cases=verdicts,
        run_id=run_id,
        llm_mode=args.llm_mode or "default",
        llm_generator_model=args.llm_generator_model or "default",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def write_report(report: EvalReport, path: str | None) -> Path:
    """Write report JSON and return its path."""
    out_path = Path(path) if path else ROOT / "data" / "eval" / "reports" / f"oracle_v1_1_{report.run_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _count(target: dict[str, dict[str, int]], key: str, verdict: str) -> None:
    target.setdefault(key, {"pass": 0, "fail": 0, "error": 0})[verdict] += 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", default=str(ROOT / "data" / "eval" / "golden_dataset_v1_1.csv"))
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--oracle-types", default="", help="comma-separated list; default: all")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--llm-mode", default=None)
    parser.add_argument("--llm-generator-model", default=None)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--report", default=None)
    parser.add_argument("--ingest-store", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_eval(args)
    out_path = write_report(report, args.report)
    print(f"[done] report -> {out_path}", flush=True)
    print(f"[done] aggregate_pass_rate = {report.aggregate_pass_rate}", flush=True)
    for name, counts in report.by_oracle_type.items():
        print(f"  {name}: pass={counts.get('pass', 0)}, fail={counts.get('fail', 0)}, error={counts.get('error', 0)}", flush=True)
    if args.ingest_store:
        from scripts.ingest_oracle_eval import ingest_report

        count = ingest_report(out_path)
        print(f"[done] ingested {count} cases into benchmark.oracle_eval_runs", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
