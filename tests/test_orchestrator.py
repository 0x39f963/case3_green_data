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
