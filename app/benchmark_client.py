from __future__ import annotations

import json
import time
from typing import Any
from urllib import error, parse, request


class BenchmarkClientError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class BenchmarkClient:
    def __init__(self, url: str, token: str, timeout_sec: int = 30, retries: int = 3) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout_sec = timeout_sec
        self.retries = max(retries, 1)

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health", auth=False)

    def register_run(
        self,
        benchmark_run_id: str,
        dataset_id: str,
        dataset_version: str,
        **fields: Any,
    ) -> dict[str, Any]:
        payload = {
            "benchmark_run_id": benchmark_run_id,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            **fields,
        }
        return self._request("POST", "/v1/runs/register", payload)

    def ingest(self, payload: dict[str, Any], replace: bool = False) -> dict[str, Any]:
        path = "/v1/ingest/run"
        if replace:
            path += "?replace=true"
        return self._request("POST", path, payload)

    def ingest_batch(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/v1/ingest/batch", payloads)

    def upsert_dataset_cases(
        self,
        dataset_id: str,
        items: list[dict[str, Any]],
        dataset_version: str | None = None,
    ) -> dict[str, Any]:
        path = "/v1/datasets/" + dataset_id + "/cases"
        if dataset_version:
            path += "?dataset_version=" + dataset_version
        return self._request("POST", path, {"items": items})

    def get_run(self, trace_id: str) -> dict[str, Any]:
        return self._request("GET", "/v1/runs/" + trace_id)

    def audit_targets(
        self,
        benchmark_run_id: str,
        *,
        trace_ids: list[str] | None = None,
        case_ids: list[str] | None = None,
        families: list[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        query: list[tuple[str, str | int]] = [("benchmark_run_id", benchmark_run_id), ("limit", limit)]
        query.extend(("trace_id", item) for item in (trace_ids or []))
        query.extend(("case_id", item) for item in (case_ids or []))
        query.extend(("family", item) for item in (families or []))
        return self._request("GET", "/v1/audit/targets?" + parse.urlencode(query))

    def upsert_audit_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/audit/reviews", payload)

    def audit_reviews(
        self,
        *,
        benchmark_run_id: str | None = None,
        reviewer_backend: str | None = None,
        reviewer_model: str | None = None,
        reviewer_prompt_version: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        query = {
            "benchmark_run_id": benchmark_run_id,
            "reviewer_backend": reviewer_backend,
            "reviewer_model": reviewer_model,
            "reviewer_prompt_version": reviewer_prompt_version,
            "limit": limit,
        }
        pairs = [(key, value) for key, value in query.items() if value]
        return self._request("GET", "/v1/audit/reviews?" + parse.urlencode(pairs))

    def audit_suggestions(self, benchmark_run_id: str, top: int = 20) -> dict[str, Any]:
        return self._request(
            "GET",
            "/v1/audit/suggestions?" + parse.urlencode({"benchmark_run_id": benchmark_run_id, "top": top}),
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: Any | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = "Bearer " + self.token

        last_error: Exception | None = None
        for attempt in range(self.retries):
            req = request.Request(self.url + path, data=body, headers=headers, method=method)
            try:
                with request.urlopen(req, timeout=self.timeout_sec) as resp:
                    return _loads(resp.read())
            except error.HTTPError as exc:
                text = exc.read().decode("utf-8", errors="replace")
                if 400 <= exc.code < 500:
                    raise BenchmarkClientError(exc.code, text) from exc
                last_error = BenchmarkClientError(exc.code, text)
            except error.URLError as exc:
                last_error = exc

            if attempt < self.retries - 1:
                time.sleep(0.5 * (2 ** attempt))

        raise BenchmarkClientError(0, str(last_error or "request failed"))


def _loads(data: bytes) -> dict[str, Any]:
    payload = json.loads(data.decode("utf-8"))
    if isinstance(payload, dict):
        return payload
    return {"items": payload}
