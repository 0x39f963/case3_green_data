from __future__ import annotations

from app import sql_guard


def _by_label(sql: str, ctx: dict | None = None) -> dict[str, object]:
    return {item.vuln_class: item for item in sql_guard.check(sql, ctx or {})}


def test_drop_is_early_ast_barrier() -> None:
    findings = _by_label("DROP TABLE sys_employee;")
    item = findings["DDL_FORBIDDEN"]
    assert sql_guard.is_early_barrier_finding(item)
    assert str(getattr(item, "detector", "")).startswith("rule.ast_forbidden.")


def test_insert_is_early_ast_barrier() -> None:
    findings = _by_label("INSERT INTO sys_employee(id) VALUES (1);")
    assert sql_guard.is_early_barrier_finding(findings["INSERT_UNSAFE"])


def test_pii_masking_ast_direct_sensitive() -> None:
    findings = _by_label(
        "SELECT email FROM sys_employee LIMIT 100;",
        {"sensitive_fields": {"sys_employee": ["email"]}},
    )
    item = findings["DIRECT_SENSITIVE"]
    assert "raw PII" in item.description
    assert getattr(item, "detector", "") == "rule.ast_pii_masking.direct_sensitive"


def test_pii_masking_oracle_mismatch() -> None:
    findings = _by_label(
        "SELECT left(email, 3) AS email_mask FROM sys_employee LIMIT 100;",
        {
            "sensitive_fields": {"sys_employee": ["email"]},
            "oracle_sql": "SELECT md5(email) AS email_hash FROM sys_employee LIMIT 100;",
        },
    )
    assert "MASKING_TYPE_MISMATCH" in findings


def test_safe_masked_email_is_not_direct_sensitive() -> None:
    findings = _by_label(
        "SELECT md5(email) AS email_hash FROM sys_employee LIMIT 100;",
        {"sensitive_fields": {"sys_employee": ["email"]}},
    )
    assert "DIRECT_SENSITIVE" not in findings
