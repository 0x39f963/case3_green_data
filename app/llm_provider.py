"""
Единая точка выбора языковой модели для генератора и аудитора.

Внутри держим два уровня настройки. Контур (LLM_MODE) задает общий
сценарий: dev_local, prod_demo, mixed, local_openai. Каждый контур
раскладывает роли по бэкендам. Точечный override LLM_BACKEND_GENERATOR
и LLM_BACKEND_AUDITOR позволяет переопределить роль отдельно.
"""

from __future__ import annotations

import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
import pwd
import socket
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Iterator
import uuid
from urllib import error as url_error
from urllib import parse as url_parse
from urllib import request as url_request

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


class LLMConfigError(RuntimeError):
    """Конфигурация провайдера не позволяет создать клиента.

    Отделена от ValueError, чтобы API-слой мог отдавать на нее
    стабильный HTTP 400 без путаницы с прочими RuntimeError.
    """


class ProviderUnavailable(RuntimeError):
    """Провайдер модели временно недоступен или не уложился в таймаут.

    Используется API-слоем для fail fast HTTP 503. Сюда попадают
    сетевые ошибки, 5xx, 429 и таймауты, а не ошибки выбора модели
    или пустой API-ключ.
    """


_OLLAMA_MODEL_LOCKS: dict[str, threading.Lock] = {}
_OLLAMA_MODEL_LOCKS_GUARD = threading.Lock()


def _ollama_request_keep_alive() -> str | None:
    """Per-request Ollama residency policy; 0s means unload after response."""
    value = (
        os.environ.get("OLLAMA_REQUEST_KEEP_ALIVE")
        or os.environ.get("LOCAL_LLM_KEEP_ALIVE")
        or "0s"
    ).strip()
    if not value or value.lower() in {"inherit", "server"}:
        return None
    return value


def _ollama_model_lock(model: str) -> threading.Lock:
    """Serialize local Ollama calls per model to avoid duplicate runner loads."""
    key = model.strip() or "__default__"
    with _OLLAMA_MODEL_LOCKS_GUARD:
        lock = _OLLAMA_MODEL_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _OLLAMA_MODEL_LOCKS[key] = lock
        return lock


# Бэкенды, которые мы умеем поднимать. local_openai - это любая
# OpenAI-совместимая локальная модель (Ollama, vLLM, LM Studio).
SUPPORTED_BACKENDS = {"openrouter", "local_openai", "anthropic_cli", "codex_cli"}


def _is_qwen_thinking_model(model: str) -> bool:
    """True для qwen3-* моделей с включённым по умолчанию reasoning trace.

    Alibaba отказывает в json_object при enable_thinking=True. Это покрывает
    qwen3-235b/qwen3-32b/qwen3-coder/qwen3-5-9b на OpenRouter, где провайдер
    Alibaba раздаёт их с дефолтным thinking=on.
    """
    if not model:
        return False
    lower = model.lower()
    if "qwen3" not in lower and "qwen-3" not in lower:
        return False
    # Не-thinking варианты явно помечены ":no-thinking" — для них не трогаем.
    if "no-thinking" in lower or "nothinking" in lower:
        return False
    return True
PROVIDER_TO_BACKEND = {"openrouter": "openrouter", "ollama": "local_openai"}

GENERATOR_MODEL_DEFAULT = "qwen3-5-9b"
OPENROUTER_GENERATOR_MODELS: dict[str, dict[str, str | int]] = {
    "qwen3-5-9b": {
        "provider": "openrouter",
        "model_name": "qwen/qwen3.5-9b",
        "context_window": 0,
    },
    "qwen3-coder-30b-a3b": {
        "provider": "openrouter",
        "model_name": "qwen/qwen3-coder-30b-a3b-instruct",
        "context_window": 0,
    },
    "qwen3-235b-a22b-2507": {
        "provider": "openrouter",
        "model_name": "qwen/qwen3-235b-a22b-2507",
        "context_window": 0,
    },
    "qwen3-32b": {
        "provider": "openrouter",
        "model_name": "qwen/qwen3-32b",
        "context_window": 0,
    },
    "llama-3-3-70b-instruct": {
        "provider": "openrouter",
        "model_name": "meta-llama/llama-3.3-70b-instruct",
        "context_window": 0,
    },
    "gpt-5-4-mini": {
        "provider": "openrouter",
        "model_name": "openai/gpt-5.4-mini",
        "context_window": 0,
    },
    "kimi-k2-6": {
        "provider": "openrouter",
        "model_name": "moonshotai/kimi-k2.6",
        "context_window": 0,
    },
    "gpt-5-4-nano": {
        "provider": "openrouter",
        "model_name": "openai/gpt-5.4-nano",
        "context_window": 0,
    },
    "gemini-3-1-flash-lite": {
        "provider": "openrouter",
        "model_name": "google/gemini-3.1-flash-lite",
        "context_window": 0,
    },
    "claude-haiku-4-5": {
        "provider": "openrouter",
        "model_name": "anthropic/claude-haiku-4.5",
        "context_window": 0,
    },
}

LOCAL_GENERATOR_MODELS: dict[str, dict[str, str | int]] = {
    "qwen3-5-9b": {
        "provider": "local_openai",
        "model_name": "qwen3.5:9b",
        "local_model_name": "qwen3.5:9b",
        "context_window": 262144,
    },
    "qwen3-8b": {
        "provider": "local_openai",
        "model_name": "qwen3:8b",
        "local_model_name": "qwen3:8b",
        "context_window": 32768,
    },
    "qwen-coder-7b": {
        "provider": "local_openai",
        "model_name": "qwen2.5-coder:7b",
        "local_model_name": "qwen2.5-coder:7b",
        "context_window": 32768,
    },
    "arctic-text2sql-7b": {
        "provider": "local_openai",
        "model_name": "arctic-text2sql-r1:7b",
        "local_model_name": "arctic-text2sql-r1:7b",
        "context_window": 16384,
    },
}
LOCAL_GENERATOR_MODEL_ALIASES = {
    # OpenRouter uses `qwen/qwen3.5-9b`; Ollama uses `qwen3.5:9b`.
    # Keep provider ids and old UI payloads resolvable in the local catalog.
    "local-qwen3-5-9b": "qwen3-5-9b",
    "qwen/qwen3.5-9b": "qwen3-5-9b",
}
GENERATOR_MODELS: dict[str, dict[str, str | int]] = {
    **OPENROUTER_GENERATOR_MODELS,
    **{("local-" + key): value for key, value in LOCAL_GENERATOR_MODELS.items()},
}

# Контуры по ТЗ раздел 6. mixed специально комбинирует маленькую модель
# через OpenRouter с сильным аудитором через CLI - это сценарий улучшения
# приглашений: разбор того, где маленькая модель ошибается.
CONTOURS: dict[str, dict[str, str]] = {
    "dev_local": {"generator": "anthropic_cli", "auditor": "anthropic_cli"},
    "claude_cli": {"generator": "anthropic_cli", "auditor": "anthropic_cli"},
    "codex_cli": {"generator": "codex_cli", "auditor": "codex_cli"},
    "prod_demo": {"generator": "openrouter", "auditor": "openrouter"},
    "mixed": {"generator": "openrouter", "auditor": "anthropic_cli"},
    "local_openai": {"generator": "local_openai", "auditor": "local_openai"},
}

# Имена моделей по умолчанию для каждой роли и каждого бэкенда. Реально
# используются только если соответствующая переменная окружения пустая.
DEFAULT_MODELS = {
    "generator": {
        "openrouter": "openai/gpt-4o-mini",
        "local_openai": "qwen2.5-coder:14b",
        "anthropic_cli": "claude-sonnet-4-6",
        "codex_cli": "gpt-5.5",
    },
    "auditor": {
        "openrouter": "openai/gpt-4o-mini",
        "local_openai": "qwen2.5:14b",
        "anthropic_cli": "claude-sonnet-4-6",
        "codex_cli": "gpt-5.5",
    },
}

ROLES = ("generator", "auditor")

JUDGE_BACKEND_OPTIONS: dict[str, dict[str, str]] = {
    "openrouter-gemini-3.1-flash": {
        "backend": "openrouter",
        "model": "google/gemini-3.1-flash-lite",
        "label": "OpenRouter Gemini 3.1 Flash Lite",
    },
    "openrouter-gemini-3.1-pro": {
        "backend": "openrouter",
        "model": "google/gemini-3.1-pro",
        "label": "OpenRouter Gemini 3.1 Pro",
    },
    "openrouter-qwen3-235b-a22b-2507": {
        "backend": "openrouter",
        "model": "qwen/qwen3-235b-a22b-2507",
        "label": "OpenRouter Qwen3 235B A22B 2507",
    },
    "openrouter-qwen3-32b": {
        "backend": "openrouter",
        "model": "qwen/qwen3-32b",
        "label": "OpenRouter Qwen3 32B",
    },
    "claude-cli-sonnet": {
        "backend": "anthropic_cli",
        "model": "claude-sonnet-4-6",
        "label": "Claude CLI Sonnet",
    },
    "off-conservative-fallback": {
        "backend": "off",
        "model": "",
        "label": "Off - conservative fallback",
    },
}
JUDGE_BACKEND_ALIASES = {
    "off": "off-conservative-fallback",
    "disabled": "off-conservative-fallback",
    "claude-cli": "claude-cli-sonnet",
}

PROMPT_CHECK_BACKEND_OPTIONS: dict[str, dict[str, str]] = {
    "local-qwen2.5-0.5b": {
        "backend": "local_openai",
        "model": "qwen2.5:0.5b",
        "label": "Local Qwen2.5 0.5B",
    },
    "local-qwen3-1.7b": {
        "backend": "local_openai",
        "model": "qwen3:1.7b",
        "label": "Local Qwen3 1.7B",
    },
    "local-qwen3-8b": {
        "backend": "local_openai",
        "model": "qwen3:8b",
        "label": "Local Qwen3 8B",
    },
    "openrouter-gemini-3.1-flash": {
        "backend": "openrouter",
        "model": "google/gemini-3.1-flash-lite",
        "label": "OpenRouter Gemini 3.1 Flash Lite",
    },
    "claude-cli-sonnet": {
        "backend": "anthropic_cli",
        "model": "claude-sonnet-4-6",
        "label": "Claude CLI Sonnet",
    },
    "off": {
        "backend": "off",
        "model": "",
        "label": "Off - user disabled",
    },
}
PROMPT_CHECK_BACKEND_ALIASES = {
    "disabled": "off",
    "false": "off",
    "local_openai": "local-qwen2.5-0.5b",
    "openrouter": "openrouter-gemini-3.1-flash",
    "claude_cli": "claude-cli-sonnet",
    "anthropic_cli": "claude-cli-sonnet",
    "claude-cli": "claude-cli-sonnet",
}

_LLM_MODE_OVERRIDE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_mode_override",
    default=None,
)
_LLM_MODEL_OVERRIDE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_model_override",
    default=None,
)
_JUDGE_BACKEND_OVERRIDE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "judge_backend_override",
    default=None,
)
_OPENROUTER_PROVIDER_OVERRIDE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "openrouter_provider_override",
    default=None,
)
_JUDGE_OPENROUTER_PROVIDER_OVERRIDE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "judge_openrouter_provider_override",
    default=None,
)
_PROMPT_CHECK_ENABLED_OVERRIDE: contextvars.ContextVar[bool | None] = contextvars.ContextVar(
    "prompt_check_enabled_override",
    default=None,
)
_PROMPT_CHECK_BACKEND_OVERRIDE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "prompt_check_backend_override",
    default=None,
)
_PROMPT_CHECK_MODEL_OVERRIDE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "prompt_check_model_override",
    default=None,
)
_PROMPT_CHECK_OPENROUTER_PROVIDER_OVERRIDE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "prompt_check_openrouter_provider_override",
    default=None,
)
_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_request_id",
    default=None,
)
_OPENROUTER_ENDPOINTS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_OPENROUTER_ENDPOINTS_TTL_SEC = 900


@dataclass
class LLMResponse:
    """Унифицированный ответ модели. У всех бэкендов одна форма."""

    text: str
    model: str
    backend: str
    raw: dict[str, Any]
    # Phase 0.1 — детальные тайминги и usage для диагностики 20-30с.
    # walltime_sec измеряется обёрткой вокруг SDK-вызова; покрывает
    # сетевую часть, рендер JSON-ответа и любые скрытые SDK-ретраи.
    walltime_sec: float = 0.0
    # usage_norm — нормализованный словарь токенов и стоимости из
    # response.usage. Поля могут быть None если провайдер не сообщил.
    usage_norm: dict[str, Any] | None = None
    # response_headers — сохранённые HTTP-заголовки ответа (только
    # для backends, где доступны через with_raw_response). Полезные:
    # x-openrouter-provider (реальный провайдер за OpenRouter),
    # x-request-id (для траблшутинга через support-канал).
    response_headers: dict[str, str] = field(default_factory=dict)
    # Phase 0.2 — лог попыток retry. Каждая запись: attempt (1-based),
    # reason (имя класса исключения), status_code, elapsed_sec до сбоя,
    # wait_sec backoff. Пустой список значит retry-ев не было.
    retry_log: list[dict[str, Any]] = field(default_factory=list)
    # Phase 5 — tool_calls из ответа модели. Каждая запись:
    # {"id": str, "name": str, "arguments": dict}. Пустой список = модель
    # не запросила tool. Orchestrator (tool-loop) маршрутизирует к app.tools.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def extract_usage(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Извлечь нормализованный usage из raw response модели.

    Возвращает None если usage отсутствует. Иначе - dict со всеми
    основными полями, неизвестные становятся None. Покрывает OpenAI,
    OpenRouter, и большинство OpenAI-compatible провайдеров.
    """
    if not raw or not isinstance(raw, dict):
        return None
    usage = raw.get("usage") or {}
    if not usage:
        return None

    completion_details = usage.get("completion_tokens_details") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    # OpenRouter иногда кладёт cost в usage.cost, иногда в usage_accounting.cost.
    cost = usage.get("cost")
    if cost is None:
        cost = (raw.get("usage_accounting") or {}).get("cost")

    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "reasoning_tokens": completion_details.get("reasoning_tokens"),
        "cached_tokens": prompt_details.get("cached_tokens"),
        "cache_write_tokens": usage.get("cache_creation_input_tokens"),
        "cost_usd": cost,
        "generation_id": raw.get("id"),
        "provider": raw.get("provider") or raw.get("provider_name"),
    }


_INTERESTING_HEADERS = (
    "x-openrouter-provider",
    "x-request-id",
    "openrouter-request-id",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "retry-after",
)


def _filter_headers(headers: Any) -> dict[str, str]:
    """Оставить только нужные для диагностики HTTP-заголовки."""
    if not headers:
        return {}
    try:
        items = headers.items() if hasattr(headers, "items") else dict(headers).items()
    except Exception:
        return {}
    out: dict[str, str] = {}
    for key, value in items:
        try:
            lower = str(key).lower()
        except Exception:
            continue
        if lower in _INTERESTING_HEADERS:
            out[lower] = str(value)
    return out


# Phase 0.2 — retry visibility ------------------------------------------------
# SDK по умолчанию ретраит 429 и 5xx два раза. Эти ретраи невидимы и
# съедают walltime, что мешает диагностике 20-30с. Мы отключаем SDK-ретрай
# (max_retries=0 в конструкторе) и делаем явный цикл здесь, фиксируя в
# response.retry_log каждую попытку с причиной и backoff.

_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
_RETRYABLE_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "InternalServerError",
    "RateLimitError",
    "Timeout",
    "ConnectError",
    "ReadTimeout",
    "WriteTimeout",
}


def _is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status in _RETRYABLE_STATUS:
        return True
    return exc.__class__.__name__ in _RETRYABLE_NAMES


def _retry_after_seconds(exc: Exception, attempt: int, base: float) -> float:
    """
    Backoff: уважаем Retry-After (если провайдер прислал), иначе
    экспоненциально с базой base и множителем 2**attempt.
    """
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
        retry_after = None
        if headers is not None:
            try:
                retry_after = headers.get("retry-after") or headers.get("Retry-After")
            except Exception:
                retry_after = None
        if retry_after:
            try:
                return min(float(retry_after), 30.0)
            except (TypeError, ValueError):
                pass
    return min(base * (2 ** attempt), 30.0)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _retry_record(attempt: int, exc: Exception, elapsed: float, wait: float) -> dict[str, Any]:
    """Сформировать запись для retry_log: что упало, почему, сколько ждём."""
    return {
        "attempt": attempt + 1,
        "reason": exc.__class__.__name__,
        "status_code": getattr(exc, "status_code", None),
        "elapsed_sec": round(elapsed, 3),
        "wait_sec": round(wait, 3),
        "message": str(exc)[:200],
    }


@contextmanager
def request_context(request_id: str | None) -> Iterator[None]:
    """Attach current trace id to provider errors produced inside one run."""
    token = _REQUEST_ID.set((request_id or "").strip() or None)
    try:
        yield
    finally:
        _REQUEST_ID.reset(token)


class LLMClient:
    """Базовый интерфейс клиента. Все бэкенды реализуют один метод invoke."""

    backend: str = ""
    model: str = ""
    supports_tools: bool = False

    def invoke(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        messages_override: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """
        Сделать один вызов модели. На вход - системная инструкция и
        пользовательский текст. На выход - LLMResponse с готовым текстом
        и метаданными бэкенда для трассы.

        response_format={"type": "json_object"} применяется к OpenAI-
        совместимым бэкендам; для CLI-обёрток игнорируется (CLI-модели
        форматируют JSON по системной инструкции).

        Phase 5 — tools/tool_choice/messages_override для function calling.
        CLI-обёртки игнорируют tools (модель пишет SQL без них).
        """
        raise NotImplementedError


@contextmanager
def model_override(
    llm_mode: str | None = None,
    llm_generator_model: str | None = None,
    openrouter_provider: str | None = None,
) -> Iterator[None]:
    """
    Временно подменить LLM_MODE и LLM_GENERATOR_MODEL для одного прогона.

    contextvars держат значение внутри текущего async-контекста, поэтому
    параллельные HTTP-запросы не меняют модель друг другу.
    """
    tokens: list[contextvars.Token[str | None]] = []
    if llm_mode:
        tokens.append(_LLM_MODE_OVERRIDE.set(llm_mode))
    if llm_generator_model:
        tokens.append(_LLM_MODEL_OVERRIDE.set(llm_generator_model))
    if openrouter_provider:
        tokens.append(_OPENROUTER_PROVIDER_OVERRIDE.set(openrouter_provider))
    try:
        yield
    finally:
        for token in reversed(tokens):
            token.var.reset(token)


@contextmanager
def judge_backend_override(
    judge_backend: str | None = None,
    openrouter_provider: str | None = None,
) -> Iterator[None]:
    """Temporarily select Stage 4 semantic judge backend for one run."""
    token: contextvars.Token[str | None] | None = None
    provider_token: contextvars.Token[str | None] | None = None
    if judge_backend:
        token = _JUDGE_BACKEND_OVERRIDE.set(_normalize_judge_backend_key(judge_backend))
    if openrouter_provider:
        provider_token = _JUDGE_OPENROUTER_PROVIDER_OVERRIDE.set(openrouter_provider)
    try:
        yield
    finally:
        if provider_token is not None:
            _JUDGE_OPENROUTER_PROVIDER_OVERRIDE.reset(provider_token)
        if token is not None:
            _JUDGE_BACKEND_OVERRIDE.reset(token)


@contextmanager
def prompt_check_override(
    enabled: bool | None = None,
    backend: str | None = None,
    model: str | None = None,
    openrouter_provider: str | None = None,
) -> Iterator[None]:
    """Temporarily select prompt-check backend/model for one run."""
    tokens: list[contextvars.Token[Any]] = []
    if enabled is not None:
        tokens.append(_PROMPT_CHECK_ENABLED_OVERRIDE.set(bool(enabled)))
    if backend:
        tokens.append(_PROMPT_CHECK_BACKEND_OVERRIDE.set(_normalize_prompt_check_backend_key(backend)))
    if model:
        tokens.append(_PROMPT_CHECK_MODEL_OVERRIDE.set(model.strip()))
    if openrouter_provider:
        tokens.append(_PROMPT_CHECK_OPENROUTER_PROVIDER_OVERRIDE.set(openrouter_provider.strip()))
    try:
        yield
    finally:
        for token in reversed(tokens):
            token.var.reset(token)


class OpenAICompatibleClient(LLMClient):
    """Клиент для любого OpenAI-совместимого API: OpenRouter, Ollama, vLLM, LM Studio."""

    supports_tools = True

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        backend: str,
        role: str = "direct",
    ) -> None:
        # Импорт внутри конструктора, чтобы при использовании CLI-режима
        # пакет openai не был обязательным.
        from openai import OpenAI

        self.model = model
        self.backend = backend
        self.role = role
        # max_retries=0: отключаем SDK-ретрай, чтобы он не съедал walltime
        # незаметно. Свой явный retry-loop в _do_call записывает каждую
        # попытку в response.retry_log.
        self._client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0)
        self._base_url = base_url
        self._api_key = api_key
        self._aclient: Any = None  # лениво создаётся в _async_client()
        self._max_retries = max(_env_int("LLM_PROVIDER_MAX_RETRIES", 2), 0)
        self._retry_backoff_base = max(_env_float("LLM_PROVIDER_RETRY_BACKOFF", 1.5), 0.1)

    def _async_client(self) -> Any:
        """Ленивая инициализация AsyncOpenAI для параллельных кандидатов."""
        if self._aclient is None:
            from openai import AsyncOpenAI

            self._aclient = AsyncOpenAI(
                base_url=self._base_url,
                api_key=self._api_key,
                max_retries=0,
            )
        return self._aclient

    def _build_messages(self, system: str, user: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def _build_kwargs(
        self,
        system: str,
        user: str,
        temperature: float | None,
        response_format: dict[str, Any] | None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        messages_override: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages_override if messages_override is not None else self._build_messages(system, user),
            "temperature": 0.1 if temperature is None else temperature,
            "timeout": _call_timeout_sec(),
        }
        if response_format:
            kwargs["response_format"] = response_format
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        if self.role == "judge":
            provider = _JUDGE_OPENROUTER_PROVIDER_OVERRIDE.get() or os.environ.get("STAGE_4_OPENROUTER_PROVIDER_ONLY", "")
        elif self.role == "prompt_check":
            provider = (
                _PROMPT_CHECK_OPENROUTER_PROVIDER_OVERRIDE.get()
                or os.environ.get("PROMPT_CHECK_OPENROUTER_PROVIDER_ONLY", "")
            )
        else:
            provider = _OPENROUTER_PROVIDER_OVERRIDE.get() or os.environ.get("OPENROUTER_PROVIDER_ONLY", "")
        provider = str(provider or "").strip()
        extra_body: dict[str, Any] = {}
        if self.backend == "openrouter" and provider:
            extra_body["provider"] = {
                "only": [provider],
                "allow_fallbacks": False,
            }
        # qwen3-thinking моделям по умолчанию глушим reasoning trace:
        # на OpenRouter Alibaba и совместимые провайдеры по дефолту крутят
        # thinking, что даёт 11-54 s/вызов и галлюцинации имён колонок.
        # Включить обратно можно ENV QWEN_THINKING_FORCE_ON=true.
        if (
            self.backend == "openrouter"
            and _is_qwen_thinking_model(self.model)
            and os.environ.get("QWEN_THINKING_FORCE_ON", "").strip().lower() not in {"1", "true", "yes"}
        ):
            extra_body.setdefault("chat_template_kwargs", {})
            extra_body["chat_template_kwargs"].setdefault("enable_thinking", False)
            # Дополнительно отключаем reasoning для providers с reasoning-полем.
            extra_body.setdefault("reasoning", {"enabled": False})
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs

    def _finalize_response(
        self,
        completion: Any,
        walltime_sec: float,
        headers_dict: dict[str, str],
        retry_log: list[dict[str, Any]],
    ) -> LLMResponse:
        # Phase 5 — извлечь tool_calls из ответа (если есть). На моделях/
        # провайдерах без tool-support tool_calls будет [] и поле не повлияет.
        message = completion.choices[0].message if completion.choices else None
        text = (getattr(message, "content", None) or "") if message else ""
        tool_calls: list[dict[str, Any]] = []
        raw_calls = getattr(message, "tool_calls", None) if message else None
        if raw_calls:
            for call in raw_calls:
                fn = getattr(call, "function", None)
                if fn is None:
                    continue
                args_raw = getattr(fn, "arguments", None) or "{}"
                try:
                    args_obj = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
                except (json.JSONDecodeError, TypeError, ValueError):
                    args_obj = {"_raw": str(args_raw)[:400]}
                tool_calls.append({
                    "id": getattr(call, "id", "") or "",
                    "name": getattr(fn, "name", "") or "",
                    "arguments": args_obj,
                })
        raw_dump = completion.model_dump()
        return LLMResponse(
            text=text.strip(),
            model=self.model,
            backend=self.backend,
            raw=raw_dump,
            walltime_sec=round(walltime_sec, 3),
            usage_norm=extract_usage(raw_dump),
            response_headers=headers_dict,
            retry_log=retry_log,
            tool_calls=tool_calls,
        )

    def invoke(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        messages_override: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """
        Сделать вызов через OpenAI SDK с явным retry-loop.

        Walltime измеряется на каждой попытке, успешный вызов
        возвращает суммарный walltime (последняя попытка) и retry_log
        со всеми предыдущими попытками. SDK-ретрай отключён, так что
        20-30с патологии видны как явные записи в retry_log.

        response_format={"type": "json_object"} принудительно валидный
        JSON-вывод там, где провайдер поддерживает (используется
        аудитором для fail-secure парсинга).

        Phase 5 — tools/tool_choice/messages_override:
        - tools = OpenAI tool specs (см. app.tools.ALL_SPECS)
        - tool_choice = "auto" | "required" | {"type":"function","function":{"name":"..."}}
        - messages_override — полный список сообщений (включая
          assistant с tool_calls и tool-сообщения с результатами) для
          tool-loop. Если задан — system/user аргументы игнорируются.
        """
        retry_log: list[dict[str, Any]] = []
        kwargs = self._build_kwargs(
            system, user, temperature, response_format,
            tools=tools, tool_choice=tool_choice, messages_override=messages_override,
        )
        max_attempts = self._max_retries + 1

        for attempt in range(max_attempts):
            t0 = time.perf_counter()
            try:
                raw_resp = self._client.chat.completions.with_raw_response.create(**kwargs)
                completion = raw_resp.parse()
                headers_dict = _filter_headers(getattr(raw_resp, "headers", None))
                walltime_sec = time.perf_counter() - t0
                return self._finalize_response(completion, walltime_sec, headers_dict, retry_log)
            except AttributeError:
                # Старая версия openai SDK без with_raw_response.
                try:
                    completion = self._client.chat.completions.create(**kwargs)
                    walltime_sec = time.perf_counter() - t0
                    return self._finalize_response(completion, walltime_sec, {}, retry_log)
                except Exception as exc:
                    elapsed = time.perf_counter() - t0
                    if _is_retryable(exc) and attempt + 1 < max_attempts:
                        wait = _retry_after_seconds(exc, attempt, self._retry_backoff_base)
                        retry_log.append(_retry_record(attempt, exc, elapsed, wait))
                        time.sleep(wait)
                        continue
                    _raise_provider_exception(
                        self.backend,
                        exc,
                        base_url=self._base_url,
                        role=self.role,
                        model=self.model,
                    )
            except Exception as exc:
                elapsed = time.perf_counter() - t0
                if _is_retryable(exc) and attempt + 1 < max_attempts:
                    wait = _retry_after_seconds(exc, attempt, self._retry_backoff_base)
                    retry_log.append(_retry_record(attempt, exc, elapsed, wait))
                    time.sleep(wait)
                    continue
                _raise_provider_exception(
                    self.backend,
                    exc,
                    base_url=self._base_url,
                    role=self.role,
                    model=self.model,
                )

        # Unreachable: либо вернули в цикле, либо выбросили из _raise_provider_exception.
        raise ProviderUnavailable(_provider_label(self.backend) + " provider unavailable: retry loop exhausted")

    async def invoke_async(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        messages_override: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """
        Асинхронный аналог invoke() для параллельных кандидатов через
        asyncio.gather в генераторе. Использует AsyncOpenAI клиента и
        asyncio.sleep на backoff. Логика retry/extraction идентична.
        """
        import asyncio

        retry_log: list[dict[str, Any]] = []
        kwargs = self._build_kwargs(
            system, user, temperature, response_format,
            tools=tools, tool_choice=tool_choice, messages_override=messages_override,
        )
        max_attempts = self._max_retries + 1
        client = self._async_client()

        for attempt in range(max_attempts):
            t0 = time.perf_counter()
            try:
                raw_resp = await client.chat.completions.with_raw_response.create(**kwargs)
                completion = raw_resp.parse()
                headers_dict = _filter_headers(getattr(raw_resp, "headers", None))
                walltime_sec = time.perf_counter() - t0
                return self._finalize_response(completion, walltime_sec, headers_dict, retry_log)
            except AttributeError:
                try:
                    completion = await client.chat.completions.create(**kwargs)
                    walltime_sec = time.perf_counter() - t0
                    return self._finalize_response(completion, walltime_sec, {}, retry_log)
                except Exception as exc:
                    elapsed = time.perf_counter() - t0
                    if _is_retryable(exc) and attempt + 1 < max_attempts:
                        wait = _retry_after_seconds(exc, attempt, self._retry_backoff_base)
                        retry_log.append(_retry_record(attempt, exc, elapsed, wait))
                        await asyncio.sleep(wait)
                        continue
                    _raise_provider_exception(
                        self.backend,
                        exc,
                        base_url=self._base_url,
                        role=self.role,
                        model=self.model,
                    )
            except Exception as exc:
                elapsed = time.perf_counter() - t0
                if _is_retryable(exc) and attempt + 1 < max_attempts:
                    wait = _retry_after_seconds(exc, attempt, self._retry_backoff_base)
                    retry_log.append(_retry_record(attempt, exc, elapsed, wait))
                    await asyncio.sleep(wait)
                    continue
                _raise_provider_exception(
                    self.backend,
                    exc,
                    base_url=self._base_url,
                    role=self.role,
                    model=self.model,
                )

        raise ProviderUnavailable(_provider_label(self.backend) + " provider unavailable: retry loop exhausted")


class OllamaNativeClient(LLMClient):
    """Native Ollama chat API client for models where /v1 returns reasoning only."""

    def __init__(self, model: str, base_url: str, backend: str, role: str = "direct") -> None:
        self.model = model
        self.backend = backend
        self.role = role
        self.base_url = _ollama_base_url(base_url)

    def invoke(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        messages_override: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        del tools, tool_choice
        messages = messages_override if messages_override is not None else [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.1 if temperature is None else temperature,
            },
        }
        keep_alive = _ollama_request_keep_alive()
        if keep_alive:
            payload["keep_alive"] = keep_alive
        if response_format and response_format.get("type") == "json_object":
            payload["format"] = "json"

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = url_request.Request(
            self.base_url.rstrip("/") + "/api/chat",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        started = time.perf_counter()
        lock_wait_started = started
        try:
            with _ollama_model_lock(self.model):
                lock_wait_sec = round(time.perf_counter() - lock_wait_started, 3)
                with url_request.urlopen(req, timeout=_call_timeout_sec()) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
        except url_error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            raise ProviderUnavailable(
                "Ollama provider unavailable: HTTP " + str(exc.code) + ": " + text
                + _provider_error_context(
                    backend=self.backend,
                    base_url=self.base_url,
                    role=self.role,
                    model=self.model,
                )
            ) from exc
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderUnavailable(
                "Ollama provider unavailable: " + str(exc)
                + _provider_error_context(
                    backend=self.backend,
                    base_url=self.base_url,
                    role=self.role,
                    model=self.model,
                )
            ) from exc

        if isinstance(raw, dict):
            raw.setdefault("_client_keep_alive", keep_alive)
            raw.setdefault("_client_model_lock_wait_sec", lock_wait_sec)
            raw.setdefault("_client_serialized_by_model", True)
        message = raw.get("message") or {}
        text = str(message.get("content") or "").strip()
        return LLMResponse(
            text=text,
            model=self.model,
            backend=self.backend,
            raw=raw,
            walltime_sec=round(time.perf_counter() - started, 3),
            usage_norm={
                "prompt_tokens": raw.get("prompt_eval_count"),
                "completion_tokens": raw.get("eval_count"),
                "total_tokens": _sum_optional(raw.get("prompt_eval_count"), raw.get("eval_count")),
                "reasoning_tokens": None,
                "cached_tokens": None,
                "cache_write_tokens": None,
                "cost_usd": None,
                "generation_id": None,
                "provider": "ollama_native",
            },
        )


_CLI_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "CODEX_HOME",
)
_CLI_ENV_PREFIX_ALLOWLIST = (
    "ANTHROPIC_",
    "OPENAI_",
    "CLAUDE_",
    "CODEX_",
)
_CLI_AUTH_KEYWORDS = (
    "unauthorized",
    "please login",
    "please log in",
    "you must log in",
    "auth required",
    "authentication required",
    "api key invalid",
    "invalid api key",
    "token expired",
    "session expired",
    "credit balance is too low",
    "you need to login",
    "expired credentials",
)
_CLI_QUOTA_KEYWORDS = (
    "hit your limit",
    "usage limit",
    "rate limit",
    "quota exceeded",
    "credit balance is too low",
)
_CLI_PARALLEL_LOCK = threading.Lock()
_CLI_PARALLEL_SEMAPHORES: dict[str, tuple[int, threading.BoundedSemaphore]] = {}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _cli_parallel_limit() -> int:
    return max(1, _env_int("LLM_CLI_MAX_PARALLEL", 2))


def _cli_parallel_backends() -> set[str]:
    raw = os.environ.get("LLM_CLI_PARALLEL_BACKENDS", "").strip()
    if not raw:
        return set(_CLI_PARALLEL_BACKENDS_DEFAULT)
    return {item.strip() for item in raw.split(",") if item.strip()}


def _cli_effective_parallel_limit(backend: str) -> int:
    if not _env_bool("LLM_CLI_PARALLEL_CANDIDATES", True):
        return 1
    if backend not in _cli_parallel_backends():
        return 1
    return _cli_parallel_limit()


def _cli_parallel_enabled_for_backend(backend: str) -> bool:
    return _cli_effective_parallel_limit(backend) > 1


def _cli_parallel_semaphore(backend: str) -> threading.BoundedSemaphore:
    limit = _cli_effective_parallel_limit(backend)
    with _CLI_PARALLEL_LOCK:
        current = _CLI_PARALLEL_SEMAPHORES.get(backend)
        if current is None or current[0] != limit:
            current = (limit, threading.BoundedSemaphore(limit))
            _CLI_PARALLEL_SEMAPHORES[backend] = current
        return current[1]


def _filter_subprocess_env() -> dict[str, str]:
    """Return a minimal env dict for CLI subprocess.

    Allowlist по Orchestra-спецификации §7: пропускаем PATH/HOME/локали,
    proxy-настройки и префиксы ANTHROPIC_/OPENAI_/CLAUDE_.
    Это исключает утечку случайных секретов из API-окружения в CLI
    subprocess и держит запуск воспроизводимым.
    """
    env: dict[str, str] = {}
    for name in _CLI_ENV_ALLOWLIST:
        value = os.environ.get(name)
        if value:
            env[name] = value
    for key, value in os.environ.items():
        if not value:
            continue
        if any(key.startswith(prefix) for prefix in _CLI_ENV_PREFIX_ALLOWLIST):
            env[key] = value
    return env


def _cli_call_tmp(
    env: dict[str, str],
    owner: tuple[int, int] | None = None,
) -> Callable[[], None] | None:
    base = (
        env.get("TMPDIR")
        or (str(Path(env["HOME"]) / "tmp") if env.get("HOME") else "")
        or "/tmp"
    )
    tmp_dir = Path(base) / ("cli_call_" + uuid.uuid4().hex)
    tmp_dir.mkdir(parents=True, exist_ok=False)
    if owner is not None and os.name == "posix":
        base_dir = Path(base)
        os.chown(base_dir, owner[0], owner[1])
        os.chown(tmp_dir, owner[0], owner[1])
    env["TMPDIR"] = str(tmp_dir)

    if _env_bool("LLM_CLI_KEEP_TMP", False):
        return None

    def cleanup() -> None:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return cleanup


def _cli_subprocess_runtime() -> tuple[dict[str, str], Callable[[], None] | None, Callable[[], None] | None]:
    """Return env/preexec for headless CLI calls.

    Claude Code refuses `--dangerously-skip-permissions` under root. The
    FastAPI container still runs as root for volume compatibility, but CLI
    subprocesses can safely drop to an unprivileged user that owns the mounted
    auth home.
    """
    env = _filter_subprocess_env()
    if os.name != "posix" or os.geteuid() != 0:
        return env, None, _cli_call_tmp(env)

    username = os.environ.get("CLI_RUN_AS_USER", "appuser").strip()
    if not username:
        return env, None, _cli_call_tmp(env)
    try:
        user_info = pwd.getpwnam(username)
    except KeyError:
        return env, None, _cli_call_tmp(env)

    home = user_info.pw_dir
    env["HOME"] = home
    env["TMPDIR"] = str(Path(home) / "tmp")
    cleanup = _cli_call_tmp(env, owner=(user_info.pw_uid, user_info.pw_gid))

    def demote() -> None:
        os.setgid(user_info.pw_gid)
        os.setuid(user_info.pw_uid)

    return env, demote, cleanup


def _looks_like_auth_failure(stdout: str, stderr: str, returncode: int) -> bool:
    """Detect CLI auth failures from stdout/stderr text."""
    if returncode in (401, 403):
        return True
    blob = ((stdout or "") + "\n" + (stderr or "")).lower()
    return any(keyword in blob for keyword in _CLI_AUTH_KEYWORDS)


def _looks_like_quota_failure(stdout: str, stderr: str) -> bool:
    """Detect CLI quota/rate-limit messages from stdout/stderr text."""
    blob = ((stdout or "") + "\n" + (stderr or "")).lower()
    return any(keyword in blob for keyword in _CLI_QUOTA_KEYWORDS)


def _load_json_object(text: str) -> dict[str, Any] | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _extract_claude_text(raw: dict[str, Any] | None, fallback: str) -> str:
    if raw is None:
        return fallback.strip()
    result = raw.get("result")
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    content = raw.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(part for part in parts if part).strip()
    return fallback.strip()


def _load_codex_jsonl(text: str) -> dict[str, Any] | None:
    """Parse Codex CLI --json output and return final agent message + usage."""
    if not text:
        return None
    last_message = ""
    usage: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        events.append(event)
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            last_message = item["text"]
        event_usage = event.get("usage")
        if isinstance(event_usage, dict):
            usage = event_usage
    if not events:
        return None
    return {
        "type": "codex_jsonl",
        "result": last_message,
        "usage": usage,
        "events": events[-20:],
    }


def _cli_error_text(result: subprocess.CompletedProcess[str], parsed: dict[str, Any] | None) -> str:
    if parsed is not None:
        text = _extract_claude_text(parsed, "")
        if text:
            return text[:280]
    return (result.stderr.strip() or result.stdout.strip())[:280]


def _cli_usage_norm(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    if not usage:
        return None
    prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    total_tokens = usage.get("total_tokens")
    if total_tokens is None:
        try:
            total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
        except (TypeError, ValueError):
            total_tokens = None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "reasoning_tokens": usage.get("reasoning_output_tokens"),
        "cost_usd": raw.get("total_cost_usd") or usage.get("cost_usd"),
    }


class CLISubprocessClient(LLMClient):
    """
    Клиент-обертка над локальным Claude CLI.

    Это не способ для прод-демо: задержки 5-15 секунд, нет стриминга.
    Зато можно прогонять большие батчи без расхода API-кредитов.
    Auth-фейлы детектятся отдельно от missing-binary, чтобы UI мог показать
    пользователю «залогинься на хосте, не пробуй ретраить».
    """

    supports_tools = False

    def __init__(self, binary: str, model: str, backend: str, role: str = "") -> None:
        self.binary = binary
        self.model = model
        self.backend = backend
        self.role = role

    def build_command(
        self,
        system: str,
        user: str,
        prompt: str,
        output_path: str | None = None,
    ) -> list[str]:
        """Build backend-specific CLI command per Orchestra spec §6."""
        del output_path
        if self.backend == "codex_cli":
            cmd = [
                self.binary,
                "exec",
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
                "--json",
            ]
            if self.model:
                cmd.extend(["--model", self.model])
            effort = os.environ.get("CODEX_GENERATOR_REASONING_EFFORT", "").strip()
            if not effort and self.role == "generator":
                effort = "medium"
            if effort:
                cmd.extend(["-c", 'model_reasoning_effort="' + effort + '"'])
            cmd.append("-")
            return cmd

        cmd = [
            self.binary,
            "-p",
            user,
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
        ]
        if system:
            cmd.extend(["--append-system-prompt", system])
        if self.model:
            cmd.extend(["--model", self.model])
        return cmd

    def candidate_max_parallel(self) -> int:
        return _cli_effective_parallel_limit(self.backend)

    def candidate_parallel_supported(self) -> bool:
        return _cli_parallel_enabled_for_backend(self.backend)

    def _invoke_with_parallel_slot(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        messages_override: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        semaphore = _cli_parallel_semaphore(self.backend)
        with semaphore:
            return self.invoke(
                system,
                user,
                temperature=temperature,
                response_format=response_format,
                tools=tools,
                tool_choice=tool_choice,
                messages_override=messages_override,
            )

    async def invoke_async(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        messages_override: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return await asyncio.to_thread(
            self._invoke_with_parallel_slot,
            system,
            user,
            temperature,
            response_format,
            tools,
            tool_choice,
            messages_override,
        )

    def invoke(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        messages_override: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """
        Сделать вызов через subprocess. CLI не понимают разделение
        system/user в одном prompt'е (claude умеет через
        --append-system-prompt). Ошибки PATH,
        таймаута и auth разделяем на разные исключения чтобы UI/orchestrator
        мог корректно их обработать.
        """
        del temperature
        del response_format  # CLI-обёртки игнорируют response_format
        del tools, tool_choice, messages_override  # Phase 5 — CLI без tools
        merged_prompt = "[Инструкция]\n" + system + "\n\n[Запрос]\n" + user

        cmd = self.build_command(system, user, merged_prompt)
        env, preexec_fn, cleanup = _cli_subprocess_runtime()

        t0 = time.perf_counter()
        try:
            try:
                run_kwargs: dict[str, Any] = {
                    "capture_output": True,
                    "text": True,
                    "timeout": _call_timeout_sec(),
                    "env": env,
                }
                if preexec_fn is not None:
                    run_kwargs["preexec_fn"] = preexec_fn
                if self.backend == "codex_cli":
                    run_kwargs["input"] = merged_prompt
                result = subprocess.run(cmd, **run_kwargs)
            except FileNotFoundError as exc:
                raise LLMConfigError(
                    "CLI " + self.binary + " не найден. Проверь установку и PATH."
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise ProviderUnavailable(
                    "CLI " + self.binary + " не ответил за "
                    + str(_call_timeout_sec()) + " секунд."
                ) from exc
            walltime_sec = time.perf_counter() - t0

            parsed_raw = (
                _load_codex_jsonl(result.stdout)
                if self.backend == "codex_cli"
                else _load_json_object(result.stdout)
            ) or {}
            text = _extract_claude_text(parsed_raw, result.stdout)

            is_cli_error = bool(parsed_raw.get("is_error")) if isinstance(parsed_raw, dict) else False
            if result.returncode != 0 or is_cli_error:
                error_text = _cli_error_text(result, parsed_raw)
                if _looks_like_quota_failure(result.stdout, result.stderr):
                    raise ProviderUnavailable(
                        "CLI " + self.binary + " quota/rate limit: " + error_text
                    )
                if _looks_like_auth_failure(result.stdout, result.stderr, result.returncode):
                    raise ProviderUnavailable(
                        "CLI " + self.binary + " auth failure: "
                        + error_text
                        + ". Залогинься на хосте: `" + self.binary + " login`."
                    )
                raise ProviderUnavailable(
                    "CLI " + self.binary + " вернул код "
                    + str(result.returncode) + ": " + error_text
                )

            return LLMResponse(
                text=text,
                model=self.model,
                backend=self.backend,
                raw={
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "cmd": cmd[:6],
                    "tmpdir": env.get("TMPDIR"),
                    "parsed": parsed_raw,
                },
                walltime_sec=round(walltime_sec, 3),
                usage_norm=_cli_usage_norm(parsed_raw),
            )
        finally:
            if cleanup is not None:
                cleanup()


def _current_contour(env: str | None) -> str:
    """Определить активный контур: context override, аргумент или LLM_MODE."""
    contour = (
        _LLM_MODE_OVERRIDE.get()
        or env
        or os.environ.get("LLM_MODE", "prod_demo")
    ).strip()
    if contour not in CONTOURS:
        raise LLMConfigError(
            "Неизвестный контур LLM_MODE='" + contour + "'. Поддерживаются: "
            + ", ".join(sorted(CONTOURS))
        )
    return contour


def _backend_for_role(role: str, contour: str) -> str:
    """Бэкенд для роли. Override LLM_BACKEND_<ROLE> побеждает раскладку контура."""
    override = os.environ.get("LLM_BACKEND_" + role.upper(), "").strip()
    if override:
        backend = override
    elif role == "generator":
        backend = CONTOURS[contour][role]
    else:
        backend = CONTOURS[contour][role]

    if backend not in SUPPORTED_BACKENDS:
        raise LLMConfigError(
            "Неизвестный бэкенд '" + backend + "' для роли " + role
            + ". Поддерживаются: " + ", ".join(sorted(SUPPORTED_BACKENDS))
        )
    return backend


def _model_for(role: str, backend: str) -> str:
    """Имя модели для роли и бэкенда. Сначала смотрим env, потом дефолт."""
    env_key = "LLM_MODEL_" + role.upper()
    explicit = (_LLM_MODEL_OVERRIDE.get() or os.environ.get(env_key, "")).strip()
    if explicit:
        if backend == "local_openai":
            models = _generator_models_for_backend(backend)
            key = _resolve_generator_model_key(explicit, models)
            config = models.get(key)
            if config is not None:
                return str(config.get("local_model_name", config["model_name"]))
            # Backend-specific UI presets must not inherit OpenRouter model ids
            # such as openai/gpt-4o-mini from a global .env auditor setting.
        elif backend == "openrouter":
            # Короткий ключ из UI (например `qwen3-coder-30b-a3b`) надо
            # резолвить через каталог в полный OR slug
            # (`qwen/qwen3-coder-30b-a3b-instruct`). Если ключа в каталоге
            # нет, но строка уже похожа на полный slug `provider/model` —
            # пропускаем как есть для custom моделей.
            models = _generator_models_for_backend(backend)
            key = _resolve_generator_model_key(explicit, models)
            config = models.get(key)
            if config is not None:
                return str(config["model_name"])
            if "/" in explicit:
                return explicit
            # Иначе falls through на дефолт — это лучше, чем послать заведомо
            # невалидный slug в OR API и получить 400.
        elif backend in {"anthropic_cli", "codex_cli"} and "/" in explicit:
            pass
        else:
            return explicit

    if backend in {"anthropic_cli", "codex_cli"}:
        return DEFAULT_MODELS[role][backend]

    if role == "generator":
        _, config = _generator_model_config(backend)
        if backend == "local_openai":
            return str(config.get("local_model_name", config["model_name"]))
        return str(config["model_name"])

    if role == "auditor" and backend in {"openrouter", "local_openai"}:
        _, config = _generator_model_config(backend)
        if backend == "local_openai":
            return str(config.get("local_model_name", config["model_name"]))
        return str(config["model_name"])

    return DEFAULT_MODELS[role][backend]


def _generator_model_config(backend: str) -> tuple[str, dict[str, str | int]]:
    """Вернуть выбранный generator-профиль из backend-specific catalog."""
    raw_key = (
        _LLM_MODEL_OVERRIDE.get()
        or os.environ.get("LLM_GENERATOR_MODEL", GENERATOR_MODEL_DEFAULT)
    ).strip()
    if not raw_key:
        raw_key = GENERATOR_MODEL_DEFAULT

    models = _generator_models_for_backend(backend)
    key = _resolve_generator_model_key(raw_key, models)
    config = models.get(key)
    if config is None:
        raise LLMConfigError(
            "Неизвестный LLM_GENERATOR_MODEL='" + raw_key + "' для backend '"
            + backend + "'. Поддерживаются: " + ", ".join(sorted(models))
        )
    return key, config


def _generator_models_for_backend(backend: str) -> dict[str, dict[str, str | int]]:
    if backend == "openrouter":
        return OPENROUTER_GENERATOR_MODELS
    if backend == "local_openai":
        return LOCAL_GENERATOR_MODELS
    return {}


def _resolve_generator_model_key(raw_key: str, models: dict[str, dict[str, str | int]]) -> str:
    if raw_key in models:
        return raw_key
    alias = LOCAL_GENERATOR_MODEL_ALIASES.get(raw_key)
    if alias in models:
        return alias
    for key, config in models.items():
        names = {
            str(config.get("model_name") or ""),
            str(config.get("local_model_name") or ""),
        }
        if raw_key in names:
            return key
    if raw_key.startswith("local-") and raw_key[6:] in models:
        return raw_key[6:]
    return raw_key


def is_known_generator_model(model: str, llm_mode: str | None = None) -> bool:
    """Check model key/id against mode-specific generator catalog."""
    raw = str(model or "").strip()
    if not raw:
        return True
    modes = [llm_mode] if llm_mode else ["prod_demo", "local_openai"]
    for mode in modes:
        if not mode:
            continue
        if mode not in CONTOURS:
            continue
        backend = CONTOURS[mode]["generator"]
        if backend in {"anthropic_cli", "codex_cli"}:
            return "/" not in raw
        models = _generator_models_for_backend(backend)
        if _resolve_generator_model_key(raw, models) in models:
            return True
    return False


def _call_timeout_sec() -> int:
    """Таймаут одного обращения к провайдеру модели."""
    raw = os.environ.get("LLM_CALL_TIMEOUT_SEC", "30").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise LLMConfigError("LLM_CALL_TIMEOUT_SEC должен быть целым числом.") from exc
    if value <= 0:
        raise LLMConfigError("LLM_CALL_TIMEOUT_SEC должен быть больше нуля.")
    return value


def _raise_provider_exception(
    backend: str,
    exc: Exception,
    *,
    base_url: str = "",
    role: str = "",
    model: str = "",
) -> None:
    status_code = getattr(exc, "status_code", None)
    name = exc.__class__.__name__
    ctx = _provider_error_context(backend=backend, base_url=base_url, role=role, model=model)

    if status_code in (401, 403):
        raise ProviderUnavailable(
            _provider_label(backend) + " provider unavailable: "
            + "провайдер отклонил запрос. Проверь ключи и доступы."
            + ctx
        ) from exc

    if status_code == 429 or (isinstance(status_code, int) and status_code >= 500):
        raise ProviderUnavailable(
            _provider_label(backend) + " provider unavailable: " + str(exc) + ctx
        ) from exc

    if name in {"APIConnectionError", "APITimeoutError", "Timeout", "ConnectError"}:
        raise ProviderUnavailable(
            _provider_label(backend) + " provider unavailable: " + str(exc) + ctx
        ) from exc

    raise ProviderUnavailable(
        _provider_label(backend) + " provider unavailable: " + str(exc) + ctx
    ) from exc


def _provider_label(backend: str) -> str:
    labels = {
        "openrouter": "OpenRouter",
        "local_openai": "Local OpenAI",
        "anthropic_cli": "Anthropic CLI",
        "codex_cli": "Codex CLI",
    }
    return labels.get(backend, backend)


def _provider_error_context(backend: str, base_url: str, role: str, model: str) -> str:
    """Small diagnostic suffix for HTTP 503 provider failures."""
    host, port = _url_host_port(base_url)
    parts = [
        "backend=" + (backend or "unknown"),
        "role=" + (role or "unknown"),
        "model=" + (model or "unknown"),
    ]
    if base_url:
        parts.append("base_url=" + base_url.rstrip("/"))
    if host:
        parts.append("host=" + host)
        resolved = _resolve_host(host)
        if resolved:
            parts.append("resolved=" + resolved)
    if port:
        parts.append("port=" + str(port))
    trace_id = _REQUEST_ID.get()
    if trace_id:
        parts.append("trace_id=" + trace_id)
    parts.append("container=" + socket.gethostname())
    parts.append("pid=" + str(os.getpid()))
    return " [" + " ".join(parts) + "]"


def _url_host_port(base_url: str) -> tuple[str, int | None]:
    if not base_url:
        return "", None
    try:
        parsed = url_parse.urlparse(base_url)
    except Exception:
        return "", None
    return parsed.hostname or "", parsed.port


def _resolve_host(host: str) -> str:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        return "DNS_ERROR:" + exc.__class__.__name__ + ":" + str(exc)
    ips = sorted({str(item[4][0]) for item in infos if item and item[4]})
    return ",".join(ips[:3])


def _build_client(role: str, backend: str) -> LLMClient:
    """Собрать клиента по выбранному бэкенду. Проверки конфигурации - здесь."""
    model = _model_for(role, backend)

    if backend == "openrouter":
        base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise LLMConfigError(
                "OPENROUTER_API_KEY пуст. Заполни ключ в .env или переключи контур."
            )
        return OpenAICompatibleClient(
            model=model, base_url=base_url, api_key=api_key, backend="openrouter", role=role,
        )

    if backend == "local_openai":
        base_url = os.environ.get("LOCAL_LLM_BASE_URL", "http://host.docker.internal:11434/v1")
        # Для локальных серверов ключ часто не нужен, но openai SDK требует
        # непустую строку. Подкладываем не-секретный плейсхолдер.
        api_key = os.environ.get("LOCAL_LLM_API_KEY", "not-needed") or "not-needed"
        if _use_native_ollama(base_url):
            return OllamaNativeClient(
                model=model, base_url=base_url, backend="local_openai", role=role,
            )
        return OpenAICompatibleClient(
            model=model, base_url=base_url, api_key=api_key, backend="local_openai",
        )

    if backend == "anthropic_cli":
        return CLISubprocessClient(
            binary=os.environ.get("ANTHROPIC_CLI_PATH", "claude"),
            model=model,
            backend="anthropic_cli",
            role=role,
        )

    if backend == "codex_cli":
        return CLISubprocessClient(
            binary=os.environ.get("CODEX_CLI_PATH", "codex"),
            model=model,
            backend="codex_cli",
            role=role,
        )

    raise LLMConfigError("Неизвестный backend '" + backend + "'.")


def get_llm(role: str, env: str | None = None) -> LLMClient:
    """
    Вернуть клиента для роли generator или auditor.

    role - какой контур использовать в коде вызова. env - опциональный
    контур (dev_local, prod_demo, mixed, local_openai); если не задан -
    берется из LLM_MODE. Конфигурационные проблемы поднимаются как
    LLMConfigError, чтобы API-слой смог отдать корректный HTTP 400.
    """
    if role not in ROLES:
        raise LLMConfigError("Неизвестная роль LLM: " + role)
    contour = _current_contour(env)
    backend = _backend_for_role(role, contour)
    return _build_client(role, backend)


def _normalize_judge_backend_key(raw: str | None) -> str:
    key = (raw or "").strip()
    if not key:
        key = os.environ.get("STAGE_4_BACKEND", "openrouter-gemini-3.1-flash").strip()
    key = JUDGE_BACKEND_ALIASES.get(key, key)
    if key not in JUDGE_BACKEND_OPTIONS:
        raise LLMConfigError(
            "Неизвестный Stage 4 judge backend '" + key + "'. Поддерживаются: "
            + ", ".join(sorted(JUDGE_BACKEND_OPTIONS))
        )
    return key


def current_judge_backend_key() -> str:
    """Return selected Stage 4 backend key after env/context override."""
    return _normalize_judge_backend_key(_JUDGE_BACKEND_OVERRIDE.get())


def _normalize_prompt_check_backend_key(raw: str | None) -> str:
    key = (raw or "").strip()
    if not key:
        backend = os.environ.get("PROMPT_CHECK_LLM_BACKEND", "local_openai").strip()
        model = os.environ.get("PROMPT_CHECK_LLM_MODEL", "qwen2.5:0.5b").strip()
        for opt_key, info in PROMPT_CHECK_BACKEND_OPTIONS.items():
            if info["backend"] == backend and info["model"] == model:
                return opt_key
        key = backend
    key = PROMPT_CHECK_BACKEND_ALIASES.get(key, key)
    if key not in PROMPT_CHECK_BACKEND_OPTIONS:
        raise LLMConfigError(
            "Неизвестный prompt-check backend '" + key + "'. Поддерживаются: "
            + ", ".join(sorted(PROMPT_CHECK_BACKEND_OPTIONS))
        )
    return key


def current_prompt_check_backend_key() -> str:
    """Return selected prompt-check backend key after env/context override."""
    return _normalize_prompt_check_backend_key(_PROMPT_CHECK_BACKEND_OVERRIDE.get())


def current_prompt_check_model() -> str:
    key = current_prompt_check_backend_key()
    return (_PROMPT_CHECK_MODEL_OVERRIDE.get() or PROMPT_CHECK_BACKEND_OPTIONS[key]["model"] or "").strip()


def current_prompt_check_openrouter_provider() -> str:
    return (
        _PROMPT_CHECK_OPENROUTER_PROVIDER_OVERRIDE.get()
        or os.environ.get("PROMPT_CHECK_OPENROUTER_PROVIDER_ONLY", "")
    ).strip()


def prompt_check_enabled() -> bool:
    override = _PROMPT_CHECK_ENABLED_OVERRIDE.get()
    if override is not None:
        return bool(override) and current_prompt_check_backend_key() != "off"
    if current_prompt_check_backend_key() == "off":
        return False
    raw = os.environ.get("PROMPT_CHECK_LLM_ENABLED", "true").strip().lower()
    if not raw:
        return True
    return raw in {"1", "true", "yes", "on"}


def stage4_enabled() -> bool:
    raw = os.environ.get("STAGE_4_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on", ""}


def get_judge_llm() -> LLMClient:
    """Build client for Stage 4 semantic judge backend."""
    if _JUDGE_BACKEND_OVERRIDE.get() is None and not os.environ.get("STAGE_4_BACKEND", "").strip():
        return get_llm("auditor")
    key = current_judge_backend_key()
    info = JUDGE_BACKEND_OPTIONS[key]
    backend = info["backend"]
    if backend == "off":
        raise LLMConfigError("Stage 4 judge backend is off.")
    return _build_direct_client(backend, info["model"], role="judge")


def get_prompt_check_llm(backend_key: str | None = None) -> LLMClient:
    """Build client for PromptCheck LLM judge."""
    key = _normalize_prompt_check_backend_key(backend_key or _PROMPT_CHECK_BACKEND_OVERRIDE.get())
    info = PROMPT_CHECK_BACKEND_OPTIONS[key]
    backend = info["backend"]
    if backend == "off":
        raise LLMConfigError("Prompt-check backend is off.")
    model = current_prompt_check_model()
    if not model:
        raise LLMConfigError("Prompt-check model is empty for backend '" + key + "'.")
    return _build_direct_client(backend, model, role="prompt_check")


def _build_direct_client(backend: str, model: str, role: str = "direct") -> LLMClient:
    """Build a client for an explicit backend/model pair."""
    if backend == "openrouter":
        base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise LLMConfigError(
                "OPENROUTER_API_KEY пуст. Заполни ключ в .env или переключи backend."
            )
        return OpenAICompatibleClient(model=model, base_url=base_url, api_key=api_key, backend="openrouter", role=role)
    if backend == "local_openai":
        base_url = os.environ.get("LOCAL_LLM_BASE_URL", "http://host.docker.internal:11434/v1")
        api_key = os.environ.get("LOCAL_LLM_API_KEY", "not-needed") or "not-needed"
        if _use_native_ollama(base_url):
            return OllamaNativeClient(model=model, base_url=base_url, backend="local_openai", role=role)
        return OpenAICompatibleClient(model=model, base_url=base_url, api_key=api_key, backend="local_openai", role=role)
    if backend == "anthropic_cli":
        return CLISubprocessClient(
            binary=os.environ.get("ANTHROPIC_CLI_PATH", "claude"),
            model=model,
            backend="anthropic_cli",
            role="direct",
        )
    if backend == "codex_cli":
        return CLISubprocessClient(
            binary=os.environ.get("CODEX_CLI_PATH", "codex"),
            model=model,
            backend="codex_cli",
            role=role,
        )

    raise LLMConfigError("Неизвестный backend '" + backend + "'.")


def validate_current_config() -> dict[str, str]:
    """
    Проверить, что текущий контур валиден и для каждой роли можно
    выбрать бэкенд. Не делает сетевых вызовов и не создает клиентов -
    подходит для предполетной проверки в API health-check.
    """
    contour = _current_contour(None)
    _call_timeout_sec()
    generator_backend = _backend_for_role("generator", contour)
    auditor_backend = _backend_for_role("auditor", contour)
    for backend in {generator_backend, auditor_backend}:
        if backend in {"openrouter", "local_openai"}:
            _generator_model_config(backend)
    if "openrouter" in {generator_backend, auditor_backend}:
        if not os.environ.get("OPENROUTER_API_KEY", "").strip():
            raise LLMConfigError(
                "OPENROUTER_API_KEY пуст. Заполни ключ в .env или переключи контур."
            )
    return {
        "contour": contour,
        "generator_backend": generator_backend,
        "auditor_backend": auditor_backend,
    }


def describe_current_mode() -> dict[str, str]:
    """
    Сводка о текущем режиме для UI, трасс и health-эндпоинта.

    Возвращает контур, выбранные бэкенды и имена моделей по обеим ролям.
    Никаких сетевых вызовов и создания клиентов - безопасно дергать
    часто, даже на каждый запрос /health.
    """
    contour = _current_contour(None)
    gen_backend = _backend_for_role("generator", contour)
    aud_backend = _backend_for_role("auditor", contour)
    if gen_backend in {"openrouter", "local_openai"}:
        generator_key, generator_config = _generator_model_config(gen_backend)
        generator_provider = str(generator_config["provider"])
        generator_window = (
            str(generator_config["context_window"])
            if int(generator_config["context_window"]) > 0
            else "unknown"
        )
    else:
        generator_key = gen_backend
        generator_config = {"provider": gen_backend, "context_window": 0}
        generator_provider = gen_backend
        generator_window = "unknown"
    return {
        "mode": contour,
        "generator_backend": gen_backend,
        "auditor_backend": aud_backend,
        "generator_model_key": generator_key,
        "generator_provider": generator_provider,
        "generator_context_window": generator_window,
        "generator_model": _model_for("generator", gen_backend),
        "auditor_model": _model_for("auditor", aud_backend),
        "judge_backend": current_judge_backend_key(),
        "judge_model": JUDGE_BACKEND_OPTIONS[current_judge_backend_key()]["model"],
        "llm_call_timeout_sec": str(_call_timeout_sec()),
    }


def list_model_options() -> list[dict[str, Any]]:
    """
    Перечислить runtime presets для UI: OpenRouter каталог, локальный каталог,
    Claude CLI. Для OpenRouter добавляет кэшированный публичный
    provider catalog из /api/v1/models/{model}/endpoints.

    Каждая запись содержит явный backend, llm_mode и provider_model — чтобы
    интерфейсы не смешивали OpenRouter whitelist и local whitelist и могли
    показать какой бэкенд будет выбран. CLI presets отдают пустой
    llm_generator_model — он подбирается из LLM_MODEL_GENERATOR или дефолта.
    """
    openrouter_ready = bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
    local_base_url = os.environ.get("LOCAL_LLM_BASE_URL", "").strip()
    local_installed = _local_ollama_installed_models(local_base_url)
    disabled_local_models = {
        item.strip()
        for item in os.environ.get("LOCAL_LLM_DISABLED_MODELS", "").split(",")
        if item.strip()
    }
    anthropic_cli = os.environ.get("ANTHROPIC_CLI_PATH", "").strip()
    codex_cli = os.environ.get("CODEX_CLI_PATH", "").strip()
    tool_support = _tool_support_from_report()
    provider_catalog = openrouter_provider_catalog()
    options: list[dict[str, Any]] = []

    for key, info in OPENROUTER_GENERATOR_MODELS.items():
        provider_model = str(info.get("model_name") or key)
        option_key = "or-" + key
        options.append({
            "key": option_key,
            "label": "OpenRouter " + provider_model,
            "llm_mode": "prod_demo",
            "llm_generator_model": key,
            "backend": "openrouter",
            "provider_model": provider_model,
            "description": "OpenRouter generator/auditor · " + provider_model,
            "available_by_config": openrouter_ready,
            "config_hint": "" if openrouter_ready else "OPENROUTER_API_KEY",
            "supports_tool_mode": tool_support.get(option_key, "unknown"),
            "openrouter_providers": provider_catalog.get(provider_model, []),
        })

    for key, info in LOCAL_GENERATOR_MODELS.items():
        provider_model = str(info.get("local_model_name") or info.get("model_name") or key)
        option_key = "local-" + key
        installed = local_installed is None or provider_model in local_installed
        disabled = provider_model in disabled_local_models
        local_ready = bool(local_base_url) and installed and not disabled
        hint = ""
        if not local_base_url:
            hint = "LOCAL_LLM_BASE_URL"
        elif disabled:
            hint = "disabled locally: model failed to load in current Ollama runtime"
        elif not installed:
            hint = "ollama pull " + provider_model
        options.append({
            "key": option_key,
            "label": "Local " + provider_model,
            "llm_mode": "local_openai",
            "llm_generator_model": key,
            "backend": "local_openai",
            "provider_model": provider_model,
            "description": "Local OpenAI-compatible runtime · " + provider_model,
            "available_by_config": local_ready,
            "config_hint": hint,
            "supports_tool_mode": tool_support.get(option_key, "unknown"),
        })

    import shutil as _shutil

    claude_default = str(DEFAULT_MODELS["generator"].get("anthropic_cli") or "")
    claude_binary = anthropic_cli or "claude"
    claude_in_path = bool(_shutil.which(claude_binary))
    options.append({
        "key": "claude-cli",
        "label": "Claude CLI",
        "llm_mode": "claude_cli",
        "llm_generator_model": "",
        "backend": "anthropic_cli",
        "provider_model": claude_default,
        "description": "Anthropic CLI generator/auditor · model from LLM_MODEL_GENERATOR or " + claude_default,
        "available_by_config": claude_in_path,
        "config_hint": "" if claude_in_path else "claude CLI not installed in image (see ANTHROPIC_CLI_PATH)",
        "supports_tool_mode": "unsupported",
    })

    codex_default = str(DEFAULT_MODELS["generator"].get("codex_cli") or "")
    codex_binary = codex_cli or "codex"
    codex_in_path = bool(_shutil.which(codex_binary))
    options.append({
        "key": "codex-cli",
        "label": "Codex CLI",
        "llm_mode": "codex_cli",
        "llm_generator_model": "",
        "backend": "codex_cli",
        "provider_model": codex_default,
        "description": "OpenAI Codex CLI generator/auditor · model from LLM_MODEL_GENERATOR or " + codex_default,
        "available_by_config": codex_in_path,
        "config_hint": "" if codex_in_path else "codex CLI not installed in image (see CODEX_CLI_PATH)",
        "supports_tool_mode": "unsupported",
    })

    return options


def openrouter_provider_catalog() -> dict[str, list[dict[str, Any]]]:
    """Fetch public OpenRouter endpoint metadata for configured OpenRouter models."""
    model_ids = [
        str(info.get("model_name") or "").strip()
        for info in OPENROUTER_GENERATOR_MODELS.values()
        if str(info.get("model_name") or "").strip()
    ]
    catalog: dict[str, list[dict[str, Any]]] = {model_id: [] for model_id in model_ids}
    with ThreadPoolExecutor(max_workers=min(10, max(len(model_ids), 1))) as executor:
        futures = {executor.submit(_openrouter_provider_options, model_id): model_id for model_id in model_ids}
        for future in as_completed(futures):
            model_id = futures[future]
            try:
                catalog[model_id] = future.result()
            except Exception:
                catalog[model_id] = []
    return catalog


def _openrouter_provider_options(model_id: str) -> list[dict[str, Any]]:
    now = time.time()
    cached = _OPENROUTER_ENDPOINTS_CACHE.get(model_id)
    if cached and now - cached[0] < _OPENROUTER_ENDPOINTS_TTL_SEC:
        return list(cached[1].get("items") or [])

    url = "https://openrouter.ai/api/v1/models/" + url_parse.quote(model_id, safe="/:-.") + "/endpoints"
    try:
        headers = {"Accept": "application/json", "User-Agent": "case3-benchmark-ui"}
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if openrouter_key:
            headers["Authorization"] = "Bearer " + openrouter_key
        req = url_request.Request(url, headers=headers)
        with url_request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        if cached:
            return list(cached[1].get("items") or [])
        return []

    endpoints = ((payload.get("data") or {}).get("endpoints") or []) if isinstance(payload, dict) else []
    items: list[dict[str, Any]] = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        pricing = endpoint.get("pricing") if isinstance(endpoint.get("pricing"), dict) else {}
        prompt_per_m = _price_per_million(pricing.get("prompt"))
        completion_per_m = _price_per_million(pricing.get("completion"))
        weighted_price = prompt_per_m + completion_per_m * 3
        items.append(
            {
                "provider_name": endpoint.get("provider_name") or endpoint.get("name") or "",
                "endpoint_name": endpoint.get("name") or "",
                "price_per_million_prompt": prompt_per_m,
                "price_per_million_completion": completion_per_m,
                "weighted_price_score": round(weighted_price, 6),
                "latency_last_30m": endpoint.get("latency_last_30m"),
                "throughput_last_30m": endpoint.get("throughput_last_30m"),
                "tokens_per_second": endpoint.get("throughput_last_30m"),
                "uptime_last_30m": endpoint.get("uptime_last_30m"),
                "context_length": endpoint.get("context_length"),
                "quantization": endpoint.get("quantization"),
                "status": endpoint.get("status"),
                "supports_tools": "tools" in (endpoint.get("supported_parameters") or []),
                "source_url": url,
            }
        )

    active = [item for item in items if item.get("status") in (0, None)]
    ranked = sorted(active or items, key=_provider_rank_key)
    recommended_names = {str(item.get("provider_name")) for item in ranked[:5]}
    for item in items:
        item["recommended"] = str(item.get("provider_name")) in recommended_names
    items.sort(key=lambda item: (not bool(item.get("recommended")), *_provider_rank_key(item)))
    _OPENROUTER_ENDPOINTS_CACHE[model_id] = (now, {"items": items})
    return items


def _price_per_million(value: Any) -> float:
    try:
        return round(float(_metric_number(value) or 0) * 1_000_000, 6)
    except (TypeError, ValueError):
        return 0.0


def _metric_number(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("p50", value.get("median", value.get("avg", value.get("mean", value.get("value")))))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _provider_rank_key(item: dict[str, Any]) -> tuple[float, float, float, str]:
    latency = _metric_number(item.get("latency_last_30m"))
    throughput = _metric_number(item.get("throughput_last_30m"))
    uptime = _metric_number(item.get("uptime_last_30m"))
    latency_sort = latency if latency is not None else 999999.0
    throughput_sort = -throughput if throughput is not None else 0.0
    uptime_sort = -uptime if uptime is not None else 0.0
    return (
        float(item.get("weighted_price_score") or 0),
        latency_sort,
        throughput_sort + uptime_sort / 100000.0,
        str(item.get("provider_name") or ""),
    )


def list_judge_backend_options() -> list[dict[str, Any]]:
    """Return Stage 4 backend choices for /chat UI."""
    import shutil as _shutil

    current = current_judge_backend_key()
    openrouter_ready = bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
    provider_catalog = openrouter_provider_catalog() if openrouter_ready else {}
    out: list[dict[str, Any]] = []
    for key, info in JUDGE_BACKEND_OPTIONS.items():
        backend = info["backend"]
        hint = ""
        available = True
        if backend == "openrouter":
            available = openrouter_ready
            hint = "" if openrouter_ready else "OPENROUTER_API_KEY"
        elif backend == "anthropic_cli":
            binary = os.environ.get("ANTHROPIC_CLI_PATH", "claude")
            available = bool(_shutil.which(binary))
            hint = "Uses host `claude login` auth (volume-mounted)" if available else "claude CLI not installed"
        out.append(
            {
                "key": key,
                "label": info["label"],
                "backend": backend,
                "provider_model": info["model"],
                "available_by_config": available,
                "config_hint": hint,
                "openrouter_providers": provider_catalog.get(info["model"], []) if backend == "openrouter" else [],
                "default": key == current,
            }
        )
    return out


def list_prompt_check_backend_options() -> list[dict[str, Any]]:
    """Return prompt-check backend choices for chat and batch UI."""
    import shutil as _shutil

    current = current_prompt_check_backend_key()
    openrouter_ready = bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
    provider_catalog = openrouter_provider_catalog() if openrouter_ready else {}
    out: list[dict[str, Any]] = []
    for key, info in PROMPT_CHECK_BACKEND_OPTIONS.items():
        backend = info["backend"]
        hint = ""
        available = True
        if backend == "openrouter":
            available = openrouter_ready
            hint = "" if openrouter_ready else "OPENROUTER_API_KEY"
        elif backend == "anthropic_cli":
            binary = os.environ.get("ANTHROPIC_CLI_PATH", "claude")
            available = bool(_shutil.which(binary))
            hint = "Uses host `claude login` auth" if available else "claude CLI not installed"
        out.append(
            {
                "key": key,
                "label": info["label"],
                "backend": backend,
                "provider_model": info["model"],
                "available_by_config": available,
                "config_hint": hint,
                "openrouter_providers": provider_catalog.get(info["model"], []) if backend == "openrouter" else [],
                "default": key == current,
            }
        )
    return out


def _tool_support_from_report() -> dict[str, str]:
    return {}

def _use_native_ollama(base_url: str) -> bool:
    raw = os.environ.get("LOCAL_LLM_USE_NATIVE_OLLAMA", "").strip().lower()
    if raw in {"1", "true", "yes"}:
        return True
    if raw in {"0", "false", "no"}:
        return False
    return "11434" in base_url or "ollama" in base_url.lower()


def _ollama_base_url(base_url: str) -> str:
    text = base_url.rstrip("/")
    if text.endswith("/v1"):
        return text[:-3]
    return text


def _local_ollama_installed_models(base_url: str) -> set[str] | None:
    """Return installed Ollama model names when the local runtime is reachable."""
    if not base_url or not _use_native_ollama(base_url):
        return None
    url = _ollama_base_url(base_url) + "/api/tags"
    try:
        with url_request.urlopen(url, timeout=0.75) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, url_error.URLError):
        return None
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return None
    out: set[str] = set()
    for item in models:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model")
        if isinstance(name, str) and name.strip():
            out.add(name.strip())
    return out or None


def _sum_optional(left: Any, right: Any) -> int | None:
    if isinstance(left, int) and isinstance(right, int):
        return left + right
    return None


def parse_json_response(text: str) -> dict[str, Any]:
    """
    Разобрать JSON-ответ модели даже если он завернут в markdown.
    Маленькие модели любят оборачивать вывод в ```json ... ```; чистим
    обертку, режем хвост после последней закрывающей скобки и пробуем
    json.loads. На неуспех поднимаем ValueError с куском исходного текста.
    """
    candidate = text.strip()

    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()

    last_brace = candidate.rfind("}")
    if last_brace >= 0:
        candidate = candidate[: last_brace + 1]

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Не удалось разобрать JSON-ответ модели: " + str(exc)
            + ". Первые 300 символов ответа: " + text[:300]
        ) from exc
