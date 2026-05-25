from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app import llm_provider
from app import pipeline_service
from app.api import app


TRACE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,}$")


class FakeResult:
    def __init__(self, trace_id: str, task: str, codex_reasoning_effort: str | None = None) -> None:
        self.final_sql = "SELECT id, name FROM sys_client WHERE status = 'active' LIMIT 100;"
        self.approved = True
        self.iterations_used = 1
        self.iterations_log: list[dict[str, Any]] = []
        self.audit_log = "approved"
        self.metadata = {
            "trace_id": trace_id,
            "decision": "approve",
            "task": task,
            "duration_sec": 0.01,
            "generator_model": "fake-smoke",
        }
        if codex_reasoning_effort:
            self.metadata["codex_reasoning_effort_env"] = codex_reasoning_effort


def test_run_and_web_chat_return_same_final_sql_for_fake_llm(tmp_path, monkeypatch):
    _patch_pipeline(tmp_path, monkeypatch)
    client = TestClient(app)
    task = "выведи список активных клиентов"

    run_data = client.post("/run", json={"task": task, "max_iterations": 1}).json()
    chat_id = _create_chat(client)
    chat_data = client.post(
        "/web/api/chats/" + chat_id + "/messages",
        json={"task": task, "max_iterations": 1},
    ).json()
    web_result = chat_data["message"]["result"]

    assert run_data["final_sql"] == web_result["final_sql"]
    assert run_data["metadata"]["decision"] == web_result["metadata"]["decision"]


def test_both_endpoints_return_trace_id(tmp_path, monkeypatch):
    _patch_pipeline(tmp_path, monkeypatch)
    client = TestClient(app)
    task = "выведи список активных клиентов"

    run_data = client.post("/run", json={"task": task, "max_iterations": 1}).json()
    chat_id = _create_chat(client)
    chat_data = client.post(
        "/web/api/chats/" + chat_id + "/messages",
        json={"task": task, "max_iterations": 1},
    ).json()

    run_trace_id = run_data["metadata"]["trace_id"]
    web_trace_id = chat_data["message"]["trace_id"]
    assert TRACE_ID_RE.fullmatch(run_trace_id)
    assert TRACE_ID_RE.fullmatch(web_trace_id)
    assert web_trace_id == chat_data["message"]["result"]["metadata"]["trace_id"]


def test_web_chat_message_stores_pipeline_result(tmp_path, monkeypatch):
    _patch_pipeline(tmp_path, monkeypatch)
    client = TestClient(app)
    task = "выведи список активных клиентов"

    chat_id = _create_chat(client)
    response = client.post(
        "/web/api/chats/" + chat_id + "/messages",
        json={"task": task, "max_iterations": 1},
    )
    data = response.json()
    response_sql = data["message"]["result"]["final_sql"]

    chat_path = tmp_path / "web_chats" / (chat_id + ".json")
    chat = json.loads(chat_path.read_text(encoding="utf-8"))
    assistant = chat["messages"][-1]

    assert assistant["role"] == "assistant"
    assert assistant["result"]["final_sql"] == response_sql
    assert assistant["trace_id"] == data["message"]["trace_id"]


def test_web_chat_accepts_codex_reasoning_effort(tmp_path, monkeypatch):
    _patch_pipeline(tmp_path, monkeypatch)
    monkeypatch.delenv("CODEX_GENERATOR_REASONING_EFFORT", raising=False)
    client = TestClient(app)
    chat_id = _create_chat(client)

    data = client.post(
        "/web/api/chats/" + chat_id + "/messages",
        json={
            "task": "выведи список активных клиентов",
            "max_iterations": 1,
            "codex_reasoning_effort": "high",
        },
    ).json()

    assert data["message"]["status"] == "approved"
    assert data["message"]["codex_reasoning_effort"] == "high"
    assert data["message"]["result"]["metadata"]["codex_reasoning_effort_env"] == "high"
    assert "CODEX_GENERATOR_REASONING_EFFORT" not in os.environ


def test_run_accepts_codex_reasoning_effort(tmp_path, monkeypatch):
    _patch_pipeline(tmp_path, monkeypatch)
    monkeypatch.delenv("CODEX_GENERATOR_REASONING_EFFORT", raising=False)
    client = TestClient(app)

    data = client.post(
        "/run",
        json={
            "task": "выведи список активных клиентов",
            "max_iterations": 1,
            "codex_reasoning_effort": "xhigh",
        },
    ).json()

    assert data["metadata"]["codex_reasoning_effort_env"] == "xhigh"
    assert "CODEX_GENERATOR_REASONING_EFFORT" not in os.environ


def _patch_pipeline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WEB_CHAT_DIR", str(tmp_path / "web_chats"))
    monkeypatch.setenv("TRACES_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("SMOKE_FAKE_LLM", "true")
    monkeypatch.setenv("LATENCY_SOFT_SEC", "2")
    monkeypatch.setenv("LATENCY_HARD_SEC", "5")
    monkeypatch.setattr(llm_provider, "validate_current_config", lambda: None)

    calls = {"count": 0}

    def fake_run_pipeline(
        *,
        task_description: str,
        max_iterations: int,
        llm_mode: str | None = None,
        llm_generator_model: str | None = None,
    ) -> FakeResult:
        del max_iterations, llm_mode, llm_generator_model
        calls["count"] += 1
        return FakeResult(
            "traceparity" + str(calls["count"]),
            task_description,
            os.environ.get("CODEX_GENERATOR_REASONING_EFFORT"),
        )

    monkeypatch.setattr(pipeline_service, "run_pipeline", fake_run_pipeline)


def _create_chat(client: TestClient) -> str:
    response = client.post("/web/api/chats", json={})
    assert response.status_code == 200
    return str(response.json()["summary"]["chat_id"])
