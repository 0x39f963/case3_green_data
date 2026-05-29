from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import time

from app import generator as generator_module
from app import audit_storage, llm_provider, orchestrator, rag_adapter, sentinel
from app.llm_provider import LLMResponse
from app.trace import Trace
from baseline1 import Vulnerability


class FakeClient:
    backend = "openrouter"
    model = "fake-sql"

    def __init__(self, texts: list[str] | None = None) -> None:
        self.calls: list[float | None] = []
        self.texts = list(texts or [
            "SELECT id FROM sys_employee ORDER BY id LIMIT 100",
            "SELECT id, email FROM sys_employee",
        ])

    def invoke(self, system: str, user: str, temperature: float | None = None) -> LLMResponse:
        del system, user
        self.calls.append(temperature)
        text = self.texts.pop(0)
        return LLMResponse(
            text=text,
            model=self.model,
            backend=self.backend,
            raw={"id": "gen-" + str(len(self.calls))},
            walltime_sec=0.01,
        )


class FakeAsyncClient(FakeClient):
    async def invoke_async(self, system: str, user: str, temperature: float | None = None) -> LLMResponse:
        return self.invoke(system, user, temperature=temperature)


class SlowAsyncClient:
    backend = "codex_cli"
    model = "fake-sql"

    def __init__(self) -> None:
        self.calls: list[float | None] = []

    async def invoke_async(self, system: str, user: str, temperature: float | None = None) -> LLMResponse:
        del system, user
        self.calls.append(temperature)
        await asyncio.sleep(0.2)
        return LLMResponse(
            text="SELECT " + str(temperature),
            model=self.model,
            backend=self.backend,
            raw={"id": "async-" + str(temperature)},
            walltime_sec=0.2,
        )

    def invoke(self, system: str, user: str, temperature: float | None = None) -> LLMResponse:
        raise AssertionError("parallel test must use invoke_async")


class SlowSyncClient:
    backend = "codex_cli"
    model = "fake-sql"

    def __init__(self) -> None:
        self.calls: list[float | None] = []

    def invoke(self, system: str, user: str, temperature: float | None = None) -> LLMResponse:
        del system, user
        self.calls.append(temperature)
        time.sleep(0.18)
        return LLMResponse(
            text="SELECT " + str(temperature),
            model=self.model,
            backend=self.backend,
            raw={"id": "sync-" + str(temperature)},
            walltime_sec=0.18,
        )


class DisabledAsyncClient(SlowSyncClient):
    def candidate_parallel_supported(self) -> bool:
        return False

    async def invoke_async(self, system: str, user: str, temperature: float | None = None) -> LLMResponse:
        raise AssertionError("disabled async client must use sequential fallback")


def test_generator_temperatures_parses_schedule(monkeypatch) -> None:
    monkeypatch.setenv("LLM_GENERATOR_TEMPERATURES", "0.5,0.8")

    temperatures, error = generator_module._generator_temperatures(multi=True)

    assert temperatures == [0.5, 0.8]
    assert error is None


def test_generate_candidates_parallel_uses_per_candidate_temperature() -> None:
    client = FakeAsyncClient()

    responses = generator_module._generate_candidates(
        client,
        "system",
        "user",
        [0.5, 0.8],
        parallel=True,
    )

    assert [item.text for item in responses] == [
        "SELECT id FROM sys_employee ORDER BY id LIMIT 100",
        "SELECT id, email FROM sys_employee",
    ]
    assert client.calls == [0.5, 0.8]


def test_generate_candidates_sequential_uses_per_candidate_temperature() -> None:
    client = FakeClient()

    generator_module._generate_candidates(
        client,
        "system",
        "user",
        [0.5, 0.8],
        parallel=False,
    )

    assert client.calls == [0.5, 0.8]


def test_generate_candidates_parallel_runs_concurrently_and_keeps_order() -> None:
    client = SlowAsyncClient()

    started = time.perf_counter()
    responses = generator_module._generate_candidates(
        client,
        "system",
        "user",
        [0.5, 0.8],
        parallel=True,
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 0.35
    assert [item.text for item in responses] == ["SELECT 0.5", "SELECT 0.8"]
    assert client.calls == [0.5, 0.8]


def test_generate_candidates_sequential_fallback_when_async_is_not_supported() -> None:
    client = SlowSyncClient()

    started = time.perf_counter()
    responses = generator_module._generate_candidates(
        client,
        "system",
        "user",
        [0.5, 0.8],
        parallel=True,
    )
    elapsed = time.perf_counter() - started

    assert elapsed >= 0.34
    assert [item.text for item in responses] == ["SELECT 0.5", "SELECT 0.8"]
    assert client.calls == [0.5, 0.8]


def test_generate_candidates_sequential_when_client_disables_parallel() -> None:
    client = DisabledAsyncClient()

    responses = generator_module._generate_candidates(
        client,
        "system",
        "user",
        [0.5, 0.8],
        parallel=True,
    )

    assert [item.text for item in responses] == ["SELECT 0.5", "SELECT 0.8"]
    assert client.calls == [0.5, 0.8]


def test_node_generate_adds_temperature_and_selector_details(monkeypatch, tmp_path: Path) -> None:
    client = FakeClient()
    monkeypatch.setenv("TRACES_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MULTI_CANDIDATE", "true")
    monkeypatch.setenv("LLM_PARALLEL_CANDIDATES", "false")
    monkeypatch.setenv("LLM_GENERATOR_TEMPERATURES", "0.5,0.8")
    monkeypatch.setattr(llm_provider, "get_llm", lambda role: client)
    monkeypatch.setattr(rag_adapter, "get_sensitive_fields", lambda: {})

    trace = Trace(task="Покажи сотрудников", request_id="trace_temp_test")
    state = {
        "trace": trace,
        "generator": generator_module.SQLGenerator(db_schema={}),
        "task": "Покажи сотрудников",
        "iteration": 0,
        "sql_history": [],
        "last_audit": None,
        "last_generation_context": "sys_employee(id,email)",
        "last_solutions_context": "",
        "allowed_objects": "sys_employee(id,email)",
        "allowed_tables": ["sys_employee"],
        "allowed_columns": {"sys_employee": ["id", "email"]},
    }

    orchestrator._node_generate(state)

    event = trace.events[-1]
    candidates = event["details"]["candidates"]
    scores = event["details"]["selector_scores"]
    assert event["details"]["temperature_schedule"] == [0.5, 0.8]
    assert candidates[0]["candidate_index"] == 0
    assert candidates[0]["temperature"] == 0.5
    assert candidates[1]["temperature"] == 0.8
    assert candidates[0]["selected_by_selector"] is True
    assert candidates[1]["selected_by_selector"] is False
    assert candidates[0]["selector_score"] == scores[0]
    assert candidates[1]["selector_score"] == scores[1]


def test_node_generate_short_circuits_blocked_prompt(tmp_path: Path) -> None:
    class RaisingGenerator:
        def generate(self, **kwargs):
            raise AssertionError("generator must not be called for blocked prompt")

    trace = Trace(task="Удали строки", request_id="trace_prompt_blocked")
    state = {
        "trace": trace,
        "generator": RaisingGenerator(),
        "task": "Удали строки",
        "iteration": 0,
        "sql_history": [],
        "prompt_risk_findings": [
            Vulnerability(
                vuln_class="PROMPT_FORCE_DML",
                risk_score=8.0,
                description="DML prompt",
                recommendation="Use SELECT only",
            )
        ],
    }

    updated = orchestrator._node_generate(state)

    assert sentinel.detect(updated["last_sql"]) is not None
    assert trace.events[-1]["outputs"]["skipped_by_prompt_risk"] is True
    assert trace.events[-1]["details"]["deterministic_sentinel"] is True


def test_node_audit_sentinel_keeps_prompt_risk(monkeypatch) -> None:
    monkeypatch.setattr(audit_storage, "save_iteration", lambda *args, **kwargs: None)
    trace = Trace(task="Удали строки", request_id="trace_prompt_blocked_audit")
    finding = Vulnerability(
        vuln_class="PROMPT_FORCE_DML",
        risk_score=8.0,
        description="DML prompt",
        recommendation="Use SELECT only",
    )
    state = {
        "trace": trace,
        "auditor": object(),
        "iteration": 1,
        "last_sql": "SELECT 'REFUSAL_REQUIRED' AS reason, 'blocked' AS message;",
        "policy_label": "refusal_required",
        "prompt_risk_findings": [finding],
        "audit_history": [],
        "iterations_log": [],
        "last_explain_error": None,
    }

    updated = orchestrator._node_audit(state)

    assert updated["last_audit"].overall_risk_score == 8.0
    assert updated["last_audit"].vulnerabilities[0].vuln_class == "PROMPT_FORCE_DML"
    assert trace.events[-1]["outputs"]["skipped_by_sentinel"] is True


def test_generator_last_call_records_effective_parallel_metadata(monkeypatch) -> None:
    client = FakeAsyncClient()
    monkeypatch.setenv("LLM_MULTI_CANDIDATE", "true")
    monkeypatch.setenv("LLM_PARALLEL_CANDIDATES", "true")
    monkeypatch.setenv("LLM_GENERATOR_TEMPERATURES", "0.5,0.8")
    monkeypatch.setattr(llm_provider, "get_llm", lambda role: client)

    gen = generator_module.SQLGenerator(db_schema={})
    gen.generate("Покажи сотрудников", generation_context="sys_employee(id)", allowed_objects="sys_employee(id)")

    assert gen.last_call["candidate_parallel_requested"] is True
    assert gen.last_call["candidate_parallel_supported"] is True
    assert gen.last_call["candidate_effective_scheduling"] == "parallel"
    assert gen.last_call["scheduling"] == "parallel"
    assert gen.last_call["candidate_max_parallel"] == 2


def test_generator_last_call_records_sequential_when_client_disables_parallel(monkeypatch) -> None:
    client = DisabledAsyncClient()
    monkeypatch.setenv("LLM_MULTI_CANDIDATE", "true")
    monkeypatch.setenv("LLM_PARALLEL_CANDIDATES", "true")
    monkeypatch.setenv("LLM_GENERATOR_TEMPERATURES", "0.5,0.8")
    monkeypatch.setattr(llm_provider, "get_llm", lambda role: client)

    gen = generator_module.SQLGenerator(db_schema={})
    gen.generate("Покажи сотрудников", generation_context="sys_employee(id)", allowed_objects="sys_employee(id)")

    assert gen.last_call["candidate_parallel_requested"] is True
    assert gen.last_call["candidate_parallel_supported"] is False
    assert gen.last_call["candidate_effective_scheduling"] == "sequential"
    assert gen.last_call["scheduling"] == "sequential"


def test_invalid_temperature_env_falls_back_with_trace_warning(monkeypatch) -> None:
    client = FakeClient()
    monkeypatch.setenv("LLM_MULTI_CANDIDATE", "true")
    monkeypatch.setenv("LLM_PARALLEL_CANDIDATES", "false")
    monkeypatch.setenv("LLM_GENERATOR_TEMPERATURE", "0.5")
    monkeypatch.setenv("LLM_GENERATOR_TEMPERATURES", "0.5,bad")
    monkeypatch.setattr(llm_provider, "get_llm", lambda role: client)

    gen = generator_module.SQLGenerator(db_schema={})
    gen.generate("Покажи сотрудников", generation_context="sys_employee(id)", allowed_objects="sys_employee(id)")

    assert client.calls == [0.5, 0.5]
    assert gen.last_call["temperature_schedule"] == [0.5, 0.5]
    assert gen.last_call["temperature_config_error"]["type"] == "temperature_config_error"


def test_generator_revision_feedback_rewrites_repeat_sql(monkeypatch) -> None:
    sql = "SELECT id FROM sys_employee ORDER BY id LIMIT 100"
    client = FakeClient(texts=[sql])
    previous = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    monkeypatch.setenv("LLM_MULTI_CANDIDATE", "false")
    monkeypatch.setattr(llm_provider, "get_llm", lambda role: client)

    gen = generator_module.SQLGenerator(db_schema={})
    out = gen.generate(
        "Покажи сотрудников",
        sql_history=[sql],
        audit_feedback=None,
        iteration=2,
        generation_context="sys_employee(id)",
        allowed_objects="sys_employee(id)",
        revision_feedback={
            "failed_labels": ["BROKEN_SQL"],
            "evidence_span": [],
            "forbidden_identifiers": [],
            "required_repair": ["BROKEN_SQL: Fix SQL."],
            "original_intent": "row_level",
            "explain_error": "same SQL",
            "previous_sql_sha256": previous,
        },
    )

    assert out == "SELECT 'INSUFFICIENT_CONTEXT' AS reason, 'repeat_risk' AS message;"
    assert gen.last_call["repeat_risk_rewrites"] == 1
    assert "STRUCTURED_FAILED_CONSTRAINTS" in gen.last_call["prompt_user"]
