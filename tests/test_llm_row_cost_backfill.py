from __future__ import annotations

from benchmark_service import ingest


def _row(usage: dict, backend: str = "codex_cli", model: str = "gpt-5.5") -> dict:
    return ingest._llm_row(
        trace_id="trace_1",
        node="generate",
        iteration=1,
        role="generator",
        backend=backend,
        provider="",
        model=model,
        generation_id="gen_1",
        prompt_type="generator_system",
        prompt_id="file:generator_system.txt",
        prompt_version=None,
        prompt_sha256="sha",
        prompt_chars=10,
        response_chars=20,
        usage=usage,
        latency_ms=100,
    )


def test_llm_row_backfills_cli_cost(monkeypatch):
    monkeypatch.setattr(
        ingest,
        "_find_tariff",
        lambda _: {
            "price_per_1k_in": 0.005,
            "price_per_1k_out": 0.030,
            "price_per_1k_cached": 0,
            "price_per_1k_reasoning": 0,
        },
    )
    row = _row({"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500})
    assert row["cost_source"] == "tariff_backfill"
    assert row["cost_usd"] == 0.020


def test_llm_row_keeps_inline_provider_cost(monkeypatch):
    monkeypatch.setattr(ingest, "_find_tariff", lambda _: None)
    row = _row(
        {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20, "cost_usd": 0.123456},
        backend="openrouter",
        model="google/gemini-2.5-pro",
    )
    assert row["cost_source"] == "provider_inline"
    assert row["cost_usd"] == 0.123456


def test_llm_row_marks_missing_tariff(monkeypatch):
    monkeypatch.setattr(ingest, "_find_tariff", lambda _: None)
    row = _row({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
    assert row["cost_source"] == "missing_tariff"
    assert row["cost_usd"] is None


def test_llm_row_marks_missing_usage(monkeypatch):
    monkeypatch.setattr(ingest, "_find_tariff", lambda _: None)
    row = _row({}, backend="local_ollama", model="qwen3.5:9b")
    assert row["cost_source"] == "missing_usage"
    assert row["cost_usd"] == 0.0


def test_step_returns_insertable_row():
    row = ingest._step(
        "trace_1",
        3,
        {
            "node": "retrieve",
            "started_at": "2026-05-21T20:00:00Z",
            "duration_sec": 0.25,
            "inputs": {"iteration": 1},
            "outputs": {"ok": True},
            "details": {"isolation_mode": "clean"},
        },
    )
    assert row["trace_id"] == "trace_1"
    assert row["step_index"] == 3
    assert row["node"] == "retrieve"
    assert row["details_summary_jsonb"]["isolation_mode"] == "clean"
