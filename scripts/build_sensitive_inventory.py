"""
Phase 4.3 — Аудит и сборка sensitive inventory.

Сравнивает три источника разметки чувствительных полей:
1. Авто-детект regex по именам колонок (app.sensitive_detector).
2. Marina rag_tools.get_sensitive_fields() — schema.json подсказки.
3. deploy/schema_overlay.json.tables[*].pii_tags — ручная бизнес-разметка.

Печатает diff между авто и overlay (что overlay не закрыл — это
индикатор того, что нужно добавить либо в overlay, либо подтвердить
безопасность). Опционально пишет машинно-читаемый inventory в
data/sensitive_auto.json.

Запуск:
    python scripts/build_sensitive_inventory.py
    python scripts/build_sensitive_inventory.py --json-out data/sensitive_auto.json
    python scripts/build_sensitive_inventory.py --fail-on-missing 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import rag_adapter, sensitive_detector  # noqa: E402


def _print_human(diff: dict[str, Any]) -> None:
    auto_only = diff.get("auto_only") or {}
    overlay_only = diff.get("overlay_only") or {}
    intersect = diff.get("intersect") or {}
    missing = diff.get("missing_from_overlay_count") or 0

    print("=" * 60)
    print("Sensitive inventory diff")
    print("=" * 60)
    print("auto_only (overlay не покрывает, найдено regex):     "
          + str(sum(len(v) for v in auto_only.values()))
          + " колонок в " + str(len(auto_only)) + " таблицах")
    print("overlay_only (бизнес-разметка, regex не ловит):       "
          + str(sum(len(v) for v in overlay_only.values()))
          + " колонок в " + str(len(overlay_only)) + " таблицах")
    print("intersect (совпадают auto + overlay):                 "
          + str(sum(len(v) for v in intersect.values()))
          + " колонок в " + str(len(intersect)) + " таблицах")
    print("")
    if auto_only:
        print("# auto_only (рекомендация: добавить в overlay или подтвердить безопасность)")
        for table, cols in sorted(auto_only.items()):
            print("  " + table + ": " + ", ".join(cols))
        print("")
    if overlay_only:
        print("# overlay_only (бизнес-разметка вне regex — нормально)")
        for table, cols in sorted(overlay_only.items())[:20]:
            print("  " + table + ": " + ", ".join(cols))
        if len(overlay_only) > 20:
            print("  ... (+" + str(len(overlay_only) - 20) + " таблиц)")
        print("")


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 4.3 sensitive inventory audit")
    parser.add_argument("--json-out", type=Path, default=None,
                        help="Записать машинно-читаемый inventory в JSON")
    parser.add_argument("--fail-on-missing", type=int, default=None,
                        help="Exit code 1 если auto_only >= N колонок (CI gate)")
    args = parser.parse_args()

    schema = rag_adapter._load_schema()
    overlay = rag_adapter._load_overlay()
    schema_tables = (schema or {}).get("tables") or {}
    overlay_tables = (overlay or {}).get("tables") or {}

    auto = sensitive_detector.detect_from_schema(schema_tables)
    diff = sensitive_detector.diff_overlay_vs_auto(auto, overlay_tables)
    merged = sensitive_detector.merge_with_overlay(auto, overlay_tables, overlay_wins=True)

    _print_human(diff)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "auto_detected": auto,
            "overlay": {
                table: sorted((item.get("pii_tags") or {}).keys())
                for table, item in overlay_tables.items()
                if isinstance(item, dict) and item.get("pii_tags")
            },
            "merged_runtime": merged,
            "diff": diff,
            "summary": {
                "auto_tables_count": len(auto),
                "overlay_tables_with_pii": sum(
                    1 for _, item in overlay_tables.items()
                    if isinstance(item, dict) and item.get("pii_tags")
                ),
                "missing_from_overlay_count": diff.get("missing_from_overlay_count") or 0,
            },
        }
        args.json_out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("inventory saved → " + str(args.json_out))

    missing = int(diff.get("missing_from_overlay_count") or 0)
    if args.fail_on_missing is not None and missing >= args.fail_on_missing:
        print(
            "FAIL: auto_only has " + str(missing) + " columns (threshold " + str(args.fail_on_missing) + ")",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
