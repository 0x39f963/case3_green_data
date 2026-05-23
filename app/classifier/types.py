"""
Общие dataclass-типы для staged classifier B3.

Типы вынесены из __init__.py, чтобы Stage 1 rules мог возвращать
Finding без circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Finding:
    """Одна находка classifier с evidence и metadata detector."""

    label: str
    severity: float
    confidence: float
    evidence_span: str
    revision_note: str
    layer: str
    detector: str
    description: str = ""
    recommendation: str = ""


@dataclass
class ClassifierOutput:
    """Выход classify(sql, ctx) для B3 classifier contract."""

    approved_by_classifier: bool
    max_severity: float
    risk_labels: list[str]
    findings: list[Finding] = field(default_factory=list)
    needs_llm_judge: bool = False
    needs_regeneration: bool = False
    stage_outputs: dict[str, Any] = field(default_factory=dict)
