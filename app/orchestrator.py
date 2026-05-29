"""
Центральный модуль продукта - цикл генерации и проверки SQL.

Реализует SQLSecuritySystem.run через LangGraph state machine. Узлы
идут в порядке prompt_check, retrieve, generate, sql_guard, explain,
audit, decide и опционально revise. Каждый узел пишет отдельное
событие в JSON-трассу.
Цикл крутится до пяти попыток. Внутри не глотаем неизвестные ошибки -
конфигурационные сбои поднимаются как LLMConfigError для API-слоя.
"""

from __future__ import annotations

import sys
import time
import os
import re
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import StateGraph, END

# Контракты заказчика.
_TASK3_ROOT = Path(__file__).resolve().parent.parent / "TASK-3"
if str(_TASK3_ROOT) not in sys.path:
    sys.path.insert(0, str(_TASK3_ROOT))

from baseline1 import (  # noqa: E402
    AuditResult,
    IterationLog,
    SQLSecuritySystem as _BaseSystem,
    SystemResult,
    Vulnerability,
)

from app import audit_log, audit_storage, business_alignment, explain_sandbox, generator_selector, intent_classifier, llm_provider, prompt_check, prompt_check_llm, rag_adapter, sentinel, sql_guard  # noqa: E402
from app.auditor import SecurityAuditor  # noqa: E402
from app.generator import SQLGenerator  # noqa: E402
from app.llm_provider import LLMConfigError, ProviderUnavailable  # noqa: E402
from app import trace as trace_utils  # noqa: E402
from app.trace import Trace  # noqa: E402


class PipelineState(TypedDict, total=False):
    """Состояние state machine между узлами LangGraph."""

    task: str
    iteration: int
    max_iterations: int
    sql_history: list[str]
    audit_history: list[AuditResult]
    prompt_risk_findings: list[Vulnerability]
    iterations_log: list[IterationLog]
    last_sql: str
    last_audit: AuditResult | None
    last_explain: explain_sandbox.ExplainResult | None
    last_explain_error: str | None
    last_guard_findings: list[Vulnerability]
    last_generation_context: str
    last_solutions_context: str
    last_revision_feedback: dict[str, Any]
    failure_signatures: list[str]
    isolation_mode: str
    allowed_tables: list[str]
    allowed_columns: dict[str, list[str]]
    allowed_objects: str
    approved: bool
    decision: str
    needs_human: bool
    human_reason: str
    abstain_reason: str
    policy_label: str
    policy_message: str
    early_barrier_blocked: bool
    early_barrier_labels: list[str]
    banned_identifiers: list[str]
    intent_kind: str
    intent_confidence: float
    intent_anchors: list[str]
    business_requirements: list[dict[str, Any]]
    trace: Trace
    generator: SQLGenerator
    auditor: SecurityAuditor


_POLICY_APPROVE = "approve"
_POLICY_REFUSAL_REQUIRED = "refusal_required"
_POLICY_INSUFFICIENT_CONTEXT = "insufficient_context"
_POLICY_REVISE_NEEDED = "revise_needed"
_POLICY_PROMPT_BLOCKED = "prompt_blocked"
_POLICY_AUDIT_UNCERTAIN = "audit_uncertain"
_POLICY_MAX_ITERATIONS = "max_iterations_exceeded"
_POLICY_REPEAT_STOP = "repeat_stop"
_POLICY_HARD_FAIL = "hard_fail"
_POLICY_APPROVE_WITH_QUALITY = "approve_with_advisory"
_REFUSAL_POLICIES = {
    _POLICY_REFUSAL_REQUIRED,
    _POLICY_INSUFFICIENT_CONTEXT,
    _POLICY_PROMPT_BLOCKED,
}


_STRICT_OVERLAY_DEFAULT = True


def _strict_overlay_enabled() -> bool:
    raw = os.environ.get("STRICT_SCHEMA_OVERLAY", "").strip().lower()
    if not raw:
        return _STRICT_OVERLAY_DEFAULT
    return raw in {"1", "true", "yes", "on"}


def _sql_sha256(sql: str) -> str:
    return hashlib.sha256((sql or "").strip().encode("utf-8")).hexdigest()


def _clean_signature_text(text: str, limit: int = 160) -> str:
    value = " ".join(str(text or "").lower().split())
    return value[:limit]


def _vuln_label(vuln: Vulnerability) -> str:
    return str(getattr(vuln, "vuln_class", "") or "")


def _critical_labels(audit: AuditResult | None) -> list[str]:
    if audit is None:
        return []
    labels: set[str] = set()
    for vuln in audit.vulnerabilities:
        label = _vuln_label(vuln)
        if not label:
            continue
        score = float(getattr(vuln, "risk_score", 0.0) or 0.0)
        if sql_guard.label_bucket(label) == "security" or score >= 6.0:
            labels.add(label)
    if not labels:
        labels = {_vuln_label(v) for v in audit.vulnerabilities if _vuln_label(v)}
    return sorted(labels)


def _failure_signature(state: PipelineState, audit: AuditResult | None) -> dict[str, Any]:
    labels = _critical_labels(audit)
    forbidden = set(state.get("banned_identifiers", []) or [])
    if audit is not None:
        forbidden |= _hallucinated_identifiers(audit)
    explain_error = _clean_signature_text(str(state.get("last_explain_error") or ""))
    if not labels and not forbidden and not explain_error:
        return {}
    return {
        "labels": labels,
        "explain_error": explain_error,
        "forbidden_identifiers": sorted(forbidden),
    }


def _failure_signature_key(signature: dict[str, Any]) -> str:
    if not signature:
        return ""
    return json.dumps(signature, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _evidence_spans(audit: AuditResult | None) -> list[str]:
    if audit is None:
        return []
    spans: list[str] = []
    for vuln in audit.vulnerabilities:
        span = str(getattr(vuln, "evidence_span", "") or getattr(vuln, "description", "") or "")
        span = " ".join(span.split())
        if span and span not in spans:
            spans.append(span[:240])
        if len(spans) >= 6:
            break
    return spans


def _required_repairs(audit: AuditResult | None) -> list[str]:
    if audit is None:
        return []
    repairs: list[str] = []
    for vuln in sorted(
        audit.vulnerabilities,
        key=lambda item: float(getattr(item, "risk_score", 0.0) or 0.0),
        reverse=True,
    ):
        label = _vuln_label(vuln)
        note = str(getattr(vuln, "revision_note", "") or getattr(vuln, "recommendation", "") or "")
        if label and note:
            repairs.append(label + ": " + note[:240])
        elif label:
            repairs.append(label)
        if len(repairs) >= 6:
            break
    return repairs


def _original_intent(state: PipelineState) -> str:
    kind = str(state.get("intent_kind", "") or "")
    if kind == intent_classifier.INTENT_AGGREGATE_SAFE:
        return "aggregate"
    if kind == intent_classifier.INTENT_ROW_LEVEL_BUSINESS:
        return "row_level"
    if kind == intent_classifier.INTENT_TOP_N:
        return "top_n"
    if kind == intent_classifier.INTENT_REFUSAL_REQUIRED:
        return "refusal"
    if kind == intent_classifier.INTENT_MASK_REQUIRED:
        return "row_level"
    task = state.get("task", "")
    if re.search(r"\bgroup\s+by\b|группир", task, re.IGNORECASE):
        return "group_by"
    return kind or "unknown"


def _build_revise_feedback(state: PipelineState, audit: AuditResult | None) -> dict[str, Any]:
    sql = state.get("last_sql", "")
    forbidden = set(state.get("banned_identifiers", []) or [])
    if audit is not None:
        forbidden |= _hallucinated_identifiers(audit)
    return {
        "failed_labels": _critical_labels(audit),
        "evidence_span": _evidence_spans(audit),
        "forbidden_identifiers": sorted(forbidden),
        "required_repair": _required_repairs(audit),
        "original_intent": _original_intent(state),
        "explain_error": str(state.get("last_explain_error") or ""),
        "previous_sql_sha256": _sql_sha256(sql) if sql else "",
    }


def _failure_abstain_reason(state: PipelineState, audit: AuditResult | None) -> str:
    labels = set(_critical_labels(audit))
    if state.get("last_explain_error"):
        return "explain_fail"
    if labels & {"BROKEN_SQL", "SYNTAX_BROKEN", "UNBOUND_PLACEHOLDER", "HALLUCINATED_TABLE", "HALLUCINATED_COLUMN", "SCHEMA_OVERLAY_MISSING", "WRONG_JOIN_PATH"}:
        return "generator_fail"
    return "low_confidence"


def _prompt_blocking_findings(state: PipelineState) -> list[Vulnerability]:
    return [
        vuln
        for vuln in state.get("prompt_risk_findings", [])
        if float(getattr(vuln, "risk_score", 0.0) or 0.0) >= 8.0
    ]


def _refusal_policy_active(state: PipelineState) -> bool:
    return bool(state.get("policy_label") in _REFUSAL_POLICIES)


def _sql_string_literal(value: str) -> str:
    return "'" + value[:500].replace("'", "''") + "'"


def _prompt_refusal_sql(findings: list[Vulnerability]) -> str:
    labels = ", ".join(v.vuln_class for v in findings) or "PROMPT_RISK"
    message = "Prompt blocked by precheck: " + labels
    return "SELECT 'REFUSAL_REQUIRED' AS reason, " + _sql_string_literal(message) + " AS message;"


def _node_intent_classify(state: PipelineState) -> PipelineState:
    """H7: deterministic intent классификация по тексту задачи.

    Не делает LLM-вызов, всегда быстрый. Результат пишется в state, дальше
    используется generator-prompt (как блок INTENT), classifier (через
    is_aggregate_safe_intent), auditor (через тот же ctx).
    """
    trace = state["trace"]
    task = state["task"]
    with trace.step("intent_classify", inputs={"task_length": len(task)}) as event:
        intent = intent_classifier.classify(task)
        event["outputs"]["intent_kind"] = intent.kind
        event["outputs"]["intent_confidence"] = intent.confidence
        event["details"]["matched_anchors"] = list(intent.matched)
    return {
        **state,
        "intent_kind": intent.kind,
        "intent_confidence": intent.confidence,
        "intent_anchors": list(intent.matched),
    }


def _node_prompt_check(state: PipelineState) -> PipelineState:
    """
    Prompt-risk prefilter.

    Runs before retrieve/generate and records intent-level risk labels.
    It does not call a model and does not block immediately; decide uses
    high-severity prompt findings after the SQL audit is visible in trace.
    """
    trace = state["trace"]
    task = state["task"]
    with trace.step(
        "prompt_check",
        inputs={
            "task_length": len(task),
            "enabled": prompt_check_llm.enabled(),
            "backend_override": llm_provider.current_prompt_check_backend_key(),
            "openrouter_provider_override": llm_provider.current_prompt_check_openrouter_provider(),
        },
    ) as event:
        if not prompt_check_llm.enabled():
            event["outputs"]["vuln_count"] = 0
            event["outputs"]["skipped_by_user"] = True
            event["details"]["llm_judge"] = {"enabled": False, "skipped": "disabled"}
            event["details"]["regex_findings"] = []
            event["details"]["findings"] = []
            return {**state, "prompt_risk_findings": []}
        regex_findings = prompt_check.check_prompt(task)
        llm_details: dict[str, Any]
        llm_findings = []
        if regex_findings:
            llm_details = {
                "enabled": prompt_check_llm.enabled(),
                "skipped": "regex_findings",
            }
        else:
            try:
                judge_result = prompt_check_llm.check_prompt(task)
                llm_details = judge_result.details
                llm_findings = [judge_result.finding] if judge_result.finding is not None else []
            except (RuntimeError, ValueError) as exc:
                llm_details = {
                    "enabled": prompt_check_llm.enabled(),
                    "classification": "unavailable",
                    "error": str(exc),
                    "backend": "unavailable",
                }
        findings = regex_findings + llm_findings
        event["outputs"]["vuln_count"] = len(findings)
        event["outputs"]["llm_classification"] = llm_details.get("classification")
        event["details"]["regex_findings"] = [v.__dict__ for v in regex_findings]
        event["details"]["llm_judge"] = llm_details
        if llm_details.get("prompt_system"):
            for key in (
                "prompt_system",
                "prompt_user",
                "prompt_meta",
                "prompt_id",
                "prompt_type",
                "prompt_version",
                "prompt_sha256",
                "prompt_source",
                "prompt_request_sha256",
                "fallback_reason",
                "prompt_fallback_reason",
            ):
                if key in llm_details:
                    event["details"][key] = llm_details[key]
        event["details"]["findings"] = [v.__dict__ for v in findings]
    return {**state, "prompt_risk_findings": findings}


def _node_retrieve(state: PipelineState) -> PipelineState:
    """
    Узел retrieve: semantic search + schema linking.

    Получает контекст из памяти Марины с business overlay, затем
    выделяет allowed_tables и allowed_columns. Generate prompt получает
    эти объекты явно, чтобы модель работала только с разрешенными
    таблицами и колонками.
    """
    trace = state["trace"]
    task = state["task"]
    with trace.step("retrieve", inputs={"task": task}) as event:
        blocking_findings = _prompt_blocking_findings(state)
        if blocking_findings:
            event["outputs"]["context_length"] = 0
            event["outputs"]["allowed_tables"] = []
            event["outputs"]["solutions_lessons_present"] = False
            event["outputs"]["rag_status"] = "skipped"
            event["outputs"]["skipped_by_prompt_risk"] = True
            event["details"]["prompt_risk_findings"] = [v.__dict__ for v in blocking_findings]
            return {
                **state,
                "last_generation_context": "",
                "last_solutions_context": "",
                "allowed_tables": [],
                "allowed_columns": {},
                "allowed_objects": "",
            }
        isolation_mode = state.get("isolation_mode") or os.environ.get("PIPELINE_ISOLATION", "production")
        # Phase 0.4 — sub-timings: cold start vs warm cache. На cold call
        # elapsed_sec показывает реальное время encode + FAISS; на warm —
        # около нуля. cache_hit=False — индикатор холодного старта.
        context_bundle, ctx_timing = rag_adapter.get_generation_context_bundle_timed(task)
        context = str(context_bundle.get("context") or "")
        rag_sources = dict(context_bundle.get("rag_sources") or {})
        hits, hits_timing = rag_adapter.get_generation_hits_timed(task)
        link = rag_adapter.schema_link(task, context)
        # TZ-7 phase 1: фильтрация RAG-паттернов по schema_scope/pii_columns_used.
        hits, scope_filter_stats = rag_adapter.filter_generation_hits_by_scope(
            hits,
            link.get("allowed_tables"),
            link.get("allowed_columns"),
        )
        # Phase 2 — уроки из похожих задач от мета-аудитора.
        #
        # Логика подключения lessons:
        # - В clean isolation по умолчанию отключено (golden eval honest),
        #   но env `USE_SOLUTIONS_LESSONS=true` форсирует включение даже
        #   в clean — это для замера эффекта обучения на golden subset.
        # - В production lessons всегда подключены.
        use_solutions_env = os.environ.get("USE_SOLUTIONS_LESSONS", "").strip().lower()
        use_solutions_override = use_solutions_env in {"1", "true", "yes", "on"}
        skip_solutions = isolation_mode == "clean" and not use_solutions_override
        if skip_solutions:
            solutions_context = ""
            solutions_timing = {
                "elapsed_sec": 0,
                "cache_hit": False,
                "fn": "get_solutions_context",
                "had_lessons": False,
                "source_meta": {"skipped_by_isolation": True},
            }
            solutions_meta = {"skipped_by_isolation": True, "isolation_mode": isolation_mode}
        else:
            solutions_context, solutions_timing = rag_adapter.get_solutions_context_timed(
                task,
                allowed_tables=link["allowed_tables"],
                allowed_columns=link["allowed_columns"],
            )
            solutions_meta = dict(solutions_timing.get("source_meta") or {})
            solutions_meta["lessons_forced_in_clean"] = (
                isolation_mode == "clean" and use_solutions_override
            )
        rag_sources["solutions"] = solutions_meta
        legacy = dict(rag_sources.get("legacy_faiss") or {})
        legacy["used"] = bool(hits or legacy.get("context_chars"))
        legacy["hit_count"] = len(hits)
        legacy["scope_filter"] = scope_filter_stats
        rag_sources["legacy_faiss"] = legacy
        event["outputs"]["context_length"] = len(context)
        event["outputs"]["allowed_tables"] = link["allowed_tables"]
        event["outputs"]["solutions_lessons_present"] = bool(solutions_context)
        event["outputs"]["rag_status"] = "degraded" if (
            (rag_sources.get("table_knowledge_v2") or {}).get("enabled")
            and (rag_sources.get("table_knowledge_v2") or {}).get("error")
        ) else "ok"
        event["details"]["generation_context"] = context
        event["details"]["isolation_mode"] = isolation_mode
        event["details"]["solutions_skipped_by_isolation"] = isolation_mode == "clean"
        event["details"]["rag_generation_hits"] = hits
        event["details"]["solutions_context"] = solutions_context
        event["details"]["rag_sources"] = rag_sources
        event["details"]["source_context_chars"] = context_bundle.get("source_context_chars") or {}
        event["details"]["allowed_columns"] = link["allowed_columns"]
        event["details"]["allowed_objects"] = link["allowed_objects"]
        event["details"]["rag_timings"] = {
            "generation_context": ctx_timing,
            "generation_hits": hits_timing,
            "solutions_context": solutions_timing,
        }
    return {
        **state,
        "last_generation_context": context,
        "last_solutions_context": solutions_context,
        "allowed_tables": link["allowed_tables"],
        "allowed_columns": link["allowed_columns"],
        "allowed_objects": link["allowed_objects"],
    }


def _node_generate(state: PipelineState) -> PipelineState:
    """Узел generate. Зовет SQLGenerator и пишет в трассу полный last_call."""
    trace = state["trace"]
    generator = state["generator"]
    iteration = state.get("iteration", 0) + 1

    with trace.step(
        "generate",
        inputs={
            "iteration": iteration,
            "task": state["task"],
            "prior_sql_count": len(state.get("sql_history", [])),
        },
    ) as event:
        banned_identifiers = sorted(set(state.get("banned_identifiers", []) or []))
        blocking_findings = _prompt_blocking_findings(state)
        if blocking_findings:
            sql = _prompt_refusal_sql(blocking_findings)
            prior_sql = state.get("sql_history", [])[-1] if state.get("sql_history") else ""
            event["outputs"]["sql"] = sql
            event["outputs"]["candidate_count"] = 1
            event["outputs"]["selected_index"] = 0
            event["outputs"]["skipped_by_prompt_risk"] = True
            event["details"] = {
                "deterministic_sentinel": True,
                "reason": "prompt_risk_precheck",
                "prompt_risk_findings": [v.__dict__ for v in blocking_findings],
                "candidates": [
                    {
                        "candidate_index": 0,
                        "sql": sql,
                        "selected_by_selector": True,
                        "selector_score": {"is_sentinel": True, "blocked_by_prompt_risk": True},
                    }
                ],
                "selector_scores": [{"is_sentinel": True, "blocked_by_prompt_risk": True}],
                "ast_tree": trace_utils.ast_tree(sql),
                "diff": trace_utils.sql_diff(prior_sql, sql),
            }
        else:
            intent_block = intent_classifier.render_prompt_block(
                intent_classifier.Intent(
                    kind=state.get("intent_kind", "unknown") or "unknown",
                    confidence=float(state.get("intent_confidence", 0.0) or 0.0),
                    matched=tuple(state.get("intent_anchors", []) or ()),
                )
            )
            prior_sql = state.get("sql_history", [])[-1] if state.get("sql_history") else ""
            selector_ctx = {
                "task": state["task"],
                "schema_context": state.get("last_generation_context", ""),
                "allowed_tables": state.get("allowed_tables", []),
                "allowed_columns": state.get("allowed_columns", {}),
                "sensitive_fields": rag_adapter.get_sensitive_fields(),
                "banned_identifiers": banned_identifiers,
                "enforce_overlay": _strict_overlay_enabled(),
                "intent_kind": state.get("intent_kind", ""),
                "intent_confidence": state.get("intent_confidence", 0.0),
            }
            requirements = business_alignment.extract_requirements(state["task"], selector_ctx)
            requirement_dicts = business_alignment.requirements_to_dicts(requirements)
            selector_ctx["business_requirements"] = requirement_dicts

            deterministic_sql = generator_selector.literal_id_filter_candidate(selector_ctx)
            if deterministic_sql:
                candidates = [deterministic_sql]
                original_candidate_count = len(candidates)
                event["details"] = {
                    "deterministic_literal_id_candidate": True,
                    "generation_context": state.get("last_generation_context", ""),
                    "allowed_objects": state.get("allowed_objects", ""),
                    "candidates": [
                        {
                            "candidate_index": 0,
                            "sql": deterministic_sql,
                            "response": "",
                            "backend": "deterministic",
                            "model": "literal_id_filter",
                        }
                    ],
                }
            else:
                generated = generator.generate(
                    task_description=state["task"],
                    sql_history=state.get("sql_history", []),
                    audit_feedback=state.get("last_audit"),
                    iteration=iteration,
                    generation_context=state.get("last_generation_context", ""),
                    allowed_objects=state.get("allowed_objects", ""),
                    solutions_context=state.get("last_solutions_context", ""),
                    banned_identifiers=banned_identifiers,
                    intent_block=intent_block,
                    revision_feedback=state.get("last_revision_feedback", {}),
                )
                candidates = generated if isinstance(generated, list) else [generated]
                original_candidate_count = len(candidates)
                candidates = generator_selector.add_literal_id_repair_candidates(candidates, selector_ctx)
                event["details"] = generator.last_call

            selected = generator_selector.select_best_with_details(candidates, selector_ctx)
            sql = selected.sql
            event["outputs"]["sql"] = sql
            event["outputs"]["candidate_count"] = len(candidates)
            event["outputs"]["selected_index"] = selected.selected_index
            if len(candidates) > original_candidate_count:
                event["details"]["literal_id_repair_added"] = True
                detail_candidates = event["details"].setdefault("candidates", [])
                if isinstance(detail_candidates, list):
                    for idx in range(original_candidate_count, len(candidates)):
                        detail_candidates.append(
                            {
                                "candidate_index": idx,
                                "sql": candidates[idx],
                                "response": "",
                                "backend": "deterministic",
                                "model": "literal_id_repair",
                            }
                        )
            event["details"]["generate_candidates"] = candidates
            event["details"]["selector_scores"] = selected.scores
            event["details"]["selected_index"] = selected.selected_index
            event["details"]["business_requirements"] = requirement_dicts
            event["details"]["selector_reason"] = (
                selected.scores[selected.selected_index].get("selector_reason")
                if 0 <= selected.selected_index < len(selected.scores)
                else ""
            )
            detail_candidates = event["details"].get("candidates")
            if isinstance(detail_candidates, list):
                for idx, item in enumerate(detail_candidates):
                    if not isinstance(item, dict) or idx >= len(candidates):
                        continue
                    item.setdefault("candidate_index", idx)
                    item["selected_by_selector"] = idx == selected.selected_index
                    if idx < len(selected.scores):
                        item["selector_score"] = selected.scores[idx]
                        item["business_requirements"] = requirement_dicts
                        item["business_alignment_findings"] = selected.scores[idx].get(
                            "business_alignment_findings",
                            [],
                        )
                        item["selector_reason"] = selected.scores[idx].get("selector_reason", "")
            event["details"]["ast_tree"] = trace_utils.ast_tree(sql)
            event["details"]["diff"] = trace_utils.sql_diff(prior_sql, sql)

    history = list(state.get("sql_history", []))
    history.append(sql)
    detected = sentinel.detect(sql)
    policy_label = state.get("policy_label", "")
    policy_message = state.get("policy_message", "")
    if detected is not None:
        policy_label = detected.kind
        policy_message = detected.message or "policy sentinel without message"
    return {
        **state,
        "iteration": iteration,
        "sql_history": history,
        "last_sql": sql,
        "policy_label": policy_label,
        "policy_message": policy_message,
        "business_requirements": business_alignment.requirements_to_dicts(
            event.get("details", {}).get("business_requirements", [])
            if isinstance(event.get("details"), dict)
            else []
        ),
    }


def _node_sql_guard(state: PipelineState) -> PipelineState:
    """
    Узел быстрых правил. Прогоняет sql_guard, кладет в state список
    предварительных уязвимостей. Аудитор получит этот список как часть
    промпта, чтобы не дублировать его выводы.
    """
    trace = state["trace"]
    sql = state["last_sql"]
    sentinel_active = _refusal_policy_active(state)
    with trace.step("sql_guard", inputs={"sql_length": len(sql)}) as event:
        if sentinel_active:
            event["outputs"]["vuln_count"] = 0
            event["outputs"]["skipped_by_sentinel"] = True
            event["details"]["findings"] = []
            event["details"]["sentinel"] = {
                "policy_label": state.get("policy_label", ""),
                "policy_message": state.get("policy_message", ""),
            }
            return {**state, "last_guard_findings": []}
        findings = sql_guard.check(
            sql,
            {
                "task": state["task"],
                "schema_context": state.get("last_generation_context", ""),
                "allowed_tables": state.get("allowed_tables", []),
                "allowed_columns": state.get("allowed_columns", {}),
                "business_requirements": state.get("business_requirements", []),
                "enforce_overlay": _strict_overlay_enabled(),
                "intent_kind": state.get("intent_kind", ""),
                "intent_confidence": state.get("intent_confidence", 0.0),
            },
        )
        business_findings = [v for v in findings if business_alignment.is_business_label(v.vuln_class)]
        event["outputs"]["vuln_count"] = len(findings)
        event["details"]["findings"] = [v.__dict__ for v in findings]
        event["details"]["business_requirements"] = business_alignment.requirements_to_dicts(
            state.get("business_requirements", [])
        )
        event["details"]["business_alignment_findings"] = business_alignment.findings_to_dicts(
            business_findings
        )
        event["details"]["evidence"] = [
            {
                "label": v.vuln_class,
                "evidence_span": str(getattr(v, "evidence_span", "")),
                "layer": str(getattr(v, "layer", "rule")),
            }
            for v in findings
            if str(getattr(v, "evidence_span", ""))
        ]
        event["details"]["ast_tree"] = trace_utils.ast_tree(sql)
    return {**state, "last_guard_findings": findings}


def _node_explain_sandbox(state: PipelineState) -> PipelineState:
    """
    Узел EXPLAIN. Если правила уже нашли multi-statement или пустой SQL,
    в базу можно даже не ходить - все равно будет ошибка. Если все
    хорошо, прогоняем EXPLAIN с READ ONLY транзакцией.
    """
    trace = state["trace"]
    sql = state["last_sql"]
    findings = state.get("last_guard_findings", [])

    blocking_classes = {"MULTI_STATEMENT", "SYNTAX_BROKEN", "BROKEN_SQL", "UNBOUND_PLACEHOLDER"}
    blocked = next(
        (v for v in findings if v.vuln_class in blocking_classes or sql_guard.is_early_barrier_finding(v)),
        None,
    )
    sentinel_active = _refusal_policy_active(state)

    with trace.step("explain_sandbox", inputs={"sql_length": len(sql)}) as event:
        if sentinel_active:
            explain = explain_sandbox.ExplainResult(
                ok=True,
                plan=None,
                error=None,
                skipped=True,
            )
            event["outputs"]["skipped_by_sentinel"] = True
            event["outputs"]["ok"] = True
            event["outputs"]["skipped"] = True
            return {**state, "last_explain": explain, "last_explain_error": None}
        if blocked is not None:
            explain = explain_sandbox.ExplainResult(
                ok=False,
                plan=None,
                error="Пропущено из-за быстрых правил: " + blocked.vuln_class,
                skipped=True,
            )
            event["outputs"]["skipped_by_guard"] = True
        else:
            explain = explain_sandbox.run_explain(sql)
        event["outputs"]["ok"] = explain.ok
        event["outputs"]["skipped"] = explain.skipped
        event["details"]["plan"] = explain.plan
        event["details"]["error"] = explain.error
        # Phase 0.5 — sub-timings EXPLAIN-сэндбокса: connect/setup/execute/fetch.
        if explain.sub_timings:
            event["details"]["sub_timings"] = explain.sub_timings

    real_error = explain.error if (not explain.ok and not explain.skipped) else None
    return {**state, "last_explain": explain, "last_explain_error": real_error}


def _node_audit(state: PipelineState) -> PipelineState:
    """
    Узел гибридного аудита. Берет уже посчитанные guard findings и
    ошибку EXPLAIN (если была), внутри auditor обращается к языковой
    модели с RAG-контекстом по безопасности.
    """
    trace = state["trace"]
    auditor = state["auditor"]
    sql = state["last_sql"]
    explain_error = state.get("last_explain_error")
    sentinel_active = _refusal_policy_active(state)
    early_findings = [
        v for v in state.get("last_guard_findings", [])
        if sql_guard.is_early_barrier_finding(v)
    ]

    if sentinel_active:
        # H1+H2: для sentinel-ответа audit не нужен — pipeline уже знает
        # policy_label и формирует публичный refusal вместо бизнес-SQL.
        result = AuditResult(
            approved=False,
            vulnerabilities=[],
            overall_risk_score=0.0,
            summary="sentinel " + state.get("policy_label", ""),
        )
        result = _add_prompt_findings(result, state.get("prompt_risk_findings", []))
        setattr(result, "metadata", {"sentinel_policy_label": state.get("policy_label", "")})
        with trace.step(
            "audit",
            inputs={
                "iteration": state["iteration"],
                "sql_length": len(sql),
                "explain_error": explain_error,
                "skipped_by_sentinel": True,
            },
        ) as event:
            event["outputs"]["approved"] = False
            event["outputs"]["overall_risk_score"] = result.overall_risk_score
            event["outputs"]["vuln_count"] = len(result.vulnerabilities)
            event["outputs"]["skipped_by_sentinel"] = True
            event["details"]["prompt_risk_findings"] = [v.__dict__ for v in state.get("prompt_risk_findings", [])]
        log_entry = IterationLog(
            timestamp=datetime.now(timezone.utc),
            iteration=state["iteration"],
            sql_query=sql,
            audit_result=result,
            revision_notes="",
        )
        audits = list(state.get("audit_history", []))
        audits.append(result)
        log = list(state.get("iterations_log", []))
        log.append(log_entry)
        audit_storage.save_iteration(trace.request_id, log_entry)
        return {
            **state,
            "audit_history": audits,
            "iterations_log": log,
            "last_audit": result,
            "approved": False,
        }

    if early_findings:
        result = AuditResult(
            approved=False,
            vulnerabilities=early_findings,
            overall_risk_score=max((v.risk_score for v in early_findings), default=0.0),
            summary=(
                "Запрос отклонен ранним AST-барьером: "
                + ", ".join(sorted({v.vuln_class for v in early_findings}))
                + "."
            ),
        )
        security_risk, quality_risk = sql_guard.split_risk_scores(early_findings)
        setattr(result, "metadata", {
            "internal_labels": [],
            "security_risk_score": security_risk,
            "quality_risk_score": quality_risk,
            "early_barrier": True,
        })
        result = _add_prompt_findings(result, state.get("prompt_risk_findings", []))
        with trace.step(
            "audit",
            inputs={
                "iteration": state["iteration"],
                "sql_length": len(sql),
                "explain_error": explain_error,
                "skipped_by_early_barrier": True,
            },
        ) as event:
            event["outputs"]["approved"] = False
            event["outputs"]["overall_risk_score"] = result.overall_risk_score
            event["outputs"]["vuln_count"] = len(result.vulnerabilities)
            event["outputs"]["skipped_by_early_barrier"] = True
            event["details"] = {
                "grouped_auditor_enabled": False,
                "skipped_model_audit": True,
                "early_barrier_findings": [v.__dict__ for v in early_findings],
                "merged_findings": [v.__dict__ for v in result.vulnerabilities],
                "summary": result.summary,
            }
        log_entry = IterationLog(
            timestamp=datetime.now(timezone.utc),
            iteration=state["iteration"],
            sql_query=sql,
            audit_result=result,
            revision_notes="",
        )
        audits = list(state.get("audit_history", []))
        audits.append(result)
        log = list(state.get("iterations_log", []))
        log.append(log_entry)
        audit_storage.save_iteration(trace.request_id, log_entry)
        return {
            **state,
            "audit_history": audits,
            "iterations_log": log,
            "last_audit": result,
            "approved": False,
            "early_barrier_blocked": True,
            "early_barrier_labels": sorted({v.vuln_class for v in early_findings}),
        }

    with trace.step(
        "audit",
        inputs={
            "iteration": state["iteration"],
            "sql_length": len(sql),
            "explain_error": explain_error,
        },
    ) as event:
        result = auditor.audit(
            sql_query=sql,
            db_schema=None,
            explain_error=explain_error,
            task=state["task"],
            schema_context=state.get("last_generation_context", ""),
            allowed_tables=state.get("allowed_tables", []),
            allowed_columns=state.get("allowed_columns", {}),
        )
        result = _add_prompt_findings(result, state.get("prompt_risk_findings", []))
        event["outputs"]["approved"] = result.approved
        event["outputs"]["overall_risk_score"] = result.overall_risk_score
        event["outputs"]["vuln_count"] = len(result.vulnerabilities)
        event["details"] = auditor.last_call
        event["details"]["business_requirements"] = business_alignment.requirements_to_dicts(
            state.get("business_requirements", [])
        )
        event["details"]["business_alignment_findings"] = business_alignment.findings_to_dicts(
            [v for v in result.vulnerabilities if business_alignment.is_business_label(v.vuln_class)]
        )

    log_entry = IterationLog(
        timestamp=datetime.now(timezone.utc),
        iteration=state["iteration"],
        sql_query=sql,
        audit_result=result,
        revision_notes="",
    )
    audits = list(state.get("audit_history", []))
    audits.append(result)
    log = list(state.get("iterations_log", []))
    log.append(log_entry)
    audit_storage.save_iteration(trace.request_id, log_entry)

    banned = set(state.get("banned_identifiers", []) or [])
    banned |= _hallucinated_identifiers(result)
    return {
        **state,
        "audit_history": audits,
        "iterations_log": log,
        "last_audit": result,
        "approved": result.approved,
        "banned_identifiers": sorted(banned),
    }


def _node_decide(state: PipelineState) -> PipelineState:
    """
    Решающий узел с трихотомией approve / abstain / revise.

    approve - риск ниже порога, нет prompt-risk и hard blockers.
    revise - есть rule findings, которые генератор может исправить.
    abstain - нужна ручная проверка: low-confidence judge, internal
    labels, prompt-risk или исчерпан лимит итераций.
    """
    trace = state["trace"]
    audit = state.get("last_audit")
    vulns = list(audit.vulnerabilities if audit else [])
    prompt_blocked = any(v.risk_score >= 8.0 for v in state.get("prompt_risk_findings", []))
    sentinel_kind = state.get("policy_label", "") if state.get("policy_label") in _REFUSAL_POLICIES else ""
    security_risk, quality_risk = sql_guard.split_risk_scores(vulns)
    quality_only_block = bool(vulns) and security_risk <= 0 and quality_risk > 0
    approved = state.get("approved", False) and not prompt_blocked and not sentinel_kind
    early_barrier = bool(state.get("early_barrier_blocked", False))
    iteration = state.get("iteration", 0)
    max_iter = state.get("max_iterations", 5)
    low_judge = any(
        str(getattr(v, "layer", "")) == "judge"
        and float(getattr(v, "confidence", 1.0)) < 0.7
        for v in vulns
    )
    internal_labels = set((getattr(audit, "metadata", {}) or {}).get("internal_labels", [])) if audit else set()
    # BROKEN_SQL/SYNTAX_BROKEN are exactly the kind of concrete findings
    # the generator can fix on the next pass. Keep only uncertainty as a
    # human-stop before the max-iteration gate.
    has_uncertain_internal = bool(internal_labels & {"AUDIT_UNCERTAIN"})
    repeat_stop_reason = _repeat_stop_reason(state, audit)
    max_iter_unresolved = iteration >= max_iter and not approved
    abstain_reason = ""
    task_anchored_security_findings = [
        v
        for v in vulns
        if str(getattr(v, "detector", "")).endswith(".task_anchor")
        and sql_guard.label_bucket(str(getattr(v, "vuln_class", ""))) == "security"
        and float(getattr(v, "risk_score", 0.0) or 0.0) >= 6.0
    ]
    task_anchored_security = bool(task_anchored_security_findings)

    if sentinel_kind:
        decision = "refuse" if sentinel_kind in {_POLICY_REFUSAL_REQUIRED, _POLICY_PROMPT_BLOCKED} else "abstain"
        needs_human = sentinel_kind == _POLICY_INSUFFICIENT_CONTEXT
        human_reason = (
            "policy sentinel: " + sentinel_kind
            + (" — " + state.get("policy_message", "") if state.get("policy_message") else "")
        )
        policy_label = sentinel_kind
        abstain_reason = "generator_fail" if sentinel_kind == _POLICY_INSUFFICIENT_CONTEXT else "correct_block"
    elif approved:
        decision = "approve"
        needs_human = False
        human_reason = ""
        policy_label = _POLICY_APPROVE
    elif prompt_blocked:
        decision = "refuse"
        needs_human = False
        human_reason = "prompt-risk заблокирован precheck"
        policy_label = _POLICY_PROMPT_BLOCKED
        abstain_reason = "correct_block"
    elif early_barrier:
        decision = "abstain"
        needs_human = False
        human_reason = "ранний AST-барьер: " + ", ".join(state.get("early_barrier_labels", []))
        policy_label = _POLICY_HARD_FAIL
        abstain_reason = "generator_fail"
    elif task_anchored_security:
        decision = "refuse"
        needs_human = False
        human_reason = "security attack embedded in task; revise cannot fix"
        policy_label = _POLICY_REFUSAL_REQUIRED
        abstain_reason = "correct_block"
    elif quality_only_block and not low_judge:
        decision = "approve"
        approved = True
        needs_human = False
        human_reason = "quality-only findings returned as advisory"
        policy_label = _POLICY_APPROVE_WITH_QUALITY
    elif low_judge:
        decision = "abstain"
        needs_human = True
        human_reason = "semantic judge вернул confidence ниже 0.7"
        policy_label = _POLICY_AUDIT_UNCERTAIN
        abstain_reason = "low_confidence"
    elif has_uncertain_internal:
        decision = "abstain"
        needs_human = True
        human_reason = "internal audit labels: " + ", ".join(sorted(internal_labels))
        policy_label = _POLICY_AUDIT_UNCERTAIN
        abstain_reason = "low_confidence"
    elif repeat_stop_reason:
        decision = "abstain"
        needs_human = True
        human_reason = repeat_stop_reason
        policy_label = _POLICY_REPEAT_STOP
        abstain_reason = _failure_abstain_reason(state, audit)
    elif max_iter_unresolved:
        decision = "abstain"
        needs_human = True
        human_reason = "достигнут лимит итераций с unresolved findings"
        policy_label = _POLICY_MAX_ITERATIONS
        abstain_reason = "max_iter"
    else:
        decision = "revise"
        needs_human = False
        human_reason = ""
        policy_label = _POLICY_REVISE_NEEDED

    with trace.step(
        "decide",
        inputs={
            "approved": approved,
            "prompt_blocked": prompt_blocked,
            "security_risk": security_risk,
            "quality_risk": quality_risk,
            "quality_only_block": quality_only_block,
            "early_barrier": early_barrier,
            "low_judge": low_judge,
            "has_uncertain_internal": has_uncertain_internal,
            "task_anchored_security": task_anchored_security,
            "repeat_stop_reason": repeat_stop_reason,
            "internal_labels": sorted(internal_labels),
            "iteration": iteration,
            "max_iterations": max_iter,
            "failure_signature": _failure_signature(state, audit),
        },
    ) as event:
        event["outputs"]["decision"] = decision
        event["outputs"]["needs_human"] = needs_human
        event["outputs"]["human_reason"] = human_reason
        event["outputs"]["abstain_reason"] = abstain_reason
        event["details"]["task_anchored_security_findings"] = [
            {
                "vuln_class": str(getattr(v, "vuln_class", "")),
                "risk_score": float(getattr(v, "risk_score", 0.0) or 0.0),
                "detector": str(getattr(v, "detector", "")),
                "evidence_span": str(getattr(v, "evidence_span", "")),
            }
            for v in task_anchored_security_findings
        ]

    return {
        **state,
        "approved": approved if decision == "approve" else False,
        "decision": decision,
        "needs_human": needs_human,
        "human_reason": human_reason,
        "abstain_reason": abstain_reason,
        "policy_label": policy_label,
    }


def _repeat_stop_reason(state: PipelineState, audit: AuditResult | None) -> str:
    """Stop deterministic retry loops when the same defect repeats."""
    if state.get("approved", False):
        return ""
    history = [sql.strip() for sql in state.get("sql_history", []) if sql.strip()]
    if len(history) >= 2 and history[-1] == history[-2]:
        return "остановлен повтор того же SQL без прогресса"

    if audit is None:
        return ""
    signature = _failure_signature(state, audit)
    signature_key = _failure_signature_key(signature)
    if signature_key and signature_key in set(state.get("failure_signatures", []) or []):
        labels = ", ".join(signature.get("labels") or [])
        return "остановлен повтор failure signature: " + (labels or "same_error")
    current = _hallucinated_identifiers(audit)
    if not current:
        return ""
    previous = set()
    for item in state.get("audit_history", [])[:-1]:
        previous |= _hallucinated_identifiers(item)
    repeated = sorted(current & previous)
    if repeated:
        return "остановлен повтор запрещенных identifiers: " + ", ".join(repeated[:8])
    return ""


def _hallucinated_identifiers(audit: AuditResult) -> set[str]:
    identifiers: set[str] = set()
    for vuln in audit.vulnerabilities:
        if vuln.vuln_class not in {"HALLUCINATED_TABLE", "HALLUCINATED_COLUMN"}:
            continue
        evidence = str(getattr(vuln, "evidence_span", "") or vuln.description)
        for item in re.split(r"[,;\s]+", evidence):
            clean = item.strip().strip(".:()[]{}\"'`").lower()
            if clean and re.match(r"^[a-z_][a-z0-9_\.]*$", clean):
                identifiers.add(clean)
    return identifiers


def _add_prompt_findings(result: AuditResult, prompt_findings: list[Vulnerability]) -> AuditResult:
    """Merge prompt-level findings into the audit result for trace and audit_log."""
    if not prompt_findings:
        return result

    by_label: dict[str, Vulnerability] = {v.vuln_class: v for v in result.vulnerabilities}
    for vuln in prompt_findings:
        current = by_label.get(vuln.vuln_class)
        if current is None or vuln.risk_score > current.risk_score:
            by_label[vuln.vuln_class] = vuln

    merged = list(by_label.values())
    overall = max(result.overall_risk_score, max((v.risk_score for v in prompt_findings), default=0.0))
    prompt_blocked = any(v.risk_score >= 8.0 for v in prompt_findings)
    summary = result.summary
    if prompt_blocked:
        labels = ", ".join(v.vuln_class for v in prompt_findings if v.risk_score >= 8.0)
        summary = summary + " Prompt precheck blocked: " + labels + "."

    updated = AuditResult(
        approved=result.approved and not prompt_blocked,
        vulnerabilities=merged,
        overall_risk_score=overall,
        summary=summary,
    )
    metadata = getattr(result, "metadata", {})
    metadata["prompt_risk_labels"] = [v.vuln_class for v in prompt_findings]
    setattr(updated, "metadata", metadata)
    return updated


def _node_revise(state: PipelineState) -> PipelineState:
    """
    Подготовка заметок для следующей попытки. Заметки прицепляются к
    логу той итерации, после которой принято решение переписывать -
    чтобы в отчете было видно, что именно сказали поправить.
    """
    trace = state["trace"]
    audit = state.get("last_audit")
    notes = (audit.summary if audit else "Нет деталей.") or "Нет деталей."
    feedback = _build_revise_feedback(state, audit)
    signature_key = _failure_signature_key(_failure_signature(state, audit))

    with trace.step("revise", inputs={"iteration": state["iteration"]}) as event:
        event["outputs"]["notes"] = notes
        event["outputs"]["structured_feedback"] = feedback

    log = list(state.get("iterations_log", []))
    revision_notes = json.dumps(feedback, ensure_ascii=False, sort_keys=True)
    if log:
        last = log[-1]
        log[-1] = IterationLog(
            timestamp=last.timestamp,
            iteration=last.iteration,
            sql_query=last.sql_query,
            audit_result=last.audit_result,
            revision_notes=revision_notes,
        )
        audit_storage.save_iteration(trace.request_id, log[-1])
    signatures = list(state.get("failure_signatures", []) or [])
    if signature_key:
        signatures.append(signature_key)
    banned = sorted(set(state.get("banned_identifiers", []) or []) | set(feedback.get("forbidden_identifiers") or []))
    return {
        **state,
        "iterations_log": log,
        "last_revision_feedback": feedback,
        "failure_signatures": signatures,
        "banned_identifiers": banned,
    }


def _route_after_decide(state: PipelineState) -> str:
    """Условный переход после decide-узла."""
    decision = state.get("decision", "abstain")
    if decision in {"approve", "abstain", "refuse"}:
        return "end"
    return "revise"


def _build_graph():
    """
    Собрать граф один раз. Линейный путь по нодам, обратная edge от
    revise к retrieve - чтобы новая итерация заново подтянула актуальный
    контекст под измененный по замечаниям SQL.
    """
    graph = StateGraph(PipelineState)
    graph.add_node("intent_classify", _node_intent_classify)
    graph.add_node("prompt_check", _node_prompt_check)
    graph.add_node("retrieve", _node_retrieve)
    graph.add_node("generate", _node_generate)
    graph.add_node("sql_guard", _node_sql_guard)
    graph.add_node("explain_sandbox", _node_explain_sandbox)
    graph.add_node("audit", _node_audit)
    graph.add_node("decide", _node_decide)
    graph.add_node("revise", _node_revise)

    graph.set_entry_point("intent_classify")
    graph.add_edge("intent_classify", "prompt_check")
    graph.add_edge("prompt_check", "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "sql_guard")
    graph.add_edge("sql_guard", "explain_sandbox")
    graph.add_edge("explain_sandbox", "audit")
    graph.add_edge("audit", "decide")
    graph.add_conditional_edges(
        "decide",
        _route_after_decide,
        {"revise": "revise", "end": END},
    )
    graph.add_edge("revise", "retrieve")

    return graph.compile()


def build_graph():
    """Public graph builder for smoke checks and architecture verification."""
    return _build_graph()


_COMPILED_GRAPH = None


def _graph():
    """Лениво собрать граф - один раз на процесс."""
    global _COMPILED_GRAPH
    if _COMPILED_GRAPH is None:
        _COMPILED_GRAPH = _build_graph()
    return _COMPILED_GRAPH


def _runtime_error_result(
    exc: BaseException,
    trace: Trace,
    started: float,
    mode_info: dict[str, str],
) -> SystemResult:
    """Собрать SystemResult для непредвиденной ошибки графа без потери трассы."""
    trace.attach_error(exc)
    trace.save()
    result = SystemResult(
        final_sql="",
        approved=False,
        iterations_used=0,
        iterations_log=[],
        audit_log=(
            "Произошла ошибка во время прогона: " + str(exc)
            + "\nРежим LLM: " + mode_info.get("mode", "?")
        ),
        metadata={
            "duration_sec": round(time.time() - started, 3),
            "trace_id": trace.request_id,
            "task": trace.task,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
            **mode_info,
        },
    )
    audit_storage.save_run(result, trace.request_id, mode_info.get("mode", ""))
    return result


class SQLSecuritySystem(_BaseSystem):
    """Реализация цикла поверх LangGraph. Сохраняет сигнатуру run() из контракта."""

    def run(self, task_description: str) -> SystemResult:
        """
        Прогнать полный цикл генерации и проверки. Возвращает SystemResult.
        Конфигурационные ошибки поднимаются наружу (LLMConfigError) -
        API-слой превращает их в HTTP 400. Прочие runtime-ошибки графа
        пишутся в трассу и возвращаются как failed SystemResult.
        """
        trace = Trace(task=task_description)
        started = time.time()
        mode_info = llm_provider.describe_current_mode()
        audit_storage.ensure_run(
            trace.request_id,
            task_description,
            mode_info.get("mode", ""),
        )

        initial_state: PipelineState = {
            "task": task_description,
            "iteration": 0,
            "max_iterations": self.max_iterations,
            "sql_history": [],
            "audit_history": [],
            "prompt_risk_findings": [],
            "iterations_log": [],
            "last_sql": "",
            "last_audit": None,
            "last_explain": None,
            "last_explain_error": None,
            "last_guard_findings": [],
            "last_generation_context": "",
            "last_solutions_context": "",
            "last_revision_feedback": {},
            "failure_signatures": [],
            "isolation_mode": os.environ.get("PIPELINE_ISOLATION", "production"),
            "allowed_tables": [],
            "allowed_columns": {},
            "allowed_objects": "",
            "approved": False,
            "decision": "",
            "needs_human": False,
            "human_reason": "",
            "abstain_reason": "",
            "policy_label": "",
            "policy_message": "",
            "banned_identifiers": [],
            "intent_kind": "",
            "intent_confidence": 0.0,
            "intent_anchors": [],
            "trace": trace,
            "generator": self.generator,
            "auditor": self.auditor,
        }

        # Каждый цикл итерации - восемь узлов. Ставим запас, чтобы пять
        # итераций не упирались в лимит рекурсии LangGraph.
        try:
            with llm_provider.request_context(trace.request_id):
                final_state = _graph().invoke(
                    initial_state,
                    config={"recursion_limit": self.max_iterations * 9 + 4},
                )
        except LLMConfigError as exc:
            # Конфигурационные ошибки не глотаем - они должны дойти до
            # API-слоя и превратиться в HTTP 400, чтобы клиент починил .env.
            trace.attach_error(exc)
            trace.save()
            raise
        except ProviderUnavailable as exc:
            # Недоступный провайдер не превращаем в failed SystemResult:
            # API должен быстро вернуть HTTP 503 без авто-fallback.
            trace.attach_error(exc)
            trace.save()
            raise
        except (RuntimeError, ValueError) as exc:
            return _runtime_error_result(exc, trace, started, mode_info)

        elapsed = round(time.time() - started, 3)
        iterations_log: list[IterationLog] = final_state.get("iterations_log", [])
        last_audit = final_state.get("last_audit")
        internal_final_sql = final_state.get("last_sql", "")
        approved = bool(final_state.get("approved", False))
        policy_label = str(final_state.get("policy_label", "") or "")
        policy_message = str(final_state.get("policy_message", "") or "")
        is_refusal_policy = policy_label in _REFUSAL_POLICIES
        is_sentinel_policy = is_refusal_policy
        final_sql = internal_final_sql if (approved or is_refusal_policy) else ""
        needs_human = bool(final_state.get("needs_human", False))
        human_reason = final_state.get("human_reason", "")
        abstain_reason = str(final_state.get("abstain_reason", "") or "")
        if is_sentinel_policy and not human_reason:
            human_reason = (
                "policy " + policy_label
                + (" — " + policy_message if policy_message else "")
            )

        refusal_message = ""
        if policy_label == _POLICY_REFUSAL_REQUIRED:
            refusal_message = policy_message or "Запрос отклонён по policy."
        elif policy_label == _POLICY_INSUFFICIENT_CONTEXT:
            refusal_message = (
                "Недостаточно контекста для безопасного SQL: "
                + (policy_message or "схема/правила не определены.")
            )
        elif policy_label == _POLICY_PROMPT_BLOCKED:
            refusal_message = (
                policy_message
                or "Запрос отклонён: обнаружена попытка инъекции или обхода правил."
            )
        elif policy_label == _POLICY_AUDIT_UNCERTAIN:
            refusal_message = "Аудит не уверен в безопасности запроса, требуется ручная проверка."
        elif policy_label == _POLICY_MAX_ITERATIONS:
            refusal_message = "За лимит итераций не удалось получить SQL, отвечающий правилам pipeline."
        elif policy_label == _POLICY_REPEAT_STOP:
            refusal_message = human_reason or "Pipeline остановлен: повторяется один и тот же неуспешный SQL."

        if last_audit is not None:
            vulns_for_split = list(getattr(last_audit, "vulnerabilities", []) or [])
        else:
            vulns_for_split = []
        security_risk, quality_risk = sql_guard.split_risk_scores(vulns_for_split)

        report = audit_log.render(
            task=task_description,
            iterations_log=iterations_log,
            approved=approved,
            final_sql=final_sql,
            mode_info=mode_info,
            include_sql=approved,
        )
        if refusal_message:
            report = report + "\n\n" + refusal_message
        elif needs_human:
            report = (
                report
                + "\n\nЗапрос требует ручной проверки. Причина: "
                + (human_reason or "abstain decision")
                + "."
            )

        result = SystemResult(
            final_sql=final_sql,
            approved=approved,
            iterations_used=final_state.get("iteration", 0),
            iterations_log=iterations_log,
            audit_log=report,
            metadata={
                "duration_sec": elapsed,
                "trace_id": trace.request_id,
                "task": task_description,
                "decision": final_state.get("decision", ""),
                "policy_label": policy_label,
                "policy_message": policy_message,
                "refusal_message": refusal_message,
                "security_risk_score": round(security_risk, 3),
                "quality_risk_score": round(quality_risk, 3),
                "banned_identifiers": sorted(set(final_state.get("banned_identifiers", []) or [])),
                "isolation_mode": final_state.get("isolation_mode", os.environ.get("PIPELINE_ISOLATION", "production")),
                "needs_human": needs_human,
                "human_reason": human_reason,
                "abstain_reason": abstain_reason,
                "internal_final_sql_len": len(internal_final_sql),
                "public_sql_hidden": bool(internal_final_sql and not approved and not is_refusal_policy),
                "quality_advisories": [
                    v.__dict__
                    for v in vulns_for_split
                    if sql_guard.label_bucket(str(getattr(v, "vuln_class", ""))) == "quality"
                ],
                **mode_info,
            },
        )

        trace.attach_result(
            {
                "approved": result.approved,
                "iterations_used": result.iterations_used,
                "overall_risk_score": last_audit.overall_risk_score if last_audit else None,
                "final_sql": result.final_sql,
                "audit_log": result.audit_log,
                "metadata": result.metadata,
            }
        )
        trace.save()
        audit_storage.save_run(result, trace.request_id, mode_info.get("mode", ""))

        return result
