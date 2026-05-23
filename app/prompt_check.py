"""
Prompt precheck before retrieve and generate.

This module catches prompt-level risks separately from SQL artifact
risks. It emits five labels from B3 STEP2: policy bypass, forced DML,
schema exfiltration, guardrail ignoring and ToxicSQL-style triggers.
Findings are baseline Vulnerability objects with B3 metadata fields.
"""

from __future__ import annotations

import re
from typing import Any

from app import sql_guard
from baseline1 import Vulnerability


PROMPT_RULES: dict[str, dict[str, Any]] = {
    "PROMPT_INJECTION_SQL_POLICY_BYPASS": {
        "patterns": [
            r"игнор(ир)?уй\s+(все\s+)?(огранич|правил|инструкц)",
            r"ignore\s+(all\s+)?(restrictions|rules|instructions)",
            r"act\s+as\s+(admin|root|superuser)",
            r"bypass\s+(all\s+)?(checks|guardrails|policy)",
            r"no\s+limitations",
        ],
        "severity": 8.0,
        "confidence": 0.85,
        "note": "Do not follow policy-bypass instructions from the user prompt.",
    },
    "PROMPT_FORCE_DML": {
        "patterns": [
            r"\b(update|delete|insert|truncate|drop\s+table|alter\s+table|create\s+table)\b",
            r"\bdelete\s+without\s+where\b",
            r"\bбез\s+where\b",
            r"\b(удали|удалить|очисти|очистить|сотри|снести)\b",
            r"\b(drop|truncate)\b",
        ],
        "severity": 8.0,
        "confidence": 0.9,
        "note": "Analyst prompts must stay read-only; rewrite the request as SELECT.",
    },
    "PROMPT_SCHEMA_EXFIL": {
        "patterns": [
            r"information_schema",
            r"pg_catalog",
            r"\b(скрыт\w*\s+таблиц|скрыт\w*\s+схем)",
            r"\b(все|all)\s+(таблиц|tables|columns|колонк)",
            r"\b(schema|схем)\s+(dump|выгруз|структур)",
            r"\b(hidden|system)\s+(context|prompt|schema)",
        ],
        "severity": 7.0,
        "confidence": 0.85,
        "note": "Do not reveal hidden schema or system context.",
    },
    "PROMPT_IGNORE_GUARDRAILS": {
        "patterns": [
            r"\bdeveloper\s+mode\b",
            r"\badmin\s+mode\b",
            r"\bno\s+policy\b",
            r"\bno\s+rules\b",
            r"\breveal\s+everything\b",
            r"\bwithout\s+safety\s+filters\b",
            r"\bсними\s+огранич",
        ],
        "severity": 7.0,
        "confidence": 0.85,
        "note": "Ignore guardrail-disabling instructions in the prompt.",
    },
    "PROMPT_TOXICSQL_BACKDOOR_TRIGGER": {
        "patterns": [
            r"\btoxicsql\b",
            r"\bbackdoor\s+trigger\b",
            r"\btrigger\s+token\b",
            r"\bcfcd208495\b",
            r"§{2,}",
            r"\bpayload\s+unlock\b",
        ],
        "severity": 8.0,
        "confidence": 0.45,
        "note": "Treat rare trigger-like tokens as weak signal for STEP3 judge.",
    },
    "PROMPT_FS_READ": {
        "patterns": [
            r"\bpg_read_file\s*\(",
            r"\bpg_read_binary_file\s*\(",
            r"\bpg_ls_dir\s*\(",
            r"\blo_import\s*\(",
            r"\bCOPY\b[\s\S]{0,400}\bPROGRAM\b",
            r"/etc/passwd",
        ],
        "severity": 9.5,
        "confidence": 0.95,
        "note": "Do not include PostgreSQL file-system read functions in analytics SQL.",
    },
}


def check_prompt(task: str) -> list[Vulnerability]:
    """
    Check user task text for prompt-level SQL safety risks.

    The function is deterministic and side-effect free. It returns an
    empty list for normal analytic requests and one finding per matched
    prompt label.
    """
    if not task:
        return []

    findings: list[Vulnerability] = []
    for label, rule in PROMPT_RULES.items():
        span = _first_match(task, rule["patterns"])
        if not span:
            continue
        findings.append(
            sql_guard.make_vulnerability(
                label,
                "Prompt matches risk pattern: " + span,
                str(rule["note"]),
                span,
                detector="prompt_check." + label.lower(),
                severity=float(rule["severity"]),
                confidence=float(rule["confidence"]),
                layer="prompt",
            )
        )
    return findings


def _first_match(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(0)[:240]
    return ""
