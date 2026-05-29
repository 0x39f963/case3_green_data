"""Load Oracle datasets."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import cast

from .types import OracleCase, OracleType


def parse_assertions(value: str | None) -> list[str]:
    """Parse pipe-separated semantic_assertions."""
    raw = (value or "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split("|") if item.strip()]


def parse_reference_params(value: str | None) -> dict[str, object]:
    """Parse JSON reference params. Bad or empty values become an empty dict."""
    raw = (value or "").strip()
    if not raw or raw == "{}":
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def load_golden_v1_1(path: Path | str) -> list[OracleCase]:
    """Read golden_dataset_v1_1.csv."""
    file_path = Path(path)
    cases: list[OracleCase] = []
    with file_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";", quotechar='"')
        for row in reader:
            cases.append(
                OracleCase(
                    test_id=row["test_id"],
                    category_id=row["category_id"],
                    category_name=row["category_name"],
                    nl_prompt=row["nl_prompt"],
                    attack_class=row.get("attack_class", ""),
                    expected_behavior=row.get("expected_behavior", ""),
                    oracle_type=cast(OracleType, row["oracle_type"]),
                    reference_sql=row.get("reference_sql", ""),
                    reference_params=parse_reference_params(row.get("reference_params")),
                    semantic_assertions=parse_assertions(row.get("semantic_assertions")),
                    comparison_method=row.get("comparison_method", "ast_semantic+pattern_assertions"),
                    oracle_notes=row.get("oracle_notes", ""),
                    severity_if_failed=row.get("severity_if_failed", "P2"),
                    raw_row=dict(row),
                )
            )
    return cases


def load_golden_v2(path: Path | str) -> list[OracleCase]:
    """Read golden_v2 JSONL and expose it through the OracleCase contract."""
    file_path = Path(path)
    cases: list[OracleCase] = []
    with file_path.open(encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            row = json.loads(raw)
            case_id = str(row.get("id") or "")
            oracle_type = str(row.get("golden_oracle_type") or "reference_sql")
            reference_sql = str(row.get("safe_rewrite") or row.get("sql") or "")
            if oracle_type in {"refusal_only", "clarification_only"}:
                reference_sql = "NO_SQL_EXPECTED"
            cases.append(
                OracleCase(
                    test_id=normalize_golden_v2_test_id(case_id),
                    category_id=str(row.get("class_id") or row.get("golden_category_id") or ""),
                    category_name=str(row.get("class_name") or row.get("class") or ""),
                    nl_prompt=str(row.get("task") or row.get("nl_query") or ""),
                    attack_class=",".join(str(item) for item in row.get("risk_labels") or []),
                    expected_behavior=_expected_behavior(row),
                    oracle_type=cast(OracleType, oracle_type),
                    reference_sql=reference_sql,
                    reference_params={},
                    semantic_assertions=[],
                    comparison_method="golden_v2_contract",
                    oracle_notes=str(row.get("notes") or ""),
                    severity_if_failed=str(row.get("severity") or ""),
                    raw_row=dict(row),
                )
            )
    return cases


def load_oracle_cases(path: Path | str) -> list[OracleCase]:
    file_path = Path(path)
    if file_path.suffix == ".jsonl":
        return load_golden_v2(file_path)
    return load_golden_v1_1(file_path)


def normalize_golden_v2_test_id(case_id: str) -> str:
    raw = str(case_id or "").rsplit("-", 1)[-1]
    return "TC-" + raw.zfill(max(4, len(raw)))


def _expected_behavior(row: dict[str, object]) -> str:
    oracle_type = str(row.get("golden_oracle_type") or "")
    if oracle_type == "refusal_only":
        return "refusal"
    if oracle_type == "clarification_only":
        return "clarification"
    return "safe_select"
