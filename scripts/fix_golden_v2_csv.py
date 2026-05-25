#!/usr/bin/env python3
"""One-off CSV repair for data/golden/golden_v2_0.csv.

Applies:
- tc-0099: typo VIMIT -> LIMIT in sql_candidate.
- schema_scope rebuild via pglast RangeVar walk for all parseable rows.
- tc-0036: manual_review=true, manual_reviewer="Ekaterina" (explicit case
  from Ekaterina's validation_report.md).

Idempotent: re-running on already-fixed file is safe; counts in stdout
show how many rows actually changed.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from pglast.parser import parse_sql, ParseError
from pglast import ast

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "data" / "golden" / "golden_v2_0.csv"


def _walk(node, out_tables: set, cte_names: set):
    if isinstance(node, ast.CommonTableExpr) and node.ctename:
        cte_names.add(node.ctename.lower())
    if isinstance(node, ast.RangeVar) and node.relname:
        out_tables.add(node.relname.lower())
    if isinstance(node, ast.RangeSubselect):
        if node.alias and node.alias.aliasname:
            cte_names.add(node.alias.aliasname.lower())
    for attr in getattr(node, "__slots__", ()) if not isinstance(node, tuple) else ():
        try:
            value = getattr(node, attr)
        except AttributeError:
            continue
        _walk_value(value, out_tables, cte_names)


def _walk_value(value, out_tables: set, cte_names: set):
    if isinstance(value, ast.Node):
        _walk(value, out_tables, cte_names)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_value(item, out_tables, cte_names)


def derive_schema_scope(sql: str) -> tuple[list[str], str]:
    """Returns (sorted_table_names, parse_status) where parse_status is
    'parsed' on success, 'unparseable' on ParseError."""
    if not sql or not sql.strip():
        return ([], "empty")
    try:
        tree = parse_sql(sql)
    except ParseError:
        return ([], "unparseable")
    tables: set[str] = set()
    cte: set[str] = set()
    for stmt in tree:
        _walk_value(stmt, tables, cte)
    # Strip CTE names (they look like tables in RangeVar after CTE expansion)
    tables -= cte
    return (sorted(tables), "parsed")


def normalize_schema_scope_value(value: str) -> list[str]:
    if not value or not value.strip():
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except json.JSONDecodeError:
        pass
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.path.exists():
        print(f"file not found: {args.path}")
        return 1

    rows = list(csv.DictReader(args.path.open(encoding="utf-8")))
    if not rows:
        print("empty CSV")
        return 1

    fieldnames = list(rows[0].keys())
    counters = Counter()

    for row in rows:
        rid = row.get("id", "")

        # F1: tc-0099 VIMIT -> LIMIT
        if rid == "golden_v2_0_tc-0099":
            sql = row["sql_candidate"]
            new_sql = sql.replace("VIMIT", "LIMIT")
            if new_sql != sql:
                row["sql_candidate"] = new_sql
                counters["tc_0099_typo_fixed"] += 1

        # F5: tc-0036 explicit manual_review
        if rid == "golden_v2_0_tc-0036":
            if row.get("manual_review", "").strip().lower() != "true":
                row["manual_review"] = "true"
                counters["tc_0036_manual_review_set"] += 1
            if not row.get("manual_reviewer", "").strip():
                row["manual_reviewer"] = "Ekaterina"
                counters["tc_0036_reviewer_set"] += 1

        # F2: schema_scope rebuild
        old_scope = normalize_schema_scope_value(row.get("schema_scope", ""))
        new_scope, status = derive_schema_scope(row.get("sql_candidate", ""))
        if status == "parsed":
            # Only replace if differs
            if sorted(s.lower() for s in old_scope) != new_scope:
                row["schema_scope"] = json.dumps(new_scope, ensure_ascii=False)
                counters["schema_scope_updated"] += 1
        elif status == "unparseable":
            # Keep existing scope, but log
            counters["schema_scope_unparseable_kept"] += 1
        elif status == "empty":
            counters["schema_scope_sql_empty"] += 1

    if args.dry_run:
        print("DRY RUN — no file modified")
    else:
        with args.path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    print(f"PASS fix_golden_v2_csv on {args.path}")
    print(f"  total rows: {len(rows)}")
    for k, v in sorted(counters.items()):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
