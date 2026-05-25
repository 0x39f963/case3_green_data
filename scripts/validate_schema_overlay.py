"""
Validate schema overlay artifacts before RAG/index integration.

Checks:
- overlay matches v1 or v2 JSON Schema;
- every table from Marina schema exists in overlay and no extra tables exist;
- v2 column sets match Marina schema;
- allowed_ops and denied_ops do not overlap.
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
