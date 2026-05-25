# Generated at: 2026-05-22 13:04:03 MSK
from __future__ import annotations

from argparse import Namespace
from dataclasses import replace

from fastapi.testclient import TestClient

from benchmark_service import api, db, runner_supervisor
from scripts.bench_oracle_existing_run import build_pipeline_response, normalize_oracle_test_id, run_oracle
from scripts._oracle.types import OracleCase


TOKEN = "bench-test-token-32-chars-000001"


BASE_CASE = OracleCase(
    test_id="TC-0001",
    category_id="1",
    category_name="cat",
    nl_prompt="prompt",
    attack_class="none",
    expected_behavior="refuse",
    oracle_type="refusal_only",
    reference_sql="NO_SQL_EXPECTED",
    reference_params={},
    semantic_assertions=["no_sql_generated", "refuse_with_explanation"],
    comparison_method="assertions",
    oracle_notes="",
    severity_if_failed="P1",
    raw_row={},
)


def test_oracle_case_id_mapping() -> None:
    assert normalize_oracle_test_id("golden_v1_0_tc-0001") == "TC-0001"
    assert normalize_oracle_test_id("tc_42") == "TC-0042"
    assert normalize_oracle_test_id("TC-12345") == "TC-12345"


def test_build_pipeline_response_from_stored_row_dispatches_refusal() -> None:
    row = {
        "trace_id": "trace_1",
        "case_id": "golden_v1_0_tc-0001",
        "model_key": "model-a",
        "decision": "abstain",
        "approved": False,
        "needs_human": True,
        "human_reason": "Refused because request is unsafe.",
        "final_sql_text": "",
    }

    from scripts._oracle.dispatchers import dispatch

    verdict = dispatch(BASE_CASE, build_pipeline_response(row))

    assert verdict.verdict == "pass"
    assert verdict.test_id == "TC-0001"


def test_oracle_existing_run_missing_only_skips_case_level_duplicates(monkeypatch) -> None:
    rows = [
        {
            "trace_id": "trace_existing",
            "benchmark_run_id": "run_1",
            "case_id": "golden_v1_0_tc-0001",
            "model_key": "model-a",
            "decision": "abstain",
            "approved": False,
            "needs_human": True,
            "human_reason": "Refused because request is unsafe.",
            "final_sql_text": "",
        },
        {
            "trace_id": "trace_new",
            "benchmark_run_id": "run_1",
            "case_id": "golden_v1_0_tc-0001",
            "model_key": "model-a",
            "decision": "abstain",
            "approved": False,
            "needs_human": True,
            "human_reason": "Refused because request is unsafe.",
            "final_sql_text": "",
        },
    ]
    saved: list[tuple[str, str]] = []

    monkeypatch.setattr("scripts.bench_oracle_existing_run._load_benchmark_env", lambda: None)
    monkeypatch.setattr("scripts.bench_oracle_existing_run.load_golden_v1_1", lambda _: [replace(BASE_CASE)])
    monkeypatch.setattr(
        db,
        "existing_oracle_keys",
        lambda run_id: {
            "case_keys": {("golden_v1_0_tc-0001", "refusal_only")},
            "trace_keys": {("trace_existing", "refusal_only")},
        },
    )
    monkeypatch.setattr(db, "list_oracle_pipeline_rows", lambda run_id: rows)
    monkeypatch.setattr(db, "oracle_counts", lambda run_id: {"pipeline_cases": 2, "completed_cases": len(saved), "missing_cases": 2 - len(saved), "pass_cases": len(saved), "fail_cases": 0, "error_cases": 0})
    monkeypatch.setattr(db, "update_oracle_run_status", lambda *args, **kwargs: None)

    def fake_insert(run_id, row, verdict, **kwargs):
        del kwargs
        saved.append((row["trace_id"], verdict["oracle_type"]))
        return {"id": len(saved), "inserted": True}

    monkeypatch.setattr(db, "insert_oracle_eval_result", fake_insert)

    report = run_oracle(
        Namespace(
            benchmark_run_id="run_1",
            golden="unused.csv",
            dataset_version="1.1",
            oracle_types="",
            case_id=[],
            limit=0,
            missing_only=True,
            status_on_error="error",
            ingest_store=True,
            report="",
        )
    )

    assert saved == []
    assert report["inserted"] == 0
    assert report["skipped"] == 2


def test_oracle_existing_run_limit_counts_new_dispatches(monkeypatch) -> None:
    rows = []
    for index in range(1, 11):
        rows.append(
            {
                "trace_id": f"trace_{index}",
                "benchmark_run_id": "run_1",
                "case_id": f"golden_v1_0_tc-{index:04d}",
                "model_key": "model-a",
                "decision": "abstain",
                "approved": False,
                "needs_human": True,
                "human_reason": "Refused because request is unsafe.",
                "final_sql_text": "",
            }
        )
    saved: list[str] = []

    monkeypatch.setattr("scripts.bench_oracle_existing_run._load_benchmark_env", lambda: None)
    monkeypatch.setattr(
        "scripts.bench_oracle_existing_run.load_golden_v1_1",
        lambda _: [replace(BASE_CASE, test_id=f"TC-{index:04d}") for index in range(1, 11)],
    )
    monkeypatch.setattr(
        db,
        "existing_oracle_keys",
        lambda run_id: {
            "case_keys": {(f"golden_v1_0_tc-{index:04d}", "refusal_only") for index in range(1, 6)},
            "trace_keys": {(f"trace_{index}", "refusal_only") for index in range(1, 6)},
        },
    )
    monkeypatch.setattr(db, "list_oracle_pipeline_rows", lambda run_id: rows)
    monkeypatch.setattr(db, "oracle_counts", lambda run_id: {"pipeline_cases": 10, "completed_cases": 5 + len(saved), "missing_cases": 5 - len(saved), "pass_cases": 5 + len(saved), "fail_cases": 0, "error_cases": 0})
    monkeypatch.setattr(db, "update_oracle_run_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(db, "insert_oracle_eval_result", lambda run_id, row, verdict, **kwargs: saved.append(row["trace_id"]) or {"inserted": True})

    report = run_oracle(
        Namespace(
            benchmark_run_id="run_1",
            golden="unused.csv",
            dataset_version="1.1",
            oracle_types="",
            case_id=[],
            limit=5,
            missing_only=True,
            status_on_error="error",
            ingest_store=True,
            report="",
        )
    )

    assert saved == ["trace_6", "trace_7", "trace_8", "trace_9", "trace_10"]
    assert report["inserted"] == 5
    assert report["skipped"] == 5
    assert report["seen_total"] == 10


def test_metrics_ea_not_evaluated_is_null_status(monkeypatch) -> None:
    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def fake_one(conn, sql, params):
        del conn, params
        if "FROM benchmark.pipeline_runs" in sql:
            return {"total": 2, "approve_rate": 0.5}
        if "FROM benchmark.case_quality_scores" in sql:
            return {}
        if "FROM benchmark.oracle_eval_runs" in sql:
            return {
                "ea_pass_rate": None,
                "ea_evaluated_cases": 0,
                "ea_pass_cases": 0,
                "ea_fail_cases": 0,
                "ea_error_cases": 0,
            }
        if "FROM benchmark.llm_calls" in sql:
            return {"total_tokens_in": 0, "total_tokens_out": 0, "total_cached_tokens": 0, "total_cost_usd": 0, "total_cost_quota_equivalent_usd": 0}
        return {}

    monkeypatch.setattr(db, "connect", lambda: FakeConn())
    monkeypatch.setattr(db, "_one", fake_one)
    monkeypatch.setattr(db, "_all", lambda conn, sql, params: [])

    metrics = db.metrics_summary_extended("run_1")

    assert metrics["ea_pass_rate"] is None
    assert metrics["ea_evaluated_cases"] == 0
    assert metrics["ea_total_cases"] == 2
    assert metrics["ea_status"] == "not_evaluated"


def test_progress_uses_persisted_oracle_block(monkeypatch) -> None:
    monkeypatch.setattr(
        db,
        "get_benchmark_run",
        lambda run_id: {
            "benchmark_run_id": run_id,
            "total_cases": 10,
            "status": "completed",
            "started_at": None,
            "isolation_mode": "clean",
            "config_jsonb": {},
            "benchmark_progress": {
                "oracle": {
                    "status": "partial",
                    "completed_cases": 4,
                    "total_missing": 6,
                    "pass_cases": 2,
                    "fail_cases": 1,
                    "error_cases": 1,
                    "log_path": "/tmp/oracle.log",
                    "error_text": None,
                }
            },
        },
    )

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(db, "connect", lambda: FakeConn())
    monkeypatch.setattr(db, "_scalar", lambda conn, sql, params: 10 if "COALESCE(approved,false) = false" not in sql else 1)
    monkeypatch.setattr(db, "judge_counts", lambda run_id, backend=None, model=None: {"pipeline_cases": 10, "scored_cases": 0, "missing_cases": 10})
    monkeypatch.setattr(db, "oracle_counts", lambda run_id: (_ for _ in ()).throw(AssertionError("oracle_counts should not be called")))

    progress = db.benchmark_progress("run_1")

    assert progress["oracle"]["status"] == "partial"
    assert progress["oracle"]["completed_cases"] == 4
    assert progress["oracle"]["total_missing"] == 6
    assert progress["oracle"]["pass_cases"] == 2
    assert progress["oracle"]["log_path"] == "/tmp/oracle.log"


def test_cases_api_filters_forward_to_db(monkeypatch) -> None:
    monkeypatch.setenv("BENCHMARK_INGEST_TOKEN", TOKEN)
    captured = {}

    def fake_list(filters):
        captured.update(filters)
        return {"items": [], "total": 0, "limit": filters["limit"], "offset": filters["offset"], "next_offset": None}

    monkeypatch.setattr(api.db, "list_benchmark_cases", fake_list)

    with TestClient(api.app) as client:
        response = client.get("/v1/benchmarks/cases?run_id=run_1&oracle_verdict=fail&smart_score_min=4&limit=5")

    assert response.status_code == 200
    assert captured["run_id"] == "run_1"
    assert captured["oracle_verdict"] == "fail"
    assert captured["smart_score_min"] == 4
    assert captured["limit"] == 5


def test_oracle_and_analysis_start_endpoints_call_supervisor(monkeypatch) -> None:
    monkeypatch.setenv("BENCHMARK_INGEST_TOKEN", TOKEN)
    captured: dict[str, dict] = {}

    def fake_oracle(run_id, payload, token):
        captured["oracle"] = {"run_id": run_id, "payload": payload, "token": token}
        return {"job_id": "oracle_job", "status": "running", "log_path": "/tmp/oracle.log"}

    def fake_analysis(run_id, payload, token):
        captured["analysis"] = {"run_id": run_id, "payload": payload, "token": token}
        return {"job_id": "analysis_job", "status": "running", "log_path": "/tmp/analysis.log"}

    monkeypatch.setattr(api.runner_supervisor, "start_oracle_subprocess", fake_oracle)
    monkeypatch.setattr(api.runner_supervisor, "start_analysis_subprocess", fake_analysis)

    with TestClient(api.app) as client:
        oracle_response = client.post(
            "/v1/benchmarks/runs/run_1/oracle/start",
            headers={"Authorization": "Bearer " + TOKEN},
            json={"limit": 5, "missing_only": True},
        )
        analysis_response = client.post(
            "/v1/benchmarks/runs/run_1/analysis/start",
            headers={"Authorization": "Bearer " + TOKEN},
            json={"backend": "codex_cli", "model": "gpt-5.5", "limit": 2},
        )

    assert oracle_response.status_code == 200
    assert analysis_response.status_code == 200
    assert captured["oracle"]["payload"]["status_on_error"] == "error"
    assert captured["analysis"]["payload"]["oracle_required"] is False


def test_new_batch_starts_posthoc_chain_with_selected_analysis_model(monkeypatch) -> None:
    monkeypatch.setenv("BENCHMARK_INGEST_TOKEN", TOKEN)
    captured: dict[str, dict] = {}

    monkeypatch.setattr(api.runner_supervisor, "dataset_case_count", lambda dataset_id, dataset_path=None: 1)
    def fake_start_db(item):
        captured["db_item"] = item
        return {"benchmark_run_id": "run_1", "status": "registered"}

    def fake_runner(item, token):
        captured["runner"] = {"item": item, "token": token}
        return {"status": "running"}

    def fake_judge(run_id, item, token, watch=False):
        captured["judge"] = {"run_id": run_id, "item": item, "token": token, "watch": watch}
        return {"status": "running"}

    def fake_posthoc(run_id, item, token):
        captured["posthoc"] = {"run_id": run_id, "item": item, "token": token}
        return {"status": "scheduled"}

    monkeypatch.setattr(api.db, "start_benchmark_run", fake_start_db)
    monkeypatch.setattr(api.runner_supervisor, "start_subprocess", fake_runner)
    monkeypatch.setattr(api.runner_supervisor, "start_judge_subprocess", fake_judge)
    monkeypatch.setattr(api.runner_supervisor, "start_posthoc_chain", fake_posthoc)

    with TestClient(api.app) as client:
        response = client.post(
            "/v1/benchmarks/runs",
            headers={"Authorization": "Bearer " + TOKEN},
            json={
                "dataset_id": "golden_v1_0",
                "models": ["codex-spark-medium"],
                "limit": 1,
                "smart_judge_backend": "codex_cli",
                "smart_judge_model": "gpt-5.3-codex-spark",
                "codex_reasoning_effort": "medium",
                "oracle_enabled": True,
                "analysis_enabled": True,
                "analysis_backend": "codex_cli",
                "analysis_model": "gpt-5.3-codex-spark",
                "analysis_codex_reasoning_effort": "xhigh",
            },
        )

    assert response.status_code == 200
    assert captured["judge"]["watch"] is True
    assert captured["posthoc"]["item"]["oracle_enabled"] is True
    assert captured["posthoc"]["item"]["analysis_model"] == "gpt-5.3-codex-spark"
    assert captured["posthoc"]["item"]["analysis_codex_reasoning_effort"] == "xhigh"


def test_runner_reaper_preserves_user_aborted_status(monkeypatch) -> None:
    class FakeProc:
        pid = 123

        def poll(self):
            return -15

    updates = []
    monkeypatch.setattr(runner_supervisor, "_PROCS", {"run_1": FakeProc()})
    monkeypatch.setattr(runner_supervisor.db, "get_benchmark_run", lambda run_id: {"config_jsonb": {"runner_status": "aborted"}})

    def fake_update(run_id, status, **kwargs):
        updates.append((run_id, status, kwargs))

    monkeypatch.setattr(runner_supervisor.db, "update_benchmark_run_status", fake_update)
    monkeypatch.setattr(runner_supervisor, "_runner_error", lambda run_id: "should not surface")

    runner_supervisor.reap_finished()

    assert updates[-1][0] == "run_1"
    assert updates[-1][1] == "aborted"
    assert updates[-1][2]["error"] is None


def test_hypothesis_canonical_key_normalizes_exact_duplicates() -> None:
    first = db.canonical_hypothesis_key(
        {
            "target_area": "generator_prompt",
            "title": "Drops tenant filter",
            "patch_hint": "Keep initiator_id after retry.",
            "failure_signature": "missing initiator_id",
        }
    )
    second = db.canonical_hypothesis_key(
        {
            "target_area": "generator_prompt",
            "title": "  drops   tenant filter ",
            "patch_hint": "keep initiator_id after retry.",
            "failure_signature": "missing initiator_id",
        }
    )

    assert first == second
    assert first.startswith("generator_prompt:")


def test_hypothesis_trigram_merge_reuses_existing_id(monkeypatch) -> None:
    calls: list[tuple[str, tuple]] = []
    state = {"select_count": 0, "evidence_count": 0}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=()):
            calls.append((sql, tuple(params)))
            self.sql = sql
            if "SELECT hypothesis_id" in sql:
                state["select_count"] += 1
                self.row = None if state["select_count"] == 1 else ("hyp_1", "other:key", 0.72)
            elif "INSERT INTO benchmark.improvement_hypotheses" in sql:
                self.row = ("hyp_1",)
            elif "INSERT INTO benchmark.hypothesis_evidence" in sql:
                state["evidence_count"] += 1
                self.row = None
            else:
                self.row = None

        def fetchone(self):
            return self.row

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def cursor(self):
            return FakeCursor()

        def commit(self):
            return None

    monkeypatch.setattr(db, "connect", lambda: FakeConn())

    first = db.upsert_hypothesis_with_evidence(
        "report_1",
        "trace_1",
        None,
        None,
        {
            "target_area": "generator_prompt",
            "title": "generator drops tenant filter",
            "patch_hint": "keep tenant predicate after retry",
        },
    )
    second = db.upsert_hypothesis_with_evidence(
        "report_2",
        "trace_2",
        None,
        None,
        {
            "target_area": "generator_prompt",
            "title": "generator drops the tenant filter after retry",
            "patch_hint": "preserve tenant predicate on retries",
        },
    )

    assert first == second == "hyp_1"
    assert state["evidence_count"] == 2
    assert any("benchmark.similarity(title || ' ' || COALESCE(patch_hint, ''), %s::text)" in sql for sql, _ in calls)
