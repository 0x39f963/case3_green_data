from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

bench = importlib.import_module("scripts.bench_run_dataset")


CASE = {
    "id": "case_1",
    "version": "v0_2",
    "family": "sql_injection",
    "user_task": "Show users",
}
MODEL = {
    "key": "model_a",
    "llm_mode": "openrouter",
    "llm_generator_model": "provider/model-a",
}
DATASET = {
    "dataset_id": "adversarial_sql_requests",
    "dataset_version": "v0_2",
    "sha256": "sha",
    "rows_count": 1,
}


def test_usage_totals_counts_only_explicit_llm_nodes() -> None:
    trace = {"events": []}
    for idx in range(5):
        trace["events"].append(
            {
                "node": "generate",
                "details": {
                    "candidates": [
                        {
                            "usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 2,
                                "total_tokens": 12,
                                "cost_usd": 0.001,
                            },
                            "response_usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 2,
                                "total_tokens": 12,
                                "cost_usd": 0.001,
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
                },
            }
        )
        trace["events"].append(
            {
                "node": "audit",
                "details": {
                    "llm_call": {
                        "id": "audit-" + str(idx),
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 2,
                            "total_tokens": 12,
                            "cost_usd": 0.001,
                        },
                        "response_usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 2,
                            "total_tokens": 12,
                            "cost_usd": 0.001,
                        },
                    },
                    "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
                },
            }
        )
    trace["events"].append(
        {
            "node": "other",
            "details": {"usage": {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000}},
        }
    )

    assert bench.usage_totals(trace) == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "cost_usd": 0.01,
    }


def test_result_row_decision_unknown_without_metadata_decision() -> None:
    row = bench.result_row(
        CASE,
        MODEL,
        {"approved": True, "metadata": {"trace_id": "trace_1"}},
        {"events": []},
        "trace_1",
        True,
        "2026-05-19T00:00:00+00:00",
    )

    assert row["decision"] == "unknown"
    assert row["approved"] is True


def test_completed_pairs_recovers_from_results_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "results.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"case_id": "case_1", "model_key": "model_a"}',
                '{"case_id": "case_2", "model_key": "model_b"}',
                '{"case_id": "", "model_key": "model_c"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert bench.completed_pairs(path) == {("case_1", "model_a"), ("case_2", "model_b")}


def test_run_pair_duplicate_skip_replace_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run_dependencies(monkeypatch)

    skip_client = _DuplicateClient()
    row, attempts = bench.run_pair(_args("skip"), DATASET, CASE, MODEL, skip_client)
    assert attempts == 1
    assert row["uploaded_to_store"] is True
    assert row["store_action"] == "duplicate_skipped"
    assert skip_client.calls == [False]

    replace_client = _DuplicateClient()
    row, _ = bench.run_pair(_args("replace"), DATASET, CASE, MODEL, replace_client)
    assert row["uploaded_to_store"] is True
    assert row["store_action"] == "replaced"
    assert replace_client.calls == [True]

    fail_client = _DuplicateClient()
    with pytest.raises(bench.ApiError) as exc:
        bench.run_pair(_args("fail"), DATASET, CASE, MODEL, fail_client)
    assert exc.value.status_code == 409
    assert fail_client.calls == [False]


def test_run_pair_passes_codex_reasoning_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_body: dict = {}

    def fake_post_run(api_url: str, body: dict, *args, **kwargs):
        del api_url, args, kwargs
        sent_body.update(body)
        return _result(), 1

    monkeypatch.setattr(bench, "post_run", fake_post_run)
    monkeypatch.setattr(bench, "load_trace", lambda *args, **kwargs: {"events": []})
    monkeypatch.setattr(bench, "report_data", lambda *args, **kwargs: None)
    monkeypatch.setattr(bench, "git_sha", lambda: "test-sha")

    codex_model = {
        **MODEL,
        "llm_mode": "codex_cli",
        "llm_generator_model": "gpt-5.3-codex-spark",
        "codex_reasoning_effort": "high",
    }
    bench.run_pair(_args("skip"), DATASET, CASE, codex_model, _DuplicateClient(raise_on_insert=False))

    assert sent_body["codex_reasoning_effort"] == "high"


def test_dataset_info_cached_for_payload_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "requests_v0_2.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    rows = [{**CASE, "id": "case_" + str(idx)} for idx in range(120)]
    calls: list[Path] = []

    def fake_sha256(path_arg: Path) -> str:
        calls.append(path_arg)
        return "dataset-sha"

    monkeypatch.setattr(bench, "sha256_file", fake_sha256)
    dataset = bench.dataset_info(path, rows)
    assert calls == [path]

    def fail_sha256(path_arg: Path) -> str:
        raise AssertionError("sha256_file must not be called during payload build")

    monkeypatch.setattr(bench, "sha256_file", fail_sha256)
    monkeypatch.setattr(bench, "report_data", lambda *args, **kwargs: None)
    monkeypatch.setattr(bench, "git_sha", lambda: "test-sha")
    models = [{**MODEL, "key": "model_" + str(idx)} for idx in range(5)]
    for case in rows:
        for model in models:
            payload = bench.build_payload(_args("skip"), dataset, case, model, _result(), {"events": []}, "trace_1")
            assert payload["dataset_id"] == "requests"
            assert payload["dataset_version"] == "v0_2"
    assert calls == [path]


def _patch_run_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bench, "post_run", lambda *args, **kwargs: (_result(), 1))
    monkeypatch.setattr(bench, "load_trace", lambda *args, **kwargs: {"events": []})
    monkeypatch.setattr(bench, "report_data", lambda *args, **kwargs: None)
    monkeypatch.setattr(bench, "git_sha", lambda: "test-sha")


def _args(on_duplicate: str) -> argparse.Namespace:
    return argparse.Namespace(
        api_url="http://api.test",
        benchmark_run_id="bench_test",
        max_iterations=5,
        on_duplicate=on_duplicate,
        retry_attempts=1,
        retry_backoff=0.0,
        timeout_sec=10,
        traces_dir=Path("."),
    )


def _result() -> dict:
    return {
        "approved": True,
        "iterations_used": 1,
        "metadata": {"trace_id": "trace_1"},
    }


class _DuplicateClient:
    def __init__(self, raise_on_insert: bool = True) -> None:
        self.calls: list[bool] = []
        self.raise_on_insert = raise_on_insert

    def ingest(self, payload: dict, replace: bool = False) -> dict:
        self.calls.append(replace)
        if self.raise_on_insert and not replace:
            raise bench.BenchmarkClientError(409, '{"error":{"code":"duplicate_logical_run"}}')
        return {"status": "ok"}
