from __future__ import annotations

import os
from pathlib import Path


SQL_DIR = Path(__file__).resolve().parent / "sql"


def main() -> int:
    migrate()
    return 0


def migrate() -> list[str]:
    dsn = _dsn()
    psycopg2 = _psycopg2()
    applied: list[str] = []
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            _ensure_table(cur)
            for path in sorted(SQL_DIR.glob("[0-9][0-9][0-9]_*.sql")):
                version = path.stem.split("_", 1)[0]
                cur.execute(
                    "SELECT 1 FROM benchmark.schema_migrations WHERE version = %s",
                    (version,),
                )
                if cur.fetchone():
                    continue
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute(
                    "INSERT INTO benchmark.schema_migrations(version) VALUES (%s)",
                    (version,),
                )
                applied.append(version)
        conn.commit()
    if applied:
        print("applied migrations: " + ",".join(applied))
    else:
        print("migrations already current")
    return applied


def latest_version() -> str | None:
    dsn = _dsn(required=False)
    if not dsn:
        return None
    psycopg2 = _psycopg2()
    with psycopg2.connect(dsn, connect_timeout=3) as conn:
        with conn.cursor() as cur:
            _ensure_table(cur)
            cur.execute("SELECT max(version) FROM benchmark.schema_migrations")
            row = cur.fetchone()
    return row[0] if row and row[0] else None


def _ensure_table(cur: object) -> None:
    cur.execute("CREATE SCHEMA IF NOT EXISTS benchmark")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS benchmark.schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def _dsn(required: bool = True) -> str:
    value = os.environ.get("BENCHMARK_DSN", "").strip()
    if not value and required:
        raise RuntimeError("BENCHMARK_DSN is not configured.")
    return value


def _psycopg2():
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("psycopg2 is required for benchmark migrations.") from exc
    return psycopg2


if __name__ == "__main__":
    raise SystemExit(main())
