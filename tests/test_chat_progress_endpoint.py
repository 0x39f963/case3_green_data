from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.api import app


def test_progress_returns_404_for_missing_chat(tmp_path, monkeypatch):
    _set_dirs(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.get("/web/api/chats/missing123/progress")

    assert response.status_code == 404


def test_progress_returns_starting_when_trace_not_created(tmp_path, monkeypatch):
    dirs = _set_dirs(tmp_path, monkeypatch)
    _write_chat(dirs["web"], "chatstart1", {"pending_trace_id": "trace_start_001"})
    client = TestClient(app)

    response = client.get("/web/api/chats/chatstart1/progress")

    assert response.status_code == 200
    data = response.json()
    assert data["trace_id"] == "trace_start_001"
    assert data["complete"] is False
    assert data["steps"] == []
    assert data["stage"] == "starting"


def test_progress_returns_partial_steps_and_active_last_step(tmp_path, monkeypatch):
    dirs = _set_dirs(tmp_path, monkeypatch)
    trace_id = "trace_part_001"
    _write_chat(dirs["web"], "chatpart1", {"pending_trace_id": trace_id})
    _write_trace(
        dirs["traces"],
        trace_id,
        {
            "request_id": trace_id,
            "task": "test",
            "started_at": "2026-05-21T00:00:00+00:00",
            "partial": True,
            "events": [_event("prompt_check", 0.01), _event("retrieve", 0.02)],
            "result": None,
            "error": None,
        },
    )
    client = TestClient(app)

    response = client.get("/web/api/chats/chatpart1/progress")

    assert response.status_code == 200
    data = response.json()
    assert data["complete"] is False
    assert data["partial"] is True
    assert [step["key"] for step in data["steps"]] == ["prompt_check", "retrieve"]
    assert data["active_step"] == "retrieve"
    assert data["steps"][-1]["active"] is True


def test_progress_returns_complete_for_finished_trace(tmp_path, monkeypatch):
    dirs = _set_dirs(tmp_path, monkeypatch)
    trace_id = "trace_done_001"
    _write_chat(
        dirs["web"],
        "chatdone1",
        {
            "messages": [
                {"role": "assistant", "trace_id": trace_id, "status": "approved"},
            ],
        },
    )
    _write_trace(dirs["traces"], trace_id, _full_trace(trace_id))
    client = TestClient(app)

    response = client.get("/web/api/chats/chatdone1/progress")

    assert response.status_code == 200
    data = response.json()
    assert data["complete"] is True
    assert data["active_step"] == ""
    assert len(data["steps"]) == 2


def test_progress_falls_back_to_last_assistant_trace_id(tmp_path, monkeypatch):
    dirs = _set_dirs(tmp_path, monkeypatch)
    trace_id = "trace_fallback_001"
    _write_chat(
        dirs["web"],
        "chatfall1",
        {
            "messages": [
                {"role": "user", "text": "first"},
                {"role": "assistant", "trace_id": "oldtrace1", "status": "approved"},
                {"role": "user", "text": "second"},
                {"role": "assistant", "trace_id": trace_id, "status": "approved"},
            ],
        },
    )
    _write_trace(dirs["traces"], trace_id, _full_trace(trace_id))
    client = TestClient(app)

    response = client.get("/web/api/chats/chatfall1/progress")

    assert response.status_code == 200
    data = response.json()
    assert data["trace_id"] == trace_id
    assert data["complete"] is True
    assert [step["key"] for step in data["steps"]] == ["prompt_check", "retrieve"]


def test_progress_handles_broken_trace_json(tmp_path, monkeypatch):
    dirs = _set_dirs(tmp_path, monkeypatch)
    trace_id = "trace_broken_001"
    _write_chat(dirs["web"], "chatbad1", {"pending_trace_id": trace_id})
    (dirs["traces"] / f"{trace_id}.json").write_text("{not json", encoding="utf-8")
    client = TestClient(app)

    response = client.get("/web/api/chats/chatbad1/progress")

    assert response.status_code == 200
    data = response.json()
    assert data["trace_id"] == trace_id
    assert data["complete"] is False
    assert data["steps"] == []


def _set_dirs(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    web = tmp_path / "web_chats"
    traces = tmp_path / "traces"
    web.mkdir()
    traces.mkdir()
    monkeypatch.setenv("WEB_CHAT_DIR", str(web))
    monkeypatch.setenv("TRACES_DIR", str(traces))
    return {"web": web, "traces": traces}


def _write_chat(web: Path, chat_id: str, data: dict[str, Any]) -> None:
    payload = {
        "chat_id": chat_id,
        "title": "test",
        "status": "running",
        "messages": [],
        "last_result": {},
    }
    payload.update(data)
    (web / f"{chat_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_trace(traces: Path, trace_id: str, data: dict[str, Any]) -> None:
    (traces / f"{trace_id}.json").write_text(
        json.dumps(data, ensure_ascii=False),
        encoding="utf-8",
    )


def _event(node: str, duration: float) -> dict[str, Any]:
    return {
        "node": node,
        "started_at": "2026-05-21T00:00:00+00:00",
        "finished_at": "2026-05-21T00:00:01+00:00",
        "duration_sec": duration,
        "inputs": {},
        "outputs": {"ok": True},
        "details": {},
    }


def _full_trace(trace_id: str) -> dict[str, Any]:
    return {
        "request_id": trace_id,
        "task": "test",
        "started_at": "2026-05-21T00:00:00+00:00",
        "finished_at": "2026-05-21T00:00:02+00:00",
        "duration_sec": 2.0,
        "events": [_event("prompt_check", 0.01), _event("retrieve", 0.02)],
        "result": {
            "approved": True,
            "final_sql": "SELECT 1",
            "metadata": {"trace_id": trace_id, "generator_model": "test"},
        },
        "error": None,
    }
