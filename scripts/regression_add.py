"""
Add permanent failed cases to data/eval/regression_cases.jsonl.

Critical false negatives from eval can be copied by case_id from the
main dataset or appended from a report row JSON file.
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

from scripts.dataset_build import DATASET_PATH, read_jsonl, write_jsonl  # noqa: E402

REGRESSION_PATH = ROOT / "data" / "eval" / "regression_cases.jsonl"
SEED_IDS = ["case3_sqlsec_000906", "case3_sqlsec_001542"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", default="")
    parser.add_argument("--case-json", default="")
    parser.add_argument("--reason", default="manual_regression_seed")
    parser.add_argument("--init", action="store_true")
    args = parser.parse_args()

    REGRESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    if args.init:
        for case_id in SEED_IDS:
            add_case(case_id=case_id, reason="seed_regression_case")
        print("initialized", REGRESSION_PATH)
        return 0

    if args.case_json:
        row = json.loads(Path(args.case_json).read_text(encoding="utf-8"))
        add_row(row, args.reason)
        print("added", row.get("id"), REGRESSION_PATH)
        return 0

    if not args.case_id:
        parser.error("--case-id, --case-json or --init is required")
    add_case(case_id=args.case_id, reason=args.reason)
    print("added", args.case_id, REGRESSION_PATH)
    return 0


def add_case(case_id: str, reason: str = "critical_false_negative") -> None:
    rows = read_jsonl(DATASET_PATH)
    by_id = {row["id"]: row for row in rows}
    if case_id not in by_id:
        raise SystemExit("case_id not found in dataset: " + case_id)
    add_row(by_id[case_id], reason)


def add_row(row: dict[str, Any], reason: str) -> None:
    current = read_jsonl(REGRESSION_PATH) if REGRESSION_PATH.exists() else []
    if any(item.get("id") == row.get("id") for item in current):
        return
    item = dict(row)
    item["split"] = "test"
    item["regression_reason"] = reason
    item["regression_source"] = "eval_false_negative"
    current.append(item)
    write_jsonl(REGRESSION_PATH, current)


if __name__ == "__main__":
    raise SystemExit(main())
