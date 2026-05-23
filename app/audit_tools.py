"""
Девять LangChain tool-функций для SQL-аудита.

Q-I1 и Q-I2 оставляют LangGraph оркестратором и выносят проверки в
узкие LangChain @tool: семь детерминированных rule tools и два judge
tools. Stage 1 classifier вызывает эти функции напрямую и получает
JSON-friendly finding dictionaries.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from app import sql_guard


JUDGE_STUB_METADATA = {"status": "needs_llm_judge_in_step3"}


@tool
def check_statement_boundary(sql: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Проверить MULTI_STATEMENT и COMMENT_TRUNCATION."""
    labels = ("MULTI_STATEMENT", "COMMENT_TRUNCATION")
    return _pack(sql_guard.check_by_labels(sql, labels, ctx))


@tool
def check_classic_sqli(sql: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Проверить classic SQL injection labels."""
    labels = (
        "SQL_INJ_CLASSIC",
        "SQL_INJ_UNION",
        "UNION_EXFIL",
        "TAUTOLOGY",
        "SQL_INJ_TIME",
        "TIME_DELAY",
    )
    return _pack(sql_guard.check_by_labels(sql, labels, ctx))


@tool
def check_plpgsql(sql: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Проверить PL/pgSQL, dynamic execute и privilege escalation."""
    labels = ("PLPGSQL_UNSAFE", "DYNAMIC_EXECUTE", "PRIV_ESCALATE")
    return _pack(sql_guard.check_by_labels(sql, labels, ctx))


@tool
def check_mutation(sql: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Проверить mutation/DDL/export с учетом allowed_ops policy."""
    labels = ("DML_NO_WHERE", "DDL_FORBIDDEN", "TRUNCATE", "COPY_EXPORT", "INSERT_UNSAFE")
    return _pack(sql_guard.check_by_labels(sql, labels, ctx))


@tool
def check_data_exposure(sql: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Проверить DIRECT_SENSITIVE, SCHEMA_LEAK и SELECT_STAR."""
    labels = ("DIRECT_SENSITIVE", "SCHEMA_LEAK", "SELECT_STAR")
    return _pack(sql_guard.check_by_labels(sql, labels, ctx))


@tool
def check_reliability(sql: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Проверить reliability labels."""
    labels = (
        "NO_PAGINATION",
        "CROSS_JOIN_EXPLOSION",
        "COST_DOS",
        "NON_SARGABLE_FILTER",
        "RECURSIVE_UNBOUNDED",
    )
    return _pack(sql_guard.check_by_labels(sql, labels, ctx))


@tool
def check_generation_quality(sql: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Проверить generation-quality labels."""
    labels = ("HALLUCINATED_TABLE", "HALLUCINATED_COLUMN", "BROKEN_SQL", "UNSAFE_CAST")
    return _pack(sql_guard.check_by_labels(sql, labels, ctx))


@tool
def judge_sensitive_exposure(sql: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Judge tool для MASKING_REQUIRED и EXCESSIVE_SCOPE."""
    return judge_semantic_correction.invoke({"sql": sql, "ctx": ctx or {}})


@tool
def judge_semantic_correction(sql: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Judge tool для semantic correction labels."""
    from app.classifier import judge

    data = ctx or {}
    findings = judge.judge_semantic(
        sql=sql,
        task=str(data.get("task", "")),
        schema_context=str(data.get("schema_context", "")),
        sensitive_fields=data.get("sensitive_fields") or {},
        allowed_tables=data.get("allowed_tables") or [],
        allowed_columns=data.get("allowed_columns") or {},
        placeholder_findings=_ctx_findings(data.get("placeholder_findings") or []),
    )
    return [_finding_to_dict(item) for item in findings]


def _pack(findings) -> list[dict[str, Any]]:
    """Преобразовать Vulnerability в стабильный tool dict."""
    return [sql_guard.vulnerability_to_dict(item) for item in findings]


def _ctx_findings(items: list[Any]) -> list[Finding]:
    from app.classifier.types import Finding

    out: list[Finding] = []
    for item in items:
        if isinstance(item, Finding):
            out.append(item)
        elif isinstance(item, dict):
            out.append(
                Finding(
                    label=str(item.get("label", "")),
                    severity=float(item.get("severity", 0.0)),
                    confidence=float(item.get("confidence", 0.0)),
                    evidence_span=str(item.get("evidence_span", "")),
                    revision_note=str(item.get("revision_note", "")),
                    layer=str(item.get("layer", "rule")),
                    detector=str(item.get("detector", "ctx")),
                    description=str(item.get("description", "")),
                    recommendation=str(item.get("recommendation", "")),
                )
            )
    return out


def _finding_to_dict(item: Any) -> dict[str, Any]:
    return {
        "label": item.label,
        "severity": item.severity,
        "confidence": item.confidence,
        "evidence_span": item.evidence_span,
        "revision_note": item.revision_note,
        "description": item.description,
        "recommendation": item.recommendation,
        "layer": item.layer,
        "detector": item.detector,
    }
