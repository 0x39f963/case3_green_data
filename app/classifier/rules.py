"""
Stage 1 classifier rules.

Этот слой - тонкая обертка над app/audit_tools.py. Он нужен, чтобы
будущий ensemble мог комбинировать Stage 1 rules, Stage 2 ML, Stage 3
encoder и Stage 4 judge без прямых вызовов tools из policy code.
"""

from __future__ import annotations

from typing import Any

from app import audit_tools
from app.classifier.types import Finding


RULE_TOOLS = [
    audit_tools.check_statement_boundary,
    audit_tools.check_classic_sqli,
    audit_tools.check_plpgsql,
    audit_tools.check_mutation,
    audit_tools.check_data_exposure,
    audit_tools.check_reliability,
    audit_tools.check_generation_quality,
    audit_tools.check_business_alignment,
]


def run_rules(sql: str, ctx: dict[str, Any] | None = None) -> list[Finding]:
    """
    Последовательно запустить семь deterministic rule tools.

    Обертка не агрегирует вывод. classify() отвечает за dedupe и policy.
    """
    data = ctx or {}
    findings: list[Finding] = []
    for tool_obj in RULE_TOOLS:
        items = tool_obj.invoke({"sql": sql, "ctx": data})
        for item in items:
            findings.append(_to_finding(item, tool_obj.name))
    return findings


def _to_finding(item: dict[str, Any], detector_default: str) -> Finding:
    return Finding(
        label=str(item.get("label", "")),
        severity=float(item.get("severity", 0.0)),
        confidence=float(item.get("confidence", 1.0)),
        evidence_span=str(item.get("evidence_span", "")),
        revision_note=str(item.get("revision_note", "")),
        layer=str(item.get("layer", "rule")),
        detector=str(item.get("detector", detector_default)),
        description=str(item.get("description", "")),
        recommendation=str(item.get("recommendation", "")),
    )
