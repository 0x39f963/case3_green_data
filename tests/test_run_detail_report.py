"""Tests for /runs/{trace_id} on-the-fly canonical report rendering."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import app


FIXTURE = Path(__file__).parent / "fixtures" / "sample_trace.json"


@pytest.fixture
def configured_dirs(tmp_path, monkeypatch):
    """Point trace and report dirs at temp paths, copy in the sample trace."""
    reports = tmp_path / "reports"
    traces = tmp_path / "traces"
    reports.mkdir()
    traces.mkdir()
    target = traces / "sample_trace_123.json"
    shutil.copyfile(FIXTURE, target)
    monkeypatch.setenv("BOT_REPORTS_DIR", str(reports))
    monkeypatch.setenv("TRACES_DIR", str(traces))
    return {"reports": reports, "traces": traces, "trace_id": "sample_trace_123"}


def test_runs_endpoint_renders_canonical_report_from_trace(configured_dirs):
    client = TestClient(app)

    response = client.get(f"/runs/{configured_dirs['trace_id']}")

    assert response.status_code == 200
    body = response.text
    # canonical template markers (test_report.html)
    assert "AI SQL Security Pipeline Report" in body
    assert "Pipeline Timeline" in body
    assert "RAG Retrieval" in body
    assert "Original User Request" in body
    # placeholder text MUST NOT appear (we rendered the real report)
    assert "HTML report is not generated yet" not in body
    assert "Trace JSON and HTML report were not found" not in body


def test_runs_endpoint_falls_back_to_placeholder_when_trace_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("TRACES_DIR", str(tmp_path / "traces"))
    (tmp_path / "reports").mkdir()
    (tmp_path / "traces").mkdir()
    client = TestClient(app)

    response = client.get("/runs/totally_missing_123")
    assert response.status_code == 200
    assert "Trace JSON and HTML report were not found" in response.text


def test_runs_endpoint_keeps_invalid_trace_id_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("TRACES_DIR", str(tmp_path / "traces"))
    (tmp_path / "reports").mkdir()
    (tmp_path / "traces").mkdir()
    client = TestClient(app)

    response = client.get("/runs/bad.id.with.dots")
    assert response.status_code == 200
    assert "Trace id is missing or invalid" in response.text


def test_runs_endpoint_prefers_existing_pregenerated_html(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("TRACES_DIR", str(tmp_path / "traces"))
    (tmp_path / "reports").mkdir()
    (tmp_path / "traces").mkdir()
    pre_html = "<html><body>PREGEN report</body></html>"
    (tmp_path / "reports" / "fasttrace1.html").write_text(pre_html, encoding="utf-8")
    client = TestClient(app)

    response = client.get("/runs/fasttrace1")
    assert response.status_code == 200
    assert "PREGEN report" in response.text


def test_runs_endpoint_prefers_trace_over_stale_pregenerated_html(configured_dirs):
    stale_html = "<html><body>STALE report</body></html>"
    (configured_dirs["reports"] / (configured_dirs["trace_id"] + ".html")).write_text(
        stale_html,
        encoding="utf-8",
    )
    client = TestClient(app)

    response = client.get(f"/runs/{configured_dirs['trace_id']}")

    assert response.status_code == 200
    assert "AI SQL Security Pipeline Report" in response.text
    assert "STALE report" not in response.text


def test_render_trace_as_report_returns_none_for_broken_json(tmp_path):
    from app import web_chat

    broken = tmp_path / "broken.json"
    broken.write_text("not json", encoding="utf-8")
    assert web_chat._render_trace_as_report("broken", broken) is None


def test_render_trace_as_report_returns_none_for_non_dict_payload(tmp_path):
    from app import web_chat

    arr = tmp_path / "arr.json"
    arr.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert web_chat._render_trace_as_report("arr", arr) is None


def test_report_data_includes_rag_source_diagnostics() -> None:
    from app import test_report

    trace = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for event in trace["events"]:
        if event.get("node") == "retrieve":
            event.setdefault("details", {})["rag_sources"] = {
                "table_knowledge_v2": {
                    "enabled": True,
                    "dsn_configured": True,
                    "dsn_host": "host.docker.internal",
                    "index_name": "table_knowledge_v2",
                    "hit_count": 6,
                    "context_chars": 1491,
                    "error": None,
                    "fallback_used": False,
                    "top_hits": [{"table_name": "corp_tech_application", "score": 0.91}],
                },
                "legacy_faiss": {
                    "used": True,
                    "hit_count": 5,
                    "role": "pg_patterns_docs_or_fallback",
                },
            }
            break
    now = datetime.now(timezone.utc)
    run = test_report.TestRun(
        run_id="rag_sources_test",
        user_id=0,
        user_name="test",
        task=trace["task"],
        model_key="test",
        model_label="test",
        llm_mode="test",
        llm_generator_model="test",
        started_at=now,
        finished_at=now,
        system_result=trace.get("result") or {},
        trace=trace,
    )

    data = test_report.build_report_data(run)

    assert any(step["label"] == "RAG Retrieval" for step in data["timeline_steps"])
    assert any((block.get("sources") or {}).get("table_knowledge_v2") for block in data["rag_blocks"])


def test_latency_candidate_drawer_shows_temperature_and_winner() -> None:
    from app import test_report

    trace = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for event in trace["events"]:
        if event.get("node") == "generate":
            event["outputs"]["selected_index"] = 1
            event.setdefault("details", {})["scheduling"] = "parallel"
            event["details"]["candidates"] = [
                {
                    "candidate_index": 0,
                    "temperature": 0.5,
                    "selected_by_selector": False,
                    "selector_score": {"labels": ["NO_PAGINATION"]},
                    "walltime_sec": 12.4,
                },
                {
                    "candidate_index": 1,
                    "temperature": 0.8,
                    "selected_by_selector": True,
                    "selector_score": {"labels": []},
                    "walltime_sec": 13.1,
                },
            ]
            break
    now = datetime.now(timezone.utc)
    run = test_report.TestRun(
        run_id="candidate_temp_test",
        user_id=0,
        user_name="test",
        task=trace["task"],
        model_key="test",
        model_label="test",
        llm_mode="test",
        llm_generator_model="test",
        started_at=now,
        finished_at=now,
        system_result=trace.get("result") or {},
        trace=trace,
    )

    data = test_report.build_report_data(run)
    html = data["drawer_items"]["latency-candidates"]["value"]

    assert "candidate 0 · temp 0.5 · not selected · labels: NO_PAGINATION" in html
    assert "candidate 1 · temp 0.8 · selected · labels: none" in html


def test_report_data_exposes_business_alignment_details() -> None:
    from app import test_report

    trace = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for event in trace["events"]:
        if event.get("node") == "generate":
            event["outputs"]["selected_index"] = 1
            event.setdefault("details", {})["business_requirements"] = [
                {
                    "type": "time_range",
                    "required": True,
                    "text": "за месяц",
                    "acceptable_columns": ["create_date"],
                }
            ]
            event["details"]["candidates"] = [
                {
                    "candidate_index": 0,
                    "temperature": 0.3,
                    "selected_by_selector": False,
                    "selector_score": {"labels": ["NO_PAGINATION"]},
                },
                {
                    "candidate_index": 1,
                    "temperature": 0.6,
                    "selected_by_selector": True,
                    "selector_score": {
                        "labels": ["MISSING_REQUIRED_FILTER"],
                        "business_alignment_labels": ["MISSING_REQUIRED_FILTER"],
                        "business_alignment_findings": [
                            {
                                "vuln_class": "MISSING_REQUIRED_FILTER",
                                "risk_score": 6.5,
                                "description": "missing month",
                            }
                        ],
                    },
                    "business_alignment_findings": [
                        {
                            "vuln_class": "MISSING_REQUIRED_FILTER",
                            "risk_score": 6.5,
                            "description": "missing month",
                        }
                    ],
                },
            ]
            break
    for event in trace["events"]:
        if event.get("node") == "audit":
            event.setdefault("details", {})["business_alignment_findings"] = [
                {
                    "vuln_class": "MISSING_REQUIRED_FILTER",
                    "risk_score": 6.5,
                    "description": "missing month",
                }
            ]
            event["details"]["merged_findings"] = event["details"]["business_alignment_findings"]
            break
    now = datetime.now(timezone.utc)
    run = test_report.TestRun(
        run_id="business_alignment_test",
        user_id=0,
        user_name="test",
        task="Покажи активных клиентов по статусам за месяц",
        model_key="test",
        model_label="test",
        llm_mode="test",
        llm_generator_model="test",
        started_at=now,
        finished_at=now,
        system_result=trace.get("result") or {},
        trace=trace,
    )

    data = test_report.build_report_data(run)
    html = test_report.render(run)

    assert data["business_alignment"]["label"] == "Blocked"
    assert data["drawer_items"]["metric-business"]["title"] == "Business alignment"
    assert "MISSING_REQUIRED_FILTER" in html
    assert "Business alignment" in html


def test_report_data_exposes_prompt_timeline_in_step_drawers() -> None:
    from app import test_report

    trace = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for event in trace["events"]:
        if event.get("node") == "generate":
            event.setdefault("details", {})["prompt_meta"] = {
                "prompt_id": "generator_system_v4",
                "prompt_type": "generator_system",
                "prompt_version": 4,
                "prompt_sha256": "abc123",
                "prompt_source": "db",
            }
            event["details"]["prompt_system"] = "system prompt"
            event["details"]["prompt_user"] = "Задача аналитика:\nПокажи сотрудников"
            break
    now = datetime.now(timezone.utc)
    run = test_report.TestRun(
        run_id="prompt_timeline_test",
        user_id=0,
        user_name="test",
        task=trace["task"],
        model_key="test",
        model_label="test",
        llm_mode="test",
        llm_generator_model="test",
        started_at=now,
        finished_at=now,
        system_result=trace.get("result") or {},
        trace=trace,
    )

    data = test_report.build_report_data(run)
    generate_step = next(step for step in data["timeline_steps"] if step["key"] == "generate")

    assert data["prompt_summary"]["label"] == "generator_system v4"
    assert generate_step["prompt_entries"][0]["prompt_id"] == "generator_system_v4"
    assert data["drawer_items"][generate_step["prompt_entries"][0]["event_key"]]["kind"] == "prompt"
