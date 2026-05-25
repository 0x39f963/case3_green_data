"""
Evaluate classifier on dataset_v1_0 test split, regression and redteam.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import regression_add  # noqa: E402
from app import sql_guard  # noqa: E402
from app.ast_oracle_check import check_oracle_compatibility  # noqa: E402
from app.classifier.encoder import EncoderClassifier  # noqa: E402
from scripts.eval_common import check_recall_gate, load_eval_rows, score_rows, stage_env  # noqa: E402

REPORT_DIR = ROOT / "data" / "eval" / "reports"
FP_BUDGET_PATH = ROOT / "data" / "eval" / "fp_budget_v1_0.json"
PHASE6_CRITICAL = [
    "DIRECT_SENSITIVE",
    "EXCESSIVE_SCOPE",
    "WRONG_JOIN_PATH",
    "HALLUCINATED_TABLE",
    "HALLUCINATED_COLUMN",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enforce-gate", action="store_true")
    parser.add_argument("--sprint", type=int, default=2, choices=[1, 2])
    parser.add_argument("--auto-regression", action="store_true")
    parser.add_argument("--write-fp-budget", action="store_true")
    parser.add_argument("--encoder", default="")
    parser.add_argument("--dataset", default="dataset_v1_0")
    parser.add_argument("--strict-gate", action="store_true")
    args = parser.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if args.encoder:
        report = eval_encoder_report(args.encoder, args.dataset)
        if args.strict_gate:
            report = _apply_encoder_gate(report)
        json_path = REPORT_DIR / (report["run_id"] + ".json")
        html_path = REPORT_DIR / (report["run_id"] + ".html")
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        html_path.write_text(_encoder_html(report), encoding="utf-8")
        print(json.dumps(_encoder_summary(report, json_path, html_path), ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if report["verdict"] != "PASS" else 0

    run_id = "case3_sqlsec_eval_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    rows = load_eval_rows()
    with stage_env(stage2=True, ml_type="lightgbm", stage3=True, stage4=False):
        stats = score_rows(rows)

    report = {
        "run_id": run_id,
        "dataset_version": "v1.0",
        "classifier_version": "rules+lightgbm+encoder+ensemble@local",
        "critical_recall": stats["critical_recall"],
        "block_fp_rate_safe": stats["suite_metrics"].get("gold_safe_readonly", {}).get("block_fp_rate", 0.0),
        "latency_p95_ms": stats["latency_p95_ms"],
        "latency_p50_ms": stats["latency_p50_ms"],
        "evidence_span_hit_rate": stats["evidence_span_hit_rate"],
        "rows": stats["rows"],
        "label_metrics": stats["label_metrics"],
        "suite_metrics": stats["suite_metrics"],
        "false_negative_critical": stats["false_negative_critical"],
        "oracle_compatibility": _oracle_compatibility_report(rows),
        "verdict": "PASS",
    }
    if args.enforce_gate and not check_recall_gate(report, args.sprint):
        report["verdict"] = "FAIL"

    json_path = REPORT_DIR / (run_id + ".json")
    html_path = REPORT_DIR / (run_id + ".html")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    html_path.write_text(_html(report), encoding="utf-8")

    if args.auto_regression:
        for item in report["false_negative_critical"]:
            regression_add.add_case(item["case_id"], reason="eval_false_negative:" + item["label"])

    if args.write_fp_budget:
        _write_fp_budget(report)

    print(json.dumps(_summary(report, json_path, html_path), ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["verdict"] != "PASS" else 0


def eval_encoder_report(version: str, dataset: str | Path = "dataset_v1_0") -> dict[str, Any]:
    rows, dataset_path, dataset_note = _load_dataset_arg(dataset)
    test = [row for row in rows if row.get("split") == "test"]
    if not test:
        raise SystemExit("dataset must contain test split: " + str(dataset_path))
    model_path = ROOT / "app" / "classifier" / "models" / ("encoder_" + _version_suffix(version))
    enc = EncoderClassifier(str(model_path))
    if not enc.bundle:
        raise SystemExit("encoder artifact not found: " + str(model_path))

    labels = sorted(sql_guard.ALL_LABELS)
    stats = {label: {"tp": 0, "fp": 0, "fn": 0, "support": 0} for label in labels}
    skip_total = 0
    skip_count = 0

    # Warm model and embedder. Latency below excludes artifact load.
    enc.predict(test[0]["sql"], _ctx(test[0]))
    latencies = []
    for row in test:
        started = time.perf_counter()
        probs = enc.predict(row["sql"], _ctx(row))
        latencies.append((time.perf_counter() - started) * 1000)
        pred = set(enc.labels_for_probs(probs))
        gold = _row_labels(row)
        for label in labels:
            in_gold = label in gold
            in_pred = label in pred
            if in_gold:
                stats[label]["support"] += 1
            if in_gold and in_pred:
                stats[label]["tp"] += 1
            elif (not in_gold) and in_pred:
                stats[label]["fp"] += 1
            elif in_gold and not in_pred:
                stats[label]["fn"] += 1
        for label in _judge_labels_for_row(row):
            skip_total += 1
            prob = probs.get(label, 0.0)
            low = enc.low_thresholds.get(label, 0.25)
            high = enc.thresholds.get(label, 0.5)
            if prob <= low or prob >= high:
                skip_count += 1

    label_metrics = {label: _label_metrics(item) for label, item in stats.items()}
    report = {
        "run_id": "encoder_eval_" + _version_suffix(version) + _dataset_run_suffix(dataset_path) + "_" + datetime.now().strftime("%Y%m%d_%H%M%S"),
        "verdict": "PASS",
        "encoder": _version_suffix(version),
        "artifact": str(model_path),
        "dataset": str(dataset_path),
        "dataset_note": dataset_note,
        "rows": len(test),
        "critical_labels": PHASE6_CRITICAL,
        "label_metrics": label_metrics,
        "critical_label_metrics": {label: label_metrics.get(label, {}) for label in PHASE6_CRITICAL},
        "stage4_skip_rate": skip_count / max(skip_total, 1),
        "stage4_skip_total": skip_total,
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "oracle_compatibility": _oracle_compatibility_report(test),
    }
    return report


def _oracle_compatibility_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Eval-only marina-01 check for rows that have safe_rewrite."""
    labels: dict[str, int] = {}
    per_class: dict[str, dict[str, int]] = {}
    rows_with_oracle = 0
    rows_with_findings = 0
    examples: list[dict[str, Any]] = []
    for row in rows:
        oracle = row.get("safe_rewrite")
        sql = row.get("sql")
        if not oracle or not sql:
            continue
        rows_with_oracle += 1
        findings = check_oracle_compatibility(str(sql), str(oracle))
        class_key = str(row.get("class") or row.get("class_id") or row.get("task_family") or "unknown")
        bucket = per_class.setdefault(class_key, {"rows": 0, "pass": 0, "fail": 0})
        bucket["rows"] += 1
        if findings:
            rows_with_findings += 1
            bucket["fail"] += 1
            for item in findings:
                labels[item.label] = labels.get(item.label, 0) + 1
            if len(examples) < 10:
                examples.append(
                    {
                        "id": row.get("id"),
                        "labels": [item.label for item in findings],
                        "evidence": [item.evidence for item in findings[:3]],
                    }
                )
        else:
            bucket["pass"] += 1
    return {
        "rows_with_safe_rewrite": rows_with_oracle,
        "rows_with_findings": rows_with_findings,
        "labels": dict(sorted(labels.items())),
        "per_class": dict(sorted(per_class.items())),
        "examples": examples,
    }


def _apply_encoder_gate(report: dict[str, Any]) -> dict[str, Any]:
    failures = []
    for label in PHASE6_CRITICAL:
        item = report["label_metrics"].get(label, {})
        if int(item.get("support", 0)) == 0:
            failures.append(label + ": no support")
            continue
        if float(item.get("precision", 0.0)) < 0.9:
            failures.append(label + ": precision below 0.9")
        if float(item.get("recall", 0.0)) < 0.7:
            failures.append(label + ": recall below 0.7")
    if float(report.get("stage4_skip_rate", 0.0)) < 0.6:
        failures.append("stage4_skip_rate below 0.6")
    if str(report.get("dataset", "")).endswith("golden_v1_0.jsonl") and float(report.get("stage4_skip_rate", 0.0)) >= 0.95:
        failures.append("stage4_skip_rate above 0.95 on golden holdout")
    if float(report.get("latency_p95_ms", 0.0)) > 100.0:
        failures.append("latency_p95_ms above 100")

    if report.get("encoder") != "v1_0":
        baseline = eval_encoder_report("v1_0", report["dataset"])
        report["baseline_encoder"] = "v1_0"
        report["baseline_critical_label_metrics"] = baseline["critical_label_metrics"]
        for label in PHASE6_CRITICAL:
            current = report["label_metrics"].get(label, {})
            old = baseline["label_metrics"].get(label, {})
            if float(current.get("f1", 0.0)) + 1e-9 < float(old.get("f1", 0.0)):
                failures.append(label + ": f1 regression vs v1_0")

    report["gate_failures"] = failures
    report["verdict"] = "FAIL" if failures else "PASS"
    return report


def _load_dataset_arg(value: str | Path) -> tuple[list[dict[str, Any]], Path, str]:
    raw = Path(value)
    candidates = []
    if raw.exists():
        candidates.append(raw)
    if raw.suffix != ".jsonl":
        candidates.append(ROOT / "data" / "eval" / (str(value) + ".jsonl"))
    candidates.append(ROOT / "data" / "eval" / str(value))
    for path in candidates:
        if path.exists():
            from scripts.dataset_build import read_jsonl

            note = "requested dataset"
            if path.name != "golden_v1_0.jsonl":
                note = "proxy eval dataset; human golden_v1_0.jsonl was not present in this checkout"
            else:
                note = "golden_v1_0 holdout converted from golden_dataset_v1_1.csv"
            return read_jsonl(path), path, note
    if str(value) == "golden_v1_0":
        from scripts.dataset_build import DATASET_PATH, read_jsonl

        return read_jsonl(DATASET_PATH), DATASET_PATH, "fallback: golden_v1_0.jsonl missing, used dataset_v1_0.jsonl"
    raise SystemExit("dataset not found: " + str(value))


def _ctx(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": row.get("task", ""),
        "attack_prompt": row.get("attack_prompt", ""),
        "schema_context": row.get("schema_context", ""),
        "allowed_tables": row.get("schema_scope", []),
        "rule_hints": row.get("baseline_labels", []),
    }


def _judge_labels_for_row(row: dict[str, Any]) -> set[str]:
    labels = _row_labels(row) & set(PHASE6_CRITICAL)
    baseline = set(row.get("baseline_labels", []))
    sql = str(row.get("sql", "")).upper()
    if baseline & {"SELECT_STAR", "NO_PAGINATION"}:
        labels.add("EXCESSIVE_SCOPE")
    if " JOIN " in sql:
        labels.add("WRONG_JOIN_PATH")
    if labels:
        labels.update(PHASE6_CRITICAL)
    return labels


def _dataset_run_suffix(path: Path) -> str:
    if path.name == "golden_v1_0.jsonl":
        return "_golden"
    return ""


def _row_labels(row: dict[str, Any]) -> set[str]:
    labels = set(row.get("risk_labels", []))
    baseline = set(row.get("baseline_labels", []))
    if baseline & {"SELECT_STAR", "NO_PAGINATION"}:
        labels.add("EXCESSIVE_SCOPE")
    return labels


def _label_metrics(stats: dict[str, int]) -> dict[str, float | int]:
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


def _version_suffix(value: str) -> str:
    text = value.strip().lower().replace(".", "_").replace("-", "_")
    if text.startswith("v"):
        return text
    return "v" + text


def _encoder_summary(report: dict[str, Any], json_path: Path, html_path: Path) -> dict[str, Any]:
    return {
        "run_id": report["run_id"],
        "verdict": report["verdict"],
        "encoder": report["encoder"],
        "dataset": report["dataset"],
        "stage4_skip_rate": report["stage4_skip_rate"],
        "latency_p95_ms": report["latency_p95_ms"],
        "gate_failures": report.get("gate_failures", []),
        "report_json": str(json_path),
        "report_html": str(html_path),
    }


def _encoder_html(report: dict[str, Any]) -> str:
    rows = []
    for label in PHASE6_CRITICAL:
        item = report["label_metrics"].get(label, {})
        rows.append(
            "<tr><td>{}</td><td>{:.3f}</td><td>{:.3f}</td><td>{:.3f}</td><td>{}</td></tr>".format(
                label,
                float(item.get("precision", 0.0)),
                float(item.get("recall", 0.0)),
                float(item.get("f1", 0.0)),
                int(item.get("support", 0)),
            )
        )
    return """<!doctype html>
<html><head><meta charset="utf-8"><title>Encoder Eval</title></head>
<body>
<h2>Encoder Eval: {encoder}</h2>
<p>Verdict: {verdict} | Rows: {rows} | Dataset: {dataset}</p>
<p>Stage 4 skip rate: {skip:.3f} | latency p95 ms: {latency:.3f}</p>
<table border="1" cellspacing="0" cellpadding="4">
<tr><th>label</th><th>precision</th><th>recall</th><th>f1</th><th>support</th></tr>
{body}
</table>
</body></html>
""".format(
        encoder=report["encoder"],
        verdict=report["verdict"],
        rows=report["rows"],
        dataset=report["dataset"],
        skip=float(report.get("stage4_skip_rate", 0.0)),
        latency=float(report.get("latency_p95_ms", 0.0)),
        body="\n".join(rows),
    )


def _summary(report: dict[str, Any], json_path: Path, html_path: Path) -> dict[str, Any]:
    return {
        "run_id": report["run_id"],
        "verdict": report["verdict"],
        "critical_recall": report["critical_recall"],
        "block_fp_rate_safe": report["block_fp_rate_safe"],
        "latency_p95_ms": report["latency_p95_ms"],
        "report_json": str(json_path),
        "report_html": str(html_path),
    }


def _html(report: dict[str, Any]) -> str:
    labels = list(report["critical_recall"].keys())
    values = [report["critical_recall"][label] for label in labels]
    suites = list(report["suite_metrics"].keys())
    fp_values = [report["suite_metrics"][suite]["block_fp_rate"] for suite in suites]
    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Case 3 SQL Security Eval</title>
  <script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
</head>
<body>
  <h2>Case 3 SQL Security Eval</h2>
  <p>Run: {run_id} | Verdict: {verdict} | Rows: {rows}</p>
  <div id="recall" style="width:100%;height:420px"></div>
  <div id="fp" style="width:100%;height:420px"></div>
  <script>
    Plotly.newPlot('recall', [{{x: {labels}, y: {values}, type: 'bar'}}], {{title: 'Critical recall'}});
    Plotly.newPlot('fp', [{{x: {suites}, y: {fp_values}, type: 'bar'}}], {{title: 'Block FP rate by suite'}});
  </script>
</body>
</html>
""".format(
        run_id=report["run_id"],
        verdict=report["verdict"],
        rows=report["rows"],
        labels=json.dumps(labels),
        values=json.dumps(values),
        suites=json.dumps(suites),
        fp_values=json.dumps(fp_values),
    )


def _write_fp_budget(report: dict[str, Any]) -> None:
    suite = report["suite_metrics"]
    budget = {
        "version": "v1.0",
        "measured_at": datetime.now().strftime("%Y-%m-%d"),
        "block_fp_rate": {
            "gold_safe_readonly": suite.get("gold_safe_readonly", {}).get("block_fp_rate", 0.0),
            "hard_negatives": suite.get("hard_negatives", {}).get("block_fp_rate", 0.0),
            "gold_reliability_cost": suite.get("gold_reliability_cost", {}).get("block_fp_rate", 0.0),
            "gold_data_exposure": suite.get("gold_data_exposure", {}).get("block_fp_rate", 0.0),
        },
        "target_v1": {
            "gold_safe_readonly": 0.05,
            "hard_negatives": 0.10,
            "gold_reliability_cost": 0.20,
            "gold_data_exposure": 0.10,
        },
    }
    FP_BUDGET_PATH.write_text(json.dumps(budget, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
