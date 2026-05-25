from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api import app


def test_prompt_candidates_api_extracts_generator_rows(tmp_path, monkeypatch) -> None:
    traces = tmp_path / "traces"
    traces.mkdir()
    monkeypatch.setenv("TRACES_DIR", str(traces))
    (traces / "trace_prompt_rows.json").write_text(
        json.dumps(
            {
                "request_id": "trace_prompt_rows",
                "task": "Покажи активных сотрудников",
                "started_at": "2026-05-25T10:00:00+00:00",
                "result": {
                    "approved": True,
                    "metadata": {"trace_id": "trace_prompt_rows"},
                },
                "events": [
                    {
                        "node": "generate",
                        "started_at": "2026-05-25T10:00:01+00:00",
                        "outputs": {"selected_index": 1},
                        "details": {
                            "iteration": 1,
                            "prompt_system": "system text",
                            "prompt_user": "user text",
                            "candidates": [
                                {
                                    "candidate_index": 0,
                                    "sql": "SELECT 0;",
                                    "temperature": 0.3,
                                    "selected_by_selector": False,
                                    "selector_score": {"labels": ["NO_PAGINATION"]},
                                    "prompt_meta": {
                                        "prompt_id": "generator_system_v2",
                                        "prompt_type": "generator_system",
                                        "prompt_version": 2,
                                        "prompt_sha256": "abc123",
                                        "prompt_source": "db",
                                    },
                                },
                                {
                                    "candidate_index": 1,
                                    "sql": "SELECT 1;",
                                    "temperature": 0.6,
                                    "selected_by_selector": True,
                                    "selector_score": {"labels": []},
                                    "prompt_meta": {
                                        "prompt_id": "generator_system_v2",
                                        "prompt_type": "generator_system",
                                        "prompt_version": 2,
                                        "prompt_sha256": "abc123",
                                        "prompt_source": "db",
                                    },
                                },
                            ],
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    client = TestClient(app)

    response = client.get("/web/api/prompt-candidates")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["rows"][0]["temperature"] == 0.3
    assert data["rows"][1]["temperature"] == 0.6
    assert data["rows"][1]["selected"] is True
    assert data["rows"][1]["prompt_key"] == "generator_system_v2@v2"
    assert data["prompt_series"]["generator_system_v2@v2"][1]["quality_score"] >= 90


def test_prompt_candidates_page_has_grid_and_menu_link() -> None:
    client = TestClient(app)

    response = client.get("/prompts/candidates")

    assert response.status_code == 200
    assert "Prompt Candidate ADGrid" in response.text
    assert "/web/api/prompt-candidates" in response.text
    assert "Prompt Runs" in response.text
