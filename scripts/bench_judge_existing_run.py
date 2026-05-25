from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark_service import db  # noqa: E402
from scripts.bench_smart_judge_worker import JudgeWorkerPool  # noqa: E402


TERMINAL_RUN_STATUSES = {"completed", "failed", "aborted"}


def main() -> int:
    args = parse_args()
    if args.backend == "off":
        print_event("CONFIG_ERROR", error="--backend off cannot run smart-judge")
        return 2

    seen: set[str] = set()
    enqueued = 0
    pool = JudgeWorkerPool(
        backend=args.backend,
        model=args.model,
        chunk_size=args.chunk_size,
        max_workers=args.workers,
        fallback_backend=args.fallback_backend,
        fallback_model=args.fallback_model,
        status_on_error=args.status_on_error,
    )
    pool.start(args.benchmark_run_id)
    counts = db.judge_counts(args.benchmark_run_id, args.backend, args.model)
    db.update_judge_run_status(
        args.benchmark_run_id,
        "running",
        backend=args.backend,
        model=args.model,
        running_workers=0,
        pending_in_queue=counts["missing_cases"],
        total_missing=counts["missing_cases"],
    )
    print_event("START", benchmark_run_id=args.benchmark_run_id, **counts)

    try:
        while True:
            remaining_limit = args.limit - enqueued if args.limit and args.limit > 0 else None
            if remaining_limit is not None and remaining_limit <= 0:
                break

            fetch_limit = remaining_limit if remaining_limit is not None else None
            trace_ids = db.list_judge_trace_ids(
                args.benchmark_run_id,
                args.backend,
                args.model,
                missing_only=args.missing_only,
                limit=fetch_limit,
            )
            fresh = [trace_id for trace_id in trace_ids if trace_id not in seen]
            run = db.get_benchmark_run(args.benchmark_run_id) or {}
            run_status = str(run.get("status") or "")
            run_is_terminal = run_status in TERMINAL_RUN_STATUSES

            if fresh:
                for trace_id in fresh:
                    seen.add(trace_id)
                    pool.enqueue(trace_id)
                    enqueued += 1
                counts = db.judge_counts(args.benchmark_run_id, args.backend, args.model)
                db.update_judge_run_status(
                    args.benchmark_run_id,
                    "running",
                    backend=args.backend,
                    model=args.model,
                    running_workers=pool.running_workers,
                    pending_in_queue=counts["missing_cases"],
                    total_missing=counts["missing_cases"],
                )
                print_event("ENQUEUED", count=len(fresh), total_enqueued=enqueued)

            if not args.watch:
                break
            if remaining_limit is not None and enqueued >= args.limit:
                break
            if run_is_terminal and not fresh:
                break
            if not fresh:
                counts = db.judge_counts(args.benchmark_run_id, args.backend, args.model)
                db.update_judge_run_status(
                    args.benchmark_run_id,
                    "waiting",
                    backend=args.backend,
                    model=args.model,
                    running_workers=pool.running_workers,
                    pending_in_queue=counts["missing_cases"],
                    total_missing=counts["missing_cases"],
                )
                time.sleep(args.poll_sec)

        pool.flush_and_join()
        counts = db.judge_counts(args.benchmark_run_id, args.backend, args.model)
        final_status = "completed" if counts["missing_cases"] == 0 else "partial"
        db.update_judge_run_status(
            args.benchmark_run_id,
            final_status,
            backend=args.backend,
            model=args.model,
            running_workers=0,
            pending_in_queue=counts["missing_cases"],
            total_missing=counts["missing_cases"],
        )
        print_event("DONE", status=final_status, total_enqueued=enqueued, **counts)
        return 0
    except KeyboardInterrupt:
        pool.abort()
        db.update_judge_run_status(args.benchmark_run_id, "aborted", backend=args.backend, model=args.model)
        print_event("ABORTED")
        return 130
    except Exception as exc:
        pool.abort()
        db.update_judge_run_status(
            args.benchmark_run_id,
            "runtime_error",
            backend=args.backend,
            model=args.model,
            running_workers=0,
            error_text=str(exc),
        )
        print_event("RUNTIME_ERROR", error=str(exc))
        return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run smart-judge for existing benchmark pipeline rows.")
    parser.add_argument("--benchmark-run-id", required=True)
    parser.add_argument("--backend", default="codex_cli")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--chunk-size", type=int, default=10)
    parser.add_argument("--missing-only", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--fallback-backend", default="claude_cli")
    parser.add_argument("--fallback-model", default="claude-sonnet-4-6")
    parser.add_argument("--status-on-error", default="runtime_error")
    parser.add_argument("--codex-reasoning-effort", default="")
    parser.add_argument("--watch", action="store_true", help="Poll for new pipeline rows until the benchmark run is terminal.")
    parser.add_argument("--poll-sec", type=float, default=5.0)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be >= 1")
    if args.chunk_size < 1:
        parser.error("--chunk-size must be >= 1")
    if args.limit < 0:
        parser.error("--limit must be >= 0")
    if args.poll_sec <= 0:
        parser.error("--poll-sec must be > 0")
    if args.codex_reasoning_effort:
        os.environ["CODEX_GENERATOR_REASONING_EFFORT"] = args.codex_reasoning_effort
    return args


def print_event(event: str, **fields: Any) -> None:
    payload = {"event": event, **fields}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
