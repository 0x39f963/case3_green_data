"""
Stage 5 classifier ensemble decision policy.

Rules keep critical OR-gate priority. ML and encoder add calibrated
probability findings. Judge findings replace semantic placeholders when
available. The output remains the ClassifierOutput contract used by
auditor and eval scripts.
"""

from __future__ import annotations

from typing import Any

from app import sql_guard
from app.classifier.ml import MLOutput
from app.classifier.types import ClassifierOutput, Finding

RISK_THRESHOLD = 4.0
CRITICAL_LABELS = frozenset(
    {
        "SQL_INJ_CLASSIC",
        "SQL_INJ_UNION",
        "SQL_INJ_TIME",
        "PLPGSQL_UNSAFE",
        "DML_NO_WHERE",
        "PRIV_ESCALATE",
        "DDL_FORBIDDEN",
        "TRUNCATE",
        "COPY_EXPORT",
        "MULTI_STATEMENT",
        "DYNAMIC_EXECUTE",
    }
)
SEMANTIC_LABELS = frozenset(
    {
        "DIRECT_SENSITIVE",
        "EXCESSIVE_SCOPE",
        "WRONG_JOIN_PATH",
        "HALLUCINATED_TABLE",
        "HALLUCINATED_COLUMN",
        "MASKING_REQUIRED",
        "AMBIGUOUS_USER_SCOPE",
    }
)


def aggregate(
    stage1_findings: list[Finding],
    stage2_output: MLOutput | None = None,
    stage3_output: dict[str, Any] | None = None,
    stage4_findings: list[Finding] | None = None,
) -> ClassifierOutput:
    findings = []
    findings.extend(stage1_findings)
    findings.extend(_stage2_findings(stage2_output))
    findings.extend(_stage3_findings(stage3_output or {}))
    findings = _merge_judge(findings, stage4_findings or [])
    findings = _dedupe(findings)

    max_severity = max((item.severity for item in findings), default=0.0)
    risk_labels = sorted({item.label for item in findings if item.label})
    critical_block = _critical_block(findings)
    # H10: high_block считаем только по security-меткам. Quality-метки
    # (NO_PAGINATION/SELECT_STAR/NON_SARGABLE/COST_DOS) — advisory.
    try:
        from app import sql_guard as _sg
        _bucket = _sg.label_bucket
    except Exception:
        _bucket = lambda _x: "security"  # noqa: E731
    high_block = any(
        _bucket(item.label) == "security"
        and item.severity >= 7.0
        and item.confidence >= 0.85
        for item in findings
    )
    needs_judge = _needs_judge(stage1_findings, stage2_output, stage3_output or {}, stage4_findings or [])
    needs_regeneration = (not critical_block) and any(4.0 <= item.severity < 7.0 for item in findings)

    if critical_block:
        action = "block"
        approved = False
    elif high_block:
        action = "block"
        approved = False
    elif needs_judge:
        action = "send_to_judge"
        approved = False
    elif needs_regeneration:
        action = "regenerate"
        approved = False
    else:
        action = "approve"
        approved = max_severity < RISK_THRESHOLD

    return ClassifierOutput(
        approved_by_classifier=approved,
        max_severity=max_severity,
        risk_labels=risk_labels,
        findings=findings,
        needs_llm_judge=needs_judge,
        needs_regeneration=needs_regeneration,
        stage_outputs={
            "stage_5_ensemble": {
                "enabled": True,
                "action": action,
                "critical_block": critical_block,
                "high_block": high_block,
            }
        },
    )


def _stage2_findings(output: MLOutput | None) -> list[Finding]:
    if output is None or not output.available:
        return []
    out: list[Finding] = []
    for label in output.labels_above_threshold:
        prob = float(output.probs.get(label, 0.0))
        out.append(
            Finding(
                label=label,
                severity=float(sql_guard.SEVERITY_BY_LABEL.get(label, 0.0)),
                confidence=prob,
                evidence_span="probability=" + f"{prob:.3f}",
                revision_note=sql_guard.REVISION_NOTES.get(label, "Review classifier probability."),
                layer="ml",
                detector=output.model_type + "_" + output.model_version,
                description="Stage 2 probability above calibrated threshold.",
                recommendation=sql_guard.REVISION_NOTES.get(label, "Review classifier probability."),
            )
        )
    return out


def _stage3_findings(output: dict[str, Any]) -> list[Finding]:
    labels = output.get("labels") or []
    probs = output.get("probs") or {}
    out: list[Finding] = []
    for label in labels:
        prob = float(probs.get(label, 0.0))
        out.append(
            Finding(
                label=label,
                severity=float(sql_guard.SEVERITY_BY_LABEL.get(label, 0.0)),
                confidence=prob,
                evidence_span="encoder_probability=" + f"{prob:.3f}",
                revision_note=sql_guard.REVISION_NOTES.get(label, "Review encoder probability."),
                layer="encoder",
                detector="modernbert_" + str(output.get("version", "v1_0")),
                description="Stage 3 probability above calibrated threshold.",
                recommendation=sql_guard.REVISION_NOTES.get(label, "Review encoder probability."),
            )
        )
    return out


def _merge_judge(findings: list[Finding], judge_findings: list[Finding]) -> list[Finding]:
    if not judge_findings:
        return findings
    judge_labels = {item.label for item in judge_findings}
    kept = [
        item for item in findings
        if not (item.label in judge_labels and item.label in SEMANTIC_LABELS)
        and item.description != "needs_llm_judge"
    ]
    return kept + judge_findings


def _dedupe(findings: list[Finding]) -> list[Finding]:
    by_label: dict[str, Finding] = {}
    for item in findings:
        old = by_label.get(item.label)
        if old is None or (item.severity, item.confidence) > (old.severity, old.confidence):
            by_label[item.label] = item
    return list(by_label.values())


def _critical_block(findings: list[Finding]) -> bool:
    for item in findings:
        if item.label in CRITICAL_LABELS and item.confidence >= 0.5:
            return True
        if item.severity >= 9.0:
            return True
    return False


def _needs_judge(
    stage1_findings: list[Finding],
    stage2_output: MLOutput | None,
    stage3_output: dict[str, Any],
    stage4_findings: list[Finding],
) -> bool:
    if stage4_findings:
        return False
    labels = {item.label for item in stage1_findings}
    if labels & SEMANTIC_LABELS:
        return True
    model_labels = set(stage2_output.labels_above_threshold if stage2_output else [])
    if model_labels & SEMANTIC_LABELS:
        return True
    return bool(model_labels and labels and model_labels != labels)


__all__ = ["aggregate", "CRITICAL_LABELS", "SEMANTIC_LABELS"]
