"""
Validate schema overlay artifacts before RAG/index integration.

Checks:
- overlay matches v1 or v2 JSON Schema;
- every table from Marina schema exists in overlay and no extra tables exist;
- v2 column sets match Marina schema;
- allowed_ops and denied_ops do not overlap;
- (v2 only) column.category == "pii" must appear as a key in table.pii_tags;
- (v2 only) no column description shorter than 16 characters;
- (v2 only) no column description equal to a single generic word from the watchlist.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "TASK-3" / "marina-case3-rag" / "schema.json"
OVERLAY_PATH = ROOT / "deploy" / "schema_overlay.json"
OVERLAY_SCHEMA_PATH = ROOT / "deploy" / "schema_overlay.schema.json"
OVERLAY_SCHEMA_V2_PATH = ROOT / "deploy" / "schema_overlay.schema.v2.json"

GENERIC_DESCRIPTION_WATCHLIST = {
    "Сумма", "Валюта", "Период", "Описание", "Тип", "Статус",
    "Идентификатор", "Маржа", "Дата", "Владелец",
}
MIN_DESCRIPTION_LENGTH = 16


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate schema overlay JSON")
    parser.add_argument("--path", type=Path, default=OVERLAY_PATH, help="Overlay JSON path")
    parser.add_argument("--schema", type=Path, default=None, help="JSON Schema path")
    args = parser.parse_args()

    failures: list[str] = []
    marina = _load_json(SCHEMA_PATH)
    overlay_path = _resolve_path(args.path)
    overlay = _load_json(overlay_path)
    overlay_schema_path = _resolve_path(args.schema) if args.schema else _default_schema_path(overlay_path, overlay)
    overlay_schema = _load_json(overlay_schema_path)

    validator = Draft202012Validator(overlay_schema)
    for err in sorted(validator.iter_errors(overlay), key=lambda item: list(item.path)):
        path = ".".join(str(part) for part in err.path) or "<root>"
        failures.append("schema " + path + ": " + err.message)

    marina_tables = set((marina.get("tables") or {}).keys())
    overlay_tables = set((overlay.get("tables") or {}).keys())
    missing = sorted(marina_tables - overlay_tables)
    extra = sorted(overlay_tables - marina_tables)
    if missing:
        failures.append("missing tables: " + ", ".join(missing))
    if extra:
        failures.append("extra tables: " + ", ".join(extra))

    is_v2 = overlay_schema_path.name.endswith(".v2.json")
    for name, item in (overlay.get("tables") or {}).items():
        allowed = {str(op).upper() for op in item.get("allowed_ops", [])}
        denied = {str(op).upper() for op in item.get("denied_ops", [])}
        conflict = sorted(allowed & denied)
        if conflict:
            failures.append(name + " has overlapping allowed_ops/denied_ops: " + ", ".join(conflict))
        columns = item.get("columns")
        if isinstance(columns, dict):
            marina_cols = set(((marina.get("tables") or {}).get(name) or {}).get("columns") or {})
            overlay_cols = set(columns)
            missing_cols = sorted(marina_cols - overlay_cols)
            extra_cols = sorted(overlay_cols - marina_cols)
            if missing_cols:
                failures.append(name + " missing columns: " + ", ".join(missing_cols[:20]))
            if extra_cols:
                failures.append(name + " extra columns: " + ", ".join(extra_cols[:20]))
        if is_v2:
            failures.extend(_check_v2_table_quality(name, item))

    if failures:
        print("schema overlay validation: FAIL")
        for item in failures:
            print("- " + item)
        return 1

    print("schema overlay validation: PASS")
    print("overlay: " + str(overlay_path.relative_to(ROOT) if overlay_path.is_relative_to(ROOT) else overlay_path))
    print("schema: " + str(overlay_schema_path.relative_to(ROOT) if overlay_schema_path.is_relative_to(ROOT) else overlay_schema_path))
    print("tables: " + str(len(overlay_tables)))
    return 0


def _resolve_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return ROOT / path


def _check_v2_table_quality(name: str, item: dict) -> list[str]:
    failures: list[str] = []
    pii_tags = item.get("pii_tags") or {}
    pii_keys = set(pii_tags.keys()) if isinstance(pii_tags, dict) else set()
    for col_name in pii_keys:
        cols = item.get("columns") or {}
        if col_name not in cols:
            failures.append(
                name + ": pii_tags references non-existent column " + repr(col_name)
            )
    columns = item.get("columns")
    if not isinstance(columns, dict):
        return failures
    for cname, cinfo in columns.items():
        if not isinstance(cinfo, dict):
            continue
        category = cinfo.get("category")
        if category == "pii" and cname not in pii_keys:
            failures.append(
                name + "." + cname + ': category="pii" but column is not listed in table.pii_tags'
            )
        description = (cinfo.get("description") or "").strip()
        if not description:
            failures.append(name + "." + cname + ": empty description")
            continue
        if len(description) < MIN_DESCRIPTION_LENGTH:
            failures.append(
                name + "." + cname + ": description shorter than " + str(MIN_DESCRIPTION_LENGTH)
                + " chars: " + repr(description)
            )
        if description in GENERIC_DESCRIPTION_WATCHLIST:
            failures.append(
                name + "." + cname + ": description is a single generic word from watchlist: "
                + repr(description)
            )
        if description.lower().strip(" .:_-") == cname.lower():
            failures.append(
                name + "." + cname + ": description is just the column name verbatim: "
                + repr(description)
            )
    return failures


def _default_schema_path(overlay_path: Path, overlay: dict) -> Path:
    tables = overlay.get("tables") or {}
    has_columns = any(isinstance(item, dict) and "columns" in item for item in tables.values())
    if has_columns or overlay_path.name.endswith("_v2.json"):
        return OVERLAY_SCHEMA_V2_PATH
    return OVERLAY_SCHEMA_PATH


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("file not found: " + str(path))
        raise SystemExit(1)
    except json.JSONDecodeError as exc:
        print("bad json " + str(path) + ": " + str(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
