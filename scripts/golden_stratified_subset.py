"""
H9: построить stratified subset golden v1.0 для rerun под новой v5-таксономией.

Стратификация:
- по `task_family` (baseline_safe_generation, authz_bypass_*, pii_overfetch_*,
  identifier_injection, classic_sql_injection_filter_values, limit_bypass,
  filter_bypass_tautology),
- внутри каждой семьи: по `golden_oracle_type`
  (reference_sql, safe_rewrite, refusal_only, policy_plus_sql).

Из каждой страты берётся `--per-stratum` кейсов (default 4). При нехватке —
берутся все доступные. Результат сохраняется в `data/eval/<output>` и пригоден
для `scripts/bench_run_dataset.py --dataset <output>`.

Usage:
  python scripts/golden_stratified_subset.py \
      --input data/eval/golden_v1_0.jsonl \
      --output data/eval/golden_v1_0_stratified_v5.jsonl \
      --per-stratum 4 \
      --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def _load(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _stratify(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        family = str(row.get("task_family") or "unknown")
        oracle = str(row.get("golden_oracle_type") or "unknown")
        buckets[(family, oracle)].append(row)
    return buckets


def _sample(buckets: dict[tuple[str, str], list[dict[str, Any]]], per_stratum: int, rng: random.Random) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for key, items in sorted(buckets.items()):
        if not items:
            continue
        take = min(per_stratum, len(items))
        picks = rng.sample(items, take)
        for item in picks:
            item = dict(item)
            item["_stratum"] = "::".join(key)
            selected.append(item)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Build stratified golden subset")
    parser.add_argument("--input", type=Path, default=Path("data/eval/golden_v1_0.jsonl"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/eval/golden_v1_0_stratified_v5.jsonl"),
    )
    parser.add_argument("--per-stratum", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-total", type=int, default=80)
    args = parser.parse_args()

    if not args.input.exists():
        print("input not found: " + str(args.input), file=sys.stderr)
        return 2

    rows = _load(args.input)
    buckets = _stratify(rows)
    rng = random.Random(args.seed)
    selected = _sample(buckets, args.per_stratum, rng)
    if args.max_total and len(selected) > args.max_total:
        selected = rng.sample(selected, args.max_total)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for item in selected:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    summary: dict[str, int] = defaultdict(int)
    for item in selected:
        summary[item.get("_stratum", "unknown")] += 1

    report = {
        "input": str(args.input),
        "output": str(args.output),
        "input_rows": len(rows),
        "selected_rows": len(selected),
        "seed": args.seed,
        "per_stratum": args.per_stratum,
        "max_total": args.max_total,
        "buckets": dict(sorted(summary.items())),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
