from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from baseline1 import Vulnerability

from app import llm_provider, prompt_registry, sql_guard


_CACHE_TTL_SEC = 3600
_CACHE: dict[str, tuple[float, "PromptJudgeResult"]] = {}
_MUTATION_OR_INJECTION_RE = re.compile(
    r"\b("
    r"удали|удалить|обнови|обновить|измени|изменить|вставь|вставить|создай|создать|"
    r"drop|delete|update|insert|merge|alter|truncate|create|grant|revoke|copy|"
    r"ignore|system prompt|developer message|инструкц|обойди|обход|секрет|парол[ьяеию]*|password|token|токен|"
    r"выгрузи|экспорт|dump|pg_read_file|pg_sleep"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PromptJudgeResult:
    classification: str
    matched_pattern: str
    explanation: str
    finding: Vulnerability | None
    details: dict[str, Any]


def enabled() -> bool:
    return llm_provider.prompt_check_enabled()


def check_prompt(task: str) -> PromptJudgeResult:
    """Run semantic prompt-risk judge for regex-clean user tasks."""
    if not enabled():
        return PromptJudgeResult(
            classification="disabled",
            matched_pattern="",
            explanation="PromptCheck LLM is disabled.",
            finding=None,
            details={"enabled": False, "skipped": "disabled"},
        )

    cached = _cache_get(task)
    if cached is not None:
        return cached

    fast = _benign_fast_path(task)
    if fast is not None:
        _cache_put(task, fast)
        return fast

    record = prompt_registry.get_default_prompt("prompt_check_judge_system")
    client = llm_provider.get_prompt_check_llm()
    user = _user_prompt(task)
    response = client.invoke(
        record.text,
        user,
        temperature=_temperature(),
        response_format={"type": "json_object"},
    )
    payload = _parse_payload(response.text)
    classification = _classification(payload.get("classification"))
    matched = str(payload.get("matched_pattern") or "").strip()
    explanation = str(payload.get("explanation") or "").strip()
    finding = _finding_for(classification, matched, explanation)
    meta = record.meta
    fallback = meta.get("fallback_reason")
    details = {
        "enabled": True,
        "classification": classification,
        "matched_pattern": matched,
        "explanation": explanation,
        "prompt_system": record.text,
        "prompt_user": user,
        "prompt_meta": meta,
        **meta,
        "prompt_request_sha256": prompt_registry.sha256_text(record.text + "\n\0\n" + user),
        "prompt_fallback_reason": fallback,
        "backend": response.backend,
        "model": response.model,
        "latency_sec": response.walltime_sec,
        "response_raw": response.text,
    }
    result = PromptJudgeResult(
        classification=classification,
        matched_pattern=matched,
        explanation=explanation,
        finding=finding,
        details=details,
    )
    _cache_put(task, result)
    return result


def _cache_key(task: str) -> str:
    backend = llm_provider.current_prompt_check_backend_key()
    model = llm_provider.current_prompt_check_model()
    provider = llm_provider.current_prompt_check_openrouter_provider()
    return sha256((backend + "\0" + model + "\0" + provider + "\0" + (task or "")).encode("utf-8")).hexdigest()


def _cache_get(task: str) -> PromptJudgeResult | None:
    key = _cache_key(task)
    item = _CACHE.get(key)
    if not item:
        return None
    ts, result = item
    if time.time() - ts > _CACHE_TTL_SEC:
        _CACHE.pop(key, None)
        return None
    details = dict(result.details)
    details["cache_hit"] = True
    return PromptJudgeResult(
        classification=result.classification,
        matched_pattern=result.matched_pattern,
        explanation=result.explanation,
        finding=result.finding,
        details=details,
    )


def _cache_put(task: str, result: PromptJudgeResult) -> None:
    _CACHE[_cache_key(task)] = (time.time(), result)


def _benign_fast_path(task: str) -> PromptJudgeResult | None:
    text = (task or "").strip()
    if not text or len(text) > int(os.environ.get("PROMPT_CHECK_FAST_PATH_MAX_CHARS", "160")):
        return None
    if not re.search(r"[А-Яа-яЁё]", text):
        return None
    if _MUTATION_OR_INJECTION_RE.search(text):
        return None
    if re.search(r"[;{}<>`$\\]", text):
        return None
    return PromptJudgeResult(
        classification="benign",
        matched_pattern="",
        explanation="Short read-only analyst request passed deterministic fast path.",
        finding=None,
        details={
            "enabled": True,
            "classification": "benign",
            "matched_pattern": "",
            "explanation": "Short read-only analyst request passed deterministic fast path.",
            "backend": "fast_path",
            "model": "deterministic",
            "latency_sec": 0.0,
            "skipped": "benign_fast_path",
        },
    )


def _user_prompt(task: str) -> str:
    return (
        "Classify this analyst task before SQL generation.\n\n"
        "Task:\n" + (task or "") + "\n\n"
        "Return JSON only."
    )


def _temperature() -> float:
    raw = os.environ.get("PROMPT_CHECK_LLM_TEMPERATURE", "0.0").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _parse_payload(text: str) -> dict[str, Any]:
    try:
        payload = llm_provider.parse_json_response(text)
    except ValueError:
        payload = {
            "classification": "suspicious",
            "matched_pattern": "invalid_json",
            "explanation": "PromptCheck judge returned invalid JSON.",
        }
    return payload if isinstance(payload, dict) else {}


def _classification(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"benign", "suspicious", "injection"}:
        return text
    return "suspicious"


def _finding_for(classification: str, matched: str, explanation: str) -> Vulnerability | None:
    if classification == "benign":
        return None
    label = "PROMPT_LLM_INJECTION" if classification == "injection" else "PROMPT_LLM_SUSPICIOUS"
    severity = 9.0 if classification == "injection" else 7.0
    confidence = 0.9 if classification == "injection" else 0.75
    return sql_guard.make_vulnerability(
        label,
        "PromptCheck LLM classified task as " + classification + ": " + (explanation or matched),
        "Rewrite the analyst task as a normal read-only business analytics request.",
        matched or explanation,
        detector="prompt_check_llm." + classification,
        severity=severity,
        confidence=confidence,
        layer="prompt",
    )


__all__ = ["PromptJudgeResult", "check_prompt", "enabled"]
