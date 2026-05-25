from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import eval_oracle_aware
from scripts._oracle.dispatchers import dispatch
from scripts._oracle.loaders import load_golden_v1_1
from scripts._oracle.types import EvalReport


GOLDEN = ROOT / "data" / "eval" / "golden_dataset_v1_1.csv"


def test_smoke_10_cases_no_pipeline() -> None:
    cases = load_golden_v1_1(GOLDEN)[:10]
    verdicts = [dispatch(case, {"approved": True, "decision": "approve", "final_sql": case.reference_sql}) for case in cases]
    assert len(verdicts) == 10
    assert {verdict.verdict for verdict in verdicts} <= {"pass", "fail"}


def test_oracle_eval_report_structure() -> None:
    cases = load_golden_v1_1(GOLDEN)[:10]
    verdicts = [dispatch(case, {"approved": True, "decision": "approve", "final_sql": case.reference_sql}) for case in cases]
    report = EvalReport(
        dataset_version="1.1",
        total_cases=len(verdicts),
        by_oracle_type={"reference_sql": {"pass": 10, "fail": 0, "error": 0}},
        by_severity={"critical": {"pass": 10, "fail": 0, "error": 0}},
        aggregate_pass_rate=1.0,
        cases=verdicts,
        run_id="test",
        created_at="2026-05-20T00:00:00+00:00",
    )
    data = asdict(report)
    assert data["dataset_version"] == "1.1"
    assert data["total_cases"] == 10
    assert "by_oracle_type" in data
    assert "cases" in data


def test_oracle_runner_network_error_creates_report(tmp_path: Path, monkeypatch) -> None:
    def fail_post_run(*args, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr(eval_oracle_aware, "post_run", fail_post_run)
    args = argparse.Namespace(
        golden=str(GOLDEN),
        limit=2,
        oracle_types="reference_sql",
        base_url="http://localhost:9",
        llm_mode=None,
        llm_generator_model=None,
        timeout=0.1,
        report=str(tmp_path / "report.json"),
        ingest_store=False,
    )
    report = eval_oracle_aware.run_eval(args)
    out = eval_oracle_aware.write_report(report, args.report)
    assert out.exists()
    assert report.total_cases == 2
    assert {case.verdict for case in report.cases} == {"error"}
