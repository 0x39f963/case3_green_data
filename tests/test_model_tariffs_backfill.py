from __future__ import annotations

from benchmark_service import ingest


def test_tariff_key_aliases_cover_cli_and_local_models():
    assert "codex-cli-gpt-5-5" in ingest._tariff_keys("codex_cli", "gpt-5.5")
    assert "claude-cli-sonnet" in ingest._tariff_keys("claude_cli", "claude-sonnet-4-6")
    assert "local-qwen3-5-9b" in ingest._tariff_keys("local_ollama", "qwen3.5:9b")
    assert "local-qwen3-5-9b" in ingest._tariff_keys("local_openai", "qwen3.5:9b")


def test_backfill_cost_uses_tariff(monkeypatch):
    row = {
        "backend": "codex_cli",
        "model": "gpt-5.5",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 1500,
        "cost_usd": None,
    }
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
    ingest._backfill_cost(row)
    assert row["cost_source"] == "tariff_backfill"
    assert row["cost_usd"] == 0.020
