from __future__ import annotations

from app import llm_provider, rag_adapter
from app import business_alignment, sql_guard
from app.auditor import SecurityAuditor
from app.llm_provider import LLMResponse


TASK = "Покажи активных клиентов по статусам за месяц"
CTX = {
    "task": TASK,
    "allowed_tables": ["sys_company"],
    "allowed_columns": {"sys_company": ["id", "status", "create_date"]},
}


def _labels(sql: str, ctx: dict | None = None) -> set[str]:
    return {item.vuln_class for item in sql_guard.check(sql, ctx or CTX)}


def test_extracts_month_active_status_and_group_requirements() -> None:
    requirements = business_alignment.extract_requirements(TASK, CTX)

    assert [item.type for item in requirements] == ["time_range", "status_filter", "group_by"]
    assert requirements[0].period == "month"
    assert "create_date" in requirements[0].acceptable_columns
    assert "status" in requirements[1].acceptable_columns


def test_missing_month_filter_is_business_blocker() -> None:
    sql = """
    SELECT sc.status, COUNT(*) AS client_count
    FROM sys_company sc
    WHERE sc.status = 1
    GROUP BY sc.status
    """

    assert "MISSING_REQUIRED_FILTER" in _labels(sql)


def test_month_filter_satisfies_business_alignment() -> None:
    sql = """
    SELECT sc.status, COUNT(*) AS client_count
    FROM sys_company sc
    WHERE sc.status = 1
      AND sc.create_date >= DATE_TRUNC('month', CURRENT_DATE)
    GROUP BY sc.status
    """

    labels = _labels(sql)
    assert "MISSING_REQUIRED_FILTER" not in labels
    assert "BUSINESS_MISMATCH" not in labels


def test_count_aggregate_with_required_time_filter_needs_no_limit() -> None:
    sql = """
    SELECT COUNT(*) AS client_count
    FROM sys_company sc
    WHERE sc.status = 1
      AND sc.create_date >= DATE_TRUNC('month', CURRENT_DATE)
    """
    ctx = {
        "task": "Посчитай активных клиентов за месяц",
        "allowed_tables": ["sys_company"],
        "allowed_columns": {"sys_company": ["id", "status", "create_date"]},
    }

    labels = _labels(sql, ctx)
    assert "NO_PAGINATION" not in labels
    assert "MISSING_REQUIRED_FILTER" not in labels


def test_count_aggregate_without_required_time_filter_is_rejected() -> None:
    sql = """
    SELECT COUNT(*) AS client_count
    FROM sys_company sc
    WHERE sc.status = 1
    """
    ctx = {
        "task": "Посчитай активных клиентов за месяц",
        "allowed_tables": ["sys_company"],
        "allowed_columns": {"sys_company": ["id", "status", "create_date"]},
    }

    labels = _labels(sql, ctx)
    assert "NO_PAGINATION" not in labels
    assert "MISSING_REQUIRED_FILTER" in labels


def test_unmapped_required_time_filter_needs_human_instead_of_silent_approve() -> None:
    ctx = {
        "task": "Покажи клиентов за месяц",
        "allowed_tables": ["sys_company"],
        "allowed_columns": {"sys_company": ["id", "name"]},
    }

    findings = sql_guard.check("SELECT id, name FROM sys_company LIMIT 100", ctx)
    item = next(v for v in findings if v.vuln_class == "BUSINESS_MISMATCH")
    assert getattr(item, "needs_human", False) is True
    assert getattr(item, "confidence", 1.0) < 0.7


def test_auditor_rejects_missing_required_filter_even_when_model_is_clean(monkeypatch) -> None:
    class CleanClient:
        backend = "fake"
        model = "fake-auditor"

        def invoke(self, system: str, user: str, response_format: dict | None = None) -> LLMResponse:
            del system, user, response_format
            return LLMResponse(
                text='{"vulnerabilities":[],"overall_risk_score":0,"summary":"clean"}',
                model=self.model,
                backend=self.backend,
                raw={"id": "fake-audit"},
                walltime_sec=0.01,
            )

    monkeypatch.setenv("PROMPT_REGISTRY_DISABLE_DB", "true")
    monkeypatch.setenv("AUDITOR_GROUPED_ENABLED", "false")
    monkeypatch.setenv("STAGE_2_ENABLED", "false")
    monkeypatch.setenv("STAGE_3_ENABLED", "false")
    monkeypatch.setenv("STAGE_4_ENABLED", "false")
    monkeypatch.setattr(llm_provider, "get_llm", lambda role: CleanClient())
    monkeypatch.setattr(rag_adapter, "get_sensitive_fields", lambda: {})
    monkeypatch.setattr(rag_adapter, "get_security_context_timed", lambda sql: ("", {"enabled": False}))
    monkeypatch.setattr(rag_adapter, "get_security_hits_timed", lambda sql: ([], {"enabled": False}))

    result = SecurityAuditor().audit(
        """
        SELECT sc.status, COUNT(*) AS client_count
        FROM sys_company sc
        WHERE sc.status = 1
        GROUP BY sc.status
        """,
        task=TASK,
        allowed_tables=["sys_company"],
        allowed_columns={"sys_company": ["id", "status", "create_date"]},
    )

    assert result.approved is False
    assert any(item.vuln_class == "MISSING_REQUIRED_FILTER" for item in result.vulnerabilities)
