#!/usr/bin/env python3
"""Append synthetic rows into the Case 3 GreenData PostgreSQL schema."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._synthetic import __version__  # noqa: E402
from scripts._synthetic.introspect import inspect_schema  # noqa: E402
from scripts._synthetic.planner import build_budget, insertion_order  # noqa: E402
from scripts._synthetic.writer import SyntheticWriter  # noqa: E402


MSK = timezone(timedelta(hours=3))
DEFAULT_OVERLAY = ROOT / "deploy" / "schema_overlay.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--target-rows", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--schema", default="public")
    parser.add_argument("--target-mode", choices=["total", "per_table_avg"], default="total")
    parser.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--table-limit", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--no-analyze", action="store_true")
    parser.add_argument("--insert-method", choices=["execute_values", "copy"], default="execute_values")
    parser.add_argument("--locale", choices=["ru_RU", "en_US"], default="ru_RU")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "data" / "synthetic" / "reports")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "data" / "synthetic" / "logs")
    args = parser.parse_args()

    try:
        return run(args)
    except ValueError as exc:
        print(json.dumps({"status": "CONFIG_ERROR", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    except psycopg2.Error as exc:
        print(json.dumps({"status": "DB_ERROR", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 3


def run(args: argparse.Namespace) -> int:
    if args.insert_method == "copy":
        raise ValueError("--insert-method copy is P1 and is not implemented in this P0 loader")
    if args.batch_size < 100 or args.batch_size > 50_000:
        raise ValueError("--batch-size must be in 100..50000")
    if not args.validate_only and args.target_rows <= 0:
        raise ValueError("--target-rows is required unless --validate-only is used")
    if args.profile:
        raise ValueError("--profile is P1 and is not implemented in this P0 loader")

    seed = args.seed or int(time.time())
    started = datetime.now(MSK)
    run_id = "synth_" + started.strftime("%Y%m%d_%H%M%S")
    args.report_dir.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.log_dir / (run_id + ".log")
    logger = _logger(log_path)
    logger.info("run_id=%s script_version=%s dsn=%s", run_id, __version__, mask_dsn(args.dsn))

    with psycopg2.connect(args.dsn) as conn:
        conn.autocommit = False
        schema = inspect_schema(conn, args.schema, args.overlay)
        order = insertion_order(schema)
        table_limits = _table_limits(args.table_limit)
        budget_plan = build_budget(schema, args.target_rows or len(schema.tables) * 20, args.target_mode, table_limits)

        if args.dry_run:
            payload = {
                "status": "DRY_RUN",
                "script_version": __version__,
                "schema": args.schema,
                "target_rows": budget_plan.target_rows,
                "budget_mode": budget_plan.mode,
                "min_sum_before_scaling": budget_plan.min_sum,
                "table_count": len(schema.tables),
                "active_pk_count": schema.active_pk_count,
                "active_fk_count": schema.active_fk_count,
                "budget": budget_plan.rows,
                "budget_sum": sum(budget_plan.rows.values()),
                "insert_order": order,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0

        writer = SyntheticWriter(
            conn=conn,
            schema=schema,
            budget=budget_plan.rows,
            order=order,
            seed=seed,
            batch_size=args.batch_size,
            locale=args.locale,
            logger=logger,
        )
        if args.validate_only:
            writer.load_state()
            fk_check = writer.validate_fk()
            pii_check = writer.validate_pii()
            payload = {"status": "PASS" if fk_check["violations"] == 0 else "FAIL", "fk_orphan_check": fk_check, "pii_check": pii_check}
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if fk_check["violations"] == 0 else 4

        result = writer.write(analyze=not args.no_analyze)

    finished = datetime.now(MSK)
    report = {
        "run_id": run_id,
        "script_version": __version__,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "elapsed_sec": round((finished - started).total_seconds(), 3),
        "dsn": dsn_public(args.dsn),
        "seed": seed,
        "schema": args.schema,
        "target_rows": budget_plan.target_rows,
        "inserted_rows": sum(result.inserted.values()),
        "budget_mode": budget_plan.mode,
        "min_sum_before_scaling": budget_plan.min_sum,
        "budget": budget_plan.rows,
        "inserted_by_table": result.inserted,
        "tables_with_errors": sorted(result.errors),
        "errors": result.errors,
        "fk_orphan_check": result.fk_orphan_check,
        "pii_check": result.pii_check,
        "sequences_updated": result.sequences_updated,
        "sequences_skipped": result.sequences_skipped,
        "sequences_skipped_reason": result.sequences_skipped_reason,
        "analyze_executed": result.analyzed,
        "sample_uniqueness": result.sample_uniqueness,
        "fragment_sha256": result.fragment_sha256,
        "warnings": _warnings(schema, budget_plan.mode),
        "log_path": str(log_path),
    }
    report_path = args.report_dir / (run_id + ".json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": "PASS" if not result.errors else "FAIL", "run_id": run_id, "report": str(report_path), "inserted_rows": report["inserted_rows"]}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not result.errors else 3


def _table_limits(items: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        if "=" not in item:
            raise ValueError("--table-limit must have T=N format")
        name, raw = item.split("=", 1)
        out[name] = int(raw)
    return out


def mask_dsn(dsn: str) -> str:
    parts = urlsplit(dsn)
    netloc = parts.netloc
    if "@" in netloc:
        creds, host = netloc.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        netloc = user + ":***@" + host
    query = urlencode([(k, "***" if "password" in k.lower() else v) for k, v in parse_qsl(parts.query)])
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def dsn_public(dsn: str) -> dict[str, str | int]:
    parts = urlsplit(dsn)
    user = ""
    host = parts.hostname or ""
    port = parts.port or 5432
    if parts.username:
        user = parts.username
    return {"host": host, "port": port, "db": parts.path.lstrip("/"), "user": user}


def _logger(path: Path) -> logging.Logger:
    logger = logging.getLogger("synthetic_loader")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def _warnings(schema: Any, mode: str) -> list[str]:
    items = [
        "synthetic data is benchmark-only and not production-like",
        "only overlay tables are written; audit_runs/audit_iterations stay untouched",
    ]
    if schema.active_fk_count:
        items.append("FK source is pg_constraint only; commented DDL FK lines are ignored")
    return items


if __name__ == "__main__":
    raise SystemExit(main())
