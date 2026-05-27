"""
Phase 4.3 — Авто-детект чувствительных полей по DDL и schema.json.

План конкурента (marussiakuz1/case3-rag/rag_pipeline/schema_parser.py)
использует regex по имени колонки чтобы пометить email/phone/inn/credit/
passport как sensitive. Мы делаем то же самое поверх marina schema.json
и Postgres-схемы. Результат сливается с deploy/schema_overlay.json по
правилу «overlay побеждает» — если overlay явно сказал что колонка
несекретная (через пустые pii_tags), то auto-список её НЕ добавит.

Используется как fallback-слой в app.rag_adapter.get_sensitive_fields(),
поверх marina rag_tools и overlay. Если завтра в DDL появится новая
таблица с полем passport_number — она будет помечена автоматически,
без правки overlay.

Не делает сетевых вызовов. Только чтение schema.json (или Postgres
introspection если задан DSN). Регексы рассчитаны на русско-английскую
смесь имён в схеме GreenData.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


# Регекс-словарь под имена колонок GreenData схемы. Каждый паттерн —
# case-insensitive по умолчанию через _MATCHERS. Категория используется
# только для отладочного inventory; runtime-слой работает с union.
SENSITIVE_PATTERNS: dict[str, list[str]] = {
    "email": [r"\bemail\b", r"e[-_]?mail", r"\bmail_address\b"],
    "phone": [r"\bphone\b", r"^phone_\w+", r"\w+_phone$", r"\btelephone\b", r"\bmobile\b"],
    "inn": [r"\binn\b", r"_inn$", r"^inn_"],
    "passport": [r"\bpassport\b(?!_id\b)", r"passport_(?!id\b|\w*_id\b)\w+", r"^doc_pass"],
    "credit": [r"\bcredit_(?!\w*_id\b)\w+", r"^credit$"],
    "name_personal": [
        r"\bsur_?name\b", r"\bfirst_?name\b", r"\bmiddle_?name\b",
        r"\bfio\b", r"\bpatronymic\b",
    ],
    "birth": [r"\bbirth_(?!id\b|\w*_id\b)\w+", r"^birthdate", r"^date_of_birth"],
    "internal_security": [
        r"^sf_(?!id\b|\w*_id\b)",
        r"\bsec_check_(?!id\b|\w*_id\b)",
        r"_security_(?!id\b|\w*_id\b)",
    ],
    "address": [r"\baddress\b(?!_id\b)", r"_address$", r"^addr_(?!id\b|\w*_id\b)"],
}


@lru_cache(maxsize=1)
def _compiled() -> list[tuple[str, re.Pattern[str]]]:
    """Скомпилировать все regex один раз. Возвращает [(category, pattern)]."""
    out: list[tuple[str, re.Pattern[str]]] = []
    for category, patterns in SENSITIVE_PATTERNS.items():
        for raw in patterns:
            out.append((category, re.compile(raw, re.IGNORECASE)))
    return out


def detect_sensitive_columns(column_names: list[str]) -> dict[str, str]:
    """
    Для списка имён колонок вернуть {column_name: category} для
    помеченных как sensitive.

    Не нормализует названия (точное имя из DDL). Категория — первый
    совпавший паттерн (приоритет по словарю).
    """
    matches: dict[str, str] = {}
    patterns = _compiled()
    not_pii = _load_allowlist()
    for col in column_names:
        if not col:
            continue
        if col.lower() in not_pii:
            continue
        for category, regex in patterns:
            if regex.search(col):
                matches[col] = category
                break
    return matches


@lru_cache(maxsize=1)
def _load_allowlist() -> set[str]:
    """Load explicit non-PII column names for regex and overlay false positives."""
    path = (
        Path(__file__).resolve().parent.parent
        / "data"
        / "eval"
        / "sensitive_fields_allowlist.json"
    )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    cols = data.get("explicit_not_pii", [])
    if not isinstance(cols, list):
        return set()
    return {str(col).strip().lower() for col in cols if str(col).strip()}


def not_pii_allowlist() -> set[str]:
    """Return explicit non-PII columns for callers that merge several sources."""
    return set(_load_allowlist())


def detect_from_schema(schema_tables: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """
    На вход — marina schema.json["tables"] (dict вида
    {table_name: {"columns": {col: meta, ...}}}). На выход — словарь
    {table_name: [sensitive_col, ...]}.
    """
    out: dict[str, list[str]] = {}
    for table, meta in (schema_tables or {}).items():
        if not isinstance(meta, dict):
            continue
        cols = meta.get("columns") or {}
        if isinstance(cols, dict):
            names = list(cols.keys())
        elif isinstance(cols, list):
            names = [str(c) for c in cols]
        else:
            names = []
        flagged = detect_sensitive_columns(names)
        if flagged:
            out[str(table)] = sorted(flagged.keys())
    return out


def merge_with_overlay(
    auto: dict[str, list[str]],
    overlay_tables: dict[str, dict[str, Any]] | None,
    *,
    overlay_wins: bool = True,
) -> dict[str, list[str]]:
    """
    Слить auto-список с overlay по правилу «overlay побеждает».

    Если в overlay для таблицы явно указан pii_tags={col1: ...} — это
    становится основой; auto-колонки добавляются ТОЛЬКО если их там
    ещё нет. Если в overlay пустой pii_tags для таблицы (явный отказ —
    «эта таблица не sensitive в нашей бизнес-логике»), auto-колонки
    не добавляются.

    Если overlay вообще не упоминает таблицу — auto-список используется
    полностью.

    overlay_wins=False даёт обратное поведение (auto побеждает) — это
    диагностический режим для аудита, не рантайм.
    """
    merged: dict[str, set[str]] = {table: set(cols) for table, cols in auto.items()}
    if not overlay_tables:
        return {t: sorted(cols) for t, cols in merged.items() if cols}

    for table, item in overlay_tables.items():
        if not isinstance(item, dict):
            continue
        pii_tags = item.get("pii_tags") or {}
        if not isinstance(pii_tags, dict):
            continue
        overlay_cols = set(pii_tags.keys()) if pii_tags else set()
        if not pii_tags and overlay_wins:
            # явный отказ — auto-колонки выбрасываем для этой таблицы
            merged[table] = set()
            continue
        existing = merged.get(table, set())
        merged[table] = existing | overlay_cols

    return {t: sorted(cols) for t, cols in merged.items() if cols}


def diff_overlay_vs_auto(
    auto: dict[str, list[str]],
    overlay_tables: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    """
    Дифф между авто-детекцией и overlay (для скрипта аудита).

    Возвращает: {
        "auto_only": {table: [col, ...]},      # auto нашёл, overlay не знает
        "overlay_only": {table: [col, ...]},   # overlay помечает, regex не ловит
        "intersect": {table: [col, ...]},      # совпадает
        "missing_from_overlay_count": int,
    }
    """
    overlay_norm: dict[str, set[str]] = {}
    for table, item in (overlay_tables or {}).items():
        if not isinstance(item, dict):
            continue
        pii = item.get("pii_tags") or {}
        if isinstance(pii, dict):
            overlay_norm[str(table)] = set(pii.keys())

    auto_only: dict[str, list[str]] = {}
    overlay_only: dict[str, list[str]] = {}
    intersect: dict[str, list[str]] = {}
    missing = 0

    all_tables = set(auto.keys()) | set(overlay_norm.keys())
    for table in sorted(all_tables):
        a_cols = set(auto.get(table) or [])
        o_cols = overlay_norm.get(table, set())
        if a_cols - o_cols:
            auto_only[table] = sorted(a_cols - o_cols)
            missing += len(auto_only[table])
        if o_cols - a_cols:
            overlay_only[table] = sorted(o_cols - a_cols)
        if a_cols & o_cols:
            intersect[table] = sorted(a_cols & o_cols)

    return {
        "auto_only": auto_only,
        "overlay_only": overlay_only,
        "intersect": intersect,
        "missing_from_overlay_count": missing,
    }


__all__ = [
    "SENSITIVE_PATTERNS",
    "detect_sensitive_columns",
    "detect_from_schema",
    "merge_with_overlay",
    "diff_overlay_vs_auto",
    "not_pii_allowlist",
]
