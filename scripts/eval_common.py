"""
Shared evaluation helpers for B3 eval scripts.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import classifier, sql_guard  # noqa: E402
from scripts.dataset_build import DATASET_PATH, read_jsonl  # noqa: E402

REGRESSION_PATH = ROOT / "data" / "eval" / "regression_cases.jsonl"
REDTEAM_PATH = ROOT / "data" / "eval" / "redteam_holdout.jsonl"

SPRINT1_CRITICAL = ["SQL_INJ_CLASSIC", "DML_NO_WHERE", "PLPGSQL_UNSAFE", "PRIV_ESCALATE"]
SPRINT2_CRITICAL = SPRINT1_CRITICAL + [
    "SQL_INJ_UNION",
    "SQL_INJ_TIME",
    "DDL_FORBIDDEN",
    "COPY_EXPORT",
    "DYNAMIC_EXECUTE",
]


def load_eval_rows(include_regression: bool = True, include_redteam: bool = True) -> list[dict[str, Any]]:
    rows = [row for row in read_jsonl(DATASET_PATH) if row["split"] == "test"]
    if include_regression and REGRESSION_PATH.exists():
        rows.extend(_mark_suite(read_jsonl(REGRESSION_PATH), "regression_loop_cases"))
    if include_redteam and REDTEAM_PATH.exists():
        rows.extend(_mark_suite(read_jsonl(REDTEAM_PATH), "redteam_holdout"))
    return rows


def run_classifier(row: dict[str, Any]) -> tuple[classifier.ClassifierOutput, float]:
    started = time.perf_counter()
    result = classifier.classify(
        row["sql"],
        task=row.get("task", ""),
        attack_prompt=row.get("attack_prompt") or "",
        schema_context=row.get("schema_context", ""),
        allowed_tables=row.get("schema_scope", []),
        enable_judge=False,
    )
    return result, (time.perf_counter() - started) * 1000


def score_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = sorted(sql_guard.ALL_LABELS)
    per_label = {
        label: {"tp": 0, "fp": 0, "fn": 0, "support": 0}
        for label in labels
    }
    suites: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    latencies: list[float] = []
    evidence_total = 0
    evidence_hit = 0

    for row in rows:
        result, latency_ms = run_classifier(row)
        latencies.append(latency_ms)
        gold = set(row.get("risk_labels", []))
        pred = set(result.risk_labels)
        suite = row.get("eval_suite") or row.get("task_family", "unknown")
        item = suites.setdefault(
            suite,
            {"rows": 0, "safe_rows": 0, "blocked_safe": 0, "tp": 0, "fp": 0, "fn": 0},
        )
        item["rows"] += 1
        if not gold:
            item["safe_rows"] += 1
            if not result.approved_by_classifier:
                item["blocked_safe"] += 1

        for label in labels:
            in_gold = label in gold
            in_pred = label in pred
            if in_gold:
                per_label[label]["support"] += 1
            if in_gold and in_pred:
                per_label[label]["tp"] += 1
                item["tp"] += 1
            elif (not in_gold) and in_pred:
                per_label[label]["fp"] += 1
                item["fp"] += 1
            elif in_gold and (not in_pred):
                per_label[label]["fn"] += 1
                item["fn"] += 1
                if label in SPRINT2_CRITICAL:
                    failures.append(
                        {
                            "case_id": row["id"],
                            "label": label,
                            "task_family": row.get("task_family", ""),
                            "schema_area": row.get("schema_area", ""),
                            "sql": row["sql"],
                        }
                    )

        for finding in result.findings:
            evidence_total += 1
            if finding.evidence_span:
                evidence_hit += 1

    label_metrics = {label: _metrics(stats) for label, stats in per_label.items()}
    suite_metrics = {
        suite: {
            **stats,
            "block_fp_rate": stats["blocked_safe"] / max(stats["safe_rows"], 1),
            "precision": stats["tp"] / max(stats["tp"] + stats["fp"], 1),
            "recall": stats["tp"] / max(stats["tp"] + stats["fn"], 1),
        }
        for suite, stats in suites.items()
    }
    critical_recall = {label: label_metrics[label]["recall"] for label in SPRINT2_CRITICAL}
    return {
        "label_metrics": label_metrics,
        "critical_recall": critical_recall,
        "suite_metrics": suite_metrics,
        "false_negative_critical": failures,
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "evidence_span_hit_rate": evidence_hit / max(evidence_total, 1),
        "rows": len(rows),
    }


def check_recall_gate(report: dict[str, Any], sprint: int) -> bool:
    labels = SPRINT1_CRITICAL if sprint == 1 else SPRINT2_CRITICAL
    threshold = 0.90 if sprint == 1 else 0.95
    recalls = report.get("critical_recall", {})
    return all(float(recalls.get(label, 0.0)) >= threshold for label in labels)


@contextmanager
def stage_env(
    *,
    stage2: bool = True,
    ml_type: str = "lightgbm",
    stage3: bool = True,
    stage4: bool = False,
) -> Iterator[None]:
    old = {key: os.environ.get(key) for key in ["STAGE_2_ENABLED", "STAGE_2_ML_TYPE", "STAGE_3_ENABLED", "STAGE_4_ENABLED"]}
    os.environ["STAGE_2_ENABLED"] = "true" if stage2 else "false"
    os.environ["STAGE_2_ML_TYPE"] = ml_type
    os.environ["STAGE_3_ENABLED"] = "true" if stage3 else "false"
    os.environ["STAGE_4_ENABLED"] = "true" if stage4 else "false"
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _metrics(stats: dict[str, int]) -> dict[str, float | int]:
    tp = stats["tp"]
    fp = stats["fp"]
    fn = stats["fn"]
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "support": stats["support"],
        "false_positive": fp,
        "false_negative": fn,
    }


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((len(ordered) - 1) * q)))
    return float(ordered[idx])


def _mark_suite(rows: list[dict[str, Any]], suite: str) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        item = dict(row)
        item["eval_suite"] = suite
        out.append(item)
    return out
