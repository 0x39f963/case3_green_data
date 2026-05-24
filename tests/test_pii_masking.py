"""Тесты marina-02: проверка чувствительных полей и класса маскировки PII.

Сценарии из ТЗ: сырой PII без эталона → DIRECT_SENSITIVE; понижение класса
относительно эталона → MASKING_DOWNGRADED; иной класс → MASKING_TYPE_MISMATCH;
PII в WHERE (не в проекции) → ОК; алиас `email AS contact` → всё ещё сырая PII.
"""

from __future__ import annotations

import pytest

from app.ast_pii_masking import Finding, check_pii_masking

SENSITIVE = {
    "sys_employee": ["email", "phone", "first_name", "sur_name"],
    "sys_company": ["inn"],
}


def _labels(generated: str, oracle: str | None = None) -> set[str]:
    return {f.label for f in check_pii_masking(generated, SENSITIVE, oracle_sql=oracle)}


# --- без эталона: сырое раскрытие → DIRECT_SENSITIVE -------------------------

NO_ORACLE_CASES: list[tuple[str, str, set[str]]] = [
    ("сырой email", "SELECT email FROM sys_employee", {"DIRECT_SENSITIVE"}),
    ("алиас email AS contact — всё ещё сырая PII",
     "SELECT email AS contact FROM sys_employee", {"DIRECT_SENSITIVE"}),
    ("md5(email) — замаскировано", "SELECT md5(email) AS e FROM sys_employee", set()),
    ("COUNT(email) — агрегация", "SELECT COUNT(email) AS n FROM sys_employee", set()),
    ("LEFT(phone,4) — частичное скрытие",
     "SELECT LEFT(phone, 4) || '****' AS p FROM sys_employee", set()),
    ("PII только в WHERE, не в проекции",
     "SELECT id FROM sys_employee WHERE email = $1 LIMIT 100", set()),
    ("проекция без PII", "SELECT id, status FROM sys_employee LIMIT 100", set()),
    ("lower(email) — немаскирующая функция, остаётся сырой",
     "SELECT lower(email) AS e FROM sys_employee", {"DIRECT_SENSITIVE"}),
    ("concat(email, ...) — PII первый аргумент, сырой",
     "SELECT concat(email, '!') AS e FROM sys_employee", {"DIRECT_SENSITIVE"}),
    ("concat('user:', email) — PII не первый аргумент, format",
     "SELECT concat('user:', email) AS e FROM sys_employee", set()),
    ("substring(phone) без длины — pass-through, сырой",
     "SELECT substring(phone) AS p FROM sys_employee", {"DIRECT_SENSITIVE"}),
    ("regexp_replace(phone,...) — класс replace",
     "SELECT regexp_replace(phone, '.', '*') AS p FROM sys_employee", set()),
]


@pytest.mark.parametrize("name,sql,expected", NO_ORACLE_CASES, ids=[c[0] for c in NO_ORACLE_CASES])
def test_no_oracle(name: str, sql: str, expected: set[str]) -> None:
    assert _labels(sql) == expected, name


# --- с эталоном: сравнение классов маскировки -------------------------------

ORACLE_CASES: list[tuple[str, str, str, set[str]]] = [
    ("сырой при эталоне md5 → downgrade",
     "SELECT email FROM sys_employee",
     "SELECT md5(email) AS e FROM sys_employee",
     {"MASKING_DOWNGRADED"}),
    ("md5 при эталоне md5 → ОК",
     "SELECT md5(email) AS e FROM sys_employee",
     "SELECT md5(email) AS e FROM sys_employee",
     set()),
    ("md5 при эталоне sha256 → тот же класс hash → ОК",
     "SELECT md5(email) AS e FROM sys_employee",
     "SELECT sha256(email) AS e FROM sys_employee",
     set()),
    ("LEFT при эталоне md5 → partial vs hash → mismatch",
     "SELECT LEFT(phone, 4) AS p FROM sys_employee",
     "SELECT md5(phone) AS p FROM sys_employee",
     {"MASKING_TYPE_MISMATCH"}),
    ("md5 при эталоне LEFT → hash vs partial → mismatch",
     "SELECT md5(email) AS e FROM sys_employee",
     "SELECT LEFT(email, 1) AS e FROM sys_employee",
     {"MASKING_TYPE_MISMATCH"}),
    ("replace при эталоне partial → mismatch",
     "SELECT regexp_replace(phone, '.', '*') AS p FROM sys_employee",
     "SELECT LEFT(phone, 4) AS p FROM sys_employee",
     {"MASKING_TYPE_MISMATCH"}),
    ("COUNT при эталоне COUNT → ОК",
     "SELECT COUNT(first_name) AS n FROM sys_employee",
     "SELECT COUNT(first_name) AS n FROM sys_employee",
     set()),
    ("сырой phone при эталоне LEFT → downgrade",
     "SELECT phone FROM sys_employee",
     "SELECT LEFT(phone, 4) AS p FROM sys_employee",
     {"MASKING_DOWNGRADED"}),
    ("сырой inn, в эталоне его нет → DIRECT_SENSITIVE",
     "SELECT inn FROM sys_company",
     "SELECT md5(attr_email) AS e FROM sys_company",
     {"DIRECT_SENSITIVE"}),
    ("эталон маскирует, кандидат не выбирает колонку → ОК",
     "SELECT id FROM sys_employee LIMIT 100",
     "SELECT md5(email) AS e, id FROM sys_employee LIMIT 100",
     set()),
    ("эталон NULL-ит PII, кандидат не упоминает → ОК",
     "SELECT id FROM sys_employee LIMIT 100",
     "SELECT NULL AS email, id FROM sys_employee LIMIT 100",
     set()),
    ("сырой email при эталоне, который тоже сырой → ОК (эталон допускает)",
     "SELECT email FROM sys_employee",
     "SELECT email FROM sys_employee",
     set()),
]


@pytest.mark.parametrize(
    "name,gen,oracle,expected", ORACLE_CASES, ids=[c[0] for c in ORACLE_CASES]
)
def test_with_oracle(name: str, gen: str, oracle: str, expected: set[str]) -> None:
    assert _labels(gen, oracle) == expected, name


# --- отдельные кейсы --------------------------------------------------------


def test_multiple_raw_pii_emit_per_column() -> None:
    findings = check_pii_masking("SELECT email, phone FROM sys_employee", SENSITIVE)
    assert {f.label for f in findings} == {"DIRECT_SENSITIVE"}
    assert {f.column for f in findings} == {"email", "phone"}
    assert len(findings) == 2


def test_broken_sql_is_not_our_concern() -> None:
    """Синтаксис ловит marina-03; здесь сломанный SQL → пустой список."""
    assert check_pii_masking("SELECT FROM WHERE", SENSITIVE) == []


def test_empty_sensitive_fields_returns_empty() -> None:
    assert check_pii_masking("SELECT email FROM sys_employee", {}) == []


def test_finding_shape() -> None:
    findings = check_pii_masking("SELECT email FROM sys_employee", SENSITIVE)
    assert isinstance(findings[0], Finding)
    assert findings[0].severity == 6
    assert findings[0].column == "email"


def test_downgrade_severity_is_high() -> None:
    findings = check_pii_masking(
        "SELECT email FROM sys_employee",
        SENSITIVE,
        oracle_sql="SELECT md5(email) AS e FROM sys_employee",
    )
    assert findings[0].label == "MASKING_DOWNGRADED"
    assert findings[0].severity == 8
