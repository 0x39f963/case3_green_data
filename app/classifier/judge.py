"""
Stage 4: семантический judge.

Один model call проверяет четыре semantic label groups:
- DIRECT_SENSITIVE;
- EXCESSIVE_SCOPE;
- WRONG_JOIN_PATH;
- HALLUCINATED_TABLE / HALLUCINATED_COLUMN.
- MASKING_REQUIRED.

Judge вызывается только для hard cases, которые Stage 1 пометил как
semantic findings или placeholders.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app import llm_provider, prompt_registry
from app.classifier.types import Finding


LABELS = {
    "DIRECT_SENSITIVE",
    "EXCESSIVE_SCOPE",
    "WRONG_JOIN_PATH",
    "HALLUCINATED_TABLE",
    "HALLUCINATED_COLUMN",
    "MASKING_REQUIRED",
    "NEEDS_HUMAN_REVIEW",
}

LAST_CALL: dict[str, Any] = {}


def judge_semantic(
    sql: str,
    task: str,
    schema_context: str,
    sensitive_fields: dict[str, list[str]],
    allowed_tables: list[str],
    allowed_columns: dict[str, list[str]] | None = None,
    placeholder_findings: list[Finding] | None = None,
) -> list[Finding]:
    """
    Проверить semantic labels одним model call и вернуть Finding list.

    Возвращаемый JSON должен иметь форму:
    {"findings": [{"label": "...", "severity": 6, "confidence": 0.9,
    "evidence_span": "...", "revision_note": "..."}]}.
    """
    backend_key = llm_provider.current_judge_backend_key()
    if backend_key == "off-conservative-fallback":
        return _off_findings(placeholder_findings or [])

    system_record = prompt_registry.get_default_prompt("semantic_judge_system")
    system = system_record.text
    user = _user_prompt(
        sql=sql,
        task=task,
        schema_context=schema_context,
        sensitive_fields=sensitive_fields,
        allowed_tables=allowed_tables,
        allowed_columns=allowed_columns or {},
        placeholder_findings=placeholder_findings or [],
    )
    client = llm_provider.get_judge_llm()
    started = time.perf_counter()
    response = client.invoke(system, user)
    latency = round(time.perf_counter() - started, 3)
    payload = _parse_payload(response.text)
    findings = _parse_findings(payload)
    global LAST_CALL
    LAST_CALL = {
        "prompt_system": system,
        "prompt_user": user,
        "prompt_meta": system_record.meta,
        **system_record.meta,
        "response_raw": response.text,
        "judge_backend": backend_key,
        "backend": response.backend,
        "model": response.model,
        "judge_model": response.model,
        "judge_decision": "findings" if findings else "clear",
        "judge_latency_sec": latency,
        "finding_count": len(findings),
    }
    return findings


def _off_findings(placeholders: list[Finding]) -> list[Finding]:
    labels = sorted({item.label for item in placeholders if item.label})
    reason = "Stage 4 judge is off; semantic tie-breaker requires manual review."
    global LAST_CALL
    LAST_CALL = {
        "prompt_meta": None,
        "judge_backend": "off-conservative-fallback",
        "backend": "off",
        "model": "",
        "judge_model": "",
        "judge_decision": "needs_review",
        "judge_latency_sec": 0.0,
        "finding_count": 1,
        "skipped_labels": labels,
    }
    return [
        Finding(
            label="NEEDS_HUMAN_REVIEW",
            severity=8.0,
            confidence=1.0,
            evidence_span=", ".join(labels),
            revision_note=reason,
            layer="judge",
            detector="stage4.off_conservative_fallback",
            description=reason,
            recommendation="Select an active Stage 4 judge backend or review manually.",
        )
    ]


def _system_prompt() -> str:
    return (
        "Ты SQL аудитор для PostgreSQL. Проверь только semantic labels: "
        "DIRECT_SENSITIVE, EXCESSIVE_SCOPE, WRONG_JOIN_PATH, "
        "HALLUCINATED_TABLE, HALLUCINATED_COLUMN. "
        "Верни только JSON object без markdown."
    )


def _user_prompt(
    *,
    sql: str,
    task: str,
    schema_context: str,
    sensitive_fields: dict[str, list[str]],
    allowed_tables: list[str],
    allowed_columns: dict[str, list[str]],
    placeholder_findings: list[Finding],
) -> str:
    hints = [
        {
            "label": item.label,
            "severity": item.severity,
            "confidence": item.confidence,
            "evidence_span": item.evidence_span,
            "description": item.description,
        }
        for item in placeholder_findings
    ]
    return (
        "Задача аналитика:\n" + task + "\n\n"
        "SQL:\n" + sql + "\n\n"
        "Schema context:\n" + schema_context[:6000] + "\n\n"
        "Sensitive fields JSON:\n"
        + json.dumps(sensitive_fields, ensure_ascii=False, sort_keys=True) + "\n\n"
        "Allowed tables JSON:\n"
        + json.dumps(allowed_tables, ensure_ascii=False) + "\n\n"
        "Allowed columns JSON:\n"
        + json.dumps(allowed_columns, ensure_ascii=False, sort_keys=True) + "\n\n"
        "Stage 1 hints JSON:\n"
        + json.dumps(hints, ensure_ascii=False, sort_keys=True) + "\n\n"
        "Правила проверки:\n"
        "- DIRECT_SENSITIVE: SQL реально раскрывает sensitive поля без агрегата или маскировки.\n"
        "- EXCESSIVE_SCOPE: SQL читает шире задачи, например нет нужного time/entity фильтра.\n"
        "- WRONG_JOIN_PATH: JOIN идет не по разрешенному FK/business path.\n"
        "- HALLUCINATED_TABLE/COLUMN: таблица или колонка отсутствует в allowed schema.\n\n"
        "- MASKING_REQUIRED: task asks to show personal data but SQL outputs raw personal fields instead of mask or aggregate.\n"
        "- Clear MASKING_REQUIRED when SQL is pure aggregate by non-personal dimensions and selects no employee id/name/contact/birthday/personnel number.\n\n"
        "Ответ JSON:\n"
        "{\"findings\":[{\"label\":\"DIRECT_SENSITIVE\",\"severity\":6,"
        "\"confidence\":0.9,\"evidence_span\":\"email\","
        "\"revision_note\":\"Используй COUNT(*) или GROUP BY вместо раскрытия email.\"}]}"
    )


def _parse_payload(text: str) -> dict[str, Any]:
    try:
        return llm_provider.parse_json_response(text)
    except ValueError:
        candidate = text.strip()
        if candidate.startswith("```"):
            candidate = candidate.strip("`").strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
        data = json.loads(candidate)
        if isinstance(data, list):
            return {"findings": data}
        if isinstance(data, dict):
            return data
        raise ValueError("Judge response must be JSON object or list.")


def _parse_findings(payload: dict[str, Any]) -> list[Finding]:
    items = payload.get("findings") or []
    out: list[Finding] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", ""))
        if label not in LABELS:
            continue
        try:
            severity = float(item.get("severity", 0.0))
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        out.append(
            Finding(
                label=label,
                severity=severity,
                confidence=confidence,
                evidence_span=str(item.get("evidence_span", "")),
                revision_note=str(item.get("revision_note", "")),
                layer="judge",
                detector="stage4.judge_semantic",
                description=str(item.get("rationale", item.get("description", ""))),
                recommendation=str(item.get("revision_note", "")),
            )
        )
    return out
