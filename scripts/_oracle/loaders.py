"""Load golden v1.1 CSV with semicolon separator."""

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
