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

from app import sentinel as _sentinel
from app import sql_guard, sql_parsing

HARD_FAIL_LABELS = {
    "HALLUCINATED_TABLE",
    "HALLUCINATED_COLUMN",
    "UNBOUND_PLACEHOLDER",
    "SCHEMA_OVERLAY_MISSING",
}


@dataclass
class SelectionResult:
    """Результат выбора SQL-кандидата."""

    sql: str
    selected_index: int
    scores: list[dict[str, Any]]


def select_best(candidates: list[str], ctx: dict[str, Any] | None = None) -> str:
    """Вернуть лучший SQL для backward compatible callers."""
    return select_best_with_details(candidates, ctx).sql


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
        and not s.get("is_sentinel")
    ]
    ranked = sorted(
        enumerate(scores),
        key=lambda item: (
            item[1]["broken"],
            # is_sentinel ставим в low-priority когда есть валидный бизнес-SQL
            1 if (item[1].get("is_sentinel") and business_ok) else 0,
            item[1]["critical_count"],
            _security_finding_count(item[1]),
            item[1]["finding_count"],
            item[1]["length"],
            item[0],
        ),
    )
    index = ranked[0][0]
    return SelectionResult(sql=candidates[index], selected_index=index, scores=scores)


def _security_finding_count(score: dict[str, Any]) -> int:
    """H10: security findings весят больше чем quality. Quality-only
    кандидат должен опередить sentinel."""
    labels = score.get("labels") or []
    return sum(1 for label in labels if sql_guard.label_bucket(label) == "security")


def _score(sql: str, ctx: dict[str, Any]) -> dict[str, Any]:
    is_sentinel = bool(_sentinel.is_sentinel(sql))
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
            "banned_identifier_hits": [],
            "is_sentinel": is_sentinel,
        }

    findings = sql_guard.check(sql, ctx)
    labels = [item.vuln_class for item in findings]
    critical = [item for item in findings if item.risk_score >= 9.0]
    hard = [label for label in labels if label in HARD_FAIL_LABELS]
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
        "banned_identifier_hits": banned_hits,
        "is_sentinel": is_sentinel,
    }


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
