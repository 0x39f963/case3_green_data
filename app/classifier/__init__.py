"""
Основной SQL risk classifier для B3.

На вход получает SQL и task/schema context. На выходе возвращает
ClassifierOutput с findings, max severity, risk labels и флагами для
judge/regeneration. STEP3 включает Stage 0 normalize, Stage 1 rules и
Stage 4 judge для семантических labels.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from . import encoder, ensemble, judge, ml, normalize, rules
from .types import ClassifierOutput, Finding


RISK_THRESHOLD = 4.0
JUDGE_LABELS = frozenset(
    {
        "DIRECT_SENSITIVE",
        "EXCESSIVE_SCOPE",
        "WRONG_JOIN_PATH",
        "HALLUCINATED_TABLE",
        "HALLUCINATED_COLUMN",
        "MASKING_REQUIRED",
    }
)


def classify(
    sql: str,
    task: str = "",
    attack_prompt: str = "",
    schema_context: str = "",
    sensitive_fields: dict[str, list[str]] | None = None,
    allowed_tables: list[str] | None = None,
    allowed_columns: dict[str, list[str]] | None = None,
    enable_judge: bool | None = None,
) -> ClassifierOutput:
    """
    Классифицировать generated SQL через staged classifier.

    Stage 0 строит canonical SQL. Stage 1 запускает rules. Stage 2 и
    Stage 3 добавляют ML/encoder probabilities, если артефакты есть и
    флаги включены. Stage 4 вызывается для semantic hard cases. Stage 5
    агрегирует итоговое решение.
    """
    norm = normalize.normalize(sql)
    rule_sql = norm.canonical or sql
    if enable_judge is None:
        enable_judge = _flag("STAGE_4_ENABLED", _flag("LLM_JUDGE_ENABLED", False))

    ctx: dict[str, Any] = {
        "task": task,
        "attack_prompt": attack_prompt,
        "schema_context": schema_context,
        "sensitive_fields": sensitive_fields or {},
        "allowed_tables": allowed_tables or [],
        "allowed_columns": allowed_columns or {},
        "normalized_sql": norm,
    }
    rule_findings = _dedupe(rules.run_rules(rule_sql, ctx))

    stage2_enabled = _flag("STAGE_2_ENABLED", ml.has_artifacts())
    stage2_output = (
        ml.predict(rule_sql, {**ctx, "stage1_findings": rule_findings})
        if stage2_enabled
        else None
    )

    stage3_enabled = _flag("STAGE_3_ENABLED", True)
    stage3_output: dict[str, Any] = {"enabled": stage3_enabled}
    if stage3_enabled:
        enc = _encoder()
        enc_ctx = {
            **ctx,
            "stage1_labels": [item.label for item in rule_findings],
            "rule_hints": [item.label for item in rule_findings],
        }
        probs = enc.predict(rule_sql, enc_ctx)
        labels = [
            label for label in enc.labels_for_probs(probs)
            if _label_possible(label, ctx)
        ]
        tie_zone = [
            label for label in enc.tie_breaker_zone_for_probs(probs)
            if _label_possible(label, ctx)
        ]
        stage3_output = {
            "enabled": True,
            "available": bool(probs),
            "probs": probs,
            "labels": labels,
            "tie_zone": tie_zone,
            "thresholds": enc.thresholds,
            "low_thresholds": enc.low_thresholds,
            "high_thresholds": enc.high_thresholds,
            "version": enc.version,
        }

    placeholders = _semantic_placeholders(rule_sql, rule_findings) if enable_judge else []
    skipped_judge_labels: list[str] = []
    if enable_judge and placeholders and stage3_output.get("available"):
        tie_labels = set(stage3_output.get("tie_zone") or [])
        kept = []
        for item in placeholders:
            if item.label in tie_labels:
                kept.append(item)
            else:
                skipped_judge_labels.append(item.label)
        placeholders = kept
    model_placeholders = _model_semantic_placeholders(stage2_output, stage3_output)
    # Pre-judge supression: погасить ML-шумные labels до Stage 4 вызова.
    _rule_labels_set = {item.label for item in rule_findings}
    if _is_aggregate_safe_intent(ctx) or "DIRECT_SENSITIVE" not in _rule_labels_set:
        model_placeholders = [p for p in model_placeholders if p.label != "MASKING_REQUIRED"]
        placeholders = [p for p in placeholders if p.label != "MASKING_REQUIRED"]
    if _sql_is_pure_aggregate(rule_sql):
        model_placeholders = [p for p in model_placeholders if p.label != "EXCESSIVE_SCOPE"]
        placeholders = [p for p in placeholders if p.label != "EXCESSIVE_SCOPE"]
    before_judge = _dedupe(rule_findings + placeholders + model_placeholders)
    judge_inputs = [item for item in before_judge if item.label in JUDGE_LABELS or item.description == "needs_llm_judge"]
    judge_findings: list[Finding] = []
    judge_error = ""
    if enable_judge and judge_inputs:
        try:
            judge_findings = judge.judge_semantic(
                sql=rule_sql,
                task=task,
                schema_context=schema_context,
                sensitive_fields=sensitive_fields or {},
                allowed_tables=allowed_tables or [],
                allowed_columns=allowed_columns or {},
                placeholder_findings=judge_inputs,
            )
        except (RuntimeError, ValueError) as exc:
            judge_error = str(exc)
    judge_call = dict(judge.LAST_CALL or {}) if judge_inputs else {}
    judge_meta = judge_call.get("prompt_meta") if isinstance(judge_call.get("prompt_meta"), dict) else None
    judge_checked_labels = {item.label for item in judge_inputs if item.label in JUDGE_LABELS}
    judge_cleared_labels = set()
    if enable_judge and judge_inputs and not judge_error and not judge_findings:
        judge_cleared_labels = set(judge_checked_labels)
    # H6 + Insight 1: suppress MASKING_REQUIRED через ensemble когда:
    # (a) intent — aggregate-safe / "без персональных полей" (anchor-based);
    # (b) AST PII gate в Stage 1 НЕ нашёл raw PII в SELECT (детерминированная
    #     evidence: если AST показал, что нет raw PII в проекции, то ML
    #     stage2/stage3 MASKING_REQUIRED заведомо ложный).
    #
    # Tc-0003 insight: для pure aggregate (SELECT COUNT(*)…) ML стабильно
    # ставит EXCESSIVE_SCOPE с conf=0.9, judge подтверждает, retry loop
    # никогда не сходится. Добавляем aggregate-evidence supression:
    # если SQL это скалярный агрегат без GROUP BY — EXCESSIVE_SCOPE
    # заведомо ложный (агрегат не имеет «scope»).
    rule_labels = {item.label for item in rule_findings}
    intent_suppressed: set[str] = set()
    if _is_aggregate_safe_intent(ctx):
        intent_suppressed.add("MASKING_REQUIRED")
    if "DIRECT_SENSITIVE" not in rule_labels:
        intent_suppressed.add("MASKING_REQUIRED")
    if _sql_is_pure_aggregate(rule_sql):
        intent_suppressed.add("EXCESSIVE_SCOPE")
    cleared_total = judge_cleared_labels | intent_suppressed
    ensemble_stage1 = [
        item for item in before_judge
        if item.label not in cleared_total and item.description != "needs_llm_judge"
    ]
    ensemble_stage2 = _filter_stage2_labels(stage2_output, cleared_total)
    ensemble_stage3 = _filter_stage3_labels(stage3_output, cleared_total)

    result = ensemble.aggregate(
        stage1_findings=ensemble_stage1,
        stage2_output=ensemble_stage2,
        stage3_output=ensemble_stage3,
        stage4_findings=judge_findings,
    )
    result.stage_outputs = {
        "stage_0_normalize": {
            "enabled": True,
            "canonical": norm.canonical,
            "statement_count": len(norm.statements),
            "comment_count": len(norm.comments),
            "parse_error": norm.parse_error,
        },
        "stage_1_rules": {"enabled": True, "finding_count": len(rule_findings)},
        "stage_2_feature_ml": {
            "enabled": bool(stage2_enabled),
            "available": bool(stage2_output and stage2_output.available),
            "model_type": stage2_output.model_type if stage2_output else "",
            "label_count": len(stage2_output.labels_above_threshold) if stage2_output else 0,
        },
        "stage_3_encoder": {
            "enabled": bool(stage3_enabled),
            "available": bool(stage3_output.get("available", False)),
            "label_count": len(stage3_output.get("labels") or []),
            "version": stage3_output.get("version", ""),
            "tie_zone": stage3_output.get("tie_zone") or [],
            "skipped_judge_labels": skipped_judge_labels,
        },
        "stage_4_llm_judge": {
            "enabled": bool(enable_judge),
            "called": bool(enable_judge and judge_inputs),
            "finding_count": len(judge_findings),
            "error": judge_error,
            "skipped_by_stage3": len(skipped_judge_labels),
            "prompt_meta": judge_meta,
            "prompt_id": (judge_meta or {}).get("prompt_id"),
            "prompt_version": (judge_meta or {}).get("prompt_version"),
            "prompt_sha256": (judge_meta or {}).get("prompt_sha256"),
            "prompt_fallback_reason": (judge_meta or {}).get("fallback_reason"),
            "judge_backend": judge_call.get("judge_backend"),
            "judge_model": judge_call.get("judge_model") or judge_call.get("model"),
            "judge_decision": judge_call.get("judge_decision"),
            "judge_latency_sec": judge_call.get("judge_latency_sec"),
            "checked_labels": sorted(judge_checked_labels),
            "cleared_labels": sorted(judge_cleared_labels),
        },
        **result.stage_outputs,
    }
    return result


def _dedupe(findings: list[Finding]) -> list[Finding]:
    by_label: dict[str, Finding] = {}
    for item in findings:
        current = by_label.get(item.label)
        if current is None or item.severity > current.severity:
            by_label[item.label] = item
    return list(by_label.values())


def _semantic_placeholders(sql: str, findings: list[Finding]) -> list[Finding]:
    """Создаём semantic placeholders для Stage 4 judge.

    Insight 3: NO_PAGINATION больше не порождает EXCESSIVE_SCOPE placeholder —
    отсутствие LIMIT это quality-замечание, не security. Из quality
    findings нельзя выводить semantic security label.
    SELECT_STAR оставляем триггером, потому что `SELECT *` действительно
    может тащить чувствительные колонки и заслуживает judge-проверки.
    """
    labels = {item.label for item in findings}
    out: list[Finding] = []
    upper = sql.upper()
    if "SELECT_STAR" in labels and "SELECT" in upper:
        out.append(_placeholder("EXCESSIVE_SCOPE", "rule.placeholder.excessive_scope"))
    if " JOIN " in upper:
        out.append(_placeholder("WRONG_JOIN_PATH", "rule.placeholder.wrong_join_path"))
    return out


def _placeholder(label: str, detector: str) -> Finding:
    return Finding(
        label=label,
        severity=0.0,
        confidence=0.0,
        evidence_span="",
        revision_note="Run semantic judge.",
        layer="rule",
        detector=detector,
        description="needs_llm_judge",
        recommendation="Run semantic judge.",
    )


def _model_semantic_placeholders(
    stage2_output: ml.MLOutput | None,
    stage3_output: dict[str, Any],
) -> list[Finding]:
    labels = set(stage2_output.labels_above_threshold if stage2_output and stage2_output.available else [])
    labels |= set(stage3_output.get("labels") or [])
    return [
        _placeholder(label, "model.placeholder." + label.lower())
        for label in sorted(labels & JUDGE_LABELS)
    ]


def _filter_stage2_labels(
    stage2_output: ml.MLOutput | None,
    cleared: set[str],
) -> ml.MLOutput | None:
    if stage2_output is None or not cleared:
        return stage2_output
    return ml.MLOutput(
        probs=stage2_output.probs,
        labels_above_threshold=[
            label for label in stage2_output.labels_above_threshold
            if label not in cleared
        ],
        calibrated_thresholds=stage2_output.calibrated_thresholds,
        model_type=stage2_output.model_type,
        model_version=stage2_output.model_version,
        available=stage2_output.available,
    )


def _filter_stage3_labels(output: dict[str, Any], cleared: set[str]) -> dict[str, Any]:
    if not cleared:
        return output
    filtered = dict(output)
    filtered["labels"] = [label for label in (output.get("labels") or []) if label not in cleared]
    filtered["tie_zone"] = [label for label in (output.get("tie_zone") or []) if label not in cleared]
    return filtered


def _merge_judge(findings: list[Finding], judge_findings: list[Finding]) -> list[Finding]:
    if not judge_findings:
        return _dedupe(findings)
    judge_labels = {item.label for item in judge_findings}
    kept = [
        item for item in findings
        if not (item.label in judge_labels and item.label in JUDGE_LABELS)
        and item.description != "needs_llm_judge"
    ]
    return _dedupe(kept + judge_findings)


def _severity_gate(findings: list[Finding]) -> bool:
    """H10: блокируем только security-метки. Quality (NO_PAGINATION,
    SELECT_STAR, NON_SARGABLE_FILTER, COST_DOS, ...) — advisory."""
    try:
        from app import sql_guard
        bucket = sql_guard.label_bucket
    except Exception:
        bucket = lambda _x: "security"  # noqa: E731
    for item in findings:
        if bucket(item.label) != "security":
            continue
        if item.severity >= 9.0:
            return True
        if item.severity >= 6.0 and item.confidence >= 0.9:
            return True
    return False


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


_AGGREGATE_INTENT_RE = __import__("re").compile(
    r"(?:без\s+(?:персональных|пдн|личных)\s+полей|без\s+ПДн|обезличенн\w*|aggregate|агрегат|count\b|подсчит\w*|"
    r"сколько|сумма|среднее|average|safe\s+(?:employee\s+)?report|сводн\w*\s+отч[её]т)",
    __import__("re").IGNORECASE,
)


def _is_aggregate_safe_intent(ctx: dict[str, Any]) -> bool:
    task = str(ctx.get("task", "") or "")
    return bool(_AGGREGATE_INTENT_RE.search(task))


def _sql_is_pure_aggregate(sql: str) -> bool:
    """True если SELECT возвращает один скалярный агрегат без GROUP BY.

    Используем helper sql_guard для единого определения по проекции.
    """
    try:
        from app import sql_guard
        return bool(sql_guard._is_pure_aggregate(sql.upper()))
    except Exception:
        return False


def _label_possible(label: str, ctx: dict[str, Any]) -> bool:
    if label.startswith("PROMPT_") and not str(ctx.get("attack_prompt", "")).strip():
        return False
    # H6: для aggregate-safe / "без персональных полей" intent ML stage 2/3
    # стабильно даёт false-positive MASKING_REQUIRED. Гасим до judge-вызова.
    if label == "MASKING_REQUIRED" and _is_aggregate_safe_intent(ctx):
        return False
    return True


@lru_cache(maxsize=1)
def _encoder() -> encoder.EncoderClassifier:
    return encoder.EncoderClassifier()


__all__ = ["ClassifierOutput", "Finding", "classify"]
