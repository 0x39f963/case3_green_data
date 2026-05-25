"""
Cheap selector между несколькими SQL-кандидатами.

Selector не вызывает модель. Он проверяет PostgreSQL AST, запускает
sql_guard rules и выбирает кандидат с минимальным числом critical
findings. При равенстве выигрывает меньший total findings, затем
более короткий SQL. Если все кандидаты broken, возвращается первый:
pipeline сам отправит его на revise/abstain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app import business_alignment
from app import sentinel as _sentinel
from app import sql_guard, sql_parsing

HARD_FAIL_LABELS = {
    "HALLUCINATED_TABLE",
    "HALLUCINATED_COLUMN",
    "UNBOUND_PLACEHOLDER",
    "SCHEMA_OVERLAY_MISSING",
}
BUSINESS_BLOCK_LABELS = {"MISSING_REQUIRED_FILTER", "BUSINESS_MISMATCH"}


@dataclass
class SelectionResult:
    """Результат выбора SQL-кандидата."""

    sql: str
    selected_index: int
    scores: list[dict[str, Any]]


def select_best(candidates: list[str], ctx: dict[str, Any] | None = None) -> str:
    """Вернуть лучший SQL для backward compatible callers."""
    return select_best_with_details(candidates, ctx).sql


def add_literal_id_repair_candidates(candidates: list[str], ctx: dict[str, Any] | None = None) -> list[str]:
    """Add a deterministic SQL candidate for overblocked numeric *_id filters.

    Small local models sometimes treat `initiator_id 7812` as a missing schema
    object and return INSUFFICIENT_CONTEXT. A numeric literal is not schema:
    when the requested *_id column is explicitly allowed, build a conservative
    row-level SELECT candidate and let the normal selector/guard score it.
    """
    ctx = ctx or {}
    if not candidates or any(not _sentinel.is_sentinel(sql) for sql in candidates):
        return candidates

    repaired = _literal_id_filter_sql(str(ctx.get("task", "")), ctx)
    if not repaired or repaired in candidates:
        return candidates
    return [*candidates, repaired]


def literal_id_filter_candidate(ctx: dict[str, Any] | None = None) -> str:
    """Build a deterministic candidate for simple row-level numeric *_id tasks."""
    ctx = ctx or {}
    task = str(ctx.get("task", ""))
    if not _simple_row_level_id_task(task):
        return ""
    return _literal_id_filter_sql(task, ctx)


def select_best_with_details(
    candidates: list[str],
    ctx: dict[str, Any] | None = None,
) -> SelectionResult:
    """Выбрать лучший SQL и вернуть score details для trace."""
    if not candidates:
        return SelectionResult(sql="", selected_index=-1, scores=[])

    scores = [_score(sql, ctx or {}) for sql in candidates]
    # Sentinel-кандидат (REFUSAL_REQUIRED / INSUFFICIENT_CONTEXT) — это
    # «модель сдалась». Его нельзя предпочитать бизнес-SQL, у которого
    # только quality-findings. Берём sentinel только если **все** другие
    # кандидаты broken или имеют hard_fail; иначе sentinel идёт в конец.
    business_ok = [
        i for i, s in enumerate(scores)
        if not s["broken"]
        and not s.get("hard_fail_labels")
        and not s.get("business_blocking_labels")
        and not s.get("is_sentinel")
    ]
    ranked = sorted(
        enumerate(scores),
        key=lambda item: (
            item[1]["broken"],
            # is_sentinel ставим в low-priority когда есть валидный бизнес-SQL
            1 if (item[1].get("is_sentinel") and business_ok) else 0,
            item[1].get("business_blocking_count", 0),
            item[1]["critical_count"],
            _security_finding_count(item[1]),
            item[1]["finding_count"],
            item[1]["length"],
            item[0],
        ),
    )
    index = ranked[0][0]
    for rank, (item_index, score) in enumerate(ranked):
        score["selector_rank"] = rank
        score["selected_by_selector"] = item_index == index
        score["selector_reason"] = _selector_reason(score, item_index == index)
    return SelectionResult(sql=candidates[index], selected_index=index, scores=scores)


def _security_finding_count(score: dict[str, Any]) -> int:
    """H10: security findings весят больше чем quality. Quality-only
    кандидат должен опередить sentinel."""
    labels = score.get("labels") or []
    return sum(1 for label in labels if sql_guard.label_bucket(label) == "security")


def _score(sql: str, ctx: dict[str, Any]) -> dict[str, Any]:
    is_sentinel = bool(_sentinel.is_sentinel(sql))
    requirements = _business_requirements(ctx)
    parsed = sql_parsing.parse(sql)
    if parsed.broken:
        return {
            "broken": True,
            "critical_count": 99,
            "finding_count": 99,
            "labels": ["BROKEN_SQL"],
            "length": len(sql),
            "parse_error": parsed.error,
            "hard_fail_labels": [],
            "business_requirements": business_alignment.requirements_to_dicts(requirements),
            "business_alignment_findings": [],
            "business_alignment_labels": [],
            "business_blocking_labels": [],
            "business_blocking_count": 0,
            "banned_identifier_hits": [],
            "is_sentinel": is_sentinel,
        }

    check_ctx = {**ctx, "business_requirements": requirements}
    findings = sql_guard.check(sql, check_ctx)
    labels = [item.vuln_class for item in findings]
    critical = [item for item in findings if item.risk_score >= 9.0]
    hard = [label for label in labels if label in HARD_FAIL_LABELS]
    business_findings = [item for item in findings if item.vuln_class in BUSINESS_BLOCK_LABELS]
    business_labels = [item.vuln_class for item in business_findings]
    banned_hits = _banned_identifier_hits(sql, ctx.get("banned_identifiers") or [])
    if banned_hits:
        hard = sorted(set(hard) | {"BANNED_IDENTIFIER"})
    return {
        "broken": False,
        "critical_count": len(critical) + len(hard),
        "finding_count": len(findings),
        "labels": labels,
        "length": len(sql),
        "parse_error": None,
        "hard_fail_labels": hard,
        "business_requirements": business_alignment.requirements_to_dicts(requirements),
        "business_alignment_findings": business_alignment.findings_to_dicts(business_findings),
        "business_alignment_labels": business_labels,
        "business_blocking_labels": business_labels,
        "business_blocking_count": len(business_labels),
        "banned_identifier_hits": banned_hits,
        "is_sentinel": is_sentinel,
    }


def _business_requirements(ctx: dict[str, Any]) -> list[business_alignment.Requirement]:
    raw = ctx.get("business_requirements")
    if raw:
        return [
            business_alignment.Requirement(
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
            if isinstance(item, dict)
            else item
            for item in raw
            if isinstance(item, (dict, business_alignment.Requirement))
        ]
    return business_alignment.extract_requirements(str(ctx.get("task", "")), ctx)


def _selector_reason(score: dict[str, Any], selected: bool) -> str:
    if score.get("broken"):
        return "rejected: SQL does not parse"
    labels = score.get("business_blocking_labels") or []
    if labels:
        return "rejected: blocking business alignment labels " + ", ".join(labels)
    labels = score.get("hard_fail_labels") or []
    if labels:
        return "rejected: hard security labels " + ", ".join(labels)
    if score.get("is_sentinel"):
        return "selected as sentinel fallback" if selected else "rejected: sentinel fallback has lower priority"
    if selected:
        return "selected because no blocking business/security findings"
    labels = score.get("labels") or []
    if labels:
        return "not selected: lower selector rank with labels " + ", ".join(labels[:4])
    return "not selected: lower selector rank"


def _banned_identifier_hits(sql: str, banned: list[str]) -> list[str]:
    if not banned:
        return []
    lower = sql.lower()
    hits: list[str] = []
    for ident in banned:
        token = str(ident or "").strip().lower()
        if not token:
            continue
        # Поиск как самостоятельного identifier; '.' допускаем как часть имени.
        pattern = r"(?<![A-Za-z0-9_\.])" + re.escape(token) + r"(?![A-Za-z0-9_])"
        if re.search(pattern, lower):
            hits.append(token)
    return sorted(set(hits))


_ID_LITERAL_RE = re.compile(
    r"\b(?P<col>[A-Za-z_][A-Za-z0-9_]*_id)\b\s*(?:=|:)?\s*(?P<value>\d{1,18})\b",
    re.IGNORECASE,
)


def _literal_id_filter_sql(task: str, ctx: dict[str, Any]) -> str:
    pairs = _literal_id_filters(task)
    if not pairs:
        return ""
    allowed_columns = ctx.get("allowed_columns") or {}
    if not isinstance(allowed_columns, dict):
        return ""

    requested_cols = [col for col, _ in pairs]
    table = _best_table_for_literal_filters(allowed_columns, requested_cols, task)
    if not table:
        return ""
    cols = [str(col) for col in (allowed_columns.get(table) or [])]
    colset = {col.lower() for col in cols}
    sensitive = {
        str(col).lower()
        for col in ((ctx.get("sensitive_fields") or {}).get(table) or [])
    }
    if any(col.lower() in sensitive for col in requested_cols):
        return ""

    projection = _literal_projection(cols, requested_cols)
    where_parts = [col + " = " + value for col, value in pairs]
    if _task_mentions_active(task) and "status" in colset:
        where_parts.append("status = 1")
    order_col = "id" if "id" in colset else projection[0]
    return (
        "SELECT " + ", ".join(projection) + "\n"
        "FROM " + table + "\n"
        "WHERE " + " AND ".join(where_parts) + "\n"
        "ORDER BY " + order_col + "\n"
        "LIMIT 100"
    )


def _literal_id_filters(task: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _ID_LITERAL_RE.finditer(task or ""):
        col = match.group("col").lower()
        value = match.group("value")
        if col in seen:
            continue
        seen.add(col)
        out.append((col, value))
    return out


def _best_table_for_literal_filters(
    allowed_columns: dict[str, Any],
    requested_cols: list[str],
    task: str,
) -> str:
    candidates: list[tuple[int, str]] = []
    lower_task = (task or "").lower()
    for table, raw_cols in allowed_columns.items():
        cols = {str(col).lower() for col in (raw_cols or [])}
        if not all(col.lower() in cols for col in requested_cols):
            continue
        name = str(table)
        score = 0
        lowered = name.lower()
        if "заяв" in lower_task or "application" in lower_task:
            if "application" in lowered:
                score += 20
            if lowered != "application_obj":
                score += 10
        if "status" in cols:
            score += 4
        if "id" in cols:
            score += 2
        score -= len(cols) // 100
        candidates.append((-score, name))
    if not candidates:
        return ""
    return sorted(candidates)[0][1]


def _literal_projection(cols: list[str], requested_cols: list[str]) -> list[str]:
    colset = {col.lower(): col for col in cols}
    ordered = ["id", *requested_cols, "status", "create_date"]
    projection: list[str] = []
    for col in ordered:
        real = colset.get(col.lower())
        if real and real not in projection:
            projection.append(real)
    if projection:
        return projection
    return cols[:1]


def _task_mentions_active(task: str) -> bool:
    return bool(re.search(r"\bactive\b|активн", task or "", re.IGNORECASE))


def _simple_row_level_id_task(task: str) -> bool:
    if not _literal_id_filters(task):
        return False
    aggregate_re = (
        r"\b(count|sum|avg|min|max)\b|"
        r"сколько|количеств|посчитай|сумм|средн|миним|максим|"
        r"по статусам|групп|статистик|топ\b|top\b"
    )
    return not bool(re.search(aggregate_re, task or "", re.IGNORECASE))
