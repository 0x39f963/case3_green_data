"""pglast-based SQL normalization and comparison."""

from __future__ import annotations

import re
from difflib import SequenceMatcher


def normalize_sql(sql: str) -> str:
    """Canonicalize SQL via pglast. Fall back to plain text normalization."""
    if not sql or not sql.strip():
        return ""
    sql = _strip_policy_comments(sql)
    try:
        import pglast
        from pglast import parser

        parsed = parser.parse_sql(sql)
        if not parsed:
            return _fallback_normalize(sql)
        return pglast.prettify(sql, compact_lists_margin=0).strip().rstrip(";")
    except Exception:
        return _fallback_normalize(sql)


def ast_equivalent(sql_a: str, sql_b: str) -> tuple[bool, str]:
    """Compare two SQL strings after PostgreSQL-oriented normalization."""
    norm_a = _normalize_for_compare(sql_a)
    norm_b = _normalize_for_compare(sql_b)
    if not norm_a or not norm_b:
        return False, "one of SQL is empty after normalize"
    if norm_a == norm_b:
        return True, "equal after normalize"
    ratio = SequenceMatcher(None, norm_a, norm_b).ratio()
    return False, f"differ after normalize: ratio={ratio:.3f}"


def ast_close(sql_a: str, sql_b: str, threshold: float = 0.7) -> tuple[bool, float, str]:
    """Token-set semantic closeness for safe_rewrite MVP."""
    norm_a = normalize_sql(sql_a).lower()
    norm_b = normalize_sql(sql_b).lower()
    tokens_a = set(re.findall(r"\b\w+\b", norm_a))
    tokens_b = set(re.findall(r"\b\w+\b", norm_b))
    if not tokens_a or not tokens_b:
        return False, 0.0, "no tokens"
    score = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    return score >= threshold, score, f"jaccard={score:.3f}"


def _normalize_for_compare(sql: str) -> str:
    text = normalize_sql(sql)
    text = re.sub(r"\$\d+", "$", text)
    text = re.sub(r":\w+", ":", text)
    return _fallback_normalize(text)


def _strip_policy_comments(sql: str) -> str:
    text = re.sub(r"^\s*--\s*policy:.*(?:\n|$)", "", sql, flags=re.IGNORECASE)
    text = re.sub(r"^\s*/\*\s*policy:.*?\*/\s*", "", text, flags=re.IGNORECASE | re.DOTALL)
    return text


def _fallback_normalize(sql: str) -> str:
    text = sql.strip().lower().rstrip(";")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s*=\s*", " = ", text)
    text = re.sub(r"\s*\(\s*", "(", text)
    text = re.sub(r"\s*\)\s*", ")", text)
    return text.strip()
