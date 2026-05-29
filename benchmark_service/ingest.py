from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any

from benchmark_service.models import RunPayload


@dataclass
class NormalizedData:
    pipeline_run: dict[str, Any]
    steps: list[dict[str, Any]]
    llm_calls: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    faiss_hits: list[dict[str, Any]]
    explain_results: list[dict[str, Any]]
    generator_candidate_metrics: list[dict[str, Any]]
    raw_payload: dict[str, Any]

    def counts(self) -> dict[str, int]:
        return {
            "pipeline_runs": 1,
            "pipeline_steps": len(self.steps),
            "llm_calls": len(self.llm_calls),
            "findings": len(self.findings),
            "faiss_hits": len(self.faiss_hits),
            "explain_results": len(self.explain_results),
            "generator_candidate_metrics": len(self.generator_candidate_metrics),
            "raw_payloads": 1,
        }


def normalize_payload(payload: RunPayload, raw_body: bytes) -> NormalizedData:
    data = payload.model_dump(mode="python")
    trace = _dict(data.get("trace"))
    system_result = _dict(data.get("system_result"))
    report_data = _dict(data.get("report_data"))
    metadata = _dict(system_result.get("metadata"))
    trace_result = _dict(trace.get("result"))

    trace_id = data["trace_id"]
    _validate_trace_id(trace_id, metadata, trace)

    events = [item for item in trace.get("events") or [] if isinstance(item, dict)]
    pipeline_run = _pipeline_run(data, system_result, trace_result, report_data, trace)
    steps = [_step(trace_id, index, event) for index, event in enumerate(events)]
    llm_calls = _llm_calls(trace_id, events, metadata)
    findings = _findings(trace_id, events)
    hits = _faiss_hits(trace_id, events)
    explain = _explain_results(trace_id, events)
    candidate_metrics = _generator_candidate_metrics(trace_id, events, pipeline_run)
    raw = {
        "trace_id": trace_id,
        "payload_jsonb": data,
        "payload_sha256": hashlib.sha256(raw_body).hexdigest(),
        "payload_size_bytes": len(raw_body),
    }
    return NormalizedData(pipeline_run, steps, llm_calls, findings, hits, explain, candidate_metrics, raw)


def _validate_trace_id(trace_id: str, metadata: dict[str, Any], trace: dict[str, Any]) -> None:
    meta_trace = str(metadata.get("trace_id") or "").strip()
    if meta_trace and meta_trace != trace_id:
        raise ValueError("trace_id does not match system_result.metadata.trace_id")
    request_id = str(trace.get("request_id") or "").strip()
    if request_id and request_id != trace_id:
        raise ValueError("trace_id does not match trace.request_id")


def _pipeline_run(
    data: dict[str, Any],
    system_result: dict[str, Any],
    trace_result: dict[str, Any],
    report_data: dict[str, Any],
    trace: dict[str, Any],
) -> dict[str, Any]:
    metadata = _dict(system_result.get("metadata"))
    final_sql = _first_text(system_result.get("final_sql"), trace_result.get("final_sql"))
    decision = _first_text(metadata.get("decision"), report_data.get("verdict"))
    approved = _bool(system_result.get("approved"))
    if not decision:
        decision = "approve" if approved else "unknown"

    return {
        "trace_id": data["trace_id"],
        "benchmark_run_id": data["benchmark_run_id"],
        "case_id": data["case_id"],
        "model_key": data["model_key"],
        "llm_mode": data.get("llm_mode") or metadata.get("mode"),
        "decision": decision,
        "approved": approved,
        "needs_human": _bool(metadata.get("needs_human")),
        "human_reason": _text(metadata.get("human_reason")),
        "abstain_reason": _text(metadata.get("abstain_reason")),
        "iterations_used": _int(_first(system_result.get("iterations_used"), trace_result.get("iterations_used"))),
        "overall_risk_score": _num(
            _first(system_result.get("overall_risk_score"), trace_result.get("overall_risk_score"), report_data.get("risk_score"))
        ),
        "duration_sec": _num(_first(trace.get("duration_sec"), metadata.get("duration_sec"), report_data.get("duration_sec"))),
        "generator_backend": _text(metadata.get("generator_backend")),
        "generator_model": _text(metadata.get("generator_model")),
        "generator_provider": _text(metadata.get("generator_provider")),
        "auditor_backend": _text(metadata.get("auditor_backend")),
        "auditor_model": _text(metadata.get("auditor_model")),
        "final_sql_sha256": hashlib.sha256(final_sql.encode("utf-8")).hexdigest() if final_sql else None,
        "final_sql_len": len(final_sql) if final_sql else None,
        "final_sql_text": final_sql or None,
        "isolation_mode": _isolation_mode(data, metadata, trace),
        "policy_label": _text(metadata.get("policy_label")),
        "security_risk_score": _num(metadata.get("security_risk_score")),
        "quality_risk_score": _num(metadata.get("quality_risk_score")),
        "refusal_message": _text(metadata.get("refusal_message")),
        "banned_identifiers": list(metadata.get("banned_identifiers") or []) or None,
    }


def _isolation_mode(data: dict[str, Any], metadata: dict[str, Any], trace: dict[str, Any]) -> str:
    value = _first_text(
        data.get("isolation_mode"),
        data.get("isolation"),
        metadata.get("isolation_mode"),
        metadata.get("isolation"),
    )
    if value:
        return value
    for event in trace.get("events") or []:
        if not isinstance(event, dict) or event.get("node") != "retrieve":
            continue
        details = _dict(event.get("details"))
        found = _first_text(details.get("isolation_mode"))
        if found:
            return found
    return "production"


def _step(trace_id: str, index: int, event: dict[str, Any]) -> dict[str, Any]:
    inputs = _dict(event.get("inputs"))
    details = _dict(event.get("details"))
    row = {
        "trace_id": trace_id,
        "step_index": index,
        "node": _text(event.get("node")),
        "iteration": _int(_first(inputs.get("iteration"), details.get("iteration"))),
        "event_started_at": _text(event.get("started_at")),
        "duration_sec": _num(event.get("duration_sec")),
        "inputs_jsonb": inputs,
        "outputs_jsonb": _dict(event.get("outputs")),
        "details_summary_jsonb": _details_summary(details),
    }
    return row


def _llm_calls(trace_id: str, events: list[dict[str, Any]], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        node = _text(event.get("node"))
        details = _dict(event.get("details"))
        prompt = _prompt_fields(details)
        inputs = _dict(event.get("inputs"))
        iteration = _int(_first(inputs.get("iteration"), details.get("iteration")))
        if node == "generate":
            rows.extend(_generator_calls(trace_id, event, iteration, metadata))
        elif node == "audit":
            rows.append(_single_llm_call(trace_id, event, iteration, "auditor", details.get("llm_call")))
        elif node == "prompt_check" and isinstance(details.get("llm_call"), dict):
            rows.append(_single_llm_call(trace_id, event, iteration, "prompt_check", details.get("llm_call")))
    return rows


def _generator_calls(
    trace_id: str,
    event: dict[str, Any],
    iteration: int | None,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    details = _dict(event.get("details"))
    prompt = _prompt_fields(details)
    tool_calls = _list(details.get("tool_llm_calls"))
    if _bool(details.get("tool_mode")) and tool_calls:
        candidates = _list(details.get("response_sql"))
    else:
        candidates = _list(details.get("candidates") or details.get("generate_candidates") or details.get("response_sql"))
    responses = _list(details.get("response_raw"))
    if not candidates:
        candidates = responses or [details]
    rows: list[dict[str, Any]] = []
    count = max(len(candidates), 1)
    latency = _latency_ms(event, count)
    for index, item in enumerate(candidates):
        call = item if isinstance(item, dict) else {}
        call_prompt = _prompt_fields(details, call)
        response = _first_text(
            call.get("response"),
            call.get("response_raw"),
            call.get("sql"),
            responses[index] if index < len(responses) else None,
            item if isinstance(item, str) else None,
        )
        usage = _usage(call)
        # Phase 0 поля per-candidate из app/generator.py:candidate_details.
        walltime_sec = call.get("walltime_sec")
        retry_log = call.get("retry_log") if isinstance(call.get("retry_log"), list) else None
        response_headers = call.get("response_headers") if isinstance(call.get("response_headers"), dict) else None
        rows.append(
            _llm_row(
                trace_id=trace_id,
                node="generate",
                iteration=iteration,
                role="generator",
                backend=_first_text(call.get("backend"), details.get("backend"), metadata.get("generator_backend")),
                provider=_first_text(call.get("provider"), metadata.get("generator_provider")),
                model=_first_text(call.get("model"), details.get("model"), metadata.get("generator_model")),
                generation_id=_first_text(call.get("generation_id"), call.get("id")),
                prompt_type=call_prompt["prompt_type"],
                prompt_id=call_prompt["prompt_id"],
                prompt_version=call_prompt["prompt_version"],
                prompt_sha256=call_prompt["prompt_sha256"],
                prompt_chars=len(_first_text(details.get("prompt_system"))) + len(_first_text(details.get("prompt_user"))),
                response_chars=len(response),
                usage=usage,
                latency_ms=latency,
                walltime_sec=walltime_sec,
                retry_log=retry_log,
                response_headers=response_headers,
            )
        )
    for item in tool_calls:
        if not isinstance(item, dict):
            continue
        call_prompt = _prompt_fields(details, item)
        response = _first_text(item.get("response"), item.get("response_raw"), item.get("sql"))
        usage = _usage(item)
        walltime_sec = item.get("walltime_sec")
        retry_log = item.get("retry_log") if isinstance(item.get("retry_log"), list) else None
        response_headers = item.get("response_headers") if isinstance(item.get("response_headers"), dict) else None
        rows.append(
            _llm_row(
                trace_id=trace_id,
                node="generate",
                iteration=iteration,
                role="generator",
                backend=_first_text(item.get("backend"), details.get("backend"), metadata.get("generator_backend")),
                provider=_first_text(item.get("provider"), metadata.get("generator_provider")),
                model=_first_text(item.get("model"), details.get("model"), metadata.get("generator_model")),
                generation_id=_first_text(item.get("generation_id"), item.get("id")),
                prompt_type=call_prompt["prompt_type"] or prompt["prompt_type"],
                prompt_id=call_prompt["prompt_id"] or prompt["prompt_id"],
                prompt_version=call_prompt["prompt_version"] or prompt["prompt_version"],
                prompt_sha256=call_prompt["prompt_sha256"] or prompt["prompt_sha256"],
                prompt_chars=len(_first_text(details.get("prompt_system"))) + len(_first_text(details.get("prompt_user"))),
                response_chars=len(response),
                usage=usage,
                latency_ms=_int(item.get("latency_ms")),
                walltime_sec=walltime_sec,
                retry_log=retry_log,
                response_headers=response_headers,
            )
        )
    return rows


def _generator_candidate_metrics(
    trace_id: str,
    events: list[dict[str, Any]],
    pipeline_run: dict[str, Any],
) -> list[dict[str, Any]]:
    audit_by_iter = _audit_by_iteration(events)
    rows: list[dict[str, Any]] = []
    for event in events:
        if _text(event.get("node")) != "generate":
            continue
        details = _dict(event.get("details"))
        prompt = _prompt_fields(details)
        inputs = _dict(event.get("inputs"))
        outputs = _dict(event.get("outputs"))
        iteration = _int(_first(inputs.get("iteration"), details.get("iteration"))) or 0
        selected_index = _int(outputs.get("selected_index"))
        candidates = _list(details.get("candidates"))
        sql_items = _list(details.get("generate_candidates") or details.get("response_sql"))
        if not candidates:
            candidates = sql_items
        scores = _list(details.get("selector_scores"))
        schedule = _list(details.get("temperature_schedule"))
        prompt_sha = _first_text(details.get("prompt_sha256"))
        if not prompt_sha:
            prompt_text = _first_text(details.get("prompt_system")) + "\n\0\n" + _first_text(details.get("prompt_user"))
            prompt_sha = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest() if prompt_text.strip() else ""

        for idx, item in enumerate(candidates):
            cand = item if isinstance(item, dict) else {}
            cand_prompt = _prompt_fields(details, cand)
            candidate_index = _int(cand.get("candidate_index"))
            if candidate_index is None:
                candidate_index = idx
            score = _dict(cand.get("selector_score"))
            if not score and idx < len(scores):
                score = _dict(scores[idx])
            selected_raw = cand.get("selected_by_selector")
            selected = bool(selected_raw) if selected_raw is not None else candidate_index == selected_index
            sql = _first_text(
                cand.get("sql"),
                sql_items[idx] if idx < len(sql_items) else None,
                cand.get("response_sql"),
                cand.get("response"),
                item if isinstance(item, str) else None,
            )
            audit = audit_by_iter.get(iteration, {})
            row = {
                "trace_id": trace_id,
                "benchmark_run_id": pipeline_run.get("benchmark_run_id"),
                "case_id": pipeline_run.get("case_id"),
                "model_key": pipeline_run.get("model_key"),
                "llm_mode": pipeline_run.get("llm_mode"),
                "generator_backend": _first_text(cand.get("backend"), details.get("backend"), pipeline_run.get("generator_backend")),
                "generator_model": _first_text(cand.get("model"), details.get("model"), pipeline_run.get("generator_model")),
                "generator_provider": _first_text(cand.get("provider"), pipeline_run.get("generator_provider")),
                "iteration": iteration,
                "candidate_index": candidate_index,
                "temperature": _num(_first(
                    cand.get("temperature"),
                    schedule[idx] if idx < len(schedule) else None,
                    details.get("temperature"),
                )),
                "temperature_applied": _bool_or_none(cand.get("temperature_applied")),
                "prompt_type": cand_prompt["prompt_type"] or prompt["prompt_type"],
                "prompt_id": cand_prompt["prompt_id"] or prompt["prompt_id"],
                "prompt_version": cand_prompt["prompt_version"] or prompt["prompt_version"],
                "prompt_sha256": cand_prompt["prompt_sha256"] or prompt_sha or None,
                "sql_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest() if sql else None,
                "sql_len": len(sql) if sql else None,
                "selected_by_selector": selected,
                "selector_broken": _bool_or_none(score.get("broken")),
                "selector_critical_count": _int(score.get("critical_count")),
                "selector_finding_count": _int(score.get("finding_count")),
                "selector_labels": _text_list(score.get("labels")),
                "selected_iteration_audit_approved": _bool_or_none(audit.get("approved")) if selected else None,
                "selected_iteration_risk_score": _num(audit.get("risk_score")) if selected else None,
                "run_decision": pipeline_run.get("decision") if selected else None,
                "run_approved": pipeline_run.get("approved") if selected else None,
                "run_needs_human": pipeline_run.get("needs_human") if selected else None,
            }
            rows.append(row)
    return rows


def _audit_by_iteration(events: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for event in events:
        if _text(event.get("node")) != "audit":
            continue
        inputs = _dict(event.get("inputs"))
        details = _dict(event.get("details"))
        outputs = _dict(event.get("outputs"))
        iteration = _int(_first(inputs.get("iteration"), details.get("iteration")))
        if iteration is None:
            continue
        out[iteration] = {
            "approved": outputs.get("approved"),
            "risk_score": _first(outputs.get("overall_risk_score"), details.get("overall_risk_score")),
        }
    return out


def _single_llm_call(
    trace_id: str,
    event: dict[str, Any],
    iteration: int | None,
    role: str,
    call_obj: Any,
) -> dict[str, Any]:
    details = _dict(event.get("details"))
    call = call_obj if isinstance(call_obj, dict) else details
    prompt = _prompt_fields(details, call)
    usage = _usage(call)
    response = _first_text(call.get("response"), call.get("response_raw"), details.get("response_raw"))
    # Phase 0 поля для auditor/prompt_check llm_call (single вызов на событие).
    walltime_sec = call.get("walltime_sec")
    retry_log = call.get("retry_log") if isinstance(call.get("retry_log"), list) else None
    response_headers = call.get("response_headers") if isinstance(call.get("response_headers"), dict) else None
    return _llm_row(
        trace_id=trace_id,
        node=_text(event.get("node")),
        iteration=iteration,
        role=role,
        backend=_first_text(call.get("backend"), details.get("backend")),
        provider=_first_text(call.get("provider")),
        model=_first_text(call.get("model"), details.get("model")),
        generation_id=_first_text(call.get("generation_id"), call.get("id")),
        prompt_type=prompt["prompt_type"],
        prompt_id=prompt["prompt_id"],
        prompt_version=prompt["prompt_version"],
        prompt_sha256=prompt["prompt_sha256"],
        prompt_chars=len(_first_text(call.get("prompt"), details.get("prompt_system")))
        + len(_first_text(call.get("prompt_user"), details.get("prompt_user"))),
        response_chars=len(response),
        usage=usage,
        latency_ms=_latency_ms(event, 1),
        walltime_sec=walltime_sec,
        retry_log=retry_log,
        response_headers=response_headers,
    )


def _llm_row(
    *,
    trace_id: str,
    node: str,
    iteration: int | None,
    role: str,
    backend: str,
    provider: str,
    model: str,
    generation_id: str,
    prompt_type: str | None,
    prompt_id: str | None,
    prompt_version: int | None,
    prompt_sha256: str | None,
    prompt_chars: int,
    response_chars: int,
    usage: dict[str, Any],
    latency_ms: int | None,
    walltime_sec: float | None = None,
    retry_log: list[dict[str, Any]] | None = None,
    response_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    # Phase 0 поля: walltime_sec/retry_log/response_headers пишутся в
    # call dict из app/llm_provider.py.LLMResponse через generator.py
    # и auditor.py. Здесь нормализуем их под колонки benchmark.llm_calls.
    retry_list = retry_log or []
    retry_count = len(retry_list)
    retry_total_wait = 0.0
    for item in retry_list:
        if not isinstance(item, dict):
            continue
        try:
            retry_total_wait += float(item.get("wait_sec") or 0)
        except (TypeError, ValueError):
            continue
    headers = response_headers or {}
    if not isinstance(headers, dict):
        headers = {}

    row = {
        "trace_id": trace_id,
        "node": node,
        "iteration": iteration,
        "role": role or "other",
        "backend": backend,
        "provider": provider,
        "model": model,
        "generation_id": generation_id or None,
        "prompt_type": prompt_type,
        "prompt_id": prompt_id,
        "prompt_version": prompt_version,
        "prompt_sha256": prompt_sha256,
        "prompt_chars": prompt_chars or None,
        "response_chars": response_chars or None,
        "prompt_tokens": _int(_first(usage.get("prompt_tokens"), usage.get("input_tokens"))),
        "completion_tokens": _int(_first(usage.get("completion_tokens"), usage.get("output_tokens"))),
        "total_tokens": _int(usage.get("total_tokens")),
        "reasoning_tokens": _int(usage.get("reasoning_tokens")),
        "cached_tokens": _int(usage.get("cached_tokens")),
        "cache_write_tokens": _int(usage.get("cache_write_tokens")),
        "cost_usd": _num(usage.get("cost_usd")),
        "cost_source": None,
        "cost_credits": _num(usage.get("cost_credits")),
        "usage_source": "inline" if usage else "unavailable",
        "usage_raw_jsonb": usage or {},
        "latency_ms": latency_ms,
        "walltime_sec": _num(walltime_sec),
        "retries_count": retry_count,
        "retry_total_wait_sec": round(retry_total_wait, 3),
        "retry_log_jsonb": retry_list,
        "provider_header": headers.get("x-openrouter-provider"),
        "request_id_header": (
            headers.get("x-request-id")
            or headers.get("openrouter-request-id")
        ),
        "response_headers_jsonb": headers,
    }
    _backfill_cost(row)
    return row


def _backfill_cost(row: dict[str, Any]) -> None:
    total_tokens = int(row.get("total_tokens") or 0)
    if total_tokens <= 0:
        row["cost_source"] = "missing_usage"
        if row.get("cost_usd") is None:
            row["cost_usd"] = 0.0
        return
    if row.get("cost_usd") not in (None, 0):
        row["cost_source"] = "provider_inline"
        return
    tariff = _find_tariff(row)
    if not tariff:
        row["cost_source"] = "missing_tariff"
        return
    cost = (
        int(row.get("prompt_tokens") or 0) / 1000.0 * float(tariff.get("price_per_1k_in") or 0)
        + int(row.get("completion_tokens") or 0) / 1000.0 * float(tariff.get("price_per_1k_out") or 0)
        + int(row.get("cached_tokens") or 0) / 1000.0 * float(tariff.get("price_per_1k_cached") or 0)
        + int(row.get("reasoning_tokens") or 0) / 1000.0 * float(tariff.get("price_per_1k_reasoning") or 0)
    )
    row["cost_usd"] = round(cost, 6)
    row["cost_source"] = "tariff_backfill"


def _find_tariff(row: dict[str, Any]) -> dict[str, Any] | None:
    dsn = os.environ.get("BENCHMARK_DSN", "").strip()
    if not dsn:
        return None
    backend = str(row.get("backend") or "").strip()
    model = str(row.get("model") or "").strip()
    provider = str(row.get("provider") or "").strip()
    keys = _tariff_keys(backend, model, provider)
    if not keys:
        return None
    try:
        import psycopg2
        import psycopg2.extras
        with psycopg2.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM benchmark.model_tariffs
                    WHERE preset_key = ANY(%s)
                    ORDER BY array_position(%s, preset_key)
                    LIMIT 1
                    """,
                    (keys, keys),
                )
                found = cur.fetchone()
                return dict(found) if found else None
    except Exception:
        return None


def _tariff_keys(backend: str, model: str, provider: str = "") -> list[str]:
    raw = [backend + "-" + model] if backend and model else []
    normalized_model = model.replace(".", "-").replace(":", "-").replace("/", "-").lower()
    normalized_backend = backend.replace("_", "-").lower()
    keys = list(raw)
    if normalized_backend and normalized_model:
        keys.append(normalized_backend + "-" + normalized_model)
    if backend == "openrouter" and provider:
        provider_key = provider.replace("/", "-").replace(".", "-").lower()
        keys.append("openrouter-" + provider_key)
    aliases = {
        "codex_cli-gpt-5.5": "codex-cli-gpt-5-5",
        "claude_cli-claude-sonnet-4-6": "claude-cli-sonnet",
        "local_ollama-qwen3.5:9b": "local-qwen3-5-9b",
        "local-ollama-qwen3-5-9b": "local-qwen3-5-9b",
        "local_openai-qwen3.5:9b": "local-qwen3-5-9b",
        "local-openai-qwen3-5-9b": "local-qwen3-5-9b",
        "openrouter-google-gemini-2-5-flash": "openrouter-gemini-2-5-flash",
        "openrouter-google-gemini-2-5-pro": "openrouter-gemini-2-5-pro",
        "openrouter-qwen-qwen-3-235b-a22b": "openrouter-qwen-235b",
    }
    out: list[str] = []
    for key in keys:
        if key and key not in out:
            out.append(key)
        alias = aliases.get(key)
        if alias and alias not in out:
            out.append(alias)
    return out


def _findings(trace_id: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        node = _text(event.get("node"))
        details = _dict(event.get("details"))
        if node == "sql_guard":
            rows.extend(_finding_rows(trace_id, node, details.get("findings"), "sql_guard"))
        elif node == "audit":
            rows.extend(_finding_rows(trace_id, node, details.get("merged_findings"), "audit"))
        elif node == "prompt_check":
            rows.extend(_finding_rows(trace_id, node, details.get("findings"), "prompt_check"))
    return rows


SEVERITY_LEVELS = {"none", "low", "medium", "high", "critical"}


def _normalize_severity(value: Any) -> tuple[str | None, float | None]:
    """Return (severity_category_or_None, numeric_risk_score_or_None).

    Severity is preserved only if it matches a known category; numeric values
    move into risk_score so analytics can filter by `severity = 'high'` without
    colliding with `risk_score = 8.5` style data.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return None, float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None, None
        lowered = stripped.lower()
        if lowered in SEVERITY_LEVELS:
            return lowered, None
        try:
            return None, float(stripped)
        except ValueError:
            return stripped, None
    return None, None


def _evidence_spans(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _finding_rows(trace_id: str, node: str, items: Any, detector: str) -> list[dict[str, Any]]:
    rows = []
    for item in _list(items):
        if not isinstance(item, dict):
            continue
        severity_raw = _first(item.get("severity"), item.get("risk_score"))
        severity_cat, numeric = _normalize_severity(severity_raw)
        explicit_score = _num(item.get("risk_score"))
        risk_score = explicit_score if explicit_score is not None else numeric
        spans = _evidence_spans(item.get("evidence_span"))
        rows.append(
            {
                "trace_id": trace_id,
                "node": node,
                "label": _first_text(item.get("label"), item.get("vuln_class")),
                "severity": severity_cat,
                "risk_score": risk_score,
                "confidence": _num(item.get("confidence")),
                "detector": _first_text(item.get("detector"), detector),
                "evidence_span": "\n".join(spans) if spans else None,
                "evidence_spans": spans,
                "payload_jsonb": item,
            }
        )
    return rows


def _faiss_hits(trace_id: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        node = _text(event.get("node"))
        details = _dict(event.get("details"))
        if node == "retrieve":
            rows.extend(_hit_rows(trace_id, node, "generation", details.get("rag_generation_hits")))
        elif node == "audit":
            rows.extend(_hit_rows(trace_id, node, "security", details.get("rag_security_hits")))
    return rows


def _hit_rows(trace_id: str, node: str, index_name: str, items: Any) -> list[dict[str, Any]]:
    rows = []
    for item in _list(items):
        if not isinstance(item, dict):
            continue
        content = _first_text(item.get("content"), item.get("text"))
        rows.append(
            {
                "trace_id": trace_id,
                "node": node,
                "index_name": index_name,
                "source": _text(item.get("source")),
                "score": _num(item.get("score")),
                "table_name": _first_text(item.get("table_name"), item.get("table")),
                "vuln_class": _text(item.get("vuln_class")),
                "doc_id": _first_text(item.get("doc_id"), item.get("pattern_id"), item.get("id"), item.get("name")),
                "content_excerpt": content[:1000] if content else "",
            }
        )
    return rows


def _explain_results(trace_id: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        if _text(event.get("node")) != "explain_sandbox":
            continue
        details = _dict(event.get("details"))
        outputs = _dict(event.get("outputs"))
        plan = details.get("plan")
        rows.append(
            {
                "trace_id": trace_id,
                "ok": _bool(outputs.get("ok")),
                "skipped": _bool(outputs.get("skipped")),
                "error": _text(details.get("error")),
                "plan_text": plan if isinstance(plan, str) else None,
                "plan_jsonb": plan if isinstance(plan, (dict, list)) else None,
                "rows_est": _num(details.get("rows_est")),
                "cost_est": _num(details.get("cost_est")),
            }
        )
    return rows


def _details_summary(details: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"keys": sorted(details.keys())}
    for key in (
        "backend",
        "model",
        "approved",
        "overall_risk_score",
        "parse_error",
        "explain_error",
        "internal_labels",
        "candidate_count",
        "iteration",
        "prompt_type",
        "prompt_id",
        "prompt_version",
        "prompt_sha256",
        "prompt_source",
        "isolation_mode",
        "solutions_skipped_by_isolation",
    ):
        if key in details:
            summary[key] = details[key]
    for key in ("rag_generation_hits", "rag_security_hits", "findings", "merged_findings", "generate_candidates"):
        value = details.get(key)
        if isinstance(value, list):
            summary[key + "_count"] = len(value)
    return summary


def _prompt_fields(details: dict[str, Any], call: dict[str, Any] | None = None) -> dict[str, Any]:
    call = call or {}
    meta = _dict(_first(call.get("prompt_meta"), details.get("prompt_meta")))
    return {
        "prompt_type": _str_or_none(_first(call.get("prompt_type"), details.get("prompt_type"), meta.get("prompt_type"))),
        "prompt_id": _str_or_none(_first(call.get("prompt_id"), details.get("prompt_id"), meta.get("prompt_id"))),
        "prompt_version": _int(_first(call.get("prompt_version"), details.get("prompt_version"), meta.get("prompt_version"))),
        "prompt_sha256": _str_or_none(_first(call.get("prompt_sha256"), details.get("prompt_sha256"), meta.get("prompt_sha256"))),
    }


def _usage(call: dict[str, Any]) -> dict[str, Any]:
    raw = call.get("usage")
    if isinstance(raw, dict):
        return raw
    nested = _dict(call.get("raw")).get("usage")
    return nested if isinstance(nested, dict) else {}


def _latency_ms(event: dict[str, Any], divisor: int) -> int | None:
    value = _num(event.get("duration_sec"))
    if value is None:
        return None
    return int((float(value) * 1000.0) / max(divisor, 1))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _first_text(*values: Any) -> str:
    value = _first(*values)
    return _text(value)


def _str_or_none(value: Any) -> str | None:
    text = _text(value).strip()
    return text or None


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool:
    return bool(value)


def _bool_or_none(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    return bool(value)


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    return [str(value)]
