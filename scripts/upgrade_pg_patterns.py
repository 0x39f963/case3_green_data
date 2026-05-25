"""
TZ-7 Phase 1.1: обогащение pg_patterns метаданными schema_scope и pii_columns_used.

Скрипт берёт `TASK-3/marina-case3-rag/rag_pipeline/knowledge_base/generation/
pg_patterns.json` и для каждого паттерна:

- парсит `example` SQL через pglast и собирает фактические таблицы и колонки;
- сравнивает с `TASK-3/marina-case3-rag/schema.json` для проверки что таблицы
  существуют в домене;
- сравнивает колонки со списком sensitive_fields из rag_adapter
  (`get_sensitive_fields()`), вычисляет `pii_columns_used`;
- записывает поля `schema_scope`, `pii_columns_used`, `derived_at` и
  `quality_grade` обратно в paths/file.

После update нужно перезапустить `python rag_pipeline/build_indices.py` чтобы
FAISS-индекс перечитал meta.

Эти новые поля используются RAG-фильтром `_mentions_disallowed_column` в
`app/rag_adapter.py` для точного отбрасывания примеров со scope-mismatch.

Usage:
    python scripts/upgrade_pg_patterns.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS_PATH = (
    ROOT
    / "TASK-3"
    / "marina-case3-rag"
    / "rag_pipeline"
    / "knowledge_base"
    / "generation"
    / "pg_patterns.json"
)
SCHEMA_PATH = ROOT / "TASK-3" / "marina-case3-rag" / "schema.json"


def _load_schema() -> dict[str, set[str]]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    tables = schema.get("tables") or {}
    out: dict[str, set[str]] = {}
    for name, meta in tables.items():
        cols = meta.get("columns") or {}
        out[name.lower()] = {str(c).lower() for c in cols}
    return out


def _load_sensitive() -> dict[str, set[str]]:
    sys.path.insert(0, str(ROOT))
    from app import rag_adapter

    sens = rag_adapter.get_sensitive_fields() or {}
    return {t.lower(): {c.lower() for c in cols} for t, cols in sens.items()}


def _parse_identifiers(sql: str) -> tuple[set[str], set[str], set[str]]:
    """Вытащить таблицы, колонки и CTE-имена.

    Возвращает (tables_lower, columns_lower, ctes_lower). CTE-имена
    не должны попасть в hallucinated_tables.
    """
    try:
        from pglast import ast, parse_sql
        from pglast.visitors import Visitor
    except ImportError:
        return set(), set(), set()
    try:
        tree = parse_sql(sql)
    except Exception:
        return set(), set(), set()

    class V(Visitor):
        def __init__(self) -> None:
            self.tables: set[str] = set()
            self.columns: set[str] = set()
            self.ctes: set[str] = set()

        def visit_RangeVar(self, _ancestors, node: ast.RangeVar) -> None:
            if node.relname:
                self.tables.add(node.relname.lower())

        def visit_ColumnRef(self, _ancestors, node: ast.ColumnRef) -> None:
            fields = node.fields or ()
            last = fields[-1] if fields else None
            if last is None:
                return
            sval = getattr(last, "sval", "") or getattr(last, "str", "")
            if sval and sval != "*":
                self.columns.add(str(sval).lower())

        def visit_CommonTableExpr(self, _ancestors, node: ast.CommonTableExpr) -> None:
            if getattr(node, "ctename", None):
                self.ctes.add(node.ctename.lower())

    v = V()
    v(tree)
    # CTE-имена обычно попадают в .tables как RangeVar дальше по AST,
    # их нужно исключить из реальных таблиц.
    return v.tables - v.ctes, v.columns, v.ctes


def _quality_grade(schema_scope: list[str], pii_used: list[str], hallucinated: list[str]) -> str:
    """Эвристика качества паттерна:
    - high — schema_scope не пустой, нет hallucinated, нет PII.
    - medium — есть PII (паттерны про маскирование, отчёты), но scope валиден.
    - low_pii_unfiltered — есть PII И scope_scope пустой (нечем фильтровать).
    - low_hallucinated — упоминается таблица, которой нет в schema.json.
    - generic — общий PG-паттерн без конкретных таблиц.
    """
    if hallucinated:
        return "low_hallucinated"
    if not schema_scope:
        return "generic"
    if pii_used:
        return "medium_pii_in_example"
    return "high"


def upgrade(dry_run: bool = False) -> dict:
    schema = _load_schema()
    sensitive = _load_sensitive()
    patterns = json.loads(PATTERNS_PATH.read_text(encoding="utf-8"))
    derived_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    stats = {
        "total": len(patterns),
        "by_grade": {},
        "with_pii": 0,
        "hallucinated_tables": [],
    }

    for p in patterns:
        sql = str(p.get("example") or "")
        tables, columns, ctes = _parse_identifiers(sql)
        real = [t for t in tables if t in schema]
        hallucinated = sorted(tables - set(real) - ctes)
        # PII колонки = пересечение колонок с sensitive set
        pii_cols: set[str] = set()
        all_pii = set()
        for cols in sensitive.values():
            all_pii |= cols
        for c in columns:
            if c in all_pii:
                pii_cols.add(c)

        schema_scope = sorted(real)
        pii_used = sorted(pii_cols)
        grade = _quality_grade(schema_scope, pii_used, hallucinated)

        p["schema_scope"] = schema_scope
        p["pii_columns_used"] = pii_used
        p["hallucinated_tables"] = hallucinated
        p["quality_grade"] = grade
        p["derived_at"] = derived_at

        if pii_used:
            stats["with_pii"] += 1
        if hallucinated:
            stats["hallucinated_tables"].extend(hallucinated)
        stats["by_grade"][grade] = stats["by_grade"].get(grade, 0) + 1

    stats["hallucinated_tables"] = sorted(set(stats["hallucinated_tables"]))[:20]

    if not dry_run:
        PATTERNS_PATH.write_text(
            json.dumps(patterns, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich pg_patterns with schema_scope + pii_columns_used")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    stats = upgrade(dry_run=args.dry_run)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
