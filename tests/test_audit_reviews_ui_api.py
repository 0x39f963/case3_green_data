from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import app as web_app
from benchmark_service import api as bench_api


TOKEN = "bench-test-token-32-chars-000001"


def test_legacy_audit_reviews_screen_and_assets_removed():
    client = TestClient(web_app)

    page = client.get("/audits/reviews")
    assert page.status_code == 404

    asset = client.get("/web/audits/static/audit_reviews.js")
    assert asset.status_code == 404

    blocked = client.get("/web/audits/static/random.txt")
    assert blocked.status_code == 404


def test_audit_reviews_list_endpoint_removed(monkeypatch):
    monkeypatch.setenv("BENCHMARK_INGEST_TOKEN", TOKEN)

    with TestClient(bench_api.app) as client:
        response = client.get(
            "/v1/audit/reviews",
            headers={"Authorization": "Bearer " + TOKEN},
        )

    assert response.status_code in {404, 405}


def test_benchmark_run_audit_report_endpoint(monkeypatch):
    monkeypatch.setenv("BENCHMARK_INGEST_TOKEN", TOKEN)
    expected = {
        "benchmark_run_id": "run_1",
        "summary": "Сводка аудита batch-run.",
        "oracle": {"top_reasons": []},
        "judge_audit": {"top_root_causes": []},
        "hypotheses": [],
    }
    monkeypatch.setattr(bench_api.db, "benchmark_run_audit_report", lambda run_id: expected if run_id == "run_1" else {})

    with TestClient(bench_api.app) as client:
        ok = client.get("/v1/benchmarks/runs/run_1/audit-report")
        missing = client.get("/v1/benchmarks/runs/missing/audit-report")

    assert ok.status_code == 200
    assert ok.json()["summary"].startswith("Сводка")
    assert missing.status_code == 404


def test_audit_review_detail_endpoint(monkeypatch):
    monkeypatch.setenv("BENCHMARK_INGEST_TOKEN", TOKEN)

    def fake_detail(review_id: str):
        if review_id == "REV_001":
            return {"review": {"review_id": review_id}, "suggestions": [], "step_scores": []}
        return {}

    monkeypatch.setattr(bench_api.db, "get_audit_review_detail", fake_detail)

    with TestClient(bench_api.app) as client:
        ok = client.get("/v1/audit/reviews/REV_001", headers={"Authorization": "Bearer " + TOKEN})
        missing = client.get("/v1/audit/reviews/REV_MISSING", headers={"Authorization": "Bearer " + TOKEN})

    assert ok.status_code == 200
    assert ok.json()["review"]["review_id"] == "REV_001"
    assert missing.status_code == 404
