"""
Compare 3 generator model profiles on the gold test subset.

Default mode is offline and deterministic: each model profile receives a
stable candidate strategy so the benchmark works without provider keys.
Live provider calls can be added later without changing report format.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import classifier  # noqa: E402
from scripts.dataset_build import DATASET_PATH, read_jsonl  # noqa: E402
from scripts.eval_common import stage_env  # noqa: E402

REPORT_DIR = ROOT / "data" / "eval" / "reports"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="qwen-coder-7b,arctic-text2sql-7b,qwen3-8b")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    models = [item.strip() for item in args.models.split(",") if item.strip()]
    rows = [row for row in read_jsonl(DATASET_PATH) if row["split"] == "test"][: args.limit]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    report = {
        "run_id": "case3_model_compare_" + datetime.now().strftime("%Y%m%d_%H%M%S"),
        "rows": len(rows),
        "models": {},
    }
    with stage_env(stage2=True, ml_type="lightgbm", stage3=True, stage4=False):
        for model in models:
            report["models"][model] = _score_model(model, rows)

    date = datetime.now().strftime("%Y-%m-%d")
    json_path = REPORT_DIR / ("model_compare_" + date + ".json")
    html_path = REPORT_DIR / ("model_compare_" + date + ".html")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    html_path.write_text(_html(report), encoding="utf-8")
    print(json.dumps({"report_json": str(json_path), "report_html": str(html_path), **report}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _score_model(model: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    approved = 0
    latencies: list[float] = []
    risks: list[float] = []
    exact = 0
    hallucination = 0
    sensitive = 0
    iterations: list[int] = []

    for idx, row in enumerate(rows):
        sql = _candidate(model, row, idx)
        started = time.perf_counter()
        result = classifier.classify(
            sql,
            task=row.get("task", ""),
            attack_prompt=row.get("attack_prompt") or "",
            schema_context=row.get("schema_context", ""),
            allowed_tables=row.get("schema_scope", []),
            enable_judge=False,
        )
        elapsed = (time.perf_counter() - started) * 1000
        latencies.append(elapsed)
        risks.append(result.max_severity)
        approved += int(result.approved_by_classifier)
        exact += int(bool(row.get("safe_rewrite")) and sql.strip() == str(row["safe_rewrite"]).strip())
        labels = set(result.risk_labels)
        hallucination += int(bool(labels & {"HALLUCINATED_TABLE", "HALLUCINATED_COLUMN"}))
        sensitive += int("DIRECT_SENSITIVE" in labels)
        iterations.append(1 if result.approved_by_classifier else 2)

    return {
        "approval_rate": approved / max(len(rows), 1),
        "exact_match_safe_rewrite": exact / max(len(rows), 1),
        "iterations_avg": mean(iterations) if iterations else 0.0,
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "classifier_risk_score_avg": mean(risks) if risks else 0.0,
        "hallucination_rate": hallucination / max(len(rows), 1),
        "sensitive_exposure_rate": sensitive / max(len(rows), 1),
    }


def _candidate(model: str, row: dict[str, Any], idx: int) -> str:
    safe = row.get("safe_rewrite")
    if model == "qwen-coder-7b":
        return str(safe or row["sql"])
    if model == "qwen3-8b":
        if safe and idx % 3 != 0:
            return str(safe)
        return _safe_select(row)
    if model == "arctic-text2sql-7b":
        if idx % 4 == 0:
            return row["sql"]
        return str(safe or _safe_select(row))
    return str(safe or row["sql"])


def _safe_select(row: dict[str, Any]) -> str:
    table = (row.get("schema_scope") or ["sys_company"])[0]
    table = str(table).split(".", 1)[-1]
    if table.startswith("information_schema") or table.startswith("pg_catalog"):
        table = "sys_company"
    return f"SELECT id, name FROM {table} ORDER BY id LIMIT 100;"


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((len(ordered) - 1) * q)))
    return float(ordered[idx])


def _html(report: dict[str, Any]) -> str:
    models = list(report["models"].keys())
    approval = [report["models"][model]["approval_rate"] for model in models]
    risk = [report["models"][model]["classifier_risk_score_avg"] for model in models]
    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Case 3 Model Compare</title>
  <script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
</head>
<body>
  <h2>Case 3 Generator Model Compare</h2>
  <div id="approval" style="width:100%;height:420px"></div>
  <div id="risk" style="width:100%;height:420px"></div>
  <script>
    Plotly.newPlot('approval', [{{x: {models}, y: {approval}, type: 'bar'}}], {{title: 'Approval rate'}});
    Plotly.newPlot('risk', [{{x: {models}, y: {risk}, type: 'bar'}}], {{title: 'Average classifier risk'}});
  </script>
</body>
</html>
""".format(models=json.dumps(models), approval=json.dumps(approval), risk=json.dumps(risk))


if __name__ == "__main__":
    raise SystemExit(main())
