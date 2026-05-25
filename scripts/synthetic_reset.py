#!/usr/bin/env python3
"""Guarded reset for local synthetic rows in overlay tables."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg2
from psycopg2 import sql

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._synthetic.introspect import load_overlay  # noqa: E402
from scripts.fill_synthetic_db import dsn_public, mask_dsn  # noqa: E402


DEFAULT_OVERLAY = ROOT / "deploy" / "schema_overlay.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--schema", default="public")
    parser.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY)
    parser.add_argument("--i-know-what-i-do", action="store_true")
    args = parser.parse_args()

    tables = sorted((load_overlay(args.overlay).get("tables") or {}).keys())
    if not tables:
        print(json.dumps({"status": "CONFIG_ERROR", "error": "overlay has no tables"}, ensure_ascii=False, indent=2))
        return 2

    with psycopg2.connect(args.dsn) as conn:
        before = _counts(conn, args.schema, tables)
        payload = {
            "dsn": dsn_public(args.dsn),
            "dsn_masked": mask_dsn(args.dsn),
            "schema": args.schema,
            "table_count": len(tables),
            "rows_before": sum(before.values()),
            "tables": tables,
        }
        if not args.i_know_what_i_do:
            payload["status"] = "DRY_RUN"
            payload["message"] = "No changes. Re-run with --i-know-what-i-do to TRUNCATE overlay tables."
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 2

        _truncate(conn, args.schema, tables)
        after = _counts(conn, args.schema, tables)
        payload["status"] = "PASS"
        payload["rows_after"] = sum(after.values())
        payload["rows_removed"] = sum(before.values()) - sum(after.values())
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0


def _counts(conn, schema: str, tables: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in tables:
            cur.execute(
                sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                    sql.Identifier(schema),
                    sql.Identifier(table),
                )
            )
            out[table] = int(cur.fetchone()[0] or 0)
    return out


def _truncate(conn, schema: str, tables: list[str]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("TRUNCATE {}").format(
                sql.SQL(", ").join(
                    sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))
                    for table in tables
                )
            )
        )
    conn.commit()


if __name__ == "__main__":
    raise SystemExit(main())
