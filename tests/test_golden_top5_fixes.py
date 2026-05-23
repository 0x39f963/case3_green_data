from __future__ import annotations

from datetime import datetime, timezone

from app import audit_log, classifier, generator_selector, sql_guard
from app.classifier import ml
from app.orchestrator import AuditResult, IterationLog, Vulnerability, _repeat_stop_reason


def _labels(sql: str, ctx: dict | None = None) -> set[str]:
    return {item.vuln_class for item in sql_guard.check(sql, ctx or {})}


def test_unbound_placeholder_is_runtime_contract_label() -> None:
    labels = _labels("SELECT id FROM scp_application WHERE initiator_id = $1")

    assert "UNBOUND_PLACEHOLDER" in labels
    assert "SQL_INJ_CLASSIC" not in labels


def test_placeholder_with_bindings_is_allowed_by_runtime_contract() -> None:
    labels = _labels(
        "SELECT id FROM scp_application WHERE initiator_id = $1",
        {"bindings": [7812]},
    )

    assert "UNBOUND_PLACEHOLDER" not in labels


def test_direct_sensitive_checks_selected_columns_not_filters() -> None:
    ctx = {"sensitive_fields": {"sys_employee": ["phone"]}}

    where_labels = _labels("SELECT id FROM sys_employee WHERE phone IS NOT NULL LIMIT 10", ctx)
    select_labels = _labels("SELECT phone FROM sys_employee LIMIT 10", ctx)

    assert "DIRECT_SENSITIVE" not in where_labels
    assert "DIRECT_SENSITIVE" in select_labels


def test_selector_hard_fails_hallucinated_schema() -> None:
    candidates = [
        "SELECT missing_col FROM scp_application LIMIT 10",
        "SELECT id FROM scp_application LIMIT 10",
    ]
    ctx = {
        "allowed_tables": ["scp_application"],
        "allowed_columns": {"scp_application": ["id"]},
    }

    selected = generator_selector.select_best_with_details(candidates, ctx)

    assert selected.selected_index == 1
    assert "HALLUCINATED_COLUMN" in selected.scores[0]["hard_fail_labels"]


def test_order_by_select_alias_is_not_hallucinated_column() -> None:
    sql = """
    SELECT se.org_id, COUNT(*) AS employee_count
    FROM sys_employee se
    GROUP BY se.org_id
    ORDER BY employee_count DESC
    LIMIT 100
    """

    labels = _labels(
        sql,
        {"allowed_tables": ["sys_employee"], "allowed_columns": {"sys_employee": ["org_id", "id"]}},
    )

    assert "HALLUCINATED_COLUMN" not in labels


def test_stage4_clear_removes_semantic_ml_false_positive(monkeypatch) -> None:
    monkeypatch.setenv("STAGE_2_ENABLED", "true")
    monkeypatch.setenv("STAGE_3_ENABLED", "false")
    monkeypatch.setenv("STAGE_4_ENABLED", "true")

    def fake_predict(sql: str, ctx: dict) -> ml.MLOutput:
        return ml.MLOutput(
            probs={"MASKING_REQUIRED": 0.91},
            labels_above_threshold=["MASKING_REQUIRED"],
            calibrated_thresholds={"MASKING_REQUIRED": 0.5},
            model_type="fake",
            model_version="test",
            available=True,
        )

    monkeypatch.setattr(classifier.ml, "predict", fake_predict)
    monkeypatch.setattr(classifier.judge, "judge_semantic", lambda **kwargs: [])
    classifier.judge.LAST_CALL = {"prompt_meta": {}, "judge_backend": "fake", "judge_model": "fake"}

    result = classifier.classify(
        "SELECT org_id, COUNT(*) AS employee_count FROM sys_employee GROUP BY org_id LIMIT 100",
        task="Сформируй безопасный отчет по сотрудникам без персональных полей",
        allowed_tables=["sys_employee"],
        allowed_columns={"sys_employee": ["org_id", "id"]},
    )

    # H6 (next-10 itertion): для aggregate-safe intent MASKING_REQUIRED гасится
    # детерминированной anchor-suppression ещё до Stage 4 — judge не вызывается.
    assert result.approved_by_classifier is True
    assert "MASKING_REQUIRED" not in result.risk_labels
    assert result.stage_outputs["stage_4_llm_judge"]["called"] is False


def test_audit_log_hides_sql_when_not_approved() -> None:
    entry = IterationLog(
        timestamp=datetime.now(timezone.utc),
        iteration=1,
        sql_query="SELECT phone FROM sys_employee LIMIT 10",
        audit_result=AuditResult(
            approved=False,
            vulnerabilities=[
                Vulnerability(
                    vuln_class="DIRECT_SENSITIVE",
                    risk_score=6,
                    description="raw phone",
                    recommendation="mask",
                )
            ],
            overall_risk_score=6,
            summary="blocked",
        ),
    )

    text = audit_log.render(
        task="Покажи телефоны",
        iterations_log=[entry],
        approved=False,
        final_sql="",
        mode_info={"mode": "test", "generator_model": "fake", "auditor_model": "fake"},
        include_sql=False,
    )

    assert "SELECT phone" not in text
    assert "скрыт" in text


def test_repeat_stop_reason_detects_same_sql() -> None:
    audit = AuditResult(approved=False, vulnerabilities=[], overall_risk_score=6, summary="")
    state = {
        "approved": False,
        "sql_history": ["SELECT $1", "SELECT $1"],
        "audit_history": [audit],
    }

    assert _repeat_stop_reason(state, audit) == "остановлен повтор того же SQL без прогресса"
