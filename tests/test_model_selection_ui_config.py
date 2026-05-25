from __future__ import annotations

from fastapi.testclient import TestClient

from app import llm_provider
from app.api import app


def _options_by_key():
    return {opt["key"]: opt for opt in llm_provider.list_model_options()}


def test_list_model_options_has_distinct_qwen_entries():
    by_key = _options_by_key()
    assert "or-qwen3-5-9b" in by_key
    assert "or-qwen3-235b-a22b-2507" in by_key
    assert "or-qwen3-32b" in by_key
    assert "local-qwen3-5-9b" in by_key
    assert by_key["or-qwen3-5-9b"]["backend"] == "openrouter"
    assert by_key["or-qwen3-235b-a22b-2507"]["provider_model"] == "qwen/qwen3-235b-a22b-2507"
    assert by_key["or-qwen3-32b"]["provider_model"] == "qwen/qwen3-32b"
    assert by_key["or-qwen3-5-9b"]["llm_mode"] == "prod_demo"
    assert by_key["or-qwen3-5-9b"]["llm_generator_model"] == "qwen3-5-9b"
    assert by_key["local-qwen3-5-9b"]["backend"] == "local_openai"
    assert by_key["local-qwen3-5-9b"]["llm_mode"] == "local_openai"
    assert by_key["local-qwen3-5-9b"]["llm_generator_model"] == "qwen3-5-9b"


def test_list_model_options_includes_cli_presets_without_generator_model():
    by_key = _options_by_key()
    assert "claude-cli" in by_key
    assert "codex-cli" in by_key
    claude = by_key["claude-cli"]
    codex = by_key["codex-cli"]
    assert claude["llm_mode"] == "claude_cli"
    assert claude["backend"] == "anthropic_cli"
    assert claude["llm_generator_model"] == ""
    assert codex["llm_mode"] == "codex_cli"
    assert codex["backend"] == "codex_cli"
    assert codex["llm_generator_model"] == ""


def test_list_model_options_keys_are_unique():
    options = llm_provider.list_model_options()
    keys = [opt["key"] for opt in options]
    assert len(keys) == len(set(keys)), "duplicate keys in list_model_options"


def test_list_model_options_have_required_fields():
    required = {
        "key", "label", "llm_mode", "llm_generator_model", "backend",
        "provider_model", "description", "available_by_config", "config_hint",
        "supports_tool_mode",
    }
    for opt in llm_provider.list_model_options():
        missing = required - set(opt.keys())
        assert not missing, "missing fields in " + opt.get("key", "?") + ": " + str(missing)


def test_web_config_returns_enriched_options_and_default():
    client = TestClient(app)
    response = client.get("/web/api/config")
    assert response.status_code == 200
    data = response.json()
    models = data.get("models") or []
    assert models, "web config returned empty models list"
    by_key = {m["key"]: m for m in models}
    assert "or-qwen3-5-9b" in by_key
    assert "or-qwen3-235b-a22b-2507" in by_key
    assert "or-qwen3-32b" in by_key
    assert "local-qwen3-5-9b" in by_key
    assert "claude-cli" in by_key
    assert "codex-cli" in by_key
    assert by_key["claude-cli"]["llm_generator_model"] in ("", None)
    assert by_key["codex-cli"]["llm_generator_model"] in ("", None)
    assert by_key["or-qwen3-5-9b"]["backend"] == "openrouter"
    assert by_key["local-qwen3-5-9b"]["backend"] == "local_openai"
    assert data.get("default_model_key")


def test_describe_current_mode_resolves_local_qwen(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "local_openai")
    monkeypatch.setenv("LLM_GENERATOR_MODEL", "qwen3-5-9b")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
    info = llm_provider.describe_current_mode()
    assert info["mode"] == "local_openai"
    assert info["generator_backend"] == "local_openai"
    assert info["generator_model_key"] == "qwen3-5-9b"
    assert info["generator_model"] == "qwen3.5:9b"


def test_describe_current_mode_maps_legacy_local_qwen_key(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "local_openai")
    monkeypatch.setenv("LLM_GENERATOR_MODEL", "qwen/qwen3.5-9b")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
    info = llm_provider.describe_current_mode()
    assert info["mode"] == "local_openai"
    assert info["generator_backend"] == "local_openai"
    assert info["generator_model_key"] == "qwen3-5-9b"


def test_describe_current_mode_resolves_or_qwen(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "prod_demo")
    monkeypatch.setenv("LLM_GENERATOR_MODEL", "qwen3-5-9b")
    info = llm_provider.describe_current_mode()
    assert info["mode"] == "prod_demo"
    assert info["generator_backend"] == "openrouter"
    assert info["generator_model_key"] == "qwen3-5-9b"


def test_describe_current_mode_resolves_or_qwen_235b(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "prod_demo")
    monkeypatch.setenv("LLM_GENERATOR_MODEL", "qwen3-235b-a22b-2507")
    info = llm_provider.describe_current_mode()
    assert info["mode"] == "prod_demo"
    assert info["generator_backend"] == "openrouter"
    assert info["generator_model_key"] == "qwen3-235b-a22b-2507"
    assert info["generator_model"] == "qwen/qwen3-235b-a22b-2507"


def test_describe_current_mode_resolves_provider_model_id_for_local(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "local_openai")
    monkeypatch.setenv("LLM_GENERATOR_MODEL", "qwen/qwen3.5-9b")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
    info = llm_provider.describe_current_mode()
    assert info["mode"] == "local_openai"
    assert info["generator_model_key"] == "qwen3-5-9b"


def test_local_mode_ignores_openrouter_auditor_env(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "local_openai")
    monkeypatch.setenv("LLM_GENERATOR_MODEL", "qwen3-5-9b")
    monkeypatch.setenv("LLM_MODEL_AUDITOR", "openai/gpt-4o-mini")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
    info = llm_provider.describe_current_mode()
    assert info["auditor_backend"] == "local_openai"
    assert info["auditor_model"] == "qwen3.5:9b"


def test_cli_mode_ignores_openrouter_auditor_env(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "codex_cli")
    monkeypatch.setenv("LLM_MODEL_AUDITOR", "openai/gpt-4o-mini")
    monkeypatch.delenv("LLM_GENERATOR_MODEL", raising=False)
    info = llm_provider.describe_current_mode()
    assert info["auditor_backend"] == "codex_cli"
    assert info["auditor_model"] == "gpt-5.5"


def test_describe_current_mode_handles_claude_cli(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "claude_cli")
    monkeypatch.delenv("LLM_GENERATOR_MODEL", raising=False)
    info = llm_provider.describe_current_mode()
    assert info["mode"] == "claude_cli"
    assert info["generator_backend"] == "anthropic_cli"
    assert info["auditor_backend"] == "anthropic_cli"


def test_describe_current_mode_handles_codex_cli(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "codex_cli")
    monkeypatch.delenv("LLM_GENERATOR_MODEL", raising=False)
    info = llm_provider.describe_current_mode()
    assert info["mode"] == "codex_cli"
    assert info["generator_backend"] == "codex_cli"
    assert info["auditor_backend"] == "codex_cli"


def test_telegram_load_models_accepts_new_presets():
    from app import telegram_bot

    data = telegram_bot.load_models()
    keys = {item["key"] for item in data["models"]}
    assert "local-qwen3-5-9b" in keys
    assert "or-qwen3-5-9b" in keys
    assert "claude-cli" in keys
    assert "codex-cli" in keys
    by_key = data["models_by_key"]
    assert by_key["claude-cli"]["llm_generator_model"] == ""
    assert by_key["codex-cli"]["llm_generator_model"] == ""


def test_chat_messages_payload_for_local_qwen(monkeypatch):
    """UI sends llm_mode=local_openai with key qwen3-5-9b for local Qwen preset."""
    captured = {}

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return {
            "approved": True,
            "final_sql": "SELECT 1;",
            "iterations_used": 1,
            "metadata": {"trace_id": "t", "duration_sec": 0.1, "generator_model": "qwen3.5:9b"},
        }

    from app import web_chat

    monkeypatch.setattr(web_chat, "execute_run", fake_execute)
    client = TestClient(app)
    create = client.post("/web/api/chats", json={"source": "test"})
    assert create.status_code == 200
    chat_id = create.json()["chat"]["chat_id"]
    response = client.post(
        f"/web/api/chats/{chat_id}/messages",
        json={
            "task": "show active clients",
            "llm_mode": "local_openai",
            "llm_generator_model": "qwen3-5-9b",
            "max_iterations": 5,
        },
    )
    assert response.status_code == 200
    assert captured["llm_mode"] == "local_openai"
    assert captured["llm_generator_model"] == "qwen3-5-9b"


def test_chat_messages_payload_for_claude_cli(monkeypatch):
    """UI sends llm_mode=claude_cli with llm_generator_model=None."""
    captured = {}

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return {
            "approved": True,
            "final_sql": "SELECT 1;",
            "iterations_used": 1,
            "metadata": {"trace_id": "t2", "duration_sec": 0.1, "generator_model": "claude-sonnet-4-6"},
        }

    from app import web_chat

    monkeypatch.setattr(web_chat, "execute_run", fake_execute)
    client = TestClient(app)
    create = client.post("/web/api/chats", json={"source": "test"})
    chat_id = create.json()["chat"]["chat_id"]
    response = client.post(
        f"/web/api/chats/{chat_id}/messages",
        json={
            "task": "audit task",
            "llm_mode": "claude_cli",
            "llm_generator_model": None,
            "max_iterations": 5,
        },
    )
    assert response.status_code == 200
    assert captured["llm_mode"] == "claude_cli"
    assert captured["llm_generator_model"] is None
