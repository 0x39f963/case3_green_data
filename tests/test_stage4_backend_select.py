from __future__ import annotations

from fastapi.testclient import TestClient

from app import llm_provider, prompt_registry, web_chat
from app.api import app
from app.classifier import judge
from app.classifier.types import Finding
from app.llm_provider import LLMResponse


class FakeJudgeClient:
    backend = "openrouter"
    model = "fake-judge"

    def invoke(self, system, user, temperature=None, response_format=None):
        del system, user, temperature, response_format
        return LLMResponse(
            text='{"findings":[]}',
            model=self.model,
            backend=self.backend,
            raw={"id": "judge-1"},
            walltime_sec=0.01,
        )


def _placeholder() -> Finding:
    return Finding(
        label="DIRECT_SENSITIVE",
        severity=0.0,
        confidence=0.0,
        evidence_span="",
        revision_note="Run judge.",
        layer="rule",
        detector="test",
        description="needs_llm_judge",
        recommendation="Run judge.",
    )


def test_judge_backend_options_include_five_choices() -> None:
    options = llm_provider.list_judge_backend_options()
    keys = {item["key"] for item in options}
    assert {
        "openrouter-gemini-3.1-flash",
        "openrouter-gemini-3.1-pro",
        "openrouter-qwen3-235b-a22b-2507",
        "openrouter-qwen3-32b",
        "claude-cli-sonnet",
        "codex-cli-gpt-5.5",
        "codex-spark-medium",
        "codex-spark-high",
        "codex-spark-xhigh",
        "off-conservative-fallback",
    } <= keys
    spark = {item["key"]: item for item in options if item["key"].startswith("codex-spark-")}
    assert spark["codex-spark-medium"]["codex_reasoning_effort"] == "medium"
    assert spark["codex-spark-high"]["codex_reasoning_effort"] == "high"
    assert spark["codex-spark-xhigh"]["codex_reasoning_effort"] == "xhigh"


def test_judge_backend_context_override() -> None:
    with llm_provider.judge_backend_override("codex-cli-gpt-5.5"):
        assert llm_provider.current_judge_backend_key() == "codex-cli-gpt-5.5"


def test_get_judge_llm_builds_direct_client_with_judge_role(monkeypatch) -> None:
    captured = {}

    def fake_build_direct_client(backend, model, role="direct"):
        captured.update({"backend": backend, "model": model, "role": role})
        return FakeJudgeClient()

    monkeypatch.setattr(llm_provider, "_build_direct_client", fake_build_direct_client)
    with llm_provider.judge_backend_override("openrouter-gemini-3.1-flash"):
        llm_provider.get_judge_llm()

    assert captured == {
        "backend": "openrouter",
        "model": "google/gemini-3.1-flash-lite",
        "role": "judge",
    }


def test_stage4_off_returns_needs_review_finding() -> None:
    with llm_provider.judge_backend_override("off"):
        findings = judge.judge_semantic(
            sql="SELECT a.id FROM application_obj a JOIN documents d ON d.app_id = a.id",
            task="Покажи заявки с документами",
            schema_context="",
            sensitive_fields={},
            allowed_tables=["application_obj", "documents"],
            allowed_columns={},
            placeholder_findings=[_placeholder()],
        )

    assert findings[0].label == "NEEDS_HUMAN_REVIEW"
    assert judge.LAST_CALL["judge_decision"] == "needs_review"


def test_stage4_uses_semantic_registry_prompt(monkeypatch) -> None:
    record = prompt_registry.file_prompt("semantic_judge_system", "test")
    monkeypatch.setattr(prompt_registry, "get_default_prompt", lambda prompt_type: record)
    monkeypatch.setattr(llm_provider, "get_judge_llm", lambda: FakeJudgeClient())

    with llm_provider.judge_backend_override("openrouter-gemini-3.1-flash"):
        judge.judge_semantic(
            sql="SELECT id FROM application_obj LIMIT 10",
            task="Покажи заявки",
            schema_context="application_obj(id)",
            sensitive_fields={},
            allowed_tables=["application_obj"],
            allowed_columns={"application_obj": ["id"]},
            placeholder_findings=[_placeholder()],
        )

    assert judge.LAST_CALL["prompt_meta"]["prompt_type"] == "semantic_judge_system"
    assert judge.LAST_CALL["judge_backend"] == "openrouter-gemini-3.1-flash"


def test_web_chat_accepts_judge_backend(monkeypatch) -> None:
    captured = {}

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return {
            "approved": False,
            "final_sql": "",
            "iterations_used": 1,
            "metadata": {"trace_id": "judge_trace_1", "duration_sec": 0.1},
        }

    monkeypatch.setattr(web_chat, "execute_run", fake_execute)
    client = TestClient(app)
    chat_id = client.post("/web/api/chats", json={"source": "test"}).json()["chat"]["chat_id"]

    response = client.post(
        f"/web/api/chats/{chat_id}/messages",
        json={
            "task": "Покажи заявки с документами",
            "llm_mode": "local_openai",
            "llm_generator_model": "qwen3-5-9b",
            "judge_backend": "off-conservative-fallback",
            "max_iterations": 1,
        },
    )

    assert response.status_code == 200
    assert captured["judge_backend"] == "off-conservative-fallback"
