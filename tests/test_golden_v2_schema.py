from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "eval" / "golden_v2.jsonl"

KNOWN_CLASSES = {
    "select_simple",
    "select_medium",
    "select_complex_joins",
    "subqueries",
    "update_provocation",
    "delete_provocation",
    "sql_injection",
    "prompt_injection",
    "bindings_bypass",
    "limit_bypass",
    "multi_table_joins_heavy",
    "pii_overfetch",
}

REQUIRED = {
    "id",
    "nl_query",
    "task",
    "sql",
    "safe_rewrite",
    "class",
    "class_id",
    "source",
    "schema_scope",
    "risk_labels",
    "manual_review",
}


def _rows() -> list[dict]:
    assert GOLDEN.exists(), "run scripts/golden_v2_csv_to_jsonl.py first"
    return [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_jsonl_is_line_delimited() -> None:
    lines = [line for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 835
    assert all(line.startswith("{") and line.endswith("}") for line in lines[:100])


def test_first_100_rows_have_required_fields() -> None:
    for idx, row in enumerate(_rows()[:100], start=1):
        missing = REQUIRED - set(row)
        assert not missing, f"row {idx} missing fields: {missing}"


def test_classes_are_known_and_cover_all_12() -> None:
    classes = {row["class"] for row in _rows()}
    assert classes == KNOWN_CLASSES


def test_ids_are_unique() -> None:
    ids = [row["id"] for row in _rows()]
    assert len(ids) == len(set(ids))
