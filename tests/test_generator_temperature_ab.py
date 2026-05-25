from __future__ import annotations

from app import generator as generator_module
from app.llm_provider import LLMResponse


class FakeClient:
    backend = "openrouter"
    model = "fake"

    def __init__(self) -> None:
        self.calls: list[float | None] = []

    def invoke(self, system: str, user: str, temperature: float | None = None) -> LLMResponse:
        del system, user
        self.calls.append(temperature)
        return LLMResponse(
            text="SELECT " + str(len(self.calls)),
            model=self.model,
            backend=self.backend,
            raw={"id": "fake-" + str(len(self.calls))},
            walltime_sec=0.01,
        )


def test_temperature_ab_schedule_uses_per_candidate_values(monkeypatch) -> None:
    monkeypatch.setenv("LLM_GENERATOR_TEMPERATURES", "0.5,0.8")
    temperatures, error = generator_module._generator_temperatures(multi=True)
    assert error is None
    assert temperatures == [0.5, 0.8]

    client = FakeClient()
    generator_module._generate_candidates(client, "system", "user", temperatures, parallel=False)
    assert client.calls == [0.5, 0.8]
