"""
Compare Stage 3 encoder artifacts on the same eval split.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval_dataset import PHASE6_CRITICAL, eval_encoder_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="v1_0")
    parser.add_argument("--candidate", default="v2_0")
    parser.add_argument("--dataset", default="dataset_v1_0")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "eval" / "reports" / "encoder_compare_v1_v2.html")
    args = parser.parse_args()

    baseline = eval_encoder_report(args.baseline, args.dataset)
    candidate = eval_encoder_report(args.candidate, args.dataset)
    rows = _rows(baseline, candidate)
    failures = [
        row["label"]
        for row in rows
        if row["candidate_f1"] + 1e-9 < row["baseline_f1"]
    ]
    report = {
        "run_id": "encoder_compare_" + datetime.now().strftime("%Y%m%d_%H%M%S"),
        "baseline": baseline["encoder"],
        "candidate": candidate["encoder"],
        "dataset": candidate["dataset"],
        "dataset_note": candidate.get("dataset_note", ""),
        "rows": rows,
        "baseline_latency_p95_ms": baseline["latency_p95_ms"],
        "candidate_latency_p95_ms": candidate["latency_p95_ms"],
        "baseline_stage4_skip_rate": baseline["stage4_skip_rate"],
        "candidate_stage4_skip_rate": candidate["stage4_skip_rate"],
        "failures": failures,
        "verdict": "FAIL" if failures else "PASS",
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    json_path = args.out.with_suffix(".json")
    args.out.write_text(_html(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "failures": failures, "report_html": str(args.out), "report_json": str(json_path)}, ensure_ascii=False, sort_keys=True))
    return 1 if failures else 0


def _rows(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for label in PHASE6_CRITICAL:
        old = baseline["label_metrics"].get(label, {})
        new = candidate["label_metrics"].get(label, {})
        out.append(
            {
                "label": label,
                "baseline_precision": float(old.get("precision", 0.0)),
                "baseline_recall": float(old.get("recall", 0.0)),
                "baseline_f1": float(old.get("f1", 0.0)),
                "candidate_precision": float(new.get("precision", 0.0)),
                "candidate_recall": float(new.get("recall", 0.0)),
                "candidate_f1": float(new.get("f1", 0.0)),
                "delta_f1": float(new.get("f1", 0.0)) - float(old.get("f1", 0.0)),
                "support": int(new.get("support", 0)),
            }
        )
    return out


def _html(report: dict[str, Any]) -> str:
    rows = []
    for item in report["rows"]:
        rows.append(
            "<tr><td>{label}</td><td>{baseline_f1:.3f}</td><td>{candidate_f1:.3f}</td><td>{delta_f1:.3f}</td><td>{support}</td></tr>".format(**item)
        )
    return """<!doctype html>
<html><head><meta charset="utf-8"><title>Encoder Compare</title></head>
<body>
<h2>Encoder Compare: {baseline} vs {candidate}</h2>
<p>Verdict: {verdict} | Dataset: {dataset}</p>
<p>Latency p95 ms: {old_latency:.3f} -> {new_latency:.3f}</p>
<p>Stage 4 skip rate: {old_skip:.3f} -> {new_skip:.3f}</p>
<table border="1" cellspacing="0" cellpadding="4">
<tr><th>label</th><th>baseline f1</th><th>candidate f1</th><th>delta f1</th><th>support</th></tr>
{body}
</table>
</body></html>
""".format(
        baseline=report["baseline"],
        candidate=report["candidate"],
        verdict=report["verdict"],
        dataset=report["dataset"],
        old_latency=float(report["baseline_latency_p95_ms"]),
        new_latency=float(report["candidate_latency_p95_ms"]),
        old_skip=float(report["baseline_stage4_skip_rate"]),
        new_skip=float(report["candidate_stage4_skip_rate"]),
        body="\n".join(rows),
    )


if __name__ == "__main__":
    raise SystemExit(main())
