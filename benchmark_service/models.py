from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RunPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    trace_id: str = Field(..., min_length=1, max_length=128)
    benchmark_run_id: str = Field(..., min_length=1, max_length=128)
    dataset_id: str = Field(..., min_length=1, max_length=128)
    dataset_version: str = Field(..., min_length=1, max_length=64)
    case_id: str = Field(..., min_length=1, max_length=128)
    model_key: str = Field(..., min_length=1, max_length=128)
    llm_mode: str = Field(..., min_length=1, max_length=128)
    system_result: dict[str, Any]
    trace: dict[str, Any]
    report_data: dict[str, Any] | None = None
    client_meta: dict[str, Any] = Field(default_factory=dict)


class BenchmarkRunRegister(BaseModel):
    benchmark_run_id: str = Field(..., min_length=1, max_length=128)
    dataset_id: str = Field(..., min_length=1, max_length=128)
    dataset_version: str = Field(..., min_length=1, max_length=64)
    started_at: str | None = None
    finished_at: str | None = None
    model_matrix: list[str] = Field(default_factory=list)
    config_jsonb: dict[str, Any] = Field(default_factory=dict)
    total_cases: int | None = None
    completed_cases: int | None = None
    status: str = "registered"
    isolation_mode: Literal["clean", "production", "snapshot"] = "production"
    parent_run_id: str | None = None
    case_ids_filter: list[str] | None = None
    prompt_version_override: str | None = None
    smart_judge_backend: str | None = None
    smart_judge_model: str | None = None
    smart_judge_chunk_size: int | None = None
    smart_judge_workers: int | None = None
    prompt_check_enabled: bool | None = None
    prompt_check_backend: str | None = None
    prompt_check_model: str | None = None
    prompt_check_openrouter_provider: str | None = None
    openrouter_providers: dict[str, str] | None = None


class BenchmarkRunStart(BaseModel):
    model_config = ConfigDict(extra="allow")

    dataset_id: str | None = None
    dataset_path: str | None = None
    schema_path: str | None = None
    dataset_version: str | None = None
    models: list[str] = Field(default_factory=list)
    model_matrix: list[str] = Field(default_factory=list)
    limit: int | None = None
    isolation: Literal["clean", "production", "snapshot"] | None = None
    isolation_mode: Literal["clean", "production", "snapshot"] | None = None
    parent_run_id: str | None = None
    case_ids_filter: list[str] | None = None
    prompt_version_override: str | None = None
    smart_judge_backend: str | None = None
    smart_judge_model: str | None = None
    smart_judge_chunk_size: int | None = None
    smart_judge_workers: int | None = None
    prompt_check_enabled: bool | None = None
    prompt_check_backend: str | None = None
    prompt_check_model: str | None = None
    prompt_check_openrouter_provider: str | None = None
    oracle_enabled: bool = True
    oracle_types: list[str] = Field(default_factory=list)
    oracle_dataset_version: str = "1.1"
    analysis_enabled: bool = True
    analysis_backend: str | None = None
    analysis_model: str | None = None
    analysis_codex_reasoning_effort: str | None = None
    analysis_oracle_required: bool = False
    codex_reasoning_effort: str | None = None
    openrouter_providers: dict[str, str] | None = None
    started_by: str | None = None


class BenchmarkJudgeStart(BaseModel):
    model_config = ConfigDict(extra="allow")

    backend: str = "codex_cli"
    model: str = "gpt-5.5"
    workers: int = 1
    chunk_size: int = 10
    missing_only: bool = True
    limit: int = 0
    fallback_backend: str | None = "claude_cli"
    fallback_model: str | None = "claude-sonnet-4-6"
    codex_reasoning_effort: str | None = None
    status_on_error: str = "runtime_error"
    watch: bool = False
    poll_sec: float = 5.0


class BenchmarkOracleStart(BaseModel):
    model_config = ConfigDict(extra="allow")

    oracle_types: list[str] = Field(default_factory=list)
    limit: int = 0
    missing_only: bool = True
    dataset_version: str = "1.1"
    workers: int = 1
    case_id: list[str] = Field(default_factory=list)
    status_on_error: str = "error"


class BenchmarkAnalysisStart(BaseModel):
    model_config = ConfigDict(extra="allow")

    backend: str = "codex_cli"
    model: str = "gpt-5.5"
    limit: int = 0
    missing_only: bool = True
    oracle_required: bool = False
    status_on_error: str = "runtime_error"
    codex_reasoning_effort: str | None = None
    trace_id: list[str] = Field(default_factory=list)


class AuditReviewPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    review: dict[str, Any]
    step_scores: list[dict[str, Any]] = Field(default_factory=list)
    sql_correctness: dict[str, Any]
    suggestions: list[dict[str, Any]] = Field(default_factory=list)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None
