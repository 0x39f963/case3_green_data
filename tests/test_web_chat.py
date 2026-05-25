from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api import app


def test_web_import_routes_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_CHAT_DIR", str(tmp_path / "web_chats"))
    monkeypatch.setenv("TRACES_DIR", str(tmp_path / "traces"))
    client = TestClient(app)

    assert client.get("/import").status_code == 404
    assert client.post("/web/api/import/run", json={}).status_code == 404


def test_web_chat_create_and_list(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_CHAT_DIR", str(tmp_path / "web_chats"))

    client = TestClient(app)
    create_response = client.post("/web/api/chats", json={"title": "Manual chat"})
    assert create_response.status_code == 200
    chat_id = create_response.json()["summary"]["chat_id"]

    list_response = client.get("/web/api/chats")
    assert list_response.status_code == 200
    items = list_response.json()["items"]
    assert [item["chat_id"] for item in items] == [chat_id]
    assert items[0]["title"] == "Manual chat"


def test_trace_prompts_api_returns_prompt_timeline(tmp_path, monkeypatch):
    trace_id = "trace_prompt_001"
    traces = tmp_path / "traces"
    traces.mkdir()
    monkeypatch.setenv("TRACES_DIR", str(traces))
    (traces / f"{trace_id}.json").write_text(
        json.dumps(
            {
                "request_id": trace_id,
                "events": [
                    {
                        "node": "generate",
                        "details": {
                            "prompt_meta": {
                                "prompt_id": "generator_system_v2",
                                "prompt_type": "generator_system",
                                "prompt_version": 2,
                                "prompt_sha256": "abc",
                                "prompt_source": "db",
                            },
                            "prompt_system": "system",
                            "prompt_user": "Задача аналитика:\nПокажи сотрудников",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    client = TestClient(app)
    response = client.get(f"/web/api/traces/{trace_id}/prompts")

    assert response.status_code == 200
    data = response.json()["prompt_trace"]
    assert data["summary"]["label"] == "generator_system v2"
    assert data["items"][0]["prompt_id"] == "generator_system_v2"
