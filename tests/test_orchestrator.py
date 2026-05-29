from __future__ import annotations

from app import orchestrator
from app.trace import Trace


def _trace(tmp_path, monkeypatch, request_id: str) -> Trace:
    monkeypatch.setenv("TRACES_DIR", str(tmp_path))
    return Trace("test task", request_id=request_id)


def _task_anchor_finding():
    return orchestrator.sql_guard.make_vulnerability(
        "LIMIT_BYPASS",
        "SQL or task bypasses pagination or configured row cap.",
        "Keep a safe row cap.",
        "full history",
        detector="rule.reliability.limit_bypass.task_anchor",
        confidence=0.95,
    )


def test_decide_approve_happy_path(tmp_path, monkeypatch) -> None:
    trace = _trace(tmp_path, monkeypatch, "decide_approve")
    audit = orchestrator.AuditResult(
        approved=True,
        vulnerabilities=[],
        overall_risk_score=0.0,
        summary="clean",
    )

    state = orchestrator._node_decide(
        {
            "trace": trace,
            "last_audit": audit,
            "approved": True,
            "prompt_risk_findings": [],
            "iteration": 1,
            "max_iterations": 5,
        }
    )

    assert state["decision"] == "approve"
    assert state["approved"] is True
    assert state["policy_label"] == orchestrator._POLICY_APPROVE
    assert trace.events[-1]["outputs"]["decision"] == "approve"


def test_task_anchor_security_refuses_at_iter1(tmp_path, monkeypatch) -> None:
    trace = _trace(tmp_path, monkeypatch, "decide_task_anchor")
    finding = _task_anchor_finding()
    audit = orchestrator.AuditResult(
        approved=False,
        vulnerabilities=[finding],
        overall_risk_score=7.0,
        summary="blocked",
    )

    state = orchestrator._node_decide(
        {
            "trace": trace,
            "last_audit": audit,
            "approved": False,
            "prompt_risk_findings": [],
            "iteration": 1,
            "max_iterations": 5,
        }
    )

    assert state["decision"] == "refuse"
    assert state["approved"] is False
    assert state["needs_human"] is False
    assert state["policy_label"] == orchestrator._POLICY_REFUSAL_REQUIRED
    assert state["human_reason"] == "security attack embedded in task; revise cannot fix"
    event = trace.events[-1]
    assert event["inputs"]["task_anchored_security"] is True
    # Trace keeps exact task-anchor evidence for audit and benchmark analysis.
    assert event["details"]["task_anchored_security_findings"] == [
        {
            "vuln_class": "LIMIT_BYPASS",
            "risk_score": 7.0,
            "detector": "rule.reliability.limit_bypass.task_anchor",
            "evidence_span": "full history",
        }
    ]


def test_task_anchor_priority_over_low_judge(tmp_path, monkeypatch) -> None:
    trace = _trace(tmp_path, monkeypatch, "decide_task_anchor_low_judge")
    task_anchor = _task_anchor_finding()
    low_judge = orchestrator.sql_guard.make_vulnerability(
        "DIRECT_SENSITIVE",
        "judge unsure",
        "Review sensitive exposure.",
        "x",
        detector="stage4.judge",
        severity=3.0,
        confidence=0.5,
        layer="judge",
    )
    audit = orchestrator.AuditResult(
        approved=False,
        vulnerabilities=[task_anchor, low_judge],
        overall_risk_score=7.0,
        summary="blocked",
    )

    state = orchestrator._node_decide(
        {
            "trace": trace,
            "last_audit": audit,
            "approved": False,
            "prompt_risk_findings": [],
            "iteration": 1,
            "max_iterations": 5,
        }
    )

    assert state["decision"] == "refuse"
    assert state["policy_label"] == orchestrator._POLICY_REFUSAL_REQUIRED
    assert state["needs_human"] is False
    assert trace.events[-1]["inputs"]["low_judge"] is True
    assert trace.events[-1]["inputs"]["task_anchored_security"] is True


def test_task_anchor_priority_over_max_iter(tmp_path, monkeypatch) -> None:
    trace = _trace(tmp_path, monkeypatch, "decide_task_anchor_max_iter")
    audit = orchestrator.AuditResult(
        approved=False,
        vulnerabilities=[_task_anchor_finding()],
        overall_risk_score=7.0,
        summary="blocked",
    )

    state = orchestrator._node_decide(
        {
            "trace": trace,
            "last_audit": audit,
            "approved": False,
            "prompt_risk_findings": [],
            "iteration": 5,
            "max_iterations": 5,
        }
    )

    assert state["decision"] == "refuse"
    assert state["policy_label"] == orchestrator._POLICY_REFUSAL_REQUIRED
    assert state["needs_human"] is False
    assert trace.events[-1]["inputs"]["task_anchored_security"] is True


def test_no_task_anchor_no_premature_refuse(tmp_path, monkeypatch) -> None:
    trace = _trace(tmp_path, monkeypatch, "decide_quality_only")
    finding = orchestrator.sql_guard.make_vulnerability(
        "NO_PAGINATION",
        "No pagination.",
        "Add a limit.",
        "SELECT id FROM t",
        detector="rule.reliability.no_pagination",
        confidence=0.95,
    )
    audit = orchestrator.AuditResult(
        approved=False,
        vulnerabilities=[finding],
        overall_risk_score=3.0,
        summary="quality only",
    )

    state = orchestrator._node_decide(
        {
            "trace": trace,
            "last_audit": audit,
            "approved": False,
            "prompt_risk_findings": [],
            "iteration": 1,
            "max_iterations": 5,
        }
    )

    assert state["decision"] == "approve"
    assert state["policy_label"] == orchestrator._POLICY_APPROVE_WITH_QUALITY
    assert state["needs_human"] is False
    assert trace.events[-1]["inputs"]["task_anchored_security"] is False


def test_node_revise_writes_structured_feedback(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(orchestrator.audit_storage, "save_iteration", lambda *args, **kwargs: None)
    trace = _trace(tmp_path, monkeypatch, "revise_structured")
    finding = orchestrator.sql_guard.make_vulnerability(
        "HALLUCINATED_COLUMN",
        "missing column lim_sum",
        "Use only real columns.",
        "lim_sum",
        detector="rule.generation.hallucinated_column",
    )
    audit = orchestrator.AuditResult(
        approved=False,
        vulnerabilities=[finding],
        overall_risk_score=6.0,
        summary="blocked",
    )
    log = orchestrator.IterationLog(
        timestamp=orchestrator.datetime.now(orchestrator.timezone.utc),
        iteration=1,
        sql_query="SELECT lim_sum FROM scp_application",
        audit_result=audit,
        revision_notes="",
    )

    state = orchestrator._node_revise(
        {
            "trace": trace,
            "iteration": 1,
            "task": "Покажи заявки",
            "last_sql": "SELECT lim_sum FROM scp_application",
            "last_audit": audit,
            "last_explain_error": "column lim_sum does not exist",
            "intent_kind": "row_level_business",
            "banned_identifiers": ["lim_sum"],
            "iterations_log": [log],
            "failure_signatures": [],
        }
    )

    feedback = state["last_revision_feedback"]
    assert set(feedback) == {
        "failed_labels",
        "evidence_span",
        "forbidden_identifiers",
        "required_repair",
        "original_intent",
        "explain_error",
        "previous_sql_sha256",
    }
    assert feedback["failed_labels"] == ["HALLUCINATED_COLUMN"]
    assert feedback["forbidden_identifiers"] == ["lim_sum"]
    assert feedback["original_intent"] == "row_level"
    assert feedback["explain_error"] == "column lim_sum does not exist"
    assert trace.events[-1]["outputs"]["structured_feedback"] == feedback
    assert state["failure_signatures"]


def test_repeat_stop_reason_detects_repeated_failure_signature(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(orchestrator.audit_storage, "save_iteration", lambda *args, **kwargs: None)
    trace = _trace(tmp_path, monkeypatch, "repeat_signature")
    finding = orchestrator.sql_guard.make_vulnerability(
        "BROKEN_SQL",
        "EXPLAIN failed",
        "Fix SQL.",
        "operator does not exist: smallint = boolean",
        detector="explain_sandbox.error",
    )
    audit = orchestrator.AuditResult(
        approved=False,
        vulnerabilities=[finding],
        overall_risk_score=6.0,
        summary="broken",
    )
    first = orchestrator._node_revise(
        {
            "trace": trace,
            "iteration": 1,
            "task": "Покажи заявки",
            "last_sql": "SELECT id FROM scp_application WHERE status = true",
            "last_audit": audit,
            "last_explain_error": "operator does not exist: smallint = boolean",
            "intent_kind": "row_level_business",
            "banned_identifiers": [],
            "iterations_log": [],
            "failure_signatures": [],
        }
    )
    second_state = {
        **first,
        "sql_history": [
            "SELECT id FROM scp_application WHERE status = true",
            "SELECT id FROM scp_application WHERE status = 'active'",
        ],
        "last_explain_error": "operator does not exist: smallint = boolean",
    }

    reason = orchestrator._repeat_stop_reason(second_state, audit)

    assert "failure signature" in reason
    assert "BROKEN_SQL" in reason


def test_decide_writes_split_abstain_reason_for_max_iter(tmp_path, monkeypatch) -> None:
    trace = _trace(tmp_path, monkeypatch, "decide_max_iter_reason")
    finding = orchestrator.sql_guard.make_vulnerability(
        "BROKEN_SQL",
        "broken",
        "Fix SQL.",
        "SELECT FROM",
        detector="rule.generation.broken_sql",
    )
    audit = orchestrator.AuditResult(
        approved=False,
        vulnerabilities=[finding],
        overall_risk_score=6.0,
        summary="broken",
    )

    state = orchestrator._node_decide(
        {
            "trace": trace,
            "last_audit": audit,
            "approved": False,
            "prompt_risk_findings": [],
            "iteration": 5,
            "max_iterations": 5,
            "failure_signatures": [],
        }
    )

    assert state["decision"] == "abstain"
    assert state["abstain_reason"] == "max_iter"
    assert trace.events[-1]["outputs"]["abstain_reason"] == "max_iter"
