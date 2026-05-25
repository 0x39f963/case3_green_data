from __future__ import annotations

import json

from fastapi.testclient import TestClient

from benchmark_service import api, db
from scripts.bench_smart_judge_worker import JudgeWorkerPool


TOKEN = "bench-test-token-32-chars-000001"


def test_list_judge_trace_ids_missing_only_sql(monkeypatch):
    captured = {}

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def fake_all(conn, sql, params):
        del conn
        captured["sql"] = sql
        captured["params"] = params
        return [{"trace_id": "trace_1"}]

    monkeypatch.setattr(db, "connect", lambda: FakeConn())
    monkeypatch.setattr(db, "_all", fake_all)

    traces = db.list_judge_trace_ids(
        "run_1",
        "openrouter",
        "google/gemini-3.1-flash-lite",
        missing_only=True,
        limit=2,
    )

    assert traces == ["trace_1"]
    assert "q.trace_id IS NULL" in captured["sql"]
    assert captured["params"] == ("openrouter", "google/gemini-3.1-flash-lite", "run_1", 2)


def test_progress_counts_selected_judge_backend(monkeypatch):
    monkeypatch.setattr(
        db,
        "get_benchmark_run",
        lambda run_id: {
            "benchmark_run_id": run_id,
            "total_cases": 10,
            "status": "completed",
            "started_at": None,
            "isolation_mode": "clean",
            "config_jsonb": {
                "smart_judge_backend": "openrouter",
                "smart_judge_model": "model-a",
                "judge_status": "partial",
                "judge_pending_in_queue": 3,
            },
        },
    )

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    calls = []

    def fake_scalar(conn, sql, params):
        del conn, params
        calls.append(sql)
        if "COALESCE(approved,false) = false" in sql:
            return 1
        return 7

    monkeypatch.setattr(db, "connect", lambda: FakeConn())
    monkeypatch.setattr(db, "_scalar", fake_scalar)
    monkeypatch.setattr(db, "judge_counts", lambda run_id, backend=None, model=None: {"pipeline_cases": 7, "scored_cases": 4, "missing_cases": 3})
    monkeypatch.setattr(db, "oracle_counts", lambda run_id: {"pipeline_cases": 7, "completed_cases": 0, "missing_cases": 7, "pass_cases": 0, "fail_cases": 0, "error_cases": 0})

    progress = db.benchmark_progress("run_1")

    assert progress["pipeline"]["completed_cases"] == 7
    assert progress["judge"]["completed_cases"] == 4
    assert progress["judge"]["pending_in_queue"] == 3
    assert progress["judge"]["status"] == "partial"
    assert progress["smart_judge_backend"] == "openrouter"


def test_progress_exposes_runner_failed_jsonl_error(monkeypatch, tmp_path):
    failed_dir = tmp_path / "data" / "bench" / "runs" / "run_1"
    failed_dir.mkdir(parents=True)
    (failed_dir / "failed.jsonl").write_text(
        json.dumps(
            {
                "case_id": "case_1",
                "model_key": "model-a",
                "http_status": 503,
                "error_class": "ApiError",
                "message": "provider unavailable",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(db, "ROOT", tmp_path)
    monkeypatch.setattr(
        db,
        "get_benchmark_run",
        lambda run_id: {
            "benchmark_run_id": run_id,
            "total_cases": 1,
            "status": "failed",
            "started_at": None,
            "isolation_mode": "clean",
            "config_jsonb": {"runner_status": "failed", "runner_return_code": 3},
        },
    )

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(db, "connect", lambda: FakeConn())
    monkeypatch.setattr(db, "_scalar", lambda conn, sql, params: 0)
    monkeypatch.setattr(db, "judge_counts", lambda run_id, backend=None, model=None: {"pipeline_cases": 0, "scored_cases": 0, "missing_cases": 0})
    monkeypatch.setattr(db, "oracle_counts", lambda run_id: {"pipeline_cases": 0, "completed_cases": 0, "missing_cases": 0, "pass_cases": 0, "fail_cases": 0, "error_cases": 0})

    progress = db.benchmark_progress("run_1")

    assert progress["pipeline"]["error_text"] == "case case_1 · model model-a · HTTP 503 · ApiError: provider unavailable"
    assert progress["runner"]["error_text"] == progress["pipeline"]["error_text"]


def test_judge_start_endpoint_calls_supervisor(monkeypatch):
    monkeypatch.setenv("BENCHMARK_INGEST_TOKEN", TOKEN)
    captured = {}

    def fake_start(run_id, payload, token, watch=False):
        captured.update({"run_id": run_id, "payload": payload, "token": token, "watch": watch})
        return {"job_id": "job_1", "status": "running", "log_path": "/tmp/judge.log"}

    monkeypatch.setattr(api.runner_supervisor, "start_judge_subprocess", fake_start)

    with TestClient(api.app) as client:
        response = client.post(
            "/v1/benchmarks/runs/run_1/judge/start",
            headers={"Authorization": "Bearer " + TOKEN},
            json={"backend": "openrouter", "model": "model-a", "workers": 1, "limit": 2},
        )

    assert response.status_code == 200
    assert response.json()["job_id"] == "job_1"
    assert captured["run_id"] == "run_1"
    assert captured["payload"]["missing_only"] is True
    assert captured["watch"] is False


def test_worker_pool_records_runtime_error(monkeypatch):
    saved = {}

    def fake_review(trace_id: str, backend: str, model: str) -> dict:
        del trace_id, backend, model
        raise RuntimeError("boom")

    def fake_insert(trace_id, run_id, **result):
        saved["trace_id"] = trace_id
        saved["run_id"] = run_id
        saved["result"] = result

    monkeypatch.setattr("scripts.bench_smart_judge_worker.review_case_quality", fake_review)
    monkeypatch.setattr("scripts.bench_smart_judge_worker.db.insert_case_quality_score", fake_insert)
    monkeypatch.setattr("scripts.bench_smart_judge_worker.db.bump_judge_completed_count", lambda run_id: None)

    pool = JudgeWorkerPool("codex_cli", "gpt-5.5", chunk_size=1, max_workers=1)
    pool.start("run_1")
    pool.enqueue("trace_1")
    pool.flush_and_join()

    assert saved["trace_id"] == "trace_1"
    assert saved["run_id"] == "run_1"
    assert saved["result"]["reviewer_status"] == "runtime_error"
    assert saved["result"]["reviewer_backend"] == "codex_cli"
