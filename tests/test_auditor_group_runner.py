from __future__ import annotations

import asyncio

from app import auditor_group_runner


def test_run_grouped_audit_returns_seven_groups() -> None:
    results = asyncio.run(auditor_group_runner.run_grouped_audit("SELECT 1 LIMIT 1", {}))
    assert [item.group_id for item in results] == ["G1", "G2", "G3", "G4", "G5", "G6", "G7"]


def test_g4_g7_are_stubs() -> None:
    results = asyncio.run(auditor_group_runner.run_grouped_audit("SELECT 1 LIMIT 1", {}))
    by_id = {item.group_id: item for item in results}
    for group_id in ("G4", "G5", "G6", "G7"):
        assert by_id[group_id].used_stub is True
        assert by_id[group_id].findings == []


def test_g1_g3_run_active_rules() -> None:
    results = asyncio.run(
        auditor_group_runner.run_grouped_audit(
            "SELECT email FROM sys_employee LIMIT 100",
            {"sensitive_fields": {"sys_employee": ["email"]}},
        )
    )
    by_id = {item.group_id: item for item in results}
    assert by_id["G1"].used_stub is False
    assert by_id["G2"].used_stub is False
    assert by_id["G3"].used_stub is False
    labels = {item.vuln_class for item in auditor_group_runner.flatten_findings(results)}
    assert "DIRECT_SENSITIVE" in labels


def test_grouped_flag_env(monkeypatch) -> None:
    monkeypatch.setenv("AUDITOR_GROUPED_ENABLED", "true")
    assert auditor_group_runner.grouped_auditor_enabled() is True
    monkeypatch.setenv("AUDITOR_GROUPED_ENABLED", "false")
    assert auditor_group_runner.grouped_auditor_enabled() is False
