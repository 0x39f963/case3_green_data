from fastapi.testclient import TestClient

from benchmark_service import api, db


TOKEN = "bench-test-token-32-chars-000001"


def test_ingest_logical_duplicate_returns_409(monkeypatch):
    monkeypatch.setenv("BENCHMARK_INGEST_TOKEN", TOKEN)

    def fake_ingest_run(data, replace=False):
        assert replace is False
        raise db.DuplicateLogicalRun("trace_existing")

    monkeypatch.setattr(api.db, "ingest_run", fake_ingest_run)

    with TestClient(api.app) as client:
        response = client.post(
            "/v1/ingest/run",
            headers={"Authorization": "Bearer " + TOKEN},
            json=_payload("trace_new"),
        )

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "duplicate_logical_run"
    assert body["error"]["details"]["existing_trace_id"] == "trace_existing"


def _payload(trace_id: str) -> dict:
    return {
        "trace_id": trace_id,
        "benchmark_run_id": "bench_test",
        "dataset_id": "dataset_test",
        "dataset_version": "v0_1",
        "case_id": "case_test",
        "model_key": "model_test",
        "llm_mode": "test",
        "system_result": {
            "metadata": {"trace_id": trace_id, "decision": "approve"},
            "approved": True,
        },
        "trace": {"request_id": trace_id, "events": [], "result": {}},
    }
