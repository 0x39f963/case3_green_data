from __future__ import annotations

from app import generator


def test_default_multi_candidate_temperature_schedule(monkeypatch) -> None:
    monkeypatch.delenv("LLM_GENERATOR_TEMPERATURE", raising=False)
    monkeypatch.delenv("LLM_GENERATOR_TEMPERATURES", raising=False)

    temperatures, error = generator._generator_temperatures(multi=True)

    assert temperatures == [0.3, 0.6]
    assert error is None


def test_single_candidate_default_temperature(monkeypatch) -> None:
    monkeypatch.delenv("LLM_GENERATOR_TEMPERATURE", raising=False)

    assert generator._generator_temperature() == 0.3
