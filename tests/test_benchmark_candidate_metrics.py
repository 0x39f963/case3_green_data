from __future__ import annotations

import json

from benchmark_service.ingest import normalize_payload
from benchmark_service.models import RunPayload


def test_normalize_payload_extracts_generator_candidate_metrics() -> None:
    data = _payload()
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")

    normalized = normalize_payload(RunPayload(**data), raw)

    assert normalized.pipeline_run["abstain_reason"] == "max_iter"
    rows = normalized.generator_candidate_metrics
    assert len(rows) == 2
    assert rows[0]["candidate_index"] == 0
    assert rows[0]["temperature"] == 0.5
    assert rows[0]["selected_by_selector"] is False
    assert rows[1]["candidate_index"] == 1
    assert rows[1]["temperature"] == 0.8
    assert rows[1]["temperature_applied"] is True
    assert rows[1]["selected_by_selector"] is True
    assert rows[1]["selector_labels"] == []
    assert rows[1]["selected_iteration_audit_approved"] is True
    assert rows[1]["selected_iteration_risk_score"] == 1.5
    assert rows[1]["run_decision"] == "approve"
    assert rows[1]["run_approved"] is True
    assert rows[1]["prompt_id"] == "generator_system"
    assert rows[1]["prompt_version"] == 3
    assert rows[1]["prompt_sha256"] == "prompt-sha"


def _payload() -> dict:
    trace_id = "trace_candidates"
    return {
        "trace_id": trace_id,
        "benchmark_run_id": "bench_temp",
        "dataset_id": "dataset_temp",
        "dataset_version": "v1",
        "case_id": "case_temp",
        "model_key": "or-qwen",
        "llm_mode": "prod_demo",
        "system_result": {
            "approved": True,
            "final_sql": "SELECT id FROM sys_employee ORDER BY id LIMIT 100",
            "iterations_used": 1,
            "metadata": {
                "trace_id": trace_id,
                "decision": "approve",
                "needs_human": False,
                "abstain_reason": "max_iter",
                "generator_backend": "openrouter",
                "generator_model": "qwen/qwen3.5-9b",
                "generator_provider": "openrouter",
            },
        },
        "trace": {
            "request_id": trace_id,
            "events": [
                {
                    "node": "generate",
                    "inputs": {"iteration": 1},
                    "outputs": {
                        "selected_index": 1,
                        "candidate_count": 2,
                        "sql": "SELECT id FROM sys_employee ORDER BY id LIMIT 100",
                    },
                    "details": {
                        "backend": "openrouter",
                        "model": "qwen/qwen3.5-9b",
                        "prompt_id": "generator_system",
                        "prompt_version": 3,
                        "prompt_sha256": "prompt-sha",
                        "temperature_schedule": [0.5, 0.8],
                        "generate_candidates": [
                            "SELECT * FROM sys_employee",
                            "SELECT id FROM sys_employee ORDER BY id LIMIT 100",
                        ],
                        "candidates": [
                            {
                                "candidate_index": 0,
                                "sql": "SELECT * FROM sys_employee",
                                "temperature": 0.5,
                                "temperature_applied": True,
                                "selected_by_selector": False,
                                "selector_score": {
                                    "broken": False,
                                    "critical_count": 0,
                                    "finding_count": 1,
                                    "labels": ["SELECT_STAR"],
                                },
                            },
                            {
                                "candidate_index": 1,
                                "sql": "SELECT id FROM sys_employee ORDER BY id LIMIT 100",
                                "temperature": 0.8,
                                "temperature_applied": True,
                                "selected_by_selector": True,
                                "selector_score": {
                                    "broken": False,
                                    "critical_count": 0,
                                    "finding_count": 0,
                                    "labels": [],
                                },
                            },
                        ],
                    },
                },
                {
                    "node": "audit",
                    "inputs": {"iteration": 1},
                    "outputs": {
                        "approved": True,
                        "overall_risk_score": 1.5,
                    },
                    "details": {},
                },
            ],
            "result": {},
        },
    }
