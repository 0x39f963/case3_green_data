from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import threading
from typing import Any

from app.llm_provider import ProviderUnavailable
from app.meta_auditor import review_case_quality
from benchmark_service import db


class JudgeWorkerPool:
    """Small in-process worker pool for online case-quality judging."""

    def __init__(
        self,
        backend: str,
        model: str,
        chunk_size: int = 10,
        max_workers: int = 3,
        fallback_backend: str = "claude_cli",
        fallback_model: str = "claude-sonnet-4-6",
        status_on_error: str = "runtime_error",
    ) -> None:
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="judge")
        self.chunk_size = max(int(chunk_size or 10), 1)
        self.pending_chunk: list[str] = []
        self.futures: list[Future[Any]] = []
        self.benchmark_run_id: str | None = None
        self.backend = backend
        self.model = model
        self.fallback_backend = fallback_backend
        self.fallback_model = fallback_model
        self.status_on_error = status_on_error
        self.stopped = threading.Event()
        self.lock = threading.Lock()

    def start(self, benchmark_run_id: str) -> None:
        self.benchmark_run_id = benchmark_run_id

    def enqueue(self, trace_id: str) -> None:
        if self.stopped.is_set():
            return
        with self.lock:
            self.pending_chunk.append(trace_id)
            if len(self.pending_chunk) >= self.chunk_size:
                self._flush_chunk_locked()

    def _flush_chunk_locked(self) -> None:
        chunk, self.pending_chunk = self.pending_chunk, []
        for trace_id in chunk:
            self.futures.append(self.executor.submit(self._judge_one, trace_id))

    def _judge_one(self, trace_id: str) -> None:
        if self.stopped.is_set() or not self.benchmark_run_id:
            return
        try:
            result = review_case_quality(trace_id, self.backend, self.model)
        except ProviderUnavailable as exc:
            status = _status_from_error(exc, default=self.status_on_error)
            if status == "quota_exhausted" and self.fallback_backend and self.fallback_backend != "off":
                try:
                    result = review_case_quality(trace_id, self.fallback_backend, self.fallback_model)
                except Exception as fallback_exc:
                    result = _error_result(
                        self.backend,
                        self.model,
                        _status_from_error(fallback_exc, default=self.status_on_error),
                        str(fallback_exc),
                        fallback_backend=self.fallback_backend,
                        fallback_model=self.fallback_model,
                    )
            else:
                result = _error_result(self.backend, self.model, status, str(exc))
        except Exception as exc:
            result = _error_result(
                self.backend,
                self.model,
                _status_from_error(exc, default=self.status_on_error),
                str(exc),
            )
        db.insert_case_quality_score(trace_id, self.benchmark_run_id, **result)
        db.bump_judge_completed_count(self.benchmark_run_id)

    def flush_and_join(self, timeout: float | None = 600) -> None:
        with self.lock:
            if self.pending_chunk:
                self._flush_chunk_locked()
        self.executor.shutdown(wait=True, cancel_futures=False)
        del timeout

    def abort(self) -> None:
        self.stopped.set()
        self.executor.shutdown(wait=False, cancel_futures=True)

    @property
    def pending_in_queue(self) -> int:
        with self.lock:
            return len(self.pending_chunk)

    @property
    def running_workers(self) -> int:
        return sum(1 for item in self.futures if item.running())


def _status_from_error(exc: Exception, *, default: str = "runtime_error") -> str:
    text = str(exc).lower()
    if "quota" in text or "rate limit" in text or "429" in text:
        return "quota_exhausted"
    if "timeout" in text or "timed out" in text or "не ответил за" in text:
        return "timeout"
    if "parse" in text or "json" in text:
        return "parse_error"
    return default or "runtime_error"


def _error_result(
    backend: str,
    model: str,
    status: str,
    error_text: str,
    *,
    fallback_backend: str | None = None,
    fallback_model: str | None = None,
) -> dict[str, Any]:
    raw: dict[str, Any] = {"status": status, "error": error_text[:2000]}
    if fallback_backend:
        raw["fallback_backend"] = fallback_backend
        raw["fallback_model"] = fallback_model
    return {
        "reviewer_backend": backend,
        "reviewer_model": model,
        "reviewer_status": status,
        "reviewer_error_text": error_text[:2000],
        "reviewer_raw_jsonb": raw,
        "sub_scores": {},
        "patch_suggestion": {
            "target_area": "none",
            "severity": "P3",
            "title": "Reviewer " + status,
            "details": error_text[:1000],
            "patch_hint": "",
            "examples": {},
        },
    }
