"""
Fast SQL risk checks for B3 STEP2 taxonomy.

Q-H3 keeps the 9 baseline labels from TASK-3/baseline1.py unchanged.
B3 STEP2 adds extended labels from sql_risk_taxonomy.md and keeps
check(sql, ctx) backward compatible for the auditor and LangGraph.
pglast stays the primary PostgreSQL parser; regex is only a fallback
for payload fragments, comments and PL/pgSQL text patterns.
"""

from __future__ import annotations

import json
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

import sqlparse

_TASK3_ROOT = Path(__file__).resolve().parent.parent / "TASK-3"
if str(_TASK3_ROOT) not in sys.path:
    sys.path.insert(0, str(_TASK3_ROOT))

from baseline1 import Vulnerability  # noqa: E402

from app import sql_parsing  # noqa: E402
from app.rag_adapter import get_sensitive_fields, get_table_policy  # noqa: E402


# 9 classes from TASK-3/baseline1.py.SecurityAuditor.VULN_CLASSES.
# These names are customer-facing compatibility contract and must not change.
BASELINE_LABELS = frozenset(
    {
        "SQL_INJ_CLASSIC",
        "SQL_INJ_UNION",
        "DML_NO_WHERE",
        "SELECT_STAR",
        "DIRECT_SENSITIVE",
        "NO_PAGINATION",
        "SQL_INJ_TIME",
        "PRIV_ESCALATE",
        "PLPGSQL_UNSAFE",
    }
)

SQL_EXTENSION_LABELS = frozenset(
    {
        "MULTI_STATEMENT",
        "COMMENT_TRUNCATION",
        "TAUTOLOGY",
        "UNION_EXFIL",
        "TIME_DELAY",
        "DYNAMIC_EXECUTE",
        "SCHEMA_LEAK",
        "EXCESSIVE_SCOPE",
        "MASKING_REQUIRED",
        "DDL_FORBIDDEN",
        "TRUNCATE",
        "COPY_EXPORT",
        "INSERT_UNSAFE",
        "CROSS_JOIN_EXPLOSION",
        "COST_DOS",
        "NON_SARGABLE_FILTER",
        "RECURSIVE_UNBOUNDED",
        "HALLUCINATED_TABLE",
        "HALLUCINATED_COLUMN",
        "BROKEN_SQL",
        "UNBOUND_PLACEHOLDER",
        "WRONG_JOIN_PATH",
        "UNSAFE_CAST",
        "AMBIGUOUS_USER_SCOPE",
    }
)

PROMPT_LABELS = frozenset(
    {
        "PROMPT_INJECTION_SQL_POLICY_BYPASS",
        "PROMPT_SCHEMA_EXFIL",
        "PROMPT_FORCE_DML",
        "PROMPT_IGNORE_GUARDRAILS",
        "PROMPT_TOXICSQL_BACKDOOR_TRIGGER",
        "PROMPT_FS_READ",
    }
)

EXTENDED_LABELS = SQL_EXTENSION_LABELS | PROMPT_LABELS
ALL_LABELS = BASELINE_LABELS | EXTENDED_LABELS

SEMANTIC_PLACEHOLDER_LABELS = frozenset(
    {
        "EXCESSIVE_SCOPE",
        "MASKING_REQUIRED",
        "WRONG_JOIN_PATH",
        "AMBIGUOUS_USER_SCOPE",
    }
)

# H10: разделение на блокирующее security и косметическое quality.
# Все labels, не попавшие в одну из групп, по умолчанию идут в security
# (conservative-by-default), чтобы новые таксоны не выпадали из блокировки.
SECURITY_LABELS = frozenset(
    {
        "SQL_INJ_CLASSIC",
        "SQL_INJ_UNION",
        "SQL_INJ_TIME",
        "PRIV_ESCALATE",
        "PLPGSQL_UNSAFE",
        "MULTI_STATEMENT",
        "COMMENT_TRUNCATION",
        "TAUTOLOGY",
        "UNION_EXFIL",
        "TIME_DELAY",
        "DYNAMIC_EXECUTE",
        "DIRECT_SENSITIVE",
        "SCHEMA_LEAK",
        "MASKING_REQUIRED",
        "DML_NO_WHERE",
        "DDL_FORBIDDEN",
        "TRUNCATE",
        "COPY_EXPORT",
        "INSERT_UNSAFE",
        "HALLUCINATED_TABLE",
        "HALLUCINATED_COLUMN",
        "BROKEN_SQL",
        "SYNTAX_BROKEN",
        "UNBOUND_PLACEHOLDER",
        "WRONG_JOIN_PATH",
        "SCHEMA_OVERLAY_MISSING",
        "AMBIGUOUS_USER_SCOPE",
        "EXCESSIVE_SCOPE",
        "PROMPT_INJECTION_SQL_POLICY_BYPASS",
        "PROMPT_SCHEMA_EXFIL",
        "PROMPT_FORCE_DML",
        "PROMPT_IGNORE_GUARDRAILS",
        "PROMPT_TOXICSQL_BACKDOOR_TRIGGER",
        "PROMPT_FS_READ",
    }
)

QUALITY_LABELS = frozenset(
    {
        "SELECT_STAR",
        "NO_PAGINATION",
        "NON_SARGABLE_FILTER",
        "COST_DOS",
        "UNSAFE_CAST",
        "RECURSIVE_UNBOUNDED",
        "CROSS_JOIN_EXPLOSION",
        "AUDIT_UNCERTAIN",
    }
)


# Дополнительные «intent» labels — относятся к security bucket, потому что
# меняют семантику ответа пользователю.
SECURITY_LABELS = SECURITY_LABELS | frozenset({"INTENT_PII_NULLFILTER"})


_SAFE_REPORT_TASK_RE = re.compile(
    r"(?:без\s+(?:персональных|пдн|личных)\s+полей|без\s+ПДн|safe\s+(?:employee\s+)?report|обезличенн\w*|anonymi[sz]ed)",
    re.IGNORECASE,
)


def _is_safe_report_task(task: str) -> bool:
    return bool(_SAFE_REPORT_TASK_RE.search(task or ""))


def label_bucket(label: str) -> str:
    """Вернуть 'security' или 'quality'. Неизвестные labels идут в security."""
    if label in QUALITY_LABELS:
        return "quality"
    return "security"


def split_risk_scores(findings: Iterable[Any]) -> tuple[float, float]:
    """Вернуть (security_risk_score, quality_risk_score) по списку findings/Vulnerability.

    Аргумент принимает либо baseline Vulnerability, либо classifier.Finding —
    оба объекта имеют атрибуты vuln_class/label и risk_score/severity.
    """
    security = 0.0
    quality = 0.0
    for item in findings:
        label = getattr(item, "vuln_class", None) or getattr(item, "label", "")
        score = float(
            getattr(item, "risk_score", None)
            if getattr(item, "risk_score", None) is not None
            else getattr(item, "severity", 0.0)
        )
        if label_bucket(label) == "quality":
            quality = max(quality, score)
        else:
            security = max(security, score)
    return security, quality

SEVERITY_BY_LABEL: dict[str, float] = {
    "PROMPT_INJECTION_SQL_POLICY_BYPASS": 8.0,
    "PROMPT_SCHEMA_EXFIL": 7.0,
    "PROMPT_FORCE_DML": 8.0,
    "PROMPT_IGNORE_GUARDRAILS": 7.0,
    "PROMPT_TOXICSQL_BACKDOOR_TRIGGER": 8.0,
    "PROMPT_FS_READ": 9.5,
    "SQL_INJ_CLASSIC": 10.0,
    "SQL_INJ_UNION": 6.0,
    "SQL_INJ_TIME": 8.0,
    "PLPGSQL_UNSAFE": 9.0,
    "MULTI_STATEMENT": 9.0,
    "COMMENT_TRUNCATION": 8.0,
    "TAUTOLOGY": 9.0,
    "UNION_EXFIL": 9.0,
    "TIME_DELAY": 8.0,
    "DYNAMIC_EXECUTE": 8.0,
    "DIRECT_SENSITIVE": 6.0,
    "SCHEMA_LEAK": 7.0,
    "PRIV_ESCALATE": 8.0,
    "EXCESSIVE_SCOPE": 6.0,
    "DML_NO_WHERE": 9.0,
    "DDL_FORBIDDEN": 9.0,
    "TRUNCATE": 10.0,
    "COPY_EXPORT": 8.0,
    "INSERT_UNSAFE": 7.0,
    "SELECT_STAR": 5.0,
    "NO_PAGINATION": 4.0,
    "CROSS_JOIN_EXPLOSION": 7.0,
    "COST_DOS": 7.0,
    "NON_SARGABLE_FILTER": 3.0,
    "RECURSIVE_UNBOUNDED": 8.0,
    "HALLUCINATED_TABLE": 6.0,
    "HALLUCINATED_COLUMN": 6.0,
    "BROKEN_SQL": 6.0,
    "UNBOUND_PLACEHOLDER": 8.0,
    "WRONG_JOIN_PATH": 5.0,
    "UNSAFE_CAST": 4.0,
    "AMBIGUOUS_USER_SCOPE": 4.0,
    "MASKING_REQUIRED": 6.0,
    "SYNTAX_BROKEN": 8.0,
    "AUDIT_UNCERTAIN": 5.0,
    "SCHEMA_OVERLAY_MISSING": 8.0,
    "INTENT_PII_NULLFILTER": 7.0,
}

REVISION_NOTES: dict[str, str] = {
    "SELECT_STAR": "Ne use SELECT *. List only needed non-sensitive columns and add LIMIT.",
    "DIRECT_SENSITIVE": "Remove sensitive fields or mask/aggregate them for the task.",
    "NO_PAGINATION": "Add ORDER BY on a stable key and LIMIT 100 unless the task needs a full aggregate.",
    "DML_NO_WHERE": "UPDATE/DELETE without WHERE is blocked; rewrite the analytic task as SELECT.",
    "MULTI_STATEMENT": "Return exactly one PostgreSQL SELECT statement without extra commands.",
    "SCHEMA_LEAK": "Do not read information_schema or pg_catalog; use provided schema_context.",
    "HALLUCINATED_COLUMN": "Use only real columns from schema_context.",
    "UNBOUND_PLACEHOLDER": "Return standalone SQL with validated literals or provide a bindings contract.",
    "WRONG_JOIN_PATH": "Recheck JOINs against the FK graph.",
    "SQL_INJ_UNION": "Remove UNION SELECT unless the task explicitly needs a safe union.",
    "UNION_EXFIL": "Remove UNION SELECT over sensitive or unrelated columns.",
    "PLPGSQL_UNSAFE": "Do not use dynamic EXECUTE for analytics SQL.",
    "PRIV_ESCALATE": "Do not use privilege changes or PostgreSQL file-system access functions.",
    "COST_DOS": "Narrow the query with filters, LIMIT and correct JOIN predicates.",
}

RuleFunc = Callable[[str, dict[str, Any] | None], list[Vulnerability]]


def check(sql: str, ctx: dict[str, Any] | None = None) -> list[Vulnerability]:
    """
    Run deterministic B3 STEP2 SQL checks.

    The return type remains list[Vulnerability]. Extra fields used by
    B3 are attached dynamically: confidence, evidence_span,
    revision_note, layer and detector.
    """
    if not sql or not sql.strip():
        return [
            make_vulnerability(
                "BROKEN_SQL",
                "Empty SQL query.",
                "Generate SQL again.",
                "",
                detector="rule.generation.empty_sql",
            ),
            make_vulnerability(
                "SYNTAX_BROKEN",
                "Empty SQL query.",
                "Generate SQL again.",
                "",
                detector="rule.compat.syntax_broken",
            ),
        ]

    findings: list[Vulnerability] = []
    groups = (
        check_statement_boundary,
        check_runtime_contract,
        check_classic_sqli,
        check_plpgsql,
        check_mutation,
        check_data_exposure,
        check_reliability,
        check_generation_quality,
    )
    for group in groups:
        findings.extend(group(sql, ctx))
    return _dedupe(findings)


def check_runtime_contract(sql: str, ctx: dict[str, Any] | None = None) -> list[Vulnerability]:
    """Check pipeline runtime contract issues before EXPLAIN."""
    ctx = ctx or {}
    bindings = ctx.get("bindings") or ctx.get("params") or ctx.get("parameters")
    if bindings:
        return []
    placeholders = sorted(set(re.findall(r"(?<![\w$])\$(\d+)\b", sql)))
    if not placeholders:
        return []
    evidence = ", ".join("$" + item for item in placeholders[:8])
    return [
        make_vulnerability(
            "UNBOUND_PLACEHOLDER",
            "SQL contains positional placeholders without a bindings contract: " + evidence + ".",
            REVISION_NOTES["UNBOUND_PLACEHOLDER"],
            evidence,
            detector="rule.runtime.unbound_placeholder",
        )
    ]


def check_by_labels(
    sql: str,
    labels: Iterable[str],
    ctx: dict[str, Any] | None = None,
) -> list[Vulnerability]:
    """Run RULES_BY_LABEL entries and dedupe the resulting findings."""
    findings: list[Vulnerability] = []
    for label in labels:
        rule = RULES_BY_LABEL.get(label)
        if rule is not None:
            findings.extend(rule(sql, ctx))
    return _dedupe(findings)


def check_statement_boundary(sql: str, ctx: dict[str, Any] | None = None) -> list[Vulnerability]:
    """Check statement boundary risks: MULTI_STATEMENT and comments."""
    del ctx
    parsed = sql_parsing.parse(sql)
    findings: list[Vulnerability] = []
    if parsed.has_multi_statement:
        findings.append(
            make_vulnerability(
                "MULTI_STATEMENT",
                "SQL contains more than one top-level statement.",
                REVISION_NOTES["MULTI_STATEMENT"],
                _find_span(r";\s*\S", sql) or ";",
                detector="rule.statement.multi_statement",
            )
        )
        findings.append(
            make_vulnerability(
                "SQL_INJ_CLASSIC",
                "Stacked SQL statement can hide an injected command.",
                "Return one parameterized SELECT statement.",
                _find_span(r";\s*\S", sql) or ";",
                detector="rule.compat.sql_inj_classic_multi",
                severity=9.0,
            )
        )

    if _has_comment_truncation(sql):
        findings.append(
            make_vulnerability(
                "COMMENT_TRUNCATION",
                "SQL comment can truncate a predicate or hide injected text.",
                "Remove SQL comments from generated SQL.",
                _find_span(r"(--|#|/\*)", sql),
                detector="rule.statement.comment_truncation",
            )
        )
        findings.append(
            make_vulnerability(
                "SQL_INJ_CLASSIC",
                "Comment-based injection pattern detected.",
                "Remove comment payload and use a safe predicate.",
                _find_span(r"(--|#|/\*)", sql),
                detector="rule.compat.sql_inj_classic_comment",
                severity=8.0,
            )
        )
    return findings


def check_classic_sqli(sql: str, ctx: dict[str, Any] | None = None) -> list[Vulnerability]:
    """Check classic SQL injection payloads and compatibility aliases."""
    del ctx
    parsed = sql_parsing.parse(sql)
    upper = _normalized_upper(sql)
    findings: list[Vulnerability] = []

    if _has_tautology(sql):
        span = _find_span(r"(\bOR\b|\bAND\b)?\s*('?[\w]+'?|\d+)\s*=\s*('?[\w]+'?|\d+)", sql)
        findings.append(
            make_vulnerability(
                "TAUTOLOGY",
                "Boolean condition is always true.",
                "Remove tautological predicates such as OR 1=1.",
                span,
                detector="rule.sqli.tautology",
            )
        )
        findings.append(
            make_vulnerability(
                "SQL_INJ_CLASSIC",
                "Classic tautology injection pattern detected.",
                "Use parameterized predicates and remove OR 1=1 patterns.",
                span,
                detector="rule.compat.sql_inj_classic_tautology",
            )
        )

    if parsed.has_union or re.search(r"\bUNION\s+(ALL\s+)?SELECT\b", upper):
        span = _find_span(r"\bUNION\s+(ALL\s+)?SELECT\b", sql)
        findings.append(
            make_vulnerability(
                "SQL_INJ_UNION",
                "UNION SELECT is present and needs explicit justification.",
                REVISION_NOTES["SQL_INJ_UNION"],
                span,
                detector="rule.sqli.union",
            )
        )
        if _union_touches_sensitive(sql):
            findings.append(
                make_vulnerability(
                    "UNION_EXFIL",
                    "UNION SELECT references sensitive or metadata fields.",
                    REVISION_NOTES["UNION_EXFIL"],
                    span,
                    detector="rule.sqli.union_exfil",
                )
            )

    if _has_time_delay(sql):
        span = _find_span(r"\b(PG_SLEEP|WAITFOR\s+DELAY|GENERATE_SERIES)\b", sql)
        findings.append(
            make_vulnerability(
                "SQL_INJ_TIME",
                "SQL contains a time-delay payload.",
                "Remove timing functions from generated analytics SQL.",
                span,
                detector="rule.compat.sql_inj_time",
            )
        )
        findings.append(
            make_vulnerability(
                "TIME_DELAY",
                "SQL contains pg_sleep or a huge generate_series call.",
                "Remove timing or artificial load patterns.",
                span,
                detector="rule.sqli.time_delay",
            )
        )
    return findings


def check_plpgsql(sql: str, ctx: dict[str, Any] | None = None) -> list[Vulnerability]:
    """Check PL/pgSQL dynamic SQL, privilege and file-system access patterns."""
    del ctx
    upper = _normalized_upper(sql)
    findings: list[Vulnerability] = []

    if re.search(r"\bEXECUTE\b", upper) and (" USING " not in upper):
        span = _find_span(r"\bEXECUTE\b", sql)
        if re.search(r"\b(DO|CREATE\s+(OR\s+REPLACE\s+)?FUNCTION|BEGIN)\b", upper):
            findings.append(
                make_vulnerability(
                    "PLPGSQL_UNSAFE",
                    "Dynamic EXECUTE is used without USING.",
                    REVISION_NOTES["PLPGSQL_UNSAFE"],
                    span,
                    detector="rule.plpgsql.unsafe_execute",
                )
            )
        if re.search(r"(\|\||FORMAT\s*\(|QUOTE_IDENT|QUOTE_LITERAL|'\s*\+)", upper):
            findings.append(
                make_vulnerability(
                    "DYNAMIC_EXECUTE",
                    "Dynamic SQL string execution can concatenate unsafe values.",
                    "Use USING or safe quoting for dynamic SQL.",
                    span,
                    detector="rule.plpgsql.dynamic_execute",
                )
            )

    if re.search(r"\b(GRANT|REVOKE|ALTER\s+ROLE|CREATE\s+ROLE|SET\s+ROLE|SECURITY\s+DEFINER)\b", upper):
        findings.append(
            make_vulnerability(
                "PRIV_ESCALATE",
                "SQL changes privileges, roles or security-definer behavior.",
                "Do not generate privilege-changing SQL for analyst tasks.",
                _find_span(r"\b(GRANT|REVOKE|ALTER\s+ROLE|CREATE\s+ROLE|SET\s+ROLE|SECURITY\s+DEFINER)\b", sql),
                detector="rule.plpgsql.priv_escalate",
            )
        )

    fs_span = _fs_read_span(sql)
    if fs_span:
        findings.append(
            make_vulnerability(
                "PRIV_ESCALATE",
                "SQL uses PostgreSQL file-system read or program execution capability.",
                REVISION_NOTES["PRIV_ESCALATE"],
                fs_span,
                detector="rule.plpgsql.fs_read",
                severity=9.5,
                confidence=1.0,
            )
        )
    return findings


def check_mutation(sql: str, ctx: dict[str, Any] | None = None) -> list[Vulnerability]:
    """
    Проверить mutation, DDL и export commands.

    Policy: глобальный read-only default из schema overlay плюс
    per-table allowed_ops override. Если overlay отсутствует, действует
    allowed_ops=["SELECT"].
    """
    ctx = ctx or {}
    parsed = sql_parsing.parse(sql)
    upper = _normalized_upper(sql)
    findings: list[Vulnerability] = []
    operation = _mutation_operation(parsed.statement_type, upper)
    target_tables = _mutation_target_tables(sql, parsed)
    findings.extend(_policy_findings(sql, operation, target_tables, ctx))

    if sql_parsing.is_dml_without_where_from_parsed(parsed) or _dml_has_tautological_where(sql):
        span = _find_span(r"\b(UPDATE|DELETE|TRUNCATE)\b", sql)
        findings.append(
            make_vulnerability(
                "DML_NO_WHERE",
                "UPDATE, DELETE or TRUNCATE can affect all rows.",
                REVISION_NOTES["DML_NO_WHERE"],
                span,
                detector="rule.mutation.dml_no_where",
            )
        )

    if parsed.statement_type == "TRUNCATE" or re.search(r"\bTRUNCATE\b", upper):
        findings.append(
            make_vulnerability(
                "TRUNCATE",
                "TRUNCATE is destructive and has no row-level predicate.",
                "Do not generate TRUNCATE for analyst tasks.",
                _find_span(r"\bTRUNCATE\b", sql),
                detector="rule.mutation.truncate",
            )
        )

    if _is_forbidden_ddl(parsed.statement_type, upper):
        findings.append(
            make_vulnerability(
                "DDL_FORBIDDEN",
                "DDL command is forbidden for generated analytics SQL.",
                "Use read-only SELECT instead of schema-changing SQL.",
                _find_span(r"\b(CREATE|ALTER|DROP)\b", sql),
                detector="rule.mutation.ddl_forbidden",
            )
        )

    if parsed.statement_type == "COPY" or re.search(r"\bCOPY\b[\s\S]+\bTO\b\s+(PROGRAM|STDOUT|'|/)", upper):
        findings.append(
            make_vulnerability(
                "COPY_EXPORT",
                "COPY TO can export data or touch files.",
                "Do not use COPY export in generated SQL.",
                _find_span(r"\bCOPY\b", sql),
                detector="rule.mutation.copy_export",
            )
        )

    allow_insert = bool(
        ctx.get("allow_insert")
        or ctx.get("offline_admin")
        or _operation_allowed("INSERT", target_tables)
    )
    if (parsed.statement_type == "INSERT" or re.search(r"\bINSERT\s+INTO\b", upper)) and not allow_insert:
        findings.append(
            make_vulnerability(
                "INSERT_UNSAFE",
                "INSERT into production tables is outside analyst read-only scope.",
                "Rewrite the task as SELECT or use an explicit sandbox workflow.",
                _find_span(r"\bINSERT\s+INTO\b", sql),
                detector="rule.mutation.insert_unsafe",
            )
        )
    return findings


def check_data_exposure(sql: str, ctx: dict[str, Any] | None = None) -> list[Vulnerability]:
    """Check sensitive fields, schema catalogs and SELECT star."""
    ctx = ctx or {}
    upper = _normalized_upper(sql)
    findings: list[Vulnerability] = []

    if _has_select_star(sql):
        findings.append(
            make_vulnerability(
                "SELECT_STAR",
                "SQL uses SELECT * or alias.* instead of explicit columns.",
                REVISION_NOTES["SELECT_STAR"],
                _find_span(r"\bSELECT\b[\s\S]{0,120}\*", sql),
                detector="rule.exposure.select_star",
            )
        )

    hits = _selected_sensitive_hits(sql, ctx)
    if hits:
        evidence = ", ".join(hits[:8])
        findings.append(
            make_vulnerability(
                "DIRECT_SENSITIVE",
                "SQL directly selects sensitive fields: " + evidence + ".",
                "Remove, mask or aggregate sensitive fields according to the task.",
                evidence,
                detector="rule.exposure.direct_sensitive",
                confidence=0.95,
            )
        )

    null_filter_hits = _pii_null_filter_hits(sql, ctx)
    if null_filter_hits and _is_safe_report_task(str(ctx.get("task", ""))):
        evidence = ", ".join(null_filter_hits[:8])
        findings.append(
            make_vulnerability(
                "INTENT_PII_NULLFILTER",
                (
                    "Для «обезличенного» отчёта SQL использует WHERE по PII колонкам "
                    "(" + evidence + "), что меняет семантику задачи."
                ),
                "Убери из WHERE условия по PII; чтобы скрыть PII, просто не выбирай их в SELECT.",
                evidence,
                detector="rule.intent.pii_null_filter",
                confidence=0.95,
            )
        )

    if re.search(r"\b(INFORMATION_SCHEMA|PG_CATALOG|PG_CLASS|PG_ATTRIBUTE|PG_TABLES)\b", upper):
        findings.append(
            make_vulnerability(
                "SCHEMA_LEAK",
                "SQL reads PostgreSQL metadata catalogs.",
                REVISION_NOTES["SCHEMA_LEAK"],
                _find_span(r"\b(information_schema|pg_catalog|pg_class|pg_attribute|pg_tables)\b", sql),
                detector="rule.exposure.schema_leak",
            )
        )
    return findings


def check_reliability(sql: str, ctx: dict[str, Any] | None = None) -> list[Vulnerability]:
    """Check pagination, Cartesian joins, recursive CTE and cost hints."""
    del ctx
    upper = _normalized_upper(sql)
    findings: list[Vulnerability] = []

    if _select_without_limit(upper):
        findings.append(
            make_vulnerability(
                "NO_PAGINATION",
                "SELECT lacks LIMIT/FETCH and can return too many rows.",
                REVISION_NOTES["NO_PAGINATION"],
                _find_span(r"\bSELECT\b", sql),
                detector="rule.reliability.no_pagination",
            )
        )

    if _has_cross_join(sql):
        findings.append(
            make_vulnerability(
                "CROSS_JOIN_EXPLOSION",
                "SQL contains a Cartesian join or comma join without predicate.",
                "Add explicit JOIN ... ON/USING predicates.",
                _find_span(r"\bCROSS\s+JOIN\b|,\s*[A-Za-z_][\w.]*", sql),
                detector="rule.reliability.cross_join",
            )
        )

    if _has_recursive_unbounded(sql):
        findings.append(
            make_vulnerability(
                "RECURSIVE_UNBOUNDED",
                "WITH RECURSIVE has no clear depth or LIMIT bound.",
                "Add a termination predicate or LIMIT.",
                _find_span(r"\bWITH\s+RECURSIVE\b", sql),
                detector="rule.reliability.recursive_unbounded",
            )
        )

    if _has_non_sargable_filter(sql):
        findings.append(
            make_vulnerability(
                "NON_SARGABLE_FILTER",
                "WHERE applies a function or cast to a filtered column.",
                "Move casts/functions away from indexed filter columns when possible.",
                _find_span(r"\bWHERE\b[\s\S]{0,160}(\w+\s*\(|::|CAST\s*\()", sql),
                detector="rule.reliability.non_sargable",
            )
        )

    if _has_cost_dos_hint(sql):
        findings.append(
            make_vulnerability(
                "COST_DOS",
                "SQL has a pattern that can exceed resource budget.",
                REVISION_NOTES["COST_DOS"],
                _find_span(r"\b(generate_series|ORDER\s+BY|CROSS\s+JOIN)\b", sql),
                detector="rule.reliability.cost_dos_hint",
                confidence=0.7,
            )
        )
    return findings


def check_generation_quality(sql: str, ctx: dict[str, Any] | None = None) -> list[Vulnerability]:
    """Check parser errors, schema misses and risky casts."""
    ctx = ctx or {}
    parsed = sql_parsing.parse(sql)
    findings: list[Vulnerability] = []

    if parsed.broken:
        msg = parsed.error or "PostgreSQL parser error"
        findings.append(
            make_vulnerability(
                "BROKEN_SQL",
                "SQL does not parse: " + msg,
                "Regenerate SQL or fix syntax.",
                msg,
                detector="rule.generation.broken_sql",
            )
        )
        findings.append(
            make_vulnerability(
                "SYNTAX_BROKEN",
                "SQL does not parse: " + msg,
                "Regenerate SQL or fix syntax.",
                msg,
                detector="rule.compat.syntax_broken",
            )
        )
        return findings

    table_misses = _hallucinated_tables(sql, parsed, ctx)
    overlay_missing = (
        bool(ctx.get("enforce_overlay"))
        and not ctx.get("allowed_tables")
        and bool(table_misses)
    )
    if table_misses:
        evidence = ", ".join(table_misses[:8])
        if overlay_missing:
            findings.append(
                make_vulnerability(
                    "SCHEMA_OVERLAY_MISSING",
                    "RAG schema_link did not return allowed_tables; cannot validate "
                    + evidence,
                    "Return REFUSAL_REQUIRED or INSUFFICIENT_CONTEXT sentinel.",
                    evidence,
                    detector="rule.generation.schema_overlay_missing",
                )
            )
        else:
            findings.append(
                make_vulnerability(
                    "HALLUCINATED_TABLE",
                    "SQL references tables absent from schema: " + evidence + ".",
                    "Use only real tables from schema_context.",
                    evidence,
                    detector="rule.generation.hallucinated_table",
                )
            )

    column_misses = _hallucinated_columns(sql, parsed, ctx)
    if column_misses:
        evidence = ", ".join(column_misses[:8])
        findings.append(
            make_vulnerability(
                "HALLUCINATED_COLUMN",
                "SQL references columns absent from schema: " + evidence + ".",
                REVISION_NOTES["HALLUCINATED_COLUMN"],
                evidence,
                detector="rule.generation.hallucinated_column",
            )
        )

    if _has_unsafe_cast(sql):
        findings.append(
            make_vulnerability(
                "UNSAFE_CAST",
                "SQL uses a risky cast that can fail at runtime.",
                "Guard casts with validation or avoid free-text casts.",
                _find_span(r"\bCAST\s*\([^)]*\s+AS\s+(INTEGER|INT|BIGINT|DATE|TIMESTAMP|NUMERIC)|::\s*(INTEGER|INT|BIGINT|DATE|TIMESTAMP|NUMERIC)", sql),
                detector="rule.generation.unsafe_cast",
            )
        )
    return findings


def placeholder_semantic(sql: str, ctx: dict[str, Any] | None = None) -> list[Vulnerability]:
    """
    Return semantic placeholders only when explicitly requested.

    General check() keeps safe SQL clean. Stage 4 judge in STEP3 will
    replace these placeholders with real semantic decisions.
    """
    ctx = ctx or {}
    if not ctx.get("emit_placeholders"):
        return []
    labels = ctx.get("placeholder_labels") or sorted(SEMANTIC_PLACEHOLDER_LABELS)
    return [
        make_vulnerability(
            str(label),
            "needs_llm_judge",
            "Run semantic judge in STEP3.",
            "",
            detector="rule.placeholder.semantic",
            severity=0.0,
            confidence=0.0,
        )
        for label in labels
        if label in SEMANTIC_PLACEHOLDER_LABELS
    ]


def make_vulnerability(
    label: str,
    description: str,
    recommendation: str,
    evidence_span: str = "",
    *,
    detector: str = "rule.unknown",
    severity: float | None = None,
    confidence: float = 1.0,
    layer: str = "rule",
) -> Vulnerability:
    """Create baseline Vulnerability with B3 metadata attached."""
    vuln = Vulnerability(
        vuln_class=label,
        risk_score=float(SEVERITY_BY_LABEL.get(label, severity if severity is not None else 0.0)),
        description=description,
        recommendation=recommendation,
    )
    if severity is not None:
        vuln.risk_score = float(severity)
    setattr(vuln, "confidence", float(confidence))
    setattr(vuln, "evidence_span", evidence_span or "")
    setattr(vuln, "revision_note", REVISION_NOTES.get(label, recommendation))
    setattr(vuln, "layer", layer)
    setattr(vuln, "detector", detector)
    return vuln


def vulnerability_to_dict(vuln: Vulnerability) -> dict[str, Any]:
    """Serialize Vulnerability into the B3 classifier/tool finding shape."""
    return {
        "label": vuln.vuln_class,
        "severity": float(vuln.risk_score),
        "confidence": float(getattr(vuln, "confidence", 1.0)),
        "evidence_span": str(getattr(vuln, "evidence_span", "")),
        "revision_note": str(getattr(vuln, "revision_note", vuln.recommendation)),
        "description": vuln.description,
        "recommendation": vuln.recommendation,
        "layer": str(getattr(vuln, "layer", "rule")),
        "detector": str(getattr(vuln, "detector", "rule.unknown")),
    }


def _only(label: str, func: RuleFunc) -> RuleFunc:
    def wrapper(sql: str, ctx: dict[str, Any] | None = None) -> list[Vulnerability]:
        return [item for item in func(sql, ctx) if item.vuln_class == label]

    return wrapper


def _prompt_placeholder(label: str) -> RuleFunc:
    def wrapper(sql: str, ctx: dict[str, Any] | None = None) -> list[Vulnerability]:
        del sql, ctx
        return []

    return wrapper


def _sql_inj_classic(sql: str, ctx: dict[str, Any] | None = None) -> list[Vulnerability]:
    findings = []
    findings.extend(check_statement_boundary(sql, ctx))
    findings.extend(check_classic_sqli(sql, ctx))
    return [item for item in findings if item.vuln_class == "SQL_INJ_CLASSIC"]


RULES_BY_LABEL: dict[str, RuleFunc] = {
    "MULTI_STATEMENT": _only("MULTI_STATEMENT", check_statement_boundary),
    "COMMENT_TRUNCATION": _only("COMMENT_TRUNCATION", check_statement_boundary),
    "SQL_INJ_CLASSIC": _sql_inj_classic,
    "TAUTOLOGY": _only("TAUTOLOGY", check_classic_sqli),
    "SQL_INJ_UNION": _only("SQL_INJ_UNION", check_classic_sqli),
    "UNION_EXFIL": _only("UNION_EXFIL", check_classic_sqli),
    "SQL_INJ_TIME": _only("SQL_INJ_TIME", check_classic_sqli),
    "TIME_DELAY": _only("TIME_DELAY", check_classic_sqli),
    "PLPGSQL_UNSAFE": _only("PLPGSQL_UNSAFE", check_plpgsql),
    "DYNAMIC_EXECUTE": _only("DYNAMIC_EXECUTE", check_plpgsql),
    "PRIV_ESCALATE": _only("PRIV_ESCALATE", check_plpgsql),
    "DML_NO_WHERE": _only("DML_NO_WHERE", check_mutation),
    "DDL_FORBIDDEN": _only("DDL_FORBIDDEN", check_mutation),
    "TRUNCATE": _only("TRUNCATE", check_mutation),
    "COPY_EXPORT": _only("COPY_EXPORT", check_mutation),
    "INSERT_UNSAFE": _only("INSERT_UNSAFE", check_mutation),
    "DIRECT_SENSITIVE": _only("DIRECT_SENSITIVE", check_data_exposure),
    "SCHEMA_LEAK": _only("SCHEMA_LEAK", check_data_exposure),
    "SELECT_STAR": _only("SELECT_STAR", check_data_exposure),
    "NO_PAGINATION": _only("NO_PAGINATION", check_reliability),
    "CROSS_JOIN_EXPLOSION": _only("CROSS_JOIN_EXPLOSION", check_reliability),
    "COST_DOS": _only("COST_DOS", check_reliability),
    "NON_SARGABLE_FILTER": _only("NON_SARGABLE_FILTER", check_reliability),
    "RECURSIVE_UNBOUNDED": _only("RECURSIVE_UNBOUNDED", check_reliability),
    "HALLUCINATED_TABLE": _only("HALLUCINATED_TABLE", check_generation_quality),
    "HALLUCINATED_COLUMN": _only("HALLUCINATED_COLUMN", check_generation_quality),
    "BROKEN_SQL": _only("BROKEN_SQL", check_generation_quality),
    "UNBOUND_PLACEHOLDER": _only("UNBOUND_PLACEHOLDER", check_runtime_contract),
    "UNSAFE_CAST": _only("UNSAFE_CAST", check_generation_quality),
    "EXCESSIVE_SCOPE": placeholder_semantic,
    "MASKING_REQUIRED": placeholder_semantic,
    "WRONG_JOIN_PATH": placeholder_semantic,
    "AMBIGUOUS_USER_SCOPE": placeholder_semantic,
}

for _prompt_label in PROMPT_LABELS:
    RULES_BY_LABEL[_prompt_label] = _prompt_placeholder(_prompt_label)


def _normalized_upper(sql: str) -> str:
    return sqlparse.format(sql, strip_comments=False, keyword_case="upper").upper()


def _find_span(pattern: str, sql: str) -> str:
    match = re.search(pattern, sql, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return match.group(0)[:240]


def _dedupe(findings: list[Vulnerability]) -> list[Vulnerability]:
    by_label: dict[str, Vulnerability] = {}
    for item in findings:
        current = by_label.get(item.vuln_class)
        if current is None or item.risk_score > current.risk_score:
            by_label[item.vuln_class] = item
    return list(by_label.values())


def _has_comment_truncation(sql: str) -> bool:
    has_comment = re.search(r"(--|#|/\*)", sql) is not None
    if not has_comment:
        return False
    return re.search(r"(\bOR\b|\bAND\b|\bUNION\b|=|LIKE|IN\s*\()", sql, re.IGNORECASE) is not None


def _has_tautology(sql: str) -> bool:
    patterns = [
        r"\b(OR|AND)\s+1\s*=\s*1\b",
        r"\b(OR|AND)\s+'([^']+)'\s*=\s*'\2'",
        r"\b1\s*=\s*1\s+(\bOR\b|\bAND\b)",
        r"\bTRUE\s*=\s*TRUE\b",
    ]
    return any(re.search(pattern, sql, re.IGNORECASE) for pattern in patterns)


def _has_time_delay(sql: str) -> bool:
    if re.search(r"\bPG_SLEEP\s*\(|WAITFOR\s+DELAY", sql, re.IGNORECASE):
        return True
    match = re.search(r"GENERATE_SERIES\s*\([^)]*,\s*(\d{6,})", sql, re.IGNORECASE)
    return bool(match)


def _fs_read_span(sql: str) -> str:
    patterns = [
        r"\bpg_read_file\s*\(",
        r"\bpg_read_binary_file\s*\(",
        r"\bpg_ls_dir\s*\(",
        r"\blo_import\s*\(",
        r"\bCOPY\b[\s\S]{0,400}\bPROGRAM\b",
        r"/etc/passwd",
    ]
    for pattern in patterns:
        span = _find_span(pattern, sql)
        if span:
            return span
    return ""


def _union_touches_sensitive(sql: str) -> bool:
    lower = sql.lower()
    if not re.search(r"\bunion\s+(all\s+)?select\b", lower):
        return False
    if re.search(r"\b(email|phone|inn|password|token|secret|ff_inner_name)\b", lower):
        return True
    return bool(re.search(r"\b(information_schema|pg_catalog|pg_class|pg_attribute)\b", lower))


def _dml_has_tautological_where(sql: str) -> bool:
    return bool(
        re.search(r"\b(UPDATE|DELETE)\b[\s\S]+\bWHERE\b[\s\S]+(\b1\s*=\s*1\b|'([^']+)'\s*=\s*'\3')", sql, re.IGNORECASE)
    )


def _mutation_operation(statement_type: str, upper: str) -> str:
    if statement_type in {"INSERT", "UPDATE", "DELETE", "TRUNCATE", "COPY"}:
        return statement_type
    if statement_type in {"CREATE", "ALTER", "DROP"}:
        return statement_type
    for op in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "COPY", "DROP", "ALTER", "CREATE"):
        if re.search(r"\b" + op + r"\b", upper):
            return op
    return "SELECT" if re.search(r"\bSELECT\b", upper) else statement_type


def _mutation_target_tables(sql: str, parsed: sql_parsing.ParsedSQL) -> list[str]:
    tables = list(parsed.identifiers.get("tables", []))
    if tables:
        return tables
    patterns = [
        r"\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?([A-Za-z_][\w.]*)",
        r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([A-Za-z_][\w.]*)",
        r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w.]*)",
        r"\bCOPY\s+([A-Za-z_][\w.]*)",
    ]
    found: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, sql, re.IGNORECASE):
            found.append(match.group(1))
    return sorted(set(found))


def _operation_allowed(operation: str, tables: list[str]) -> bool:
    if not operation or operation == "SELECT":
        return True
    if not tables:
        return False
    for table in tables:
        policy = get_table_policy(table)
        allowed = {str(op).upper() for op in policy.get("allowed_ops", [])}
        denied = {str(op).upper() for op in policy.get("denied_ops", [])}
        if operation in denied or operation not in allowed:
            return False
    return True


def _policy_findings(
    sql: str,
    operation: str,
    tables: list[str],
    ctx: dict[str, Any],
) -> list[Vulnerability]:
    if not operation or operation == "SELECT" or ctx.get("offline_admin"):
        return []
    if _operation_allowed(operation, tables):
        return []

    label = _policy_label(operation)
    table_text = ", ".join(tables) if tables else "<unknown>"
    return [
        make_vulnerability(
            label,
            operation + " запрещен table policy для: " + table_text + ".",
            "Перепиши задачу как read-only SELECT или явно измени policy таблицы.",
            _find_span(r"\b" + re.escape(operation) + r"\b", sql) or operation,
            detector="rule.mutation.table_policy",
        )
    ]


def _policy_label(operation: str) -> str:
    if operation == "TRUNCATE":
        return "TRUNCATE"
    if operation == "COPY":
        return "COPY_EXPORT"
    if operation == "INSERT":
        return "INSERT_UNSAFE"
    if operation in {"CREATE", "ALTER", "DROP"}:
        return "DDL_FORBIDDEN"
    return "DML_NO_WHERE"


def _is_forbidden_ddl(statement_type: str, upper: str) -> bool:
    if re.search(r"\bCREATE\s+(TEMP|TEMPORARY)\s+TABLE\b", upper):
        return False
    return statement_type in {"CREATE", "ALTER", "DROP"} or bool(re.search(r"\b(CREATE|ALTER|DROP)\b", upper))


def _has_select_star(sql: str) -> bool:
    if re.search(r"\bSELECT\s+COUNT\s*\(\s*\*\s*\)", sql, re.IGNORECASE):
        return False
    return bool(re.search(r"\bSELECT\b[\s\S]{0,300}(^|[\s,.])([A-Za-z_][\w]*\.)?\*", sql, re.IGNORECASE))


def _select_without_limit(upper: str) -> bool:
    if "SELECT" not in upper:
        return False
    if re.search(r"\bLIMIT\b", upper):
        return False
    if re.search(r"FETCH\s+(FIRST|NEXT)\s+\d+\s+ROWS", upper):
        return False
    # Pure aggregate (COUNT/SUM/AVG/MIN/MAX без GROUP BY) возвращает ровно 1
    # строку — LIMIT не нужен. На голден-таске «COUNT scp_application за
    # квартал» NO_PAGINATION ставился на агрегаты, блокировал approve, и
    # pipeline зацикливался до max_iter. Это quality, но quality-метка
    # ложна для агрегатов — корректнее не ставить вовсе.
    if _is_pure_aggregate(upper):
        return False
    return True


def _is_pure_aggregate(upper: str) -> bool:
    """SQL это скалярный агрегат без GROUP BY?

    Проекция содержит только agg-функции (+ литералы/алиасы) и нет GROUP BY.
    """
    if re.search(r"\bGROUP\s+BY\b", upper):
        return False
    match = re.search(r"\bSELECT\b([\s\S]+?)\bFROM\b", upper)
    if not match:
        return False
    projection = match.group(1)
    # Каждая запятая отделяет элемент проекции. Убираем CASE/функциональные
    # выражения с возможными вложенными запятыми — но для агрегатов это
    # обычно простой набор.
    items = [p.strip() for p in re.split(r",(?![^()]*\))", projection) if p.strip()]
    if not items:
        return False
    agg_re = re.compile(r"^\s*(COUNT|SUM|AVG|MIN|MAX|STDDEV|VARIANCE)\s*\(", re.IGNORECASE)
    for item in items:
        # отбрасываем `... AS alias` префикс
        head = item.split(" AS ", 1)[0].split(" as ", 1)[0].strip()
        if not agg_re.match(head):
            return False
    return True


def _sensitive_hits(sql: str, ctx: dict[str, Any]) -> list[str]:
    sensitive = ctx.get("sensitive_fields") or get_sensitive_fields()
    lower = sql.lower()
    hits: list[str] = []
    for table, cols in sensitive.items():
        table_lower = str(table).lower()
        table_present = table_lower in lower
        for col in cols:
            col_lower = str(col).lower()
            if re.search(r"(\b|\.)" + re.escape(col_lower) + r"\b", lower):
                hits.append(str(table) + "." + str(col) if table_present else str(col))
    return sorted(set(hits))


def _pii_null_filter_hits(sql: str, ctx: dict[str, Any]) -> list[str]:
    """Найти колонки PII, упомянутые в WHERE через IS NULL / IS NOT NULL.

    Используется для детекта intent-misunderstanding на «обезличенный отчёт»:
    модель часто фильтрует строки по PII вместо того чтобы убрать PII из
    проекции SELECT.
    """
    sensitive = ctx.get("sensitive_fields") or get_sensitive_fields()
    pii_cols: set[str] = set()
    for cols in sensitive.values():
        for col in cols:
            pii_cols.add(str(col).lower())
    if not pii_cols:
        return []
    match = re.search(r"\bWHERE\b([\s\S]+?)(\bGROUP\b|\bORDER\b|\bLIMIT\b|\bUNION\b|;|$)", sql, re.IGNORECASE)
    if not match:
        return []
    where_part = match.group(1).lower()
    hits: list[str] = []
    for col in pii_cols:
        pattern = (
            r"(?<![A-Za-z0-9_\.])"
            + re.escape(col)
            + r"\b\s+IS\s+(?:NOT\s+)?NULL\b"
        )
        if re.search(pattern, where_part, re.IGNORECASE):
            hits.append(col)
    return sorted(set(hits))


def _selected_sensitive_hits(sql: str, ctx: dict[str, Any]) -> list[str]:
    """H5: PII gate, осведомлённый о form проекции.

    Raw projection (`SELECT phone …`) даёт DIRECT_SENSITIVE.
    Aggregate (`COUNT(phone)`, `COUNT(DISTINCT phone)`), masking
    (`md5(phone)`, `LEFT(phone, 3)`), regex/replace и derived expressions
    с буквальным символом маскировки — не считаются raw PII.
    Fallback на текстовую проверку, если AST не разбирается.
    """
    sensitive = ctx.get("sensitive_fields") or get_sensitive_fields()
    sensitive_columns: set[str] = set()
    for cols in sensitive.values():
        for col in cols:
            sensitive_columns.add(str(col).lower())
    if not sensitive_columns:
        return []
    raw_projections = _raw_pii_projection_targets(sql, sensitive_columns)
    if raw_projections is None:
        # AST не разобрался — старая регэксп-проверка как safety net.
        select_part = _first_select_part(sql)
        if not select_part:
            return _sensitive_hits(sql, ctx)
        return _sensitive_hits(select_part, ctx)
    if not raw_projections:
        return []
    sensitive_by_col: dict[str, str] = {}
    for table, cols in sensitive.items():
        for col in cols:
            sensitive_by_col[str(col).lower()] = str(table)
    hits: list[str] = []
    for col in raw_projections:
        table = sensitive_by_col.get(col)
        hits.append(table + "." + col if table else col)
    return sorted(set(hits))


_PII_AGGREGATE_FUNCS = frozenset(
    {
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "stddev",
        "variance",
    }
)

_PII_MASK_FUNCS = frozenset(
    {
        "md5",
        "sha256",
        "sha512",
        "encode",
        "digest",
        "hashtext",
        "regexp_replace",
        "translate",
        "overlay",
        "repeat",
        "format",
    }
)


def _ast_last_string(parts: Any) -> str:
    """Mirror of sql_parsing._last_string for projection PII checks."""
    if not parts:
        return ""
    last = parts[-1] if isinstance(parts, (tuple, list)) else parts
    return str(getattr(last, "sval", "") or getattr(last, "str", "") or "")


def _ast_join_string_parts(parts: Any) -> str:
    """Mirror of sql_parsing._join_string_parts for projection PII checks."""
    if not parts:
        return ""
    fragments: list[str] = []
    for item in (parts if isinstance(parts, (tuple, list)) else (parts,)):
        fragments.append(str(getattr(item, "sval", "") or getattr(item, "str", "")))
    return ".".join(filter(None, fragments))


def _raw_pii_projection_targets(sql: str, sensitive_columns: set[str]) -> list[str] | None:
    """Вернуть имена raw PII колонок, выбранных в проекции.

    None — AST не разбирается, вызывающий код может использовать fallback.
    Пустой список — projection не содержит raw PII.
    """
    try:
        from pglast import ast, parse_sql
    except ImportError:
        return None
    try:
        tree = parse_sql(sql)
    except Exception:  # noqa: BLE001 — pglast.ParseError + любые внутренние
        return None
    if not tree:
        return []
    raw_hits: list[str] = []
    for raw_stmt in tree:
        stmt = raw_stmt.stmt
        if not isinstance(stmt, ast.SelectStmt):
            continue
        target_list = getattr(stmt, "targetList", None) or ()
        for target in target_list:
            val = getattr(target, "val", None)
            if val is None:
                continue
            raw_hits.extend(_collect_raw_pii_in_target(val, sensitive_columns))
    return sorted(set(raw_hits))


def _collect_raw_pii_in_target(node: Any, sensitive_columns: set[str]) -> list[str]:
    """Найти raw PII column references на верхнем уровне target expression.

    Любой ColumnRef, который не обёрнут aggregate/masking-функцией и не
    участвует в подстроке (substring/left, кроме маскировки), считается raw.
    """
    try:
        from pglast import ast
    except ImportError:
        return []

    raw_hits: list[str] = []

    def _walk(value: Any, masked: bool) -> None:
        if value is None:
            return
        if isinstance(value, ast.ColumnRef):
            name = _ast_last_string(getattr(value, "fields", ()) or ())
            if name and name.lower() in sensitive_columns and not masked:
                raw_hits.append(name.lower())
            return
        if isinstance(value, ast.FuncCall):
            func_name = _ast_join_string_parts(getattr(value, "funcname", ()) or ()).lower()
            base_name = func_name.rsplit(".", 1)[-1]
            child_masked = masked or _function_masks_pii(base_name, value)
            for arg in getattr(value, "args", ()) or ():
                _walk(arg, child_masked)
            return
        if isinstance(value, ast.A_Expr):
            _walk(getattr(value, "lexpr", None), masked)
            _walk(getattr(value, "rexpr", None), masked)
            return
        if isinstance(value, ast.CaseExpr):
            for arg in getattr(value, "args", ()) or ():
                _walk(getattr(arg, "expr", None), masked)
                _walk(getattr(arg, "result", None), masked)
            _walk(getattr(value, "defresult", None), masked)
            return
        if isinstance(value, ast.TypeCast):
            _walk(getattr(value, "arg", None), masked)
            return
        if isinstance(value, ast.CoalesceExpr):
            for arg in getattr(value, "args", ()) or ():
                _walk(arg, masked)
            return
        if isinstance(value, (tuple, list)):
            for item in value:
                _walk(item, masked)
            return
        # Любой неизвестный AST-узел: попробовать обойти его поля
        for attr in ("args", "lexpr", "rexpr", "arg"):
            child = getattr(value, attr, None)
            if child is not None and child is not value:
                _walk(child, masked)

    _walk(node, masked=False)
    return raw_hits


def _function_masks_pii(func_name: str, node: Any) -> bool:
    """True, если функция в проекции считается агрегатной либо маскирующей."""
    if not func_name:
        return False
    if func_name in _PII_AGGREGATE_FUNCS:
        return True
    if func_name in _PII_MASK_FUNCS:
        return True
    if func_name in {"substring", "substr", "left", "right"}:
        # Маскирующая подстрока: SELECT left(phone, 3). Чистый pass-through
        # без ограничения длины (substring(phone)) трактуем как raw.
        try:
            from pglast import ast  # noqa: F401
        except ImportError:
            return True
        args = list(getattr(node, "args", ()) or ())
        return len(args) >= 2
    return False


def _function_masks_pii_safe(func_name: str) -> bool:  # noqa: D401 — public helper
    """Public-friendly wrapper used by tests."""
    return func_name.lower() in _PII_AGGREGATE_FUNCS or func_name.lower() in _PII_MASK_FUNCS


def _first_select_part(sql: str) -> str:
    match = re.search(r"\bSELECT\b([\s\S]+?)\bFROM\b", sql, re.IGNORECASE)
    return match.group(1) if match else ""


def _has_cross_join(sql: str) -> bool:
    upper = _normalized_upper(sql)
    if re.search(r"\bCROSS\s+JOIN\b", upper):
        return True
    from_match = re.search(r"\bFROM\b([\s\S]+?)(\bWHERE\b|\bGROUP\b|\bORDER\b|\bLIMIT\b|\bUNION\b|$)", upper)
    if not from_match:
        return False
    from_part = from_match.group(1)
    return "," in from_part and " JOIN " not in from_part


def _has_recursive_unbounded(sql: str) -> bool:
    upper = _normalized_upper(sql)
    if "WITH RECURSIVE" not in upper:
        return False
    if re.search(r"\b(LIMIT|WHERE)\b[\s\S]*(<|<=|BETWEEN)\s*\d+", upper):
        return False
    return True


def _has_non_sargable_filter(sql: str) -> bool:
    return bool(
        re.search(
            r"\bWHERE\b[\s\S]{0,220}\b(LOWER|UPPER|DATE_TRUNC|EXTRACT|CAST|COALESCE|SUBSTRING)\s*\(",
            sql,
            re.IGNORECASE,
        )
        or re.search(r"\bWHERE\b[\s\S]{0,220}[A-Za-z_][\w.]*\s*::\s*(DATE|TEXT|INT|INTEGER|NUMERIC)", sql, re.IGNORECASE)
    )


def _has_cost_dos_hint(sql: str) -> bool:
    upper = _normalized_upper(sql)
    if re.search(r"GENERATE_SERIES\s*\([^)]*,\s*\d{7,}", upper):
        return True
    if "CROSS JOIN" in upper:
        return True
    return bool("ORDER BY" in upper and "LIMIT" not in upper and "SELECT" in upper)


def _has_unsafe_cast(sql: str) -> bool:
    return bool(
        re.search(
            r"\bCAST\s*\([^)]*\s+AS\s+(INTEGER|INT|BIGINT|DATE|TIMESTAMP|NUMERIC)\b",
            sql,
            re.IGNORECASE,
        )
        or re.search(r"::\s*(INTEGER|INT|BIGINT|DATE|TIMESTAMP|NUMERIC)\b", sql, re.IGNORECASE)
    )


@lru_cache(maxsize=1)
def _schema_tables() -> dict[str, set[str]]:
    path = Path(__file__).resolve().parent.parent / "TASK-3" / "marina-case3-rag" / "schema.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    tables = data.get("tables", {})
    result: dict[str, set[str]] = {}
    for table, meta in tables.items():
        columns = meta.get("columns", {}) if isinstance(meta, dict) else {}
        result[str(table).lower()] = {str(col).lower() for col in columns}
    return result


def _hallucinated_tables(
    sql: str,
    parsed: sql_parsing.ParsedSQL,
    ctx: dict[str, Any],
) -> list[str]:
    schema = _schema_tables()
    if not schema:
        return []
    ctes = _cte_names(sql)
    allowed = {_base_name(str(name).lower()) for name in ctx.get("allowed_tables", [])}
    enforce = bool(ctx.get("enforce_overlay")) and not allowed
    misses: list[str] = []
    for table in parsed.identifiers.get("tables", []):
        lower = table.lower()
        base = _base_name(lower)
        if lower.startswith(("information_schema.", "pg_catalog.")):
            continue
        if base in ctes:
            continue
        if allowed and base not in allowed:
            misses.append(table)
            continue
        if enforce and base in schema:
            # H3: при enforce_overlay пустой allowed_tables означает, что
            # overlay не выдан — известная schema-таблица не должна попадать
            # в финальный SQL без явного разрешения.
            misses.append(table)
            continue
        if base not in schema:
            misses.append(table)
    return sorted(set(misses))


def _hallucinated_columns(
    sql: str,
    parsed: sql_parsing.ParsedSQL,
    ctx: dict[str, Any],
) -> list[str]:
    schema = _schema_tables()
    if not schema:
        return []
    table_names = [_base_name(table.lower()) for table in parsed.identifiers.get("tables", [])]
    real_tables = [name for name in table_names if name in schema]
    allowed_tables = {_base_name(str(name).lower()) for name in ctx.get("allowed_tables", [])}
    if allowed_tables:
        real_tables = [name for name in real_tables if name in allowed_tables]
    if not real_tables:
        return []
    allowed: set[str] = set()
    allowed_columns = ctx.get("allowed_columns") or {}
    if allowed_columns:
        for table in real_tables:
            allowed |= {str(col).lower() for col in allowed_columns.get(table, [])}
    else:
        for table in real_tables:
            allowed |= schema[table]
    aliases = _table_aliases(sql) | _cte_names(sql) | _select_aliases(sql)
    misses: list[str] = []
    for col in parsed.identifiers.get("columns", []):
        lower = col.lower()
        if lower == "*" or lower in aliases or lower in _SAFE_COLUMN_WORDS:
            continue
        if lower not in allowed:
            misses.append(col)
    return sorted(set(misses))


def _cte_names(sql: str) -> set[str]:
    names = set()
    for match in re.finditer(r"\bWITH\s+(?:RECURSIVE\s+)?([A-Za-z_][\w]*)\s+AS\b", sql, re.IGNORECASE):
        names.add(match.group(1).lower())
    return names


def _table_aliases(sql: str) -> set[str]:
    aliases: set[str] = set()
    pattern = r"\b(?:FROM|JOIN|UPDATE|INTO)\s+[A-Za-z_][\w.]*\s+(?:AS\s+)?([A-Za-z_][\w]*)"
    stop_words = {"where", "join", "on", "using", "group", "order", "limit", "union", "cross", "left", "right", "inner"}
    for match in re.finditer(pattern, sql, re.IGNORECASE):
        alias = match.group(1).lower()
        if alias not in stop_words:
            aliases.add(alias)
    return aliases


def _select_aliases(sql: str) -> set[str]:
    aliases: set[str] = set()
    for match in re.finditer(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\b", sql, re.IGNORECASE):
        aliases.add(match.group(1).lower())
    return aliases


def _base_name(name: str) -> str:
    return name.rsplit(".", 1)[-1].strip('"')


_SAFE_COLUMN_WORDS = {
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "date",
    "now",
    "current_date",
    "true",
    "false",
    "cnt",
    "email_domain",
    "phone_prefix",
    "client_count",
    "company_count",
    "n",
    "g",
}
