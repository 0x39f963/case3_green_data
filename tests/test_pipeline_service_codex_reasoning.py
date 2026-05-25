from __future__ import annotations

import asyncio
import os
from typing import Any

from app import llm_provider, pipeline_service


class FakeResult:
    def __init__(self) -> None:
        self.final_sql = "SELECT 1;"
        self.approved = True
        self.iterations_used = 1
        self.iterations_log: list[dict[str, Any]] = []
        self.audit_log = "approved"
        self.metadata = {
            "trace_id": "codex_reasoning_test",
            "decision": "approve",
            "codex_reasoning_effort_env": os.environ.get("CODEX_GENERATOR_REASONING_EFFORT"),
        }


def test_execute_run_accepts_codex_reasoning_effort(monkeypatch) -> None:
    monkeypatch.delenv("CODEX_GENERATOR_REASONING_EFFORT", raising=False)
    monkeypatch.setenv("LATENCY_SOFT_SEC", "2")
    monkeypatch.setenv("LATENCY_HARD_SEC", "5")
    monkeypatch.setattr(llm_provider, "validate_current_config", lambda: None)

    def fake_run_pipeline(
        *,
        task_description: str,
        max_iterations: int,
        llm_mode: str | None = None,
        llm_generator_model: str | None = None,
    ) -> FakeResult:
        del task_description, max_iterations, llm_mode, llm_generator_model
        return FakeResult()

    monkeypatch.setattr(pipeline_service, "run_pipeline", fake_run_pipeline)

    result = asyncio.run(
        pipeline_service.execute_run(
            task="contract smoke",
            max_iterations=1,
            codex_reasoning_effort=" high ",
        )
    )

    assert result["metadata"]["codex_reasoning_effort_env"] == "high"
    assert "CODEX_GENERATOR_REASONING_EFFORT" not in os.environ
