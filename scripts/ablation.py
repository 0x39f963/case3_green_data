"""
Ablation E0-E4 for classifier stages.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval_common import SPRINT2_CRITICAL, load_eval_rows, score_rows, stage_env  # noqa: E402

REPORT_DIR = ROOT / "data" / "eval" / "reports"


CONFIGS = [
    ("E0", "rules only", {"stage2": False, "ml_type": "lr", "stage3": False, "stage4": False}),
    ("E1", "+Stage 2 LR", {"stage2": True, "ml_type": "lr", "stage3": False, "stage4": False}),
    ("E2", "+Stage 2 LightGBM", {"stage2": True, "ml_type": "lightgbm", "stage3": False, "stage4": False}),
    ("E3", "+Stage 3 encoder", {"stage2": True, "ml_type": "lightgbm", "stage3": True, "stage4": False}),
    ("E4", "+Stage 5 ensemble", {"stage2": True, "ml_type": "lightgbm", "stage3": True, "stage4": False}),
]


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_eval_rows()
    results: list[dict[str, Any]] = []
    for name, title, cfg in CONFIGS:
        with stage_env(**cfg):
            stats = score_rows(rows)
        results.append(_pack(name, title, stats))

    deltas = _deltas(results)
    report = {
        "run_id": "case3_ablation_" + datetime.now().strftime("%Y%m%d_%H%M%S"),
        "rows": len(rows),
        "results": results,
        "deltas": deltas,
    }
    json_path = REPORT_DIR / "ablation_report.json"
    html_path = REPORT_DIR / "ablation_report.html"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    html_path.write_text(_html(report), encoding="utf-8")
    _print_table(results, deltas, json_path, html_path)
    return 0 if results[-1]["critical_recall_avg"] >= results[0]["critical_recall_avg"] else 1


def _pack(name: str, title: str, stats: dict[str, Any]) -> dict[str, Any]:
    critical_avg = sum(stats["critical_recall"].get(label, 0.0) for label in SPRINT2_CRITICAL) / len(SPRINT2_CRITICAL)
    return {
        "stage": name,
        "title": title,
        "critical_recall_avg": critical_avg,
        "block_fp_rate_safe": stats["suite_metrics"].get("gold_safe_readonly", {}).get("block_fp_rate", 0.0),
        "latency_p95_ms": stats["latency_p95_ms"],
        "evidence_span_hit_rate": stats["evidence_span_hit_rate"],
        "critical_recall": stats["critical_recall"],
    }


def _deltas(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    prev = 0.0
    for item in results:
        value = item["critical_recall_avg"]
        out.append({"stage": item["stage"], "delta_recall": value - prev, "critical_recall_avg": value})
        prev = value
    return out


def _print_table(results: list[dict[str, Any]], deltas: list[dict[str, Any]], json_path: Path, html_path: Path) -> None:
    print("stage | title | critical_recall_avg | delta | block_fp_safe | latency_p95_ms")
    print("---|---|---:|---:|---:|---:")
    for item, delta in zip(results, deltas):
        print(
            item["stage"] + " | "
            + item["title"] + " | "
            + f"{item['critical_recall_avg']:.4f} | "
            + f"{delta['delta_recall']:.4f} | "
            + f"{item['block_fp_rate_safe']:.4f} | "
            + f"{item['latency_p95_ms']:.2f}"
        )
    print("rules дают " + f"{results[0]['critical_recall_avg']:.2%}" + ", +LR дает "
          + f"{deltas[1]['delta_recall']:.2%}" + ", +LightGBM дает "
          + f"{deltas[2]['delta_recall']:.2%}" + ", +encoder дает "
          + f"{deltas[3]['delta_recall']:.2%}" + ", +ensemble дает "
          + f"{deltas[4]['delta_recall']:.2%}" + ".")
    print("report_json", json_path)
    print("report_html", html_path)


def _html(report: dict[str, Any]) -> str:
    stages = [item["stage"] for item in report["results"]]
    recall = [item["critical_recall_avg"] for item in report["results"]]
    fp = [item["block_fp_rate_safe"] for item in report["results"]]
    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Case 3 Ablation</title>
  <script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
</head>
<body>
  <h2>Case 3 Classifier Ablation E0-E4</h2>
  <div id="recall" style="width:100%;height:420px"></div>
  <div id="fp" style="width:100%;height:420px"></div>
  <script>
    Plotly.newPlot('recall', [{{x: {stages}, y: {recall}, type: 'bar'}}], {{title: 'Critical recall avg'}});
    Plotly.newPlot('fp', [{{x: {stages}, y: {fp}, type: 'bar'}}], {{title: 'Safe block FP'}});
  </script>
</body>
</html>
""".format(stages=json.dumps(stages), recall=json.dumps(recall), fp=json.dumps(fp))


if __name__ == "__main__":
    raise SystemExit(main())
