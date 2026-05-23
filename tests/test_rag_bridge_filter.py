"""Tests for the bridge_multiselect filter in RAG retrieval."""

from __future__ import annotations

import pytest

from app import rag_adapter


@pytest.fixture(autouse=True)
def _reset_caches():
    rag_adapter._entity_role_map_reset_for_tests()
    rag_adapter.get_generation_context.cache_clear()
    rag_adapter.get_generation_hits.cache_clear()
    yield
    rag_adapter._entity_role_map_reset_for_tests()
    rag_adapter.get_generation_context.cache_clear()
    rag_adapter.get_generation_hits.cache_clear()


def test_entity_role_map_loads_from_csv():
    mapping = rag_adapter._entity_role_map()
    assert mapping, "entity_role map should not be empty when CSV exists"
    assert mapping.get("ms_0n8ohjyx7oszo6a47ca9g0s6f") == "bridge_multiselect"
    assert mapping.get("corp_tech_application") == "application_fact"


def test_is_bridge_table_true_for_ms_multiselect():
    assert rag_adapter.is_bridge_table("ms_0n8ohjyx7oszo6a47ca9g0s6f") is True


def test_is_bridge_table_false_for_corp_tech_application():
    assert rag_adapter.is_bridge_table("corp_tech_application") is False


def test_is_bridge_table_false_for_empty():
    assert rag_adapter.is_bridge_table("") is False
    assert rag_adapter.is_bridge_table("   ") is False


def test_is_bridge_table_false_for_unknown_table():
    # Не из нашего индекса — не bridge, не trust-default'им
    assert rag_adapter.is_bridge_table("totally_unknown_table_xyz") is False


def test_bridge_blacklist_default_contains_multiselect(monkeypatch):
    monkeypatch.delenv("RAG_BRIDGE_BLACKLIST_ROLES", raising=False)
    assert "bridge_multiselect" in rag_adapter._bridge_blacklist()


def test_bridge_blacklist_env_override(monkeypatch):
    monkeypatch.setenv("RAG_BRIDGE_BLACKLIST_ROLES", "bridge_multiselect,bridge_fact")
    blacklist = rag_adapter._bridge_blacklist()
    assert blacklist == {"bridge_multiselect", "bridge_fact"}


def test_strip_bridge_table_blocks_removes_ms_block():
    raw = (
        "Таблица: corp_tech_application\n"
        "Описание: Заявка на корп. тех.\n"
        "Колонки: id, status\n"
        "\n"
        "Таблица: ms_0n8ohjyx7oszo6a47ca9g0s6f\n"
        "Описание: MultiSelect container: SCP_STATE_ID_MS. ObjType: КТ. Заявка\n"
        "Колонки: id, obj_id\n"
        "\n"
        "Таблица: corp_tech_contract\n"
        "Описание: Договор\n"
        "Колонки: id, contract_date\n"
    )
    out = rag_adapter.strip_bridge_table_blocks(raw)
    assert "ms_0n8ohjyx7oszo6a47ca9g0s6f" not in out
    assert "corp_tech_application" in out
    assert "corp_tech_contract" in out


def test_strip_bridge_table_blocks_preserves_prefix():
    raw = "Prefix text without headers.\nТаблица: ms_0n8ohjyx7oszo6a47ca9g0s6f\nrest"
    out = rag_adapter.strip_bridge_table_blocks(raw)
    assert out.startswith("Prefix text without headers.")
    assert "ms_0n8ohjyx7oszo6a47ca9g0s6f" not in out


def test_strip_bridge_table_blocks_noop_when_no_headers():
    raw = "No structured headers here, just free text."
    assert rag_adapter.strip_bridge_table_blocks(raw) == raw


def test_filter_bridge_hits_drops_ms_tables():
    hits = [
        {"table_name": "corp_tech_application", "score": 0.91, "text": "..."},
        {"table_name": "ms_0n8ohjyx7oszo6a47ca9g0s6f", "score": 0.88, "text": "..."},
        {"table_name": "corp_tech_contract", "score": 0.75, "text": "..."},
    ]
    filtered = rag_adapter._filter_bridge_hits(hits)
    names = [hit["table_name"] for hit in filtered]
    assert names == ["corp_tech_application", "corp_tech_contract"]


def test_get_generation_hits_filters_bridges(monkeypatch):
    """End-to-end: monkeypatch FAISS to return a bridge hit, verify it's dropped."""

    def fake_search(task, index, metadata, top_k):
        return [
            {"table_name": "corp_tech_application", "score": 0.92, "text": "приложение"},
            {"table_name": "ms_0n8ohjyx7oszo6a47ca9g0s6f", "score": 0.85, "text": "multiselect"},
            {"table_name": "corp_tech_contract", "score": 0.81, "text": "договор"},
            {"table_name": "ms_0golbfqyrdq4im6jf6ajivwy9", "score": 0.78, "text": "multiselect 2"},
        ]

    def fake_load():
        return ("index_stub", [])

    monkeypatch.setattr(rag_adapter.rag_tools, "_search", fake_search)
    monkeypatch.setattr(rag_adapter.rag_tools, "_load_generation_index", fake_load)
    monkeypatch.setattr(rag_adapter.rag_tools, "DEFAULT_TOP_K_GENERATION", 4, raising=False)
    rag_adapter.get_generation_hits.cache_clear()

    hits = rag_adapter.get_generation_hits("Выбери все заявки")
    names = [hit.get("table_name") for hit in hits]
    assert "ms_0n8ohjyx7oszo6a47ca9g0s6f" not in names
    assert "ms_0golbfqyrdq4im6jf6ajivwy9" not in names
    assert "corp_tech_application" in names
    assert "corp_tech_contract" in names
