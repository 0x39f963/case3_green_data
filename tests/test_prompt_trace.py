from __future__ import annotations

from app import prompt_trace


def test_prompt_trace_builds_versioned_prompt_parts_and_sources() -> None:
    trace = {
        "request_id": "trace_prompt_001",
        "task": "Покажи сотрудников",
        "events": [
            {
                "node": "retrieve",
                "started_at": "2026-05-21T10:00:00Z",
                "duration_sec": 0.2,
                "details": {
                    "generation_context": "sys_employee(id, name)",
                    "rag_sources": {
                        "table_knowledge_v2": {
                            "enabled": True,
                            "hit_count": 1,
                            "context_chars": 21,
                            "dsn_host": "postgres",
                        }
                    },
                    "rag_generation_hits": [
                        {"table_name": "sys_employee", "score": 0.91, "text": "employee table"}
                    ],
                },
            },
            {
                "node": "generate",
                "started_at": "2026-05-21T10:00:01Z",
                "duration_sec": 1.1,
                "inputs": {"iteration": 1},
                "details": {
                    "prompt_meta": {
                        "prompt_id": "generator_system_v3",
                        "prompt_type": "generator_system",
                        "prompt_version": 3,
                        "prompt_sha256": "abc123",
                        "prompt_source": "db",
                    },
                    "prompt_system": "Use safe PostgreSQL.",
                    "prompt_user": (
                        "Задача аналитика:\n"
                        "Покажи сотрудников\n\n"
                        "Контекст из памяти (релевантные таблицы и шаблоны PostgreSQL):\n"
                        "sys_employee(id, name)"
                    ),
                },
            },
        ],
    }

    result = prompt_trace.build_prompt_trace(trace)

    assert result["summary"]["label"] == "generator_system v3"
    item = result["items"][0]
    assert item["prompt_id"] == "generator_system_v3"
    assert item["prompt_version"] == 3
    assert item["sources"]["retrieve_event_index"] == "event-0"
    assert item["sources"]["rag_generation_hits"][0]["table_name"] == "sys_employee"
    assert [part["kind"] for part in item["parts"]] == ["system", "task", "rag_context"]


def test_prompt_trace_marks_legacy_trace_prompt_without_version() -> None:
    trace = {
        "request_id": "trace_legacy_001",
        "events": [
            {
                "node": "audit",
                "details": {
                    "prompt_system": "Audit SQL.",
                    "prompt_user": "SQL для проверки:\nSELECT 1",
                },
            }
        ],
    }

    result = prompt_trace.build_prompt_trace(trace)

    item = result["items"][0]
    assert item["prompt_type"] == "auditor_system"
    assert item["prompt_version"] is None
    assert item["prompt_source"] == "trace_legacy"
    assert result["summary"]["label"] == "auditor_system legacy"
