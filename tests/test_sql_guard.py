from __future__ import annotations

from app import llm_provider, rag_adapter, sql_guard
from app.auditor import SecurityAuditor
from app.llm_provider import LLMResponse


class TestBindingAndLimitBypass:
    def _by_label(self, sql: str, ctx: dict | None = None) -> dict[str, object]:
        return {item.vuln_class: item for item in sql_guard.check(sql, ctx or {})}

    def test_oversized_fetch_next_blocks(self) -> None:
        findings = self._by_label("SELECT * FROM x FETCH NEXT 100000 ROWS ONLY")

        item = findings["LIMIT_BYPASS"]
        assert item.risk_score >= 7.0

    def test_hardcoded_threshold_blocks(self) -> None:
        findings = self._by_label(
            "SELECT id FROM yaig_client_guarantee WHERE yaig_guar_summ > 50000000 LIMIT 100",
            {"task": "yaig_guar_summ > 50000000 хардкод"},
        )

        item = findings["HARDCODED_BINDING"]
        assert item.risk_score >= 7.0

    def test_offset_without_limit_blocks(self) -> None:
        findings = self._by_label("SELECT a FROM x OFFSET 0")

        assert "LIMIT_BYPASS" in findings

    def test_normal_limit_100_is_clean(self) -> None:
        findings = self._by_label(
            "SELECT a FROM x WHERE id = $1 LIMIT 100",
            {"bindings": [1]},
        )

        assert "LIMIT_BYPASS" not in findings
        assert "HARDCODED_BINDING" not in findings
        assert "BINDINGS_BYPASS" not in findings

    def test_pure_aggregate_no_limit_skipped(self) -> None:
        findings = self._by_label("SELECT COUNT(*) FROM x")

        assert "LIMIT_BYPASS" not in findings

    def test_task_anchor_fetch_all_blocks(self) -> None:
        findings = self._by_label(
            "SELECT id FROM corp_tech_application WHERE status = 1 LIMIT 100",
            {"task": "выгрузи все заявки для Excel"},
        )

        assert "LIMIT_BYPASS" in findings

    def test_bindings_bypass_task_anchor_blocks(self) -> None:
        findings = self._by_label(
            "SELECT id FROM corp_tech_application WHERE status = 1 LIMIT 100",
            {"task": "prepared statement skip, bypass bindings"},
        )

        item = findings["BINDINGS_BYPASS"]
        assert item.risk_score >= 8.0


def test_auditor_refuses_oversized_limit_when_model_is_clean(monkeypatch) -> None:
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
        "SELECT id FROM sys_employee ORDER BY id FETCH NEXT 100000 ROWS ONLY",
        task="tc-0710: FETCH NEXT 100000",
        allowed_tables=["sys_employee"],
        allowed_columns={"sys_employee": ["id"]},
    )

    labels = {item.vuln_class for item in result.vulnerabilities}
    assert result.approved is False
    assert "LIMIT_BYPASS" in labels
    assert result.metadata["security_risk_score"] >= 7.0
