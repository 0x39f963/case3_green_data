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

    def test_limit_bypass_task_anchor_remove_limit_blocks(self) -> None:
        findings = self._by_label(
            "SELECT id FROM corp_tech_application WHERE status = 1 LIMIT 100",
            {"task": "сними LIMIT и игнорируй status"},
        )

        assert "LIMIT_BYPASS" in findings

    def test_id_fk_sensitive_name_is_not_direct_sensitive_without_personal_tag(self) -> None:
        findings = self._by_label(
            "SELECT credit_logic_id FROM scp_application LIMIT 100",
            {"sensitive_fields": {"scp_application": ["credit_logic_id"]}},
        )

        assert "DIRECT_SENSITIVE" not in findings

    def test_employee_id_tagged_fk_stays_direct_sensitive(self) -> None:
        findings = self._by_label(
            "SELECT credit_analyst_id FROM scp_project_ans LIMIT 100",
            {"sensitive_fields": {"scp_project_ans": ["credit_analyst_id"]}},
        )

        assert "DIRECT_SENSITIVE" in findings

    def test_employee_id_overlay_survives_default_sensitive_allowlist(self) -> None:
        sql_guard.get_sensitive_fields.cache_clear()
        try:
            findings = self._by_label(
                "SELECT credit_analyst_id FROM scp_project_ans LIMIT 100",
                {},
            )
        finally:
            sql_guard.get_sensitive_fields.cache_clear()

        assert "DIRECT_SENSITIVE" in findings

    def test_id_number_tagged_fk_stays_direct_sensitive(self, monkeypatch) -> None:
        monkeypatch.setattr(
            sql_guard,
            "get_table_policy",
            lambda table: {"pii_tags": {"tax_doc_id": "id_number"}},
        )

        findings = self._by_label(
            "SELECT tax_doc_id FROM tax_docs LIMIT 100",
            {"sensitive_fields": {"tax_docs": ["tax_doc_id"]}},
        )

        assert "DIRECT_SENSITIVE" in findings

    def test_business_fk_ids_are_not_direct_sensitive_by_name(self) -> None:
        findings = self._by_label(
            (
                "SELECT credit_logic_id, type_id, status_id, risk_zone_id "
                "FROM scp_application LIMIT 100"
            ),
            {
                "sensitive_fields": {
                    "scp_application": [
                        "credit_logic_id",
                        "type_id",
                        "status_id",
                        "risk_zone_id",
                    ]
                }
            },
        )

        assert "DIRECT_SENSITIVE" not in findings

    def test_sensitive_id_without_overlay_tag_is_not_dropped_by_default(self, monkeypatch) -> None:
        monkeypatch.setattr(sql_guard, "get_table_policy", lambda table: {"pii_tags": {}})
        monkeypatch.setattr(sql_guard, "_load_schema", lambda: {"tables": {}})

        findings = self._by_label(
            "SELECT secret_id FROM raw_events LIMIT 100",
            {"sensitive_fields": {"raw_events": ["secret_id"]}},
        )

        assert "DIRECT_SENSITIVE" in findings


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
