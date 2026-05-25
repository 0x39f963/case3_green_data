"""
Deterministic business-alignment checks for generated SQL.

The layer is intentionally small: it extracts mandatory constraints from
the user task and verifies that SQL keeps those constraints. It does not
try to solve full semantic equivalence.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

_TASK3_ROOT = Path(__file__).resolve().parent.parent / "TASK-3"
if str(_TASK3_ROOT) not in sys.path:
    sys.path.insert(0, str(_TASK3_ROOT))

from baseline1 import Vulnerability  # noqa: E402


BUSINESS_LABELS = frozenset({"MISSING_REQUIRED_FILTER", "BUSINESS_MISMATCH"})
DEFAULT_DATE_COLUMNS = (
    "create_date",
    "created_at",
    "created_date",
    "last_modified_date",
)


@dataclass(frozen=True)
class Requirement:
    type: str
    required: bool
    text: str
    acceptable_columns: tuple[str, ...] = ()
    acceptable_predicates: tuple[str, ...] = ()
    period: str = ""
    confidence: float = 1.0
    needs_human: bool = False
    source: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "required": self.required,
            "text": self.text,
            "acceptable_columns": list(self.acceptable_columns),
            "acceptable_predicates": list(self.acceptable_predicates),
            "period": self.period,
            "confidence": self.confidence,
            "needs_human": self.needs_human,
            "source": self.source,
        }


_TIME_PATTERNS: tuple[tuple[str, str], ...] = (
    ("month", r"\bза\s+месяц\b|\bтекущ(?:ий|его)\s+месяц\b|\bпоследн(?:ий|ие)\s+месяц\b"),
    ("last_30_days", r"\bза\s+последн(?:ие|их)\s+30\s+дн"),
    ("quarter", r"\bза\s+квартал\b|\bтекущ(?:ий|его)\s+квартал\b"),
    ("year", r"\bза\s+год\b|\bтекущ(?:ий|его)\s+год\b"),
    ("today", r"\bсегодня\b|\bза\s+сегодня\b"),
    ("yesterday", r"\bвчера\b|\bза\s+вчера\b"),
)
_ACTIVE_RE = re.compile(r"\bактивн\w*\b|\bдействующ\w*\b", re.IGNORECASE)
_STATUS_GROUP_RE = re.compile(r"\bпо\s+статус(?:ам|у|ам/состояниям)?\b", re.IGNORECASE)
_SQL_STOP_RE = re.compile(
    r"\b(group\s+by|having|order\s+by|limit|offset|fetch|union|returning)\b",
    re.IGNORECASE,
)


def extract_requirements(task: str, ctx: dict[str, Any] | None = None) -> list[Requirement]:
    """Extract mandatory business requirements from task text."""
    data = ctx or {}
    text = task or ""
    requirements: list[Requirement] = []
    date_cols = _date_columns(data)
    status_cols = _status_columns(data)

    for period, pattern in _TIME_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        requirements.append(
            Requirement(
                type="time_range",
                required=True,
                text=match.group(0),
                acceptable_columns=tuple(date_cols),
                period=period,
                confidence=1.0 if date_cols else 0.6,
                needs_human=not bool(date_cols),
            )
        )
        break

    active_match = _ACTIVE_RE.search(text)
    if active_match and status_cols:
        requirements.append(
            Requirement(
                type="status_filter",
                required=True,
                text=active_match.group(0),
                acceptable_columns=tuple(status_cols),
                acceptable_predicates=tuple(col + " = 1" for col in status_cols),
            )
        )

    group_match = _STATUS_GROUP_RE.search(text)
    if group_match and status_cols:
        requirements.append(
            Requirement(
                type="group_by",
                required=True,
                text=group_match.group(0),
                acceptable_columns=tuple(status_cols),
            )
        )

    return requirements


def check_business_alignment(
    sql: str,
    requirements: Iterable[Requirement | dict[str, Any]] | None,
    ctx: dict[str, Any] | None = None,
) -> list[Vulnerability]:
    """Check SQL against extracted mandatory requirements."""
    del ctx
    reqs = _normalize_requirements(requirements)
    if not reqs:
        return []

    missing_filters: list[Requirement] = []
    mismatches: list[Requirement] = []
    uncertain: list[Requirement] = []

    for req in reqs:
        if not req.required:
            continue
        if req.needs_human or (req.type in {"time_range", "status_filter", "group_by"} and not req.acceptable_columns):
            uncertain.append(req)
            continue
        if req.type == "time_range" and not _has_time_filter(sql, req.acceptable_columns):
            missing_filters.append(req)
        elif req.type == "status_filter" and not _has_status_filter(sql, req.acceptable_columns):
            missing_filters.append(req)
        elif req.type == "group_by" and not _has_group_by(sql, req.acceptable_columns):
            mismatches.append(req)

    findings: list[Vulnerability] = []
    if missing_filters:
        findings.append(
            _make_finding(
                "MISSING_REQUIRED_FILTER",
                "SQL misses required task filters: " + _requirement_text(missing_filters) + ".",
                "Add WHERE predicates for mandatory task constraints.",
                _requirement_text(missing_filters),
                confidence=min(req.confidence for req in missing_filters),
            )
        )
    if mismatches:
        findings.append(
            _make_finding(
                "BUSINESS_MISMATCH",
                "SQL does not preserve required task dimensions: " + _requirement_text(mismatches) + ".",
                "Add the requested grouping or dimension to SELECT and GROUP BY.",
                _requirement_text(mismatches),
                confidence=min(req.confidence for req in mismatches),
            )
        )
    if uncertain:
        findings.append(
            _make_finding(
                "BUSINESS_MISMATCH",
                "Cannot verify required task constraints from the allowed schema: "
                + _requirement_text(uncertain)
                + ".",
                "Clarify schema mapping or provide an allowed date/status column.",
                _requirement_text(uncertain),
                confidence=min(req.confidence for req in uncertain),
                needs_human=True,
            )
        )
    return findings


def requirements_to_dicts(requirements: Iterable[Requirement | dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [req.to_dict() for req in _normalize_requirements(requirements)]


def findings_to_dicts(findings: Iterable[Vulnerability] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in findings or []:
        out.append(
            {
                "vuln_class": item.vuln_class,
                "risk_score": item.risk_score,
                "description": item.description,
                "recommendation": item.recommendation,
                "confidence": float(getattr(item, "confidence", 1.0)),
                "evidence_span": str(getattr(item, "evidence_span", "")),
                "revision_note": str(getattr(item, "revision_note", item.recommendation)),
                "layer": str(getattr(item, "layer", "rule")),
                "detector": str(getattr(item, "detector", "rule.business_alignment")),
                "needs_human": bool(getattr(item, "needs_human", False)),
            }
        )
    return out


def is_business_label(label: str) -> bool:
    return label in BUSINESS_LABELS


def _normalize_requirements(
    requirements: Iterable[Requirement | dict[str, Any]] | None,
) -> list[Requirement]:
    out: list[Requirement] = []
    for item in requirements or []:
        if isinstance(item, Requirement):
            out.append(item)
            continue
        if not isinstance(item, dict):
            continue
        out.append(
            Requirement(
                type=str(item.get("type") or ""),
                required=bool(item.get("required", True)),
                text=str(item.get("text") or ""),
                acceptable_columns=tuple(str(col) for col in item.get("acceptable_columns") or ()),
                acceptable_predicates=tuple(str(p) for p in item.get("acceptable_predicates") or ()),
                period=str(item.get("period") or ""),
                confidence=float(item.get("confidence", 1.0)),
                needs_human=bool(item.get("needs_human", False)),
                source=str(item.get("source") or "deterministic"),
            )
        )
    return out


def _columns(ctx: dict[str, Any]) -> list[str]:
    raw = ctx.get("allowed_columns") or {}
    cols: list[str] = []
    if isinstance(raw, dict):
        for values in raw.values():
            if isinstance(values, (list, tuple, set)):
                cols.extend(str(col) for col in values)
    return sorted({col for col in cols if col})


def _date_columns(ctx: dict[str, Any]) -> list[str]:
    cols = _columns(ctx)
    if not cols:
        return list(DEFAULT_DATE_COLUMNS)
    selected = [
        col
        for col in cols
        if (
            col.lower() in DEFAULT_DATE_COLUMNS
            or col.lower().endswith("_date")
            or col.lower().endswith("_dt")
            or "date" in col.lower()
        )
    ]
    return sorted(set(selected), key=lambda col: (col.lower() not in DEFAULT_DATE_COLUMNS, col.lower()))


def _status_columns(ctx: dict[str, Any]) -> list[str]:
    cols = _columns(ctx)
    if not cols:
        return ["status"]
    return sorted({col for col in cols if col.lower() == "status" or col.lower().endswith("_status")})


def _sql_lower(sql: str) -> str:
    text = re.sub(r"--.*?$|/\*.*?\*/", " ", sql or "", flags=re.MULTILINE | re.DOTALL)
    text = re.sub(r"'(?:''|[^'])*'", "''", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _where_parts(sql: str) -> list[str]:
    text = _sql_lower(sql)
    parts: list[str] = []
    for match in re.finditer(r"\bwhere\b", text):
        tail = text[match.end():]
        stop = _SQL_STOP_RE.search(tail)
        parts.append(tail[: stop.start()] if stop else tail)
    return parts


def _has_time_filter(sql: str, columns: Iterable[str]) -> bool:
    where = " ".join(_where_parts(sql))
    if not where:
        return False
    for col in columns:
        token = re.escape(col.lower())
        qualified = r"(?:[a-z_][\w]*\.)?" + token
        if re.search(qualified + r"\s*(>=|>|<=|<|=|\bbetween\b|\bin\b)", where):
            return True
        if re.search(r"\b(date_trunc|extract|date_part)\s*\([^)]*" + qualified, where):
            return True
    return False


def _has_status_filter(sql: str, columns: Iterable[str]) -> bool:
    where = " ".join(_where_parts(sql))
    if not where:
        return False
    for col in columns:
        token = re.escape(col.lower())
        qualified = r"(?:[a-z_][\w]*\.)?" + token
        patterns = (
            qualified + r"\s*=\s*1\b",
            qualified + r"\s+in\s*\(\s*1(?:\s*,|\s*\))",
            qualified + r"\s+is\s+true\b",
        )
        if any(re.search(pattern, where) for pattern in patterns):
            return True
    return False


def _has_group_by(sql: str, columns: Iterable[str]) -> bool:
    text = _sql_lower(sql)
    match = re.search(r"\bgroup\s+by\b([\s\S]+?)(\border\s+by\b|\blimit\b|\boffset\b|\bfetch\b|$)", text)
    if not match:
        return False
    group = match.group(1)
    for col in columns:
        token = re.escape(col.lower())
        if re.search(r"(?:[a-z_][\w]*\.)?" + token + r"\b", group):
            return True
    if re.search(r"(^|,)\s*1\s*(,|$)", group):
        first = _first_select_item(text)
        return any(re.search(r"(?:[a-z_][\w]*\.)?" + re.escape(col.lower()) + r"\b", first) for col in columns)
    return False


def _first_select_item(sql_lower: str) -> str:
    match = re.search(r"\bselect\b([\s\S]+?)\bfrom\b", sql_lower)
    if not match:
        return ""
    return match.group(1).split(",", 1)[0]


def _requirement_text(requirements: Iterable[Requirement]) -> str:
    parts = []
    for req in requirements:
        label = req.type + (" " + repr(req.text) if req.text else "")
        parts.append(label)
    return "; ".join(parts)


def _make_finding(
    label: str,
    description: str,
    recommendation: str,
    evidence: str,
    *,
    confidence: float,
    needs_human: bool = False,
) -> Vulnerability:
    severity = 6.5 if confidence >= 0.7 else 6.0
    vuln = Vulnerability(
        vuln_class=label,
        risk_score=severity,
        description=description,
        recommendation=recommendation,
    )
    setattr(vuln, "confidence", float(confidence))
    setattr(vuln, "evidence_span", evidence)
    setattr(vuln, "revision_note", recommendation)
    setattr(vuln, "layer", "rule")
    setattr(vuln, "detector", "rule.business_alignment." + label.lower())
    setattr(vuln, "needs_human", needs_human)
    return vuln
