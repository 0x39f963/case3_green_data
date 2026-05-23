from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import generator as generator_module
from app import llm_provider, prompt_registry, rag_adapter
from app.api import app
from app.auditor import SecurityAuditor
from app.llm_provider import LLMResponse


class FakeGeneratorClient:
    backend = "openrouter"
    model = "fake-generator"

    def invoke(self, system: str, user: str, temperature: float | None = None) -> LLMResponse:
        del system, user, temperature
        return LLMResponse(
            text="SELECT id FROM sys_employee ORDER BY id LIMIT 100",
            model=self.model,
            backend=self.backend,
            raw={"id": "gen-1"},
            walltime_sec=0.01,
        )


class FakeAuditorClient:
    backend = "openrouter"
    model = "fake-auditor"

    def invoke(self, system: str, user: str, response_format: dict | None = None) -> LLMResponse:
        del system, user, response_format
        return LLMResponse(
            text='{"vulnerabilities":[],"overall_risk_score":0,"summary":"ok"}',
            model=self.model,
            backend=self.backend,
            raw={"id": "aud-1"},
            walltime_sec=0.01,
        )


def test_prompt_registry_file_fallback_when_db_not_configured(monkeypatch, tmp_path: Path) -> None:
    prompt_file = tmp_path / "generator_system.txt"
    prompt_file.write_text("system prompt", encoding="utf-8")
    monkeypatch.setattr(prompt_registry, "PROMPTS_DIR", tmp_path)
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    record = prompt_registry.get_default_prompt("generator_system")

    assert record.id == "file:generator_system.txt"
    assert record.version is None
    assert record.meta["prompt_source"] == "file"
    assert record.meta["fallback_reason"] == "db_not_configured"
    assert record.text_sha256 == prompt_registry.sha256_text("system prompt")


def test_prompts_registry_compat_get_default(monkeypatch, tmp_path: Path) -> None:
    from app.prompts import registry

    prompt_file = tmp_path / "semantic_judge_system.txt"
    prompt_file.write_text("semantic judge", encoding="utf-8")
    monkeypatch.setattr(prompt_registry, "PROMPTS_DIR", tmp_path)
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    item = registry.get_default("semantic_judge_system")

    assert item["prompt_id"] == "file:semantic_judge_system.txt"
    assert item["version"] is None
    assert item["text"] == "semantic judge"
    assert item["sha256"] == prompt_registry.sha256_text("semantic judge")


def test_seed_defaults_dry_run_reads_system_prompt_files(monkeypatch, tmp_path: Path) -> None:
    for name in prompt_registry.PROMPT_FILES.values():
        (tmp_path / name).write_text("text for " + name, encoding="utf-8")
    monkeypatch.setattr(prompt_registry, "PROMPTS_DIR", tmp_path)

    rows = prompt_registry.seed_defaults(dry_run=True)

    assert len(rows) == len(prompt_registry.PROMPT_FILES)
    assert rows[0]["version"] == prompt_registry.DEFAULT_SEED_VERSION
    assert rows[0]["status"] == "active"
    assert rows[0]["is_default"] is True
    assert {row["seed_status"] for row in rows} == {"dry_run"}


def test_generator_last_call_contains_prompt_meta(monkeypatch) -> None:
    monkeypatch.setenv("PROMPT_REGISTRY_DISABLE_DB", "true")
    monkeypatch.setenv("LLM_MULTI_CANDIDATE", "false")
    monkeypatch.setattr(llm_provider, "get_llm", lambda role: FakeGeneratorClient())

    gen = generator_module.SQLGenerator(db_schema={})
    gen.generate("Покажи сотрудников", generation_context="sys_employee(id)", allowed_objects="sys_employee(id)")

    meta = gen.last_call["prompt_meta"]
    assert meta["prompt_type"] == "generator_system"
    assert meta["prompt_source"] == "file"
    assert gen.last_call["prompt_id"] == meta["prompt_id"]
    assert gen.last_call["candidates"][0]["prompt_sha256"] == meta["prompt_sha256"]


def test_auditor_last_call_contains_prompt_meta(monkeypatch) -> None:
    monkeypatch.setenv("PROMPT_REGISTRY_DISABLE_DB", "true")
    monkeypatch.setenv("STAGE_3_ENABLED", "false")
    monkeypatch.setattr(llm_provider, "get_llm", lambda role: FakeAuditorClient())
    monkeypatch.setattr(rag_adapter, "get_sensitive_fields", lambda: {})
    monkeypatch.setattr(rag_adapter, "get_security_context_timed", lambda sql: ("", {"elapsed_sec": 0}))
    monkeypatch.setattr(rag_adapter, "get_security_hits_timed", lambda sql: ([], {"elapsed_sec": 0}))

    auditor = SecurityAuditor()
    auditor.audit(
        "SELECT id FROM sys_employee ORDER BY id LIMIT 100",
        task="Покажи сотрудников",
        schema_context="sys_employee(id)",
        allowed_tables=["sys_employee"],
        allowed_columns={"sys_employee": ["id"]},
    )

    meta = auditor.last_call["prompt_meta"]
    assert meta["prompt_type"] == "auditor_system"
    assert auditor.last_call["llm_call"]["prompt_id"] == meta["prompt_id"]


def test_system_prompt_api_list_and_clone(monkeypatch) -> None:
    items = [
        {
            "id": "generator_system_v1",
            "prompt_type": "generator_system",
            "version": 1,
            "name": "Generator",
            "status": "active",
            "is_default": True,
            "text_sha256": "abc",
        }
    ]

    monkeypatch.setattr(prompt_registry, "list_prompts", lambda prompt_type=None: items)
    monkeypatch.setattr(prompt_registry, "clone_prompt", lambda prompt_id, created_by=None: {
        "id": "generator_system_v2",
        "prompt_type": "generator_system",
        "version": 2,
        "name": "Generator draft",
        "status": "draft",
        "is_default": False,
        "text": "prompt",
    })

    client = TestClient(app)
    list_response = client.get("/web/api/system-prompts")
    clone_response = client.post("/web/api/system-prompts/generator_system_v1/clone", json={})

    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["id"] == "generator_system_v1"
    assert clone_response.status_code == 200
    assert clone_response.json()["prompt"]["status"] == "draft"


def test_system_prompt_api_save_as_default_creates_new_version(monkeypatch) -> None:
    captured = {}

    def save_as_default(prompt_id: str, *, name=None, text=None, notes=None, created_by=None) -> dict:
        captured.update(
            {
                "prompt_id": prompt_id,
                "name": name,
                "text": text,
                "notes": notes,
                "created_by": created_by,
            }
        )
        return {
            "id": "generator_system_v2",
            "prompt_type": "generator_system",
            "version": 2,
            "name": name,
            "status": "active",
            "is_default": True,
            "text": text,
            "notes": notes,
        }

    monkeypatch.setattr(prompt_registry, "save_as_default_version", save_as_default)

    client = TestClient(app)
    response = client.post(
        "/web/api/system-prompts/generator_system_v1/save-as-default",
        json={"name": "Generator v2", "text": "new prompt text", "notes": "trial"},
    )

    assert response.status_code == 200
    assert response.json()["prompt"]["id"] == "generator_system_v2"
    assert response.json()["prompt"]["is_default"] is True
    assert captured["prompt_id"] == "generator_system_v1"
    assert captured["text"] == "new prompt text"


def test_system_prompt_api_conflict_maps_to_409(monkeypatch) -> None:
    def fail_archive(prompt_id: str) -> dict:
        del prompt_id
        raise prompt_registry.PromptConflict("default prompt cannot be archived")

    monkeypatch.setattr(prompt_registry, "archive_prompt", fail_archive)

    client = TestClient(app)
    response = client.post("/web/api/system-prompts/generator_system_v1/archive")

    assert response.status_code == 409
    assert "default prompt" in response.json()["detail"]
