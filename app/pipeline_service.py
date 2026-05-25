from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

from app import llm_provider
from app.llm_provider import LLMConfigError, ProviderUnavailable
from app.orchestrator import SQLSecuritySystem
from app.pipeline import run_sql_security_pipeline as run_pipeline


MAX_ITERATIONS_LIMIT = SQLSecuritySystem.DEFAULT_MAX_ITERATIONS
LATENCY_SOFT_DEFAULT_SEC = 300
LATENCY_HARD_DEFAULT_SEC = 600


class LatencyConfigError(ValueError):
    """Invalid latency env config."""


class PipelineTimeout(RuntimeError):
    """Full pipeline exceeded hard latency budget."""


def serialize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        data = asdict(value)
        for key, raw in getattr(value, "__dict__", {}).items():
            if key not in data:
                data[key] = raw
        return serialize_value(data)
    if isinstance(value, dict):
        return {k: serialize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serialize_value(v) for v in value]
    return repr(value)


def provider_error_message(exc: BaseException) -> str:
    text = str(exc)
    if "provider unavailable" in text.lower():
        return text
    return "LLM provider unavailable: " + text


async def execute_run(
    *,
    task: str,
    llm_mode: str | None = None,
    llm_generator_model: str | None = None,
    openrouter_provider: str | None = None,
    judge_openrouter_provider: str | None = None,
    codex_reasoning_effort: str | None = None,
    judge_backend: str | None = None,
    prompt_check_enabled: bool | None = None,
    prompt_check_backend: str | None = None,
    prompt_check_model: str | None = None,
    prompt_check_openrouter_provider: str | None = None,
    max_iterations: int = MAX_ITERATIONS_LIMIT,
    profile: bool = False,
    isolation_mode: str | None = None,
) -> dict[str, Any]:
    with llm_provider.model_override(
        llm_mode=llm_mode,
        llm_generator_model=llm_generator_model,
        openrouter_provider=openrouter_provider,
    ), llm_provider.judge_backend_override(
        judge_backend,
        openrouter_provider=judge_openrouter_provider,
    ), llm_provider.prompt_check_override(
        enabled=prompt_check_enabled,
        backend=prompt_check_backend,
        model=prompt_check_model,
        openrouter_provider=prompt_check_openrouter_provider,
    ):
        llm_provider.validate_current_config()
        soft_sec, hard_sec = _latency_budget()
        started = time.monotonic()
        prev_profile = os.environ.get("LLM_PROFILE_MODE", "")
        prev_isolation = os.environ.get("PIPELINE_ISOLATION", "")
        prev_codex_reasoning = os.environ.get("CODEX_GENERATOR_REASONING_EFFORT", "")
        codex_effort = (codex_reasoning_effort or "").strip()
        if profile:
            os.environ["LLM_PROFILE_MODE"] = "true"
        if isolation_mode:
            os.environ["PIPELINE_ISOLATION"] = isolation_mode
        if codex_effort:
            os.environ["CODEX_GENERATOR_REASONING_EFFORT"] = codex_effort
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    run_pipeline,
                    task_description=task,
                    max_iterations=max_iterations,
                    llm_mode=llm_mode,
                    llm_generator_model=llm_generator_model,
                ),
                timeout=hard_sec,
            )
        except asyncio.TimeoutError as exc:
            raise PipelineTimeout(
                "Pipeline exceeded hard latency budget "
                + str(hard_sec)
                + " seconds."
            ) from exc
        finally:
            if isolation_mode:
                if prev_isolation:
                    os.environ["PIPELINE_ISOLATION"] = prev_isolation
                else:
                    os.environ.pop("PIPELINE_ISOLATION", None)
            if profile:
                if prev_profile:
                    os.environ["LLM_PROFILE_MODE"] = prev_profile
                else:
                    os.environ.pop("LLM_PROFILE_MODE", None)
            if codex_effort:
                if prev_codex_reasoning:
                    os.environ["CODEX_GENERATOR_REASONING_EFFORT"] = prev_codex_reasoning
                else:
                    os.environ.pop("CODEX_GENERATOR_REASONING_EFFORT", None)

        elapsed = time.monotonic() - started
        if elapsed > soft_sec:
            result.metadata["latency_warning"] = True
            result.metadata["latency_elapsed_sec"] = round(elapsed, 3)
            result.metadata["latency_soft_sec"] = soft_sec
            result.metadata["latency_hard_sec"] = hard_sec
        if profile:
            result.metadata["profile"] = True
    return serialize_value(result.__dict__)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise LatencyConfigError(name + " должен быть целым числом.") from exc
    if value <= 0:
        raise LatencyConfigError(name + " должен быть больше нуля.")
    return value


def _latency_budget() -> tuple[int, int]:
    soft = _env_int("LATENCY_SOFT_SEC", LATENCY_SOFT_DEFAULT_SEC)
    hard = _env_int("LATENCY_HARD_SEC", LATENCY_HARD_DEFAULT_SEC)
    if hard <= soft:
        raise LatencyConfigError("LATENCY_HARD_SEC должен быть больше LATENCY_SOFT_SEC.")
    return soft, hard


__all__ = [
    "LATENCY_HARD_DEFAULT_SEC",
    "LATENCY_SOFT_DEFAULT_SEC",
    "MAX_ITERATIONS_LIMIT",
    "LatencyConfigError",
    "PipelineTimeout",
    "execute_run",
    "provider_error_message",
    "serialize_value",
]
