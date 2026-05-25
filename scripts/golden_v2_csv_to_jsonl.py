#!/usr/bin/env python3
"""Convert Golden v2 CSV to line-delimited JSONL."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

CLASS_BY_ID = {
    1: "select_simple",
    2: "select_medium",
    3: "select_complex_joins",
    4: "subqueries",
    5: "update_provocation",
    6: "delete_provocation",
    7: "sql_injection",
    8: "prompt_injection",
    9: "bindings_bypass",
    10: "limit_bypass",
    11: "multi_table_joins_heavy",
    12: "pii_overfetch",
}

REQUIRED = {"id", "class_id", "task", "sql_candidate"}


def convert(csv_path: Path, out_path: Path, strict: bool = False) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "input": 0,
        "output": 0,
        "skipped": [],
        "per_class": {slug: 0 for slug in CLASS_BY_ID.values()},
        "manual_review_true": 0,
        "missing_to_950": 0,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open(encoding="utf-8", newline="") as src, out_path.open("w", encoding="utf-8") as out:
        reader = csv.DictReader(src)
        for row_num, row in enumerate(reader, start=2):
            stats["input"] += 1
            errors = _row_errors(row)
            if errors:
                stats["skipped"].append({"row": row_num, "errors": errors})
                if strict:
                    raise ValueError(f"row {row_num}: {errors}")
                continue
            item = _build_item(row)
            stats["per_class"][item["class"]] += 1
            if item["manual_review"]:
                stats["manual_review_true"] += 1
            stats["output"] += 1
            out.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    stats["missing_to_950"] = max(950 - int(stats["output"]), 0)
    stats["per_class"] = {key: value for key, value in stats["per_class"].items() if value}
    return stats


def _row_errors(row: dict[str, str]) -> list[str]:
    errors = [f"missing {field}" for field in sorted(REQUIRED) if not (row.get(field) or "").strip()]
    class_id = _int(row.get("class_id"))
    if class_id not in CLASS_BY_ID:
        errors.append("unknown class_id " + str(row.get("class_id")))
    if not _json_list(row.get("schema_scope"), default=[]):
        errors.append("schema_scope must be a JSON array with at least one item")
    if _json_list(row.get("risk_labels"), default=None) is None:
        errors.append("risk_labels must be a JSON array")
    return errors


def _build_item(row: dict[str, str]) -> dict[str, Any]:
    class_id = int(row["class_id"])
    klass = CLASS_BY_ID[class_id]
    schema_scope = _json_list(row.get("schema_scope"), default=[])
    risk_labels = _json_list(row.get("risk_labels"), default=[])
    safe_rewrite = _nullable_sql(row.get("safe_rewrite"))
    sql = (row.get("sql_candidate") or "").strip()
    task_family = _task_family(row.get("notes")) or klass
    return {
        "id": (row.get("id") or "").strip(),
        "nl_query": (row.get("task") or "").strip(),
        "task": (row.get("task") or "").strip(),
        "attack_prompt": None,
        "sql": sql,
        "sql_candidate": sql,
        "safe_rewrite": safe_rewrite,
        "dialect": "postgresql",
        "schema_scope": schema_scope,
        "schema_context": _schema_context(schema_scope),
        "risk_labels": risk_labels,
        "severity": _int(row.get("severity"), default=0),
        "evidence_span": [],
        "source": "golden_v2",
        "original_source": (row.get("source") or "").strip(),
        "split": "test",
        "intent_labels": [],
        "sql_labels": risk_labels,
        "baseline_labels": [label for label in risk_labels if label in _baseline_labels()],
        "schema_area": _schema_area(schema_scope),
        "task_family": task_family,
        "eval_suite": "golden_v2_mvp_835",
        "language": (row.get("language") or "ru").strip() or "ru",
        "taxonomy_version": "v2.0",
        "judge_label_version": "v2.0",
        "model_source": "golden_v2_partial",
        "parse_status": "parsed",
        "source_seed_id": "golden_v2:" + (row.get("id") or "").strip(),
        "golden_category_id": class_id,
        "golden_oracle_type": "reference_sql" if safe_rewrite else "refusal_only",
        "class": klass,
        "class_id": class_id,
        "class_name": (row.get("class_name") or "").strip(),
        "manual_review": _bool(row.get("manual_review")),
        "manual_reviewer": (row.get("manual_reviewer") or "").strip() or None,
        "notes": (row.get("notes") or "").strip(),
    }


def _json_list(value: str | None, default: list[Any] | None) -> list[Any] | None:
    if value is None or not value.strip():
        return default
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return default
    return parsed if isinstance(parsed, list) else default


def _nullable_sql(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or text.upper() == "NULL":
        return None
    return text


def _bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y"}


def _int(value: str | None, default: int | None = None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _schema_context(schema_scope: list[Any]) -> str:
    tables = [str(item) for item in schema_scope if str(item).strip()]
    return "allowed tables: " + ", ".join(tables)


def _schema_area(schema_scope: list[Any]) -> str:
    first = str(schema_scope[0]) if schema_scope else "unknown"
    return first.split("_", 1)[0] if "_" in first else first


def _task_family(notes: str | None) -> str:
    text = (notes or "").strip()
    marker = "migrated_from:"
    if marker in text:
        return text.split(marker, 1)[1].strip() or ""
    return ""


def _baseline_labels() -> set[str]:
    return {
        "SQL_INJ_CLASSIC",
        "SQL_INJ_UNION",
        "DML_NO_WHERE",
        "SELECT_STAR",
        "DIRECT_SENSITIVE",
        "NO_PAGINATION",
        "SQL_INJ_TIME",
        "PRIV_ESCALATE",
        "PLPGSQL_UNSAFE",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("data/golden/golden_v2_0.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/eval/golden_v2.jsonl"))
    parser.add_argument("--stats-output", type=Path, default=None)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    if not args.input.exists():
        print("input not found: " + str(args.input))
        return 1
    stats = convert(args.input, args.output, strict=args.strict)
    if args.stats_output:
        args.stats_output.parent.mkdir(parents=True, exist_ok=True)
        args.stats_output.write_text(json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(
        "PASS golden_v2 conversion: input={input}, output={output}, skipped={skipped}, missing_to_950={missing}".format(
            input=stats["input"],
            output=stats["output"],
            skipped=len(stats["skipped"]),
            missing=stats["missing_to_950"],
        )
    )
    print(json.dumps({"per_class": stats["per_class"]}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
