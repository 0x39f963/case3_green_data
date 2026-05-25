"""Dataclasses for oracle-aware eval."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


OracleType = Literal[
    "reference_sql",
    "refusal_only",
    "clarification_only",
    "safe_rewrite",
    "policy_plus_sql",
]


@dataclass
class OracleCase:
    """One row from golden v1.1."""

    test_id: str
    category_id: str
    category_name: str
    nl_prompt: str
    attack_class: str
    expected_behavior: str
    oracle_type: OracleType
    reference_sql: str
    reference_params: dict[str, Any]
    semantic_assertions: list[str]
    comparison_method: str
    oracle_notes: str
    severity_if_failed: str
    raw_row: dict[str, Any]


@dataclass
class AssertionVerdict:
    """Result of one semantic_assertion check."""

    name: str
    passed: bool
    reason: str = ""


@dataclass
class OracleVerdict:
    """Final verdict for one case."""

    test_id: str
    oracle_type: str
    verdict: Literal["pass", "fail", "error"]
    ast_semantic_ok: bool | None
    assertions: list[AssertionVerdict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    pipeline_decision: str | None = None
    pipeline_final_sql: str | None = None
    elapsed_sec: float = 0.0
    error_message: str | None = None
    category_id: str | None = None
    severity_if_failed: str | None = None


@dataclass
class EvalReport:
    """Aggregate oracle-aware eval report."""

    dataset_version: str
    total_cases: int
    by_oracle_type: dict[str, dict[str, int]]
    by_severity: dict[str, dict[str, int]]
    aggregate_pass_rate: float
    cases: list[OracleVerdict] = field(default_factory=list)
    run_id: str = ""
    llm_mode: str = ""
    llm_generator_model: str = ""
    created_at: str = ""
