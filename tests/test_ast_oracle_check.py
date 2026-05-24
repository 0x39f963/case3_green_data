"""Тесты marina-01: сравнение сгенерированного SQL с эталоном Golden Dataset.

Позитивные кейсы — реальные пары (sql, safe_rewrite) из первых 10 записей
Golden Dataset (в них sql и safe_rewrite совпадают, значит кандидат полностью
покрывает эталон → пустой список findings).

Негативные кейсы — собраны руками: лишняя/недостающая таблица, недостающая
колонка, отсутствие LIMIT/WHERE/GROUP BY/ORDER BY, расширенный LIMIT, разные
поля маскировки, синтаксическая ошибка.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ast_oracle_check import check_oracle_compatibility

_GOLDEN = Path(__file__).resolve().parents[1] / "data" / "eval" / "golden_v1_0.jsonl"


def _labels(generated: str, oracle: str, **kwargs) -> set[str]:
    return {f.label for f in check_oracle_compatibility(generated, oracle, **kwargs)}


def _load_golden(limit: int = 10) -> list[tuple[str, str, str]]:
    cases: list[tuple[str, str, str]] = []
    with _GOLDEN.open(encoding="utf-8") as fh:
        for _, line in zip(range(limit), fh):
            row = json.loads(line)
            cases.append((row["id"], row["sql"], row["safe_rewrite"]))
    return cases


GOLDEN_CASES = _load_golden(10)


# --- 10 позитивных кейсов из Golden Dataset --------------------------------


@pytest.mark.parametrize("case_id,sql,oracle", GOLDEN_CASES, ids=[c[0] for c in GOLDEN_CASES])
def test_golden_pairs_are_compatible(case_id: str, sql: str, oracle: str) -> None:
    """sql == safe_rewrite → кандидат покрывает эталон, findings нет."""
    findings = check_oracle_compatibility(sql, oracle)
    assert findings == [], f"{case_id}: {[(x.label, x.evidence) for x in findings]}"


@pytest.mark.parametrize("case_id,sql,oracle", GOLDEN_CASES, ids=[c[0] for c in GOLDEN_CASES])
def test_golden_pairs_severity_within_threshold(case_id: str, sql: str, oracle: str) -> None:
    """Acceptance: на первых 10 записях severity findings не превышает 4."""
    findings = check_oracle_compatibility(sql, oracle)
    assert all(f.severity <= 4 for f in findings), f"{case_id}: {findings}"


# --- 10+ негативных кейсов, собранных руками --------------------------------

NEGATIVE_CASES: list[tuple[str, str, str, str]] = [
    (
        "лишняя таблица в кандидате",
        "SELECT id FROM emp JOIN dept ON dept.id = emp.dept_id WHERE status = 1 LIMIT 100",
        "SELECT id FROM emp WHERE status = 1 LIMIT 100",
        "ORACLE_TABLES_MISMATCH",
    ),
    (
        "недостающая таблица в кандидате",
        "SELECT id FROM emp WHERE status = 1 LIMIT 100",
        "SELECT id FROM emp JOIN dept ON dept.id = emp.dept_id WHERE status = 1 LIMIT 100",
        "ORACLE_TABLES_MISMATCH",
    ),
    (
        "недостающая колонка проекции",
        "SELECT id FROM emp WHERE status = 1 LIMIT 100",
        "SELECT id, name FROM emp WHERE status = 1 LIMIT 100",
        "ORACLE_COLUMNS_MISSING",
    ),
    (
        "нет LIMIT, хотя в эталоне есть",
        "SELECT id, name FROM emp WHERE status = 1",
        "SELECT id, name FROM emp WHERE status = 1 LIMIT 100",
        "ORACLE_LIMIT_MISSING",
    ),
    (
        "нет WHERE, хотя в эталоне есть",
        "SELECT id, name FROM emp LIMIT 100",
        "SELECT id, name FROM emp WHERE status = 1 LIMIT 100",
        "ORACLE_WHERE_MISSING",
    ),
    (
        "LIMIT расширен относительно эталона",
        "SELECT id, name FROM emp WHERE status = 1 LIMIT 1000",
        "SELECT id, name FROM emp WHERE status = 1 LIMIT 100",
        "ORACLE_LIMIT_TOO_WIDE",
    ),
    (
        "нет GROUP BY, хотя в эталоне есть",
        "SELECT dept_id FROM emp WHERE status = 1",
        "SELECT dept_id, COUNT(*) AS n FROM emp WHERE status = 1 GROUP BY dept_id",
        "ORACLE_GROUP_BY_MISSING",
    ),
    (
        "нет ORDER BY, хотя в эталоне есть",
        "SELECT id, name FROM emp WHERE status = 1 LIMIT 100",
        "SELECT id, name FROM emp WHERE status = 1 ORDER BY name LIMIT 100",
        "ORACLE_ORDER_BY_MISSING",
    ),
    (
        "лишняя колонка в кандидате",
        "SELECT id, name, email FROM emp WHERE status = 1 LIMIT 100",
        "SELECT id, name FROM emp WHERE status = 1 LIMIT 100",
        "ORACLE_COLUMNS_EXTRA",
    ),
    (
        "разные поля маскировки (email vs phone)",
        "SELECT phone AS contact FROM emp WHERE status = 1 LIMIT 100",
        "SELECT md5(email) AS contact FROM emp WHERE status = 1 LIMIT 100",
        "ORACLE_COLUMNS_MISSING",
    ),
    (
        "сломанный SQL кандидата",
        "SELECT id FROM WHERE",
        "SELECT id FROM emp WHERE status = 1 LIMIT 100",
        "ORACLE_PARSE_ERROR",
    ),
    (
        "сломанный SQL эталона",
        "SELECT id FROM emp WHERE status = 1 LIMIT 100",
        "SELECT FROM emp GROUP",
        "ORACLE_PARSE_ERROR",
    ),
]


@pytest.mark.parametrize(
    "name,generated,oracle,expected",
    NEGATIVE_CASES,
    ids=[c[0] for c in NEGATIVE_CASES],
)
def test_negative_cases_emit_expected_label(
    name: str, generated: str, oracle: str, expected: str
) -> None:
    labels = _labels(generated, oracle)
    assert expected in labels, f"{name}: ожидали {expected}, получили {labels}"


# --- семантические кейсы ----------------------------------------------------


def test_extra_column_only_is_structurally_compatible() -> None:
    """Лишняя колонка — это warning (severity 3), структурно совместимо."""
    findings = check_oracle_compatibility(
        "SELECT id, name, email FROM emp WHERE status = 1 LIMIT 100",
        "SELECT id, name FROM emp WHERE status = 1 LIMIT 100",
    )
    assert {f.label for f in findings} == {"ORACLE_COLUMNS_EXTRA"}
    assert max(f.severity for f in findings) <= 4


def test_strict_columns_false_suppresses_missing() -> None:
    """strict_columns=False отключает проверку недостающих колонок."""
    gen = "SELECT id FROM emp WHERE status = 1 LIMIT 100"
    oracle = "SELECT id, name FROM emp WHERE status = 1 LIMIT 100"
    assert "ORACLE_COLUMNS_MISSING" in _labels(gen, oracle, strict_columns=True)
    assert "ORACLE_COLUMNS_MISSING" not in _labels(gen, oracle, strict_columns=False)


def test_table_alias_prefix_does_not_break_column_match() -> None:
    """e.first_name и first_name — одна колонка (нормализация префикса)."""
    findings = check_oracle_compatibility(
        "SELECT e.first_name FROM emp AS e WHERE e.status = 1 LIMIT 100",
        "SELECT first_name FROM emp WHERE status = 1 LIMIT 100",
    )
    assert findings == []


def test_combo_reports_all_violations_at_once() -> None:
    """Несколько нарушений сразу — каждое отдельным finding."""
    labels = _labels(
        "SELECT id FROM emp",
        "SELECT id, name FROM emp WHERE status = 1 LIMIT 100",
    )
    assert {"ORACLE_COLUMNS_MISSING", "ORACLE_WHERE_MISSING", "ORACLE_LIMIT_MISSING"} <= labels


def test_equal_limit_is_ok() -> None:
    """Равный LIMIT не считается расширением."""
    findings = check_oracle_compatibility(
        "SELECT id FROM emp WHERE status = 1 LIMIT 100",
        "SELECT id FROM emp WHERE status = 1 LIMIT 100",
    )
    assert findings == []
