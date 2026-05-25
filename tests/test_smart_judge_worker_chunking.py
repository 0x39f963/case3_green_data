from __future__ import annotations

from scripts.bench_smart_judge_worker import JudgeWorkerPool


def test_worker_pool_flushes_by_chunk(monkeypatch):
    calls: list[str] = []
    saved: list[str] = []

    def fake_review(trace_id: str, backend: str, model: str) -> dict:
        calls.append(trace_id)
        return {
            "sub_scores": {
                "sql_correctness": 8,
                "security": 9,
                "intent_fidelity": 8,
                "schema_usage": 8,
                "rag_facts_used": 8,
                "decision_rationale": 8,
                "performance": 8,
                "robustness": 8,
                "retry_efficiency": 8,
            },
            "patch_suggestion": {"target_area": "none", "severity": "P3", "title": "ok"},
        }

    monkeypatch.setattr("scripts.bench_smart_judge_worker.review_case_quality", fake_review)
    monkeypatch.setattr("scripts.bench_smart_judge_worker.db.insert_case_quality_score", lambda trace_id, run_id, **_: saved.append(trace_id))
    monkeypatch.setattr("scripts.bench_smart_judge_worker.db.bump_judge_completed_count", lambda run_id: None)

    pool = JudgeWorkerPool("codex_cli", "gpt-5.5", chunk_size=10, max_workers=2)
    pool.start("run_1")
    for idx in range(25):
        pool.enqueue("trace_" + str(idx))

    assert len(pool.pending_chunk) == 5
    pool.flush_and_join()
    assert sorted(calls) == sorted("trace_" + str(idx) for idx in range(25))
    assert sorted(saved) == sorted("trace_" + str(idx) for idx in range(25))
