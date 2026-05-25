#!/usr/bin/env python3
"""Build table_knowledge_v2 RAG index in benchmark.rag_embeddings."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from psycopg2.extras import execute_values


INDEX_NAME = "table_knowledge_v2"
DEFAULT_CSV_PATH = Path("data/rag/v2/table_knowledge_index_v2.csv")
EXPECTED_ROWS = 60


def _bench_dsn() -> str:
    dsn = os.environ.get("BENCHMARK_DSN", "").strip()
    if dsn:
        return dsn
    user = os.environ.get("BENCH_USER", "bench")
    password = os.environ.get("BENCH_PASSWORD", "bench")
    host = os.environ.get("BENCH_PG_HOST", "127.0.0.1")
    port = os.environ.get("BENCH_PG_PORT", "15434")
    db = os.environ.get("BENCH_DB", "bench")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _read_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    with csv_path.open(encoding="utf-8", newline="") as f:
        next(f, None)  # row 1 is generated_at metadata, row 2 is the real header
        reader = csv.DictReader(f, delimiter=",")
        fields = reader.fieldnames or []
        if "table_name" not in fields:
            raise ValueError("CSV header does not contain table_name")
        if "llm_index_text" not in fields:
            raise ValueError("CSV header does not contain llm_index_text")
        rows = list(reader)

    if len(rows) != EXPECTED_ROWS:
        raise ValueError(f"Expected {EXPECTED_ROWS} data rows, got {len(rows)}")

    bad = [idx + 3 for idx, row in enumerate(rows) if not row.get("table_name") or not row.get("llm_index_text")]
    if bad:
        raise ValueError(f"Missing table_name or llm_index_text at CSV rows: {bad[:10]}")
    return rows


def _metadata(row: dict[str, str], row_num: int) -> dict[str, Any]:
    return {
        "table_name": row["table_name"],
        "business_domain": row.get("business_domain", ""),
        "entity_role": row.get("entity_role", ""),
        "grain": row.get("grain", ""),
        "aliases": row.get("aliases", ""),
        "natural_language_triggers": row.get("natural_language_triggers", ""),
        "use_when": row.get("use_when", ""),
        "avoid_when": row.get("avoid_when", ""),
        "primary_key": row.get("primary_key", ""),
        "related_tables": row.get("related_tables", ""),
        "approved_joins": row.get("approved_joins", ""),
        "sensitive_columns": row.get("sensitive_columns", ""),
        "pii_tags": row.get("pii_tags", ""),
        "confidence": row.get("confidence", "high"),
        "index_version": "v2.0",
        "source_csv_row": row_num,
    }


def _embed(texts: list[str], batch_size: int) -> Any:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("intfloat/multilingual-e5-small", local_files_only=True)
    return model.encode(
        ["passage: " + text for text in texts],
        normalize_embeddings=True,
        batch_size=batch_size,
    )


def _existing_count(cur: Any) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM benchmark.rag_embeddings WHERE index_name = %s",
        (INDEX_NAME,),
    )
    return int(cur.fetchone()[0])


def _insert_rows(rows: list[dict[str, str]], embeddings: Any, force: bool, batch_size: int) -> tuple[int, int]:
    import psycopg2

    dsn = _bench_dsn()
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            existing = _existing_count(cur)
            if existing:
                if force:
                    cur.execute(
                        "DELETE FROM benchmark.rag_embeddings WHERE index_name = %s",
                        (INDEX_NAME,),
                    )
                    deleted = int(cur.rowcount)
                    print(f"[INFO] DELETE FROM benchmark.rag_embeddings WHERE index_name='{INDEX_NAME}' (deleted: {deleted})")
                elif existing == EXPECTED_ROWS:
                    print(f"[WARN] Index '{INDEX_NAME}' already has {existing} rows; no-op. Use --force to rebuild.")
                    return 0, existing
                else:
                    raise RuntimeError(
                        f"Index '{INDEX_NAME}' already has {existing} rows; use --force to rebuild cleanly."
                    )
            else:
                deleted = 0

            records = []
            for idx, (row, vec) in enumerate(zip(rows, embeddings, strict=True), start=3):
                records.append(
                    (
                        INDEX_NAME,
                        row["llm_index_text"],
                        json.dumps(_metadata(row, idx), ensure_ascii=False),
                        vec.astype("float32").tolist(),
                        None,
                    )
                )

            execute_values(
                cur,
                """
                INSERT INTO benchmark.rag_embeddings
                    (index_name, text, metadata, embedding, source_trace_id)
                VALUES %s
                """,
                records,
                template="(%s, %s, %s::jsonb, %s, %s)",
                page_size=batch_size,
            )
            inserted = len(records)
            count = _existing_count(cur)
        conn.commit()
    return inserted, count


def _dry_run(rows: list[dict[str, str]], csv_path: Path) -> None:
    print(f"[INFO] Read {len(rows)} rows from {csv_path}")
    print("[INFO] Dry run: no embedding and no database call")
    for row in rows[:3]:
        text = row["llm_index_text"].replace("\n", " ")
        print(
            "[SAMPLE] "
            + row["table_name"]
            + " | domain="
            + row.get("business_domain", "")
            + " | text="
            + text[:160]
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-path", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--batch-size", type=int, default=60)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.batch_size < 1:
            raise ValueError("--batch-size must be >= 1")
        rows = _read_rows(args.csv_path)
        if args.dry_run:
            _dry_run(rows, args.csv_path)
            return 0

        print(f"[INFO] Read {len(rows)} rows from {args.csv_path}")
        started = time.perf_counter()
        embeddings = _embed([row["llm_index_text"] for row in rows], batch_size=args.batch_size)
        elapsed = time.perf_counter() - started
        avg_ms = elapsed * 1000 / max(len(rows), 1)
        shape = getattr(embeddings, "shape", None)
        print(f"[INFO] Encoded {len(rows)} texts with multilingual-e5-small (avg latency {avg_ms:.1f}ms per text)")
        print(f"[INFO] Embedding shape: {shape}")

        inserted, count = _insert_rows(rows, embeddings, force=args.force, batch_size=args.batch_size)
        if inserted == EXPECTED_ROWS:
            print(f"[INFO] INSERT {inserted} records (transaction committed)")
        print(f"[INFO] Verification: SELECT COUNT(*) = {count}")
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
