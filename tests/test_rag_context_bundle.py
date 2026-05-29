"""Unit tests for generation RAG source diagnostics."""

from __future__ import annotations

import pytest

from app import rag_adapter


@pytest.fixture(autouse=True)
def _clear_context_cache() -> None:
    rag_adapter.get_generation_context.cache_clear()
    yield
    rag_adapter.get_generation_context.cache_clear()


def _fake_v2_hit() -> dict:
    return {
        "id": 1,
        "text": "business table",
        "score": 0.91,
        "metadata": {
            "table_name": "corp_tech_application",
            "business_domain": "applications",
            "entity_role": "application_fact",
            "related_tables": "corp_tech_contract",
            "approved_joins": "corp_tech_application.contract_id = corp_tech_contract.id",
            "sensitive_columns": "",
        },
    }


def test_generation_context_uses_v2_block_from_mock_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLE_KNOWLEDGE_V2_ENABLED", "true")
    monkeypatch.setattr(
        rag_adapter.rag_tools,
        "get_generation_context",
        lambda task: "Таблица: sys_employee\nОписание: legacy context",
    )

    def fake_search(task: str, top_k: int = rag_adapter.TABLE_KNOWLEDGE_V2_TOP_K):
        meta = rag_adapter._bench_dsn_meta("table_knowledge_v2", enabled=True)
        meta["hit_count"] = 1
        meta["top_hits"] = [{"table_name": "corp_tech_application", "score": 0.91}]
        return [_fake_v2_hit()], meta

    monkeypatch.setattr(rag_adapter, "_table_knowledge_v2_search_with_meta", fake_search)

    text = rag_adapter.get_generation_context("Выбери все заявки")

    assert text.startswith("=== BUSINESS TABLES (v2) ===")
    assert "corp_tech_application" in text
    assert len(text) <= rag_adapter.GENERATION_MAX_CHARS


def test_v2_block_survives_generation_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLE_KNOWLEDGE_V2_ENABLED", "true")
    monkeypatch.setattr(rag_adapter, "GENERATION_MAX_CHARS", 260)
    monkeypatch.setattr(
        rag_adapter.rag_tools,
        "get_generation_context",
        lambda task: "Таблица: sys_employee\n" + ("legacy paragraph\n\n" * 80),
    )

    def fake_search(task: str, top_k: int = rag_adapter.TABLE_KNOWLEDGE_V2_TOP_K):
        meta = rag_adapter._bench_dsn_meta("table_knowledge_v2", enabled=True)
        meta["hit_count"] = 1
        meta["top_hits"] = [{"table_name": "corp_tech_application", "score": 0.91}]
        return [_fake_v2_hit()], meta

    monkeypatch.setattr(rag_adapter, "_table_knowledge_v2_search_with_meta", fake_search)

    text = rag_adapter.get_generation_context("Выбери все заявки")

    assert "=== BUSINESS TABLES (v2) ===" in text
    assert "corp_tech_application" in text
    assert len(text) <= 260


def test_v2_db_error_returns_degraded_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLE_KNOWLEDGE_V2_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_DSN", "postgresql://bench:secret@127.0.0.1:1/bench")

    text, meta = rag_adapter.get_table_knowledge_v2_context_with_meta("test")

    assert text == ""
    assert meta["enabled"] is True
    assert meta["hit_count"] == 0
    assert meta["fallback_used"] is True
    assert meta["fallback_reason"] in {"db_error", "psycopg2_import_error"}
    assert "secret" not in str(meta.get("error"))


def test_schema_link_reads_tables_from_v2_block() -> None:
    context = "=== BUSINESS TABLES (v2) ===\n- sys_employee (hr, role=employee_dim)"

    link = rag_adapter.schema_link("покажи сотрудников", context)

    assert link["allowed_tables"]
    assert link["allowed_tables"][0] == "sys_employee"


def test_schema_link_keeps_v2_table_before_legacy_context() -> None:
    context = (
        "=== BUSINESS TABLES (v2) ===\n"
        "- sys_employee (hr, role=employee_dim)\n\n"
        "Таблица: offices_psb\nОписание: legacy office hit"
    )

    link = rag_adapter.schema_link("покажи сотрудников", context)

    assert link["allowed_tables"][:2] == ["sys_employee", "offices_psb"]


def test_generation_context_adds_type_hints_for_status_and_fk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLE_KNOWLEDGE_V2_ENABLED", "false")
    monkeypatch.setattr(
        rag_adapter.rag_tools,
        "get_generation_context",
        lambda task: "Таблица: scp_application\nОписание: legacy context",
    )

    text = rag_adapter.get_generation_context("Покажи заявки клиентов по сегменту и статусу")

    assert "Type hints:" in text
    assert "status smallint" in text
    assert "initiator_id bigint (FK->sys_company.id)" in text
    assert "scp_business_segment bigint (FK->business_segment.id)" in text


def test_generation_context_adds_vetted_join_recipe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLE_KNOWLEDGE_V2_ENABLED", "false")
    monkeypatch.setattr(
        rag_adapter.rag_tools,
        "get_generation_context",
        lambda task: "Таблица: scp_application\nОписание: legacy context",
    )

    text = rag_adapter.get_generation_context("Топ клиентов по сумме кредитов и сегменту")

    assert "=== JOIN RECIPES (vetted short cards) ===" in text
    assert "top clients by amount + segment" in text
    assert "credit_contract.link_customer_id = sys_company.id" in text
