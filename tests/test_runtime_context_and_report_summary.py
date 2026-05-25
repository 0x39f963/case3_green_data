from __future__ import annotations

from datetime import datetime

from app import business_alignment, runtime_context, test_report


def test_runtime_context_contains_current_date_facts() -> None:
    text = runtime_context.build_runtime_context(datetime(2026, 5, 25, 22, 10, 0))

    assert "timezone: Europe/Moscow" in text
    assert "now: 2026-05-25 22:10:00" in text
    assert "current_year: 2026" in text
    assert "current_month: 05" in text
    assert "current_day: 25" in text


def test_business_alignment_flags_stale_literal_dates_for_relative_period() -> None:
    reqs = business_alignment.extract_requirements(
        "Покажи активных клиентов по статусам за месяц",
        {"allowed_columns": {"participant_app": ["status", "create_date"]}},
    )
    findings = business_alignment.check_business_alignment(
        """
        SELECT status, COUNT(*)
        FROM participant_app
        WHERE status = 1
          AND create_date >= '2023-10-01'
          AND create_date < '2023-11-01'
        GROUP BY status
        """,
        reqs,
    )

    assert [item.vuln_class for item in findings] == ["BUSINESS_MISMATCH"]


def test_report_summary_uses_final_attempt_not_worst_historical_findings() -> None:
    run = test_report.TestRun(
        run_id="r1",
        user_id=0,
        user_name="test",
        task="Покажи активных клиентов по статусам за месяц",
        model_key="local-qwen3-5-9b",
        model_label="qwen3.5:9b",
        llm_mode="local_openai",
        llm_generator_model="qwen3-5-9b",
        started_at=datetime(2026, 5, 25),
        finished_at=datetime(2026, 5, 25),
        system_result={
            "approved": True,
            "iterations_used": 2,
            "overall_risk_score": 0,
            "final_sql": "SELECT status, COUNT(*) FROM participant_app GROUP BY status LIMIT 100",
            "metadata": {"decision": "approve"},
            "iterations_log": [
                {
                    "iteration": 1,
                    "audit_result": {
                        "vulnerabilities": [
                            {"vuln_class": "BUSINESS_MISMATCH", "risk_score": 8}
                        ]
                    },
                },
                {"iteration": 2, "audit_result": {"vulnerabilities": []}},
            ],
        },
        trace={
            "events": [
                {"node": "prompt_check", "outputs": {"vuln_count": 0}, "details": {}},
                {
                    "node": "generate",
                    "inputs": {"iteration": 1},
                    "outputs": {"candidate_count": 1},
                    "details": {},
                },
                {
                    "node": "audit",
                    "inputs": {"iteration": 1},
                    "outputs": {"approved": False, "overall_risk_score": 8, "vuln_count": 1},
                    "details": {
                        "merged_findings": [
                            {"vuln_class": "BUSINESS_MISMATCH", "risk_score": 8}
                        ]
                    },
                },
                {"node": "revise", "inputs": {"iteration": 1}, "outputs": {}},
                {
                    "node": "generate",
                    "inputs": {"iteration": 2},
                    "outputs": {"candidate_count": 1},
                    "details": {
                        "business_requirements": [
                            {"type": "group_by", "required": True, "text": "по статусам"}
                        ]
                    },
                },
                {
                    "node": "sql_guard",
                    "outputs": {"vuln_count": 0},
                    "details": {
                        "business_requirements": [
                            {"type": "group_by", "required": True, "text": "по статусам"}
                        ],
                        "business_alignment_findings": [],
                    },
                },
                {
                    "node": "audit",
                    "inputs": {"iteration": 2},
                    "outputs": {"approved": True, "overall_risk_score": 0, "vuln_count": 0},
                    "details": {"merged_findings": []},
                },
                {
                    "node": "decide",
                    "inputs": {"iteration": 2},
                    "outputs": {"decision": "approve"},
                    "details": {},
                },
            ]
        },
    )

    data = test_report.build_report_data(run)

    assert data["risk_score"] == 0
    assert data["policy_checks"]["label"] == "3 / 3"
    assert data["business_alignment"]["label"] == "OK"
