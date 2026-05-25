"""
Cron-entry для Phase 2 meta-аудитора.

Достаёт последние pipeline_runs из benchmark.* WHERE meta_audited=false,
запускает Claude CLI Opus 4.7 на каждом, сохраняет урок в
benchmark.rag_embeddings(index_name='solutions'), ставит флаг.

Запуск через cron раз в час:
    0 * * * * cd /home/x39963/web/mipt/case3 && \\
        TRACES_DIR=$(pwd)/data/traces \\
        BENCHMARK_DSN=postgresql://... \\
        .venv/bin/python scripts/run_meta_audit.py --limit 20 >> /var/log/meta_audit.log 2>&1

Или вручную для отладки:
    python scripts/run_meta_audit.py --limit 5

Не часть продуктового пути. Latency пользователю не виден.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import psycopg2
import psycopg2.extras

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app import meta_auditor  # noqa: E402


def _pick_traces(limit: int) -> list[dict[str, object]]:
    """Свежие pipeline_runs без мета-аудита, новые сначала."""
    with psycopg2.connect(meta_auditor._bench_dsn()) as conn:
        conn.set_session(readonly=True)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT trace_id, created_at, iterations_used, approved
                FROM benchmark.pipeline_runs
                WHERE meta_audited = FALSE
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 2 meta-audit batch")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--model", default=meta_auditor.DEFAULT_MODEL)
    parser.add_argument("--timeout-sec", type=int, default=meta_auditor.DEFAULT_TIMEOUT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-approved", action="store_true",
                        help="по умолчанию пропускаем уже approved прогоны "
                             "(чтобы не загромождать индекс solutions success-case-ами)")
    args = parser.parse_args()

    traces = _pick_traces(args.limit)
    if not traces:
        print("no pending traces for meta-audit")
        return 0

    print("Picked " + str(len(traces)) + " traces for meta-audit")
    ok = 0
    failed = 0
    skipped = 0
    started = time.time()
    for row in traces:
        trace_id = str(row["trace_id"])
        approved = bool(row.get("approved"))
        if approved and not args.include_approved:
            skipped += 1
            print("[skip] " + trace_id + " — approved (use --include-approved to override)")
            continue
        t0 = time.time()
        try:
            if args.dry_run:
                result = meta_auditor.review_trace(
                    trace_id, model=args.model, timeout_sec=args.timeout_sec
                )
                elapsed = time.time() - t0
                print("[dry] " + trace_id + " · " + result.task_type
                      + " · {:.1f}s".format(elapsed))
                print("       lesson: " + result.lesson_for_generator[:160])
            else:
                result, eid = meta_auditor.review_and_save(
                    trace_id, model=args.model, timeout_sec=args.timeout_sec
                )
                elapsed = time.time() - t0
                print("[ok ] " + trace_id + " · " + result.task_type
                      + " · lesson_id=" + str(eid)
                      + " · {:.1f}s".format(elapsed))
            ok += 1
        except Exception as exc:
            elapsed = time.time() - t0
            failed += 1
            print("[err] " + trace_id + " · {:.1f}s · {}".format(elapsed, str(exc)[:200]))

    total_elapsed = time.time() - started
    print("=" * 60)
    print("done: ok={} failed={} skipped={} total={:.1f}s".format(
        ok, failed, skipped, total_elapsed
    ))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
