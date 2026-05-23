import json
import os
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

pytestmark = pytest.mark.skipif(
    not os.environ.get("BENCHMARK_DSN") and not os.environ.get("RUN_BENCHMARK_TESTS"),
    reason="Тесты требуют benchmark Postgres; задайте BENCHMARK_DSN или RUN_BENCHMARK_TESTS=1.",
)

from app import rag_adapter  # noqa: E402


GT_PATH = Path(__file__).parent / "fixtures" / "table_knowledge_v2_gt.json"
LOCAL_BENCH_DSN = "postgresql://bench:bench@127.0.0.1:15434/bench"


@pytest.fixture(autouse=True)
def bench_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    if not os.environ.get("BENCHMARK_DSN"):
        monkeypatch.setenv("BENCHMARK_DSN", LOCAL_BENCH_DSN)


@pytest.fixture()
def gt_fixture() -> dict:
    return json.loads(GT_PATH.read_text(encoding="utf-8"))


def test_table_knowledge_v2_index_exists() -> None:
    """Builder должен залить 60 строк в индекс."""
    with psycopg2.connect(rag_adapter._bench_dsn_for_solutions()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM benchmark.rag_embeddings WHERE index_name = 'table_knowledge_v2'"
            )
            assert cur.fetchone()[0] == 60
            cur.execute(
                """
                SELECT array_length(embedding, 1), metadata->>'table_name'
                FROM benchmark.rag_embeddings
                WHERE index_name = 'table_knowledge_v2'
                LIMIT 1
                """
            )
            dim, table_name = cur.fetchone()
    assert dim == 384
    assert table_name


def test_table_knowledge_v2_hit_rate(gt_fixture: dict) -> None:
    """Hit-rate top-6 >= 0.8 на ground truth fixture."""
    hits = 0
    total = 0
    for item in gt_fixture["queries"]:
        results = rag_adapter._table_knowledge_v2_search(item["task"], top_k=6)
        result_tables = {row["metadata"]["table_name"] for row in results}
        for table in item["expected_top_tables"]:
            total += 1
            if table in result_tables:
                hits += 1

    hit_rate = hits / total
    assert hit_rate >= 0.8, f"hit_rate={hit_rate:.3f} < 0.8"


def test_table_knowledge_v2_no_db_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без БД - пустой список, не падает."""
    monkeypatch.setenv("BENCHMARK_DSN", "postgresql://invalid:invalid@127.0.0.1:1/none")
    results = rag_adapter._table_knowledge_v2_search("test")
    assert results == []


def test_get_generation_context_with_v2_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """С флагом on - блок BUSINESS TABLES (v2) появляется в выводе."""
    monkeypatch.setenv("TABLE_KNOWLEDGE_V2_ENABLED", "true")
    rag_adapter.get_generation_context.cache_clear()
    text = rag_adapter.get_generation_context("список клиентов")
    assert "=== BUSINESS TABLES (v2) ===" in text
    assert len(text) <= rag_adapter.GENERATION_MAX_CHARS


def test_get_generation_context_with_v2_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """С флагом off - блок BUSINESS TABLES (v2) отсутствует."""
    monkeypatch.setenv("TABLE_KNOWLEDGE_V2_ENABLED", "false")
    rag_adapter.get_generation_context.cache_clear()
    text = rag_adapter.get_generation_context("список клиентов")
    assert "=== BUSINESS TABLES (v2) ===" not in text
    assert len(text) <= rag_adapter.GENERATION_MAX_CHARS
