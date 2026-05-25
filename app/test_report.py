"""
Standalone HTML-отчет для тестового Telegram-бота.

Модуль принимает SystemResult и JSON-трассу одного pipeline run,
собирает читаемую сводку, diff, AST, EXPLAIN и полный список событий.
Результат сохраняется локально и отправляется ботом как HTML-файл.
"""

from __future__ import annotations

import json
import os
import platform
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app import prompt_trace, trace as trace_utils


MAX_BLOCK_CHARS = 100_000
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_SQL_EVENT_SPECS_PATH = Path(__file__).resolve().parent / "static" / "audit_reviews" / "sql_event_specs.json"
BUSINESS_ALIGNMENT_LABELS = {"MISSING_REQUIRED_FILTER", "BUSINESS_MISMATCH"}


@dataclass
class TestRun:
    run_id: str
    user_id: int
    user_name: str
    task: str
    model_key: str
    model_label: str
    llm_mode: str
    llm_generator_model: str
    started_at: datetime
    finished_at: datetime
    system_result: dict[str, Any]
    trace: dict[str, Any]


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return _safe(asdict(value))
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe(v) for v in value]
    if hasattr(value, "__dict__"):
        return _safe(vars(value))
    return repr(value)


def _json_text(value: Any) -> str:
    text = json.dumps(_safe(value), ensure_ascii=False, indent=2)
    return _clip(text)


def _sql_event_specs() -> dict[str, Any]:
    try:
        data = json.loads(_SQL_EVENT_SPECS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"events": {}}
    return data if isinstance(data, dict) else {"events": {}}


def _clip(text: str, limit: int = MAX_BLOCK_CHARS) -> str:
    if len(text) <= limit:
        return text
    hidden = len(text) - limit
    return text[:limit] + "\n\n[обрезано " + str(hidden) + " символов, полный блок см. в JSON-трассе]"


def _events(trace: dict[str, Any]) -> list[dict[str, Any]]:
    raw = trace.get("events") or []
    return [item for item in raw if isinstance(item, dict)]


def _metadata(run: TestRun) -> dict[str, Any]:
    meta = run.system_result.get("metadata") or {}
    return meta if isinstance(meta, dict) else {}


def _final_sql(run: TestRun) -> str:
    value = run.system_result.get("final_sql")
    if isinstance(value, str):
        return value
    result = run.trace.get("result") or {}
    if isinstance(result, dict) and isinstance(result.get("final_sql"), str):
        return result["final_sql"]
    return ""


def _verdict(run: TestRun) -> str:
    meta = _metadata(run)
    if meta.get("needs_human"):
        return "abstain"
    decision = str(meta.get("decision") or "").strip()
    if decision:
        return "approved" if decision == "approve" else decision
    return "approved" if run.system_result.get("approved") else "rejected"


def _risk_items(run: TestRun) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    max_risk: defaultdict[str, float] = defaultdict(float)

    def add(item: Any) -> None:
        if not isinstance(item, dict):
            return
        label = str(item.get("vuln_class") or item.get("label") or "").strip()
        if not label:
            return
        counts[label] += 1
        try:
            risk = float(item.get("risk_score") or item.get("severity") or 0)
        except (TypeError, ValueError):
            risk = 0.0
        max_risk[label] = max(max_risk[label], risk)

    for entry in run.system_result.get("iterations_log") or []:
        if not isinstance(entry, dict):
            continue
        audit = entry.get("audit_result") or {}
        if isinstance(audit, dict):
            for item in audit.get("vulnerabilities") or []:
                add(item)

    for event in _events(run.trace):
        details = event.get("details") or {}
        if not isinstance(details, dict):
            continue
        for key in ("merged_findings", "rule_findings", "model_findings", "findings"):
            for item in details.get(key) or []:
                add(item)

    rows = [
        {"label": label, "count": counts[label], "max_risk": max_risk[label]}
        for label in counts
    ]
    return sorted(rows, key=lambda item: (-item["count"], -item["max_risk"], item["label"]))[:3]


def _event_outcome(event: dict[str, Any]) -> str:
    outputs = event.get("outputs") or {}
    if not isinstance(outputs, dict):
        return ""
    if "decision" in outputs:
        return str(outputs.get("decision"))
    if "approved" in outputs:
        return "approved=" + str(outputs.get("approved"))
    if "vuln_count" in outputs:
        return "vuln_count=" + str(outputs.get("vuln_count"))
    if "ok" in outputs:
        return "ok=" + str(outputs.get("ok"))
    if "candidate_count" in outputs:
        return (
            "candidates="
            + str(outputs.get("candidate_count"))
            + ", selected="
            + str(outputs.get("selected_index"))
        )
    if "context_length" in outputs:
        return "context_length=" + str(outputs.get("context_length"))
    if "notes" in outputs:
        return "notes prepared"
    return ""


def _node_rows(run: TestRun) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_iter = 1
    for event in _events(run.trace):
        inputs = event.get("inputs") or {}
        if isinstance(inputs, dict) and inputs.get("iteration"):
            try:
                current_iter = int(inputs["iteration"])
            except (TypeError, ValueError):
                current_iter = current_iter
        rows.append(
            {
                "iter": current_iter,
                "node": event.get("node", ""),
                "ms": int(float(event.get("duration_sec") or 0) * 1000),
                "sec": round(float(event.get("duration_sec") or 0), 3),
                "outcome": _event_outcome(event),
            }
        )
    return rows


def _trace_duration_sec(run: TestRun) -> float:
    try:
        value = float(run.trace.get("duration_sec") or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value > 0:
        return round(value, 3)
    return _duration_sec(run)


def _timing_rows(run: TestRun) -> list[dict[str, Any]]:
    rows = _node_rows(run)
    total = sum(float(row["sec"]) for row in rows)
    max_sec = max((float(row["sec"]) for row in rows), default=0.0)
    for row in rows:
        sec = float(row["sec"])
        row["pct"] = round((sec / total * 100.0), 1) if total > 0 else 0.0
        row["bar_width"] = round((sec / max_sec * 100.0), 1) if max_sec > 0 else 0.0
    return rows


def _diffs(run: TestRun) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in _events(run.trace):
        details = event.get("details") or {}
        inputs = event.get("inputs") or {}
        if not isinstance(details, dict):
            continue
        diff = details.get("diff")
        if not diff:
            continue
        rows.append(
            {
                "title": "iteration " + str(inputs.get("iteration", "")),
                "text": str(diff),
            }
        )
    return rows


def _ast_text(sql: str) -> str:
    if not sql.strip():
        return ""
    return _json_text(trace_utils.ast_tree(sql))


def _ast_data(sql: str) -> dict[str, Any]:
    if not sql.strip():
        return {"ok": False, "error": "empty sql", "tree": None}
    return trace_utils.ast_tree(sql)


def _explain_text(run: TestRun) -> str:
    for event in _events(run.trace):
        if event.get("node") != "explain_sandbox":
            continue
        details = event.get("details") or {}
        if not isinstance(details, dict):
            continue
        plan = details.get("plan")
        error = details.get("error")
        if plan:
            return _clip(str(plan))
        if error:
            return _clip(str(error))
    return ""


def _explain_json(run: TestRun, explain_text: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for index, event in enumerate(_events(run.trace), 1):
        if event.get("node") != "explain_sandbox":
            continue
        details = event.get("details") or {}
        outputs = event.get("outputs") or {}
        rows.append(
            {
                "event_index": index,
                "started_at": event.get("started_at"),
                "duration_sec": event.get("duration_sec"),
                "ok": outputs.get("ok") if isinstance(outputs, dict) else None,
                "skipped": outputs.get("skipped") if isinstance(outputs, dict) else None,
                "plan": details.get("plan") if isinstance(details, dict) else None,
                "error": details.get("error") if isinstance(details, dict) else None,
                "sub_timings": details.get("sub_timings") if isinstance(details, dict) else {},
            }
        )
    return {
        "plan_text": explain_text,
        "events": rows,
        "event_count": len(rows),
    }


def _rag_blocks(run: TestRun) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for index, event in enumerate(_events(run.trace), 1):
        details = event.get("details") or {}
        if not isinstance(details, dict):
            continue
        sources = details.get("rag_sources") or {}
        if isinstance(sources, dict) and sources:
            blocks.append(
                {
                    "title": "context sources",
                    "node": event.get("node", ""),
                    "event_index": index,
                    "sources": sources,
                    "hits": [],
                }
            )
        for key, title in (
            ("rag_generation_hits", "generation RAG hits"),
            ("rag_security_hits", "security RAG hits"),
        ):
            hits = details.get(key) or []
            if hits:
                blocks.append(
                    {
                        "title": title,
                        "node": event.get("node", ""),
                        "event_index": index,
                        "hits": hits,
                    }
                )
    return blocks


def _duration_sec(run: TestRun) -> float:
    return round((run.finished_at - run.started_at).total_seconds(), 3)


_STEP_LABELS = {
    "prompt_check": "Request Ingestion",
    "retrieve": "RAG Retrieval",
    "generate": "SQL Generation",
    "sql_guard": "Guardrails & Policy Checks",
    "explain_sandbox": "Finalization & Explain",
    "audit": "Validation",
    "decide": "Decision",
    "revise": "Revision Notes",
}

_STEP_COLORS = {
    "prompt_check": "#22c55e",
    "retrieve": "#60a5fa",
    "generate": "#22c55e",
    "sql_guard": "#2563eb",
    "explain_sandbox": "#64748b",
    "audit": "#16a34a",
    "decide": "#8b97aa",
    "revise": "#94a3b8",
}

_PII_LABEL_PARTS = (
    "PII",
    "SENSITIVE",
    "PERSONAL",
    "EMAIL",
    "PHONE",
    "PASSPORT",
    "INN",
    "DIRECT_SENSITIVE",
    "SELECT_STAR",
)


def _fmt_sec(value: Any) -> str:
    try:
        sec = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if sec >= 100:
        return f"{sec:.1f}s"
    return f"{sec:.3f}s"


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _fmt_count(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if number >= 1000:
        return f"{number / 1000:.1f}k"
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}"


def _fmt_time(value: Any) -> str:
    if isinstance(value, datetime):
        item = value
    elif isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            item = datetime.fromisoformat(raw)
        except ValueError:
            return value
    else:
        return "unknown"
    if item.tzinfo is None:
        item = item.replace(tzinfo=timezone.utc)
    item = item.astimezone(timezone.utc)
    return item.strftime("%b %d, %Y %H:%M:%S UTC")


def _status_label(run: TestRun, verdict: str) -> str:
    if run.trace.get("error") or run.system_result.get("error"):
        return "failed"
    if _metadata(run).get("needs_human") or verdict in {"abstain", "revise"}:
        return "abstain"
    if verdict in {"approved", "approve", "success"}:
        return "success"
    return "failed"


def _status_text(status: str) -> str:
    if status == "success":
        return "COMPLETED"
    if status == "abstain":
        return "NEEDS REVIEW"
    return "FAILED"


def _verdict_label(status: str) -> str:
    if status == "success":
        return "SAFE"
    if status == "abstain":
        return "REVIEW"
    return "BLOCKED"


def _risk_score(run: TestRun) -> float | None:
    for value in (
        run.system_result.get("overall_risk_score"),
        (run.trace.get("result") or {}).get("overall_risk_score")
        if isinstance(run.trace.get("result"), dict)
        else None,
        _metadata(run).get("overall_risk_score"),
    ):
        try:
            if value is not None:
                return round(float(value), 2)
        except (TypeError, ValueError):
            pass

    values: list[float] = []
    for event in _events(run.trace):
        for block_name in ("outputs", "details"):
            block = event.get(block_name) or {}
            if not isinstance(block, dict):
                continue
            try:
                value = block.get("overall_risk_score")
                if value is not None:
                    values.append(float(value))
            except (TypeError, ValueError):
                pass
    if not values:
        return None
    return round(max(values), 2)


def _final_iteration(run: TestRun) -> int:
    try:
        value = int(run.system_result.get("iterations_used") or 0)
    except (TypeError, ValueError):
        value = 0
    if value > 0:
        return value
    for event in reversed(_events(run.trace)):
        inputs = event.get("inputs") or {}
        if isinstance(inputs, dict) and inputs.get("iteration"):
            try:
                return int(inputs["iteration"])
            except (TypeError, ValueError):
                pass
    return 0


def _final_attempt_events(run: TestRun) -> list[dict[str, Any]]:
    events = _events(run.trace)
    if not events:
        return []
    final_iter = _final_iteration(run)
    start = -1
    for idx, event in enumerate(events):
        if event.get("node") != "generate":
            continue
        inputs = event.get("inputs") or {}
        if not isinstance(inputs, dict):
            continue
        try:
            iteration = int(inputs.get("iteration") or 0)
        except (TypeError, ValueError):
            iteration = 0
        if final_iter <= 0 or iteration == final_iter:
            start = idx
    if start < 0:
        return []
    out: list[dict[str, Any]] = []
    for event in events[start:]:
        if out and event.get("node") == "generate":
            break
        if out and event.get("node") == "revise":
            break
        out.append(event)
        if event.get("node") == "decide":
            break
    return out


def _final_findings(run: TestRun) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(item: Any, source: str) -> None:
        if not isinstance(item, dict):
            return
        label = str(item.get("vuln_class") or item.get("label") or "").strip()
        if not label:
            return
        row = dict(item)
        row["label"] = label
        row["source"] = source
        rows.append(row)

    final_iter = _final_iteration(run)
    for entry in run.system_result.get("iterations_log") or []:
        if not isinstance(entry, dict):
            continue
        try:
            iteration = int(entry.get("iteration") or 0)
        except (TypeError, ValueError):
            iteration = 0
        if final_iter > 0 and iteration != final_iter:
            continue
        audit = entry.get("audit_result") or {}
        if isinstance(audit, dict):
            for item in audit.get("vulnerabilities") or []:
                add(item, "system_result.final_iteration")

    for event in _final_attempt_events(run):
        details = event.get("details") or {}
        if not isinstance(details, dict):
            continue
        node = str(event.get("node") or "event")
        for key in ("merged_findings", "rule_findings", "model_findings", "findings"):
            for item in details.get(key) or []:
                add(item, node + "." + key)
        for item in details.get("business_alignment_findings") or []:
            add(item, node + ".business_alignment_findings")
    return rows


def _risk_level(score: float | None) -> str:
    if score is None:
        return "Unknown"
    if score <= 3:
        return "Low"
    if score <= 6:
        return "Medium"
    return "High"


def _findings(run: TestRun) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(item: Any, source: str) -> None:
        if not isinstance(item, dict):
            return
        label = str(item.get("vuln_class") or item.get("label") or "").strip()
        if not label:
            return
        row = dict(item)
        row["label"] = label
        row["source"] = source
        rows.append(row)

    for entry in run.system_result.get("iterations_log") or []:
        if not isinstance(entry, dict):
            continue
        audit = entry.get("audit_result") or {}
        if not isinstance(audit, dict):
            continue
        for item in audit.get("vulnerabilities") or []:
            add(item, "system_result.iterations_log")

    for event in _events(run.trace):
        details = event.get("details") or {}
        if not isinstance(details, dict):
            continue
        node = str(event.get("node") or "event")
        for key in ("merged_findings", "rule_findings", "model_findings", "findings"):
            for item in details.get(key) or []:
                add(item, node + "." + key)
    return rows


def _business_alignment(run: TestRun, findings: list[dict[str, Any]]) -> dict[str, Any]:
    requirements: list[dict[str, Any]] = []
    alignment_findings: list[dict[str, Any]] = []
    seen_req: set[str] = set()
    seen_finding: set[str] = set()

    def add_req(item: Any) -> None:
        if not isinstance(item, dict):
            return
        key = json.dumps(_safe(item), ensure_ascii=False, sort_keys=True)
        if key in seen_req:
            return
        seen_req.add(key)
        requirements.append(dict(item))

    def add_finding(item: Any, source: str) -> None:
        if not isinstance(item, dict):
            return
        label = str(item.get("vuln_class") or item.get("label") or "").strip()
        if label not in BUSINESS_ALIGNMENT_LABELS:
            return
        row = dict(item)
        row["label"] = label
        row.setdefault("source", source)
        key = json.dumps(_safe(row), ensure_ascii=False, sort_keys=True)
        if key in seen_finding:
            return
        seen_finding.add(key)
        alignment_findings.append(row)

    scoped_events = _final_attempt_events(run) or _events(run.trace)
    for event in scoped_events:
        details = event.get("details") or {}
        if not isinstance(details, dict):
            continue
        source = str(event.get("node") or "event")
        for item in details.get("business_requirements") or []:
            add_req(item)
        for item in details.get("business_alignment_findings") or []:
            add_finding(item, source + ".business_alignment_findings")

    final_events = _final_attempt_events(run)
    scoped_findings = _final_findings(run) if final_events else findings
    for item in scoped_findings:
        add_finding(item, str(item.get("source") or "findings"))

    labels = sorted({str(item.get("label") or item.get("vuln_class")) for item in alignment_findings})
    if alignment_findings:
        label = "Blocked"
        sub = ", ".join(labels[:2])
        tone = "bad"
    elif requirements:
        label = "OK"
        sub = str(len(requirements)) + " requirement(s)"
        tone = "ok"
    else:
        label = "N/A"
        sub = "No extracted requirements"
        tone = "neutral"
    return {
        "label": label,
        "sub": sub,
        "tone": tone,
        "requirements": requirements,
        "findings": alignment_findings,
        "labels": labels,
    }


def _policy_checks(run: TestRun) -> dict[str, Any]:
    checks = [
        event
        for event in _events(run.trace)
        if event.get("node") == "prompt_check"
    ]
    checks.extend(
        event
        for event in _final_attempt_events(run)
        if event.get("node") in {"sql_guard", "audit"}
    )
    if not checks:
        return {"passed": None, "total": None, "label": "unknown", "sub": "No check events"}

    failed = 0
    for event in checks:
        outputs = event.get("outputs") or {}
        if not isinstance(outputs, dict):
            continue
        try:
            vuln_count = int(outputs.get("vuln_count") or 0)
        except (TypeError, ValueError):
            vuln_count = 0
        approved = outputs.get("approved")
        if vuln_count > 0 or approved is False:
            failed += 1
    passed = max(len(checks) - failed, 0)
    return {
        "passed": passed,
        "total": len(checks),
        "label": f"{passed} / {len(checks)}",
        "sub": "Passed" if failed == 0 else f"{failed} flagged",
    }


def _pii_risk(findings: list[dict[str, Any]]) -> dict[str, Any]:
    labels = []
    for item in findings:
        label = str(item.get("label") or item.get("vuln_class") or "").upper()
        if any(part in label for part in _PII_LABEL_PARTS):
            labels.append(label)
    if not labels:
        return {"label": "Low", "sub": "No PII detected", "labels": []}
    return {"label": "High", "sub": ", ".join(sorted(set(labels))[:2]), "labels": labels}


def _explain_stats(text: str) -> dict[str, str]:
    rows = "unknown"
    cost = "unknown"
    rows_match = re.search(r"\brows=(\d+)\b", text or "")
    if rows_match:
        rows = _fmt_count(rows_match.group(1))
    cost_match = re.search(r"\bcost=([0-9.]+)\.\.([0-9.]+)", text or "")
    if cost_match:
        cost = cost_match.group(2)
    return {"rows": rows, "cost": cost}


def _token_usage(run: TestRun) -> dict[str, Any] | None:
    normalized = _normalized_token_usage(run)
    if normalized:
        return normalized

    found: list[dict[str, Any]] = []

    def scan(value: Any) -> None:
        if isinstance(value, dict):
            lower = {str(k).lower(): v for k, v in value.items()}
            prompt = lower.get("prompt_tokens", lower.get("input_tokens"))
            completion = lower.get("completion_tokens", lower.get("output_tokens"))
            total = lower.get("total_tokens", lower.get("tokens"))
            if prompt is not None or completion is not None or total is not None:
                found.append({"input": prompt, "output": completion, "total": total})
            for child in value.values():
                scan(child)
        elif isinstance(value, list):
            for child in value:
                scan(child)

    scan(run.trace)
    scan(run.system_result)
    for item in found:
        try:
            input_tokens = int(item["input"] or 0)
            output_tokens = int(item["output"] or 0)
            total_tokens = int(item["total"] or input_tokens + output_tokens)
        except (TypeError, ValueError):
            continue
        if input_tokens or output_tokens or total_tokens:
            return {
                "input": input_tokens or None,
                "output": output_tokens or None,
                "total": total_tokens or None,
            }
    return None


# Phase 0.7 — Latency breakdown ----------------------------------------------
# Три новых блока в отчёте: per-LLM-call (walltime + reasoning_tokens +
# cached + provider + retry), per-candidate в узле generate (видно
# parallel vs sequential и сколько секунд сэкономили asyncio.gather),
# cost-per-run (total $ + breakdown по моделям/ролям).
# Поля заполняются из trace.event.details.candidates[*] и .llm_call
# (см. app/generator.py и app/auditor.py).


_NODE_ROLE_MAP = {
    "generate": "generator",
    "audit": "auditor",
    "prompt_check": "prompt_check",
}


def _iter_from(event: dict[str, Any]) -> int:
    inputs = event.get("inputs") or {}
    if isinstance(inputs, dict):
        try:
            return int(inputs.get("iteration") or 1)
        except (TypeError, ValueError):
            return 1
    return 1


def _retry_sum_sec(retry_log: Any) -> float:
    if not isinstance(retry_log, list):
        return 0.0
    total = 0.0
    for item in retry_log:
        if isinstance(item, dict):
            try:
                total += float(item.get("wait_sec") or 0)
            except (TypeError, ValueError):
                continue
    return round(total, 3)


def _retry_count(retry_log: Any) -> int:
    if not isinstance(retry_log, list):
        return 0
    return len(retry_log)


def _llm_call_rows(run: TestRun) -> list[dict[str, Any]]:
    """
    Собрать все LLM-вызовы по trace с walltime и нормализованным usage.

    Идёт по event.details.candidates[*] (для узла generate) и
    event.details.llm_call (для audit/prompt_check). Это разворачивает
    «один узел = один вызов» в реальную картину (2 кандидата = 2 вызова).
    """
    rows: list[dict[str, Any]] = []
    for event in _events(run.trace):
        node = str(event.get("node") or "")
        details = event.get("details") or {}
        if not isinstance(details, dict):
            continue
        iteration = _iter_from(event)
        outputs = event.get("outputs") or {}
        if not isinstance(outputs, dict):
            outputs = {}
        selected_index = outputs.get("selected_index")
        try:
            selected_index_int = int(selected_index)
        except (TypeError, ValueError):
            selected_index_int = None

        # generator candidates (массив)
        candidates = details.get("candidates") or []
        scheduling = str(details.get("scheduling") or ("sequential" if len(candidates) == 1 else ""))
        scores = details.get("selector_scores") or []
        if not isinstance(scores, list):
            scores = []
        if isinstance(candidates, list):
            for idx, cand in enumerate(candidates):
                if not isinstance(cand, dict):
                    continue
                usage = cand.get("usage") or cand.get("response_usage") or {}
                if not isinstance(usage, dict):
                    usage = {}
                score = cand.get("selector_score")
                if not isinstance(score, dict) and idx < len(scores) and isinstance(scores[idx], dict):
                    score = scores[idx]
                if not isinstance(score, dict):
                    score = {}
                labels = score.get("labels") or cand.get("selector_labels") or []
                if not isinstance(labels, list):
                    labels = [labels]
                selected = cand.get("selected_by_selector")
                if selected is None and selected_index_int is not None:
                    selected = idx == selected_index_int
                rows.append(
                    {
                        "node": node,
                        "role": _NODE_ROLE_MAP.get(node, node),
                        "iteration": iteration,
                        "candidate_index": idx,
                        "scheduling": scheduling or "sequential",
                        "model": cand.get("model"),
                        "backend": cand.get("backend"),
                        "provider": cand.get("provider") or usage.get("provider"),
                        "temperature": cand.get("temperature"),
                        "temperature_applied": cand.get("temperature_applied"),
                        "selected_by_selector": bool(selected) if selected is not None else False,
                        "selector_labels": [str(item) for item in labels if item],
                        "walltime_sec": cand.get("walltime_sec"),
                        "tokens_in": usage.get("prompt_tokens"),
                        "tokens_out": usage.get("completion_tokens"),
                        "tokens_total": usage.get("total_tokens"),
                        "reasoning_tokens": usage.get("reasoning_tokens"),
                        "cached_tokens": usage.get("cached_tokens"),
                        "cache_write_tokens": usage.get("cache_write_tokens"),
                        "cost_usd": usage.get("cost_usd"),
                        "generation_id": cand.get("generation_id") or usage.get("generation_id"),
                        "retry_count": _retry_count(cand.get("retry_log")),
                        "retry_total_wait_sec": _retry_sum_sec(cand.get("retry_log")),
                        "response_headers": cand.get("response_headers") or {},
                    }
                )

        # auditor / prompt_check llm_call (один объект)
        call = details.get("llm_call")
        if isinstance(call, dict):
            usage = call.get("usage") or {}
            if not isinstance(usage, dict):
                usage = {}
            rows.append(
                {
                    "node": node,
                    "role": _NODE_ROLE_MAP.get(node, node),
                    "iteration": iteration,
                    "candidate_index": 0,
                    "scheduling": "single",
                    "model": call.get("model"),
                    "backend": call.get("backend"),
                    "provider": call.get("provider") or usage.get("provider"),
                    "walltime_sec": call.get("walltime_sec"),
                    "tokens_in": usage.get("prompt_tokens"),
                    "tokens_out": usage.get("completion_tokens"),
                    "tokens_total": usage.get("total_tokens"),
                    "reasoning_tokens": usage.get("reasoning_tokens"),
                    "cached_tokens": usage.get("cached_tokens"),
                    "cache_write_tokens": usage.get("cache_write_tokens"),
                    "cost_usd": usage.get("cost_usd"),
                    "generation_id": call.get("generation_id") or usage.get("generation_id"),
                    "retry_count": _retry_count(call.get("retry_log")),
                    "retry_total_wait_sec": _retry_sum_sec(call.get("retry_log")),
                    "response_headers": call.get("response_headers") or {},
                }
            )
    return rows


def _candidate_groups(llm_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Сгруппировать кандидаты узла generate по итерации.

    Для каждой группы считаем sum walltime и max walltime. На parallel-
    scheduling saving_sec = sum - max (это секунды сэкономленные через
    asyncio.gather). На sequential saving_sec = 0.
    """
    groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in llm_calls:
        if row.get("node") != "generate":
            continue
        key = (row.get("iteration") or 1, row.get("scheduling") or "sequential")
        groups.setdefault(key, []).append(row)
    out: list[dict[str, Any]] = []
    for (iteration, scheduling), items in sorted(groups.items()):
        walltimes = [float(r.get("walltime_sec") or 0) for r in items]
        max_w = max(walltimes) if walltimes else 0.0
        sum_w = sum(walltimes) if walltimes else 0.0
        saving = round(sum_w - max_w, 3) if scheduling == "parallel" else 0.0
        out.append(
            {
                "iteration": iteration,
                "scheduling": scheduling,
                "candidate_count": len(items),
                "max_walltime_sec": round(max_w, 3),
                "sum_walltime_sec": round(sum_w, 3),
                "saving_sec": saving,
                "items": items,
            }
        )
    return out


def _cost_summary(llm_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Total $/прогон с разбивкой по роли и модели."""
    total = 0.0
    by_role: dict[str, float] = {}
    by_model: dict[str, float] = {}
    for row in llm_calls:
        cost = row.get("cost_usd")
        if cost is None:
            continue
        try:
            value = float(cost)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        total += value
        role = str(row.get("role") or "unknown")
        by_role[role] = by_role.get(role, 0.0) + value
        model = str(row.get("model") or "unknown")
        by_model[model] = by_model.get(model, 0.0) + value
    return {
        "total_usd": round(total, 6),
        "by_role": [{"role": k, "cost_usd": round(v, 6)} for k, v in sorted(by_role.items())],
        "by_model": [{"model": k, "cost_usd": round(v, 6)} for k, v in sorted(by_model.items())],
    }


def _fmt_sec_short(value: Any) -> str:
    """Короткое форматирование секунд: 0.42s, 1.4s, 18.4s."""
    try:
        num = float(value or 0)
    except (TypeError, ValueError):
        return "0s"
    if num < 1:
        return "{:.0f}ms".format(num * 1000)
    if num < 10:
        return "{:.2f}s".format(num)
    return "{:.1f}s".format(num)


def _rag_timings_summary(run: TestRun) -> dict[str, Any]:
    """
    Собрать sub-timings RAG и EXPLAIN из trace.event.details для overview tile.

    Возвращает {rag_cold_count, rag_total_sec, explain_total_sec, ...} —
    компактный набор для отображения «cold start vs warm cache» и
    «EXPLAIN connect vs query».
    """
    rag_cold_count = 0
    rag_warm_count = 0
    rag_total_sec = 0.0
    explain_total_sec = 0.0
    explain_phases: dict[str, float] = {}
    for event in _events(run.trace):
        node = str(event.get("node") or "")
        details = event.get("details") or {}
        if not isinstance(details, dict):
            continue
        rag_timings = details.get("rag_timings") or {}
        if isinstance(rag_timings, dict):
            for key, timing in rag_timings.items():
                if not isinstance(timing, dict):
                    continue
                try:
                    elapsed = float(timing.get("elapsed_sec") or 0)
                except (TypeError, ValueError):
                    elapsed = 0.0
                rag_total_sec += elapsed
                if timing.get("cache_hit"):
                    rag_warm_count += 1
                else:
                    rag_cold_count += 1
        if node == "explain_sandbox":
            sub = details.get("sub_timings") or {}
            if isinstance(sub, dict):
                for phase, val in sub.items():
                    if phase == "total_sec":
                        continue
                    try:
                        explain_phases[phase] = explain_phases.get(phase, 0.0) + float(val or 0)
                    except (TypeError, ValueError):
                        continue
                try:
                    explain_total_sec += float(sub.get("total_sec") or 0)
                except (TypeError, ValueError):
                    pass
    return {
        "rag_cold_count": rag_cold_count,
        "rag_warm_count": rag_warm_count,
        "rag_total_sec": round(rag_total_sec, 3),
        "explain_total_sec": round(explain_total_sec, 3),
        "explain_phases": {k: round(v, 4) for k, v in explain_phases.items()},
    }


def _latency_overview(
    run: TestRun,
    llm_calls: list[dict[str, Any]],
    candidate_groups: list[dict[str, Any]],
    cost_summary: dict[str, Any],
    rag_summary: dict[str, Any],
) -> dict[str, Any]:
    """
    Подготовить компактный overview-блок Latency Breakdown.

    Tiles (4 квадратика): pipeline-walltime, LLM-walltime sum, retries, cost.
    Slowest (top-3): самые долгие spans с метками и data-detail ссылкой на drawer.
    """
    pipeline_sec = _trace_duration_sec(run) or _duration_sec(run) or 0.0
    llm_total_sec = sum(float(r.get("walltime_sec") or 0) for r in llm_calls)
    retry_count_total = sum(int(r.get("retry_count") or 0) for r in llm_calls)
    retry_wait_total = sum(float(r.get("retry_total_wait_sec") or 0) for r in llm_calls)
    cost_total = float(cost_summary.get("total_usd") or 0)

    # Tile 4: cost — bad если > $0.01 на прогон, ok если 0, warn между.
    if cost_total >= 0.01:
        cost_tone = "warn"
    elif cost_total > 0:
        cost_tone = "neutral"
    else:
        cost_tone = "neutral"

    # Tile 3: retries — bad если есть, ok если нет.
    if retry_count_total > 0:
        retry_tone = "bad"
        retry_sub = "{:.1f}s в backoff".format(retry_wait_total) if retry_wait_total else "no backoff time"
    else:
        retry_tone = "ok"
        retry_sub = "no SDK retries"

    # Tile 2: LLM walltime — warn если > 5s, bad если > 15s.
    if llm_total_sec >= 15:
        llm_tone = "bad"
    elif llm_total_sec >= 5:
        llm_tone = "warn"
    else:
        llm_tone = "neutral"

    # Tile 1: pipeline — warn если > 15s, bad если > 30s.
    if pipeline_sec >= 30:
        pipeline_tone = "bad"
    elif pipeline_sec >= 15:
        pipeline_tone = "warn"
    else:
        pipeline_tone = "neutral"

    tiles = [
        {
            "label": "Pipeline walltime",
            "value": _fmt_sec_short(pipeline_sec),
            "sub": "{} LLM calls · {} candidates".format(
                len(llm_calls),
                sum(g.get("candidate_count", 0) for g in candidate_groups),
            ),
            "tone": pipeline_tone,
            "drawer_key": "latency-calls",
        },
        {
            "label": "LLM walltime",
            "value": _fmt_sec_short(llm_total_sec),
            "sub": "сумма по всем вызовам",
            "tone": llm_tone,
            "drawer_key": "latency-calls",
        },
        {
            "label": "Retries",
            "value": str(retry_count_total) if retry_count_total else "0",
            "sub": retry_sub,
            "tone": retry_tone,
            "drawer_key": "latency-retries",
        },
        {
            "label": "Total cost",
            "value": ("${:.6f}".format(cost_total)) if cost_total else "—",
            "sub": "{} models".format(len(cost_summary.get("by_model") or [])) if cost_summary.get("by_model") else "usage unknown",
            "tone": cost_tone,
            "drawer_key": "latency-cost",
        },
    ]

    # Slowest spans: топ-3 из (узел generate с total wall) + audit llm + EXPLAIN + RAG.
    spans: list[dict[str, Any]] = []
    for grp in candidate_groups:
        if not grp.get("candidate_count"):
            continue
        time_sec = grp["sum_walltime_sec"] if grp["scheduling"] == "sequential" else grp["max_walltime_sec"]
        chips = [
            {"text": grp["scheduling"], "cls": "lat-pill-parallel" if grp["scheduling"] == "parallel" else "lat-pill-sequential"},
            {"text": "iter " + str(grp["iteration"]), "cls": "pill-blue"},
            {"text": str(grp["candidate_count"]) + " cand", "cls": "pill-blue"},
        ]
        if grp.get("saving_sec"):
            chips.append({"text": "saved " + _fmt_sec_short(grp["saving_sec"]), "cls": "pill-green"})
        spans.append(
            {
                "name": "generate · {} candidates".format(grp["candidate_count"]),
                "time_sec": time_sec,
                "time_label": _fmt_sec_short(time_sec),
                "chips": chips,
                "drawer_key": "latency-candidates",
                "tone": "warn" if time_sec >= 10 else ("bad" if time_sec >= 20 else "neutral"),
            }
        )
    for row in llm_calls:
        if row.get("node") == "generate":
            # Уже представлено в candidate_groups, не дублируем.
            continue
        wt = float(row.get("walltime_sec") or 0)
        if wt < 0.1:
            continue
        chips = []
        if row.get("provider") or row.get("backend"):
            chips.append({"text": str(row.get("provider") or row.get("backend")), "cls": "pill-blue"})
        if row.get("reasoning_tokens"):
            chips.append({"text": "reasoning " + str(row["reasoning_tokens"]), "cls": "pill-amber"})
        if row.get("cached_tokens"):
            chips.append({"text": "cached " + str(row["cached_tokens"]), "cls": "pill-green"})
        if row.get("retry_count"):
            chips.append({"text": str(row["retry_count"]) + " retry", "cls": "pill-red"})
        spans.append(
            {
                "name": str(row.get("node") or "llm") + " call",
                "time_sec": wt,
                "time_label": _fmt_sec_short(wt),
                "chips": chips,
                "drawer_key": "latency-calls",
                "tone": "warn" if wt >= 5 else ("bad" if wt >= 15 else "neutral"),
            }
        )
    if rag_summary.get("rag_total_sec", 0) > 0:
        chips = [
            {"text": str(rag_summary.get("rag_cold_count", 0)) + " cold", "cls": "lat-pill-cold"},
            {"text": str(rag_summary.get("rag_warm_count", 0)) + " warm", "cls": "lat-pill-warm"},
        ]
        spans.append(
            {
                "name": "RAG retrieve · 4 indices",
                "time_sec": rag_summary["rag_total_sec"],
                "time_label": _fmt_sec_short(rag_summary["rag_total_sec"]),
                "chips": chips,
                "drawer_key": "latency-rag",
                "tone": "neutral",
            }
        )
    if rag_summary.get("explain_total_sec", 0) > 0:
        spans.append(
            {
                "name": "EXPLAIN sandbox",
                "time_sec": rag_summary["explain_total_sec"],
                "time_label": _fmt_sec_short(rag_summary["explain_total_sec"]),
                "chips": [{"text": "psycopg2", "cls": "pill-blue"}],
                "drawer_key": "latency-explain",
                "tone": "neutral",
            }
        )

    spans.sort(key=lambda x: x["time_sec"], reverse=True)
    top = spans[:3]
    max_span_sec = top[0]["time_sec"] if top else 1.0
    for span in top:
        span["bar_pct"] = (
            round(span["time_sec"] / max_span_sec * 100, 1) if max_span_sec > 0 else 0.0
        )

    actions: list[dict[str, str]] = [
        {"label": "Per-LLM calls", "drawer_key": "latency-calls"},
    ]
    if candidate_groups:
        actions.append({"label": "Candidates", "drawer_key": "latency-candidates"})
    if cost_summary.get("total_usd"):
        actions.append({"label": "Cost", "drawer_key": "latency-cost"})
    if rag_summary.get("rag_total_sec", 0) > 0:
        actions.append({"label": "RAG", "drawer_key": "latency-rag"})
    if rag_summary.get("explain_total_sec", 0) > 0:
        actions.append({"label": "EXPLAIN", "drawer_key": "latency-explain"})

    return {
        "tiles": tiles,
        "slowest": top,
        "total_label": _fmt_sec_short(pipeline_sec),
        "llm_total_label": _fmt_sec_short(llm_total_sec),
        "actions": actions,
    }


def _esc(value: Any) -> str:
    """HTML-эскейп для значений из payload в pre-rendered HTML-drawerах."""
    import html as _html_mod
    if value is None:
        return ""
    return _html_mod.escape(str(value), quote=True)


def _drawer_latency_calls_html(llm_calls: list[dict[str, Any]]) -> str:
    """Полная таблица per-LLM-call для drawer-уровня (kind=html)."""
    if not llm_calls:
        return "<div class='drawer-card'>LLM-вызовы не записаны.</div>"
    rows_html: list[str] = []
    for row in llm_calls:
        node = _esc(row.get("node") or "-")
        if row.get("scheduling") and row.get("scheduling") != "single":
            node += "·" + _esc(row.get("candidate_index"))
        retries = row.get("retry_count") or 0
        retry_cell = ""
        if retries:
            retry_cell = (
                str(retries) + (" (+" + _fmt_sec_short(row.get("retry_total_wait_sec") or 0) + ")")
            )
        cost_cell = "${:.6f}".format(float(row["cost_usd"])) if row.get("cost_usd") else "—"
        rows_html.append(
            "<tr>"
            "<td>" + node + "</td>"
            "<td class='num'>" + _esc(row.get("iteration")) + "</td>"
            "<td>" + _esc(row.get("model") or "—") + "</td>"
            "<td>" + _esc(row.get("provider") or row.get("backend") or "—") + "</td>"
            "<td class='num'>" + _fmt_sec_short(row.get("walltime_sec")) + "</td>"
            "<td class='num'>" + _esc(row.get("tokens_in") or "—") + "</td>"
            "<td class='num'>" + _esc(row.get("tokens_out") or "—") + "</td>"
            "<td class='num " + ("warn" if row.get("reasoning_tokens") else "") + "'>"
            + _esc(row.get("reasoning_tokens") or "—") + "</td>"
            "<td class='num " + ("ok" if row.get("cached_tokens") else "") + "'>"
            + _esc(row.get("cached_tokens") or "—") + "</td>"
            "<td class='num " + ("bad" if retries else "muted") + "'>"
            + (retry_cell or "—") + "</td>"
            "<td class='num'>" + cost_cell + "</td>"
            "</tr>"
        )
    table = (
        "<div class='drawer-card-title'>Per-LLM-call breakdown</div>"
        "<table class='lat-drawer-table'>"
        "<thead><tr>"
        "<th>node</th><th>iter</th><th>model</th><th>provider</th>"
        "<th>walltime</th><th>in</th><th>out</th><th>reason</th>"
        "<th>cached</th><th>retries</th><th>cost USD</th>"
        "</tr></thead>"
        "<tbody>" + "".join(rows_html) + "</tbody>"
        "</table>"
        "<div class='drawer-small' style='margin-top:8px'>"
        "Walltime — реальное время вызова на нашей стороне; reason/cached — поля из "
        "<code>usage.completion_tokens_details.reasoning_tokens</code> и "
        "<code>prompt_tokens_details.cached_tokens</code>. Retries — попытки нашего "
        "явного retry-loop (SDK auto-retry отключён)."
        "</div>"
    )
    return table


def _drawer_latency_candidates_html(candidate_groups: list[dict[str, Any]]) -> str:
    if not candidate_groups:
        return "<div class='drawer-card'>Узел generate не сработал в этом прогоне.</div>"
    sections: list[str] = []
    for grp in candidate_groups:
        max_sec = grp.get("max_walltime_sec") or 1.0
        items = grp.get("items") or []
        if max_sec <= 0:
            max_sec = 1.0
        bars: list[str] = []
        for idx, row in enumerate(items):
            wt = float(row.get("walltime_sec") or 0)
            pct = round(wt / max_sec * 100, 1) if max_sec else 0.0
            temp = row.get("temperature")
            temp_text = "temp " + _esc(temp) if temp is not None and temp != "" else "temp n/a"
            selected = bool(row.get("selected_by_selector"))
            selected_text = "selected" if selected else "not selected"
            labels = row.get("selector_labels") or []
            labels_text = ", ".join(str(item) for item in labels) if labels else "none"
            label = (
                "candidate " + _esc(row.get("candidate_index", idx))
                + " · " + temp_text
                + " · " + selected_text
                + " · labels: " + _esc(labels_text)
            )
            row_cls = "lat-cand-bar selected" if selected else "lat-cand-bar"
            bars.append(
                "<div class='" + row_cls + "'>"
                "<span class='lcb-label'>" + label + "</span>"
                "<span class='lcb-track'><span class='lcb-fill' style='width:" + str(pct) + "%'></span></span>"
                "<span class='lcb-time'>" + _fmt_sec_short(wt) + "</span>"
                "</div>"
            )
        saving_block = ""
        if grp.get("saving_sec"):
            saving_block = (
                "<div class='lat-saving'>asyncio.gather сэкономил "
                + _fmt_sec_short(grp["saving_sec"])
                + " против sequential</div>"
            )
        sections.append(
            "<div class='drawer-card'>"
            "<div class='drawer-card-head'>"
            "<div class='drawer-card-title'>Iteration " + _esc(grp.get("iteration")) + " · "
            + _esc(grp.get("scheduling")) + "</div>"
            "<div class='drawer-small'>max " + _fmt_sec_short(grp.get("max_walltime_sec"))
            + " · sum " + _fmt_sec_short(grp.get("sum_walltime_sec")) + "</div>"
            "</div>"
            "<div class='lat-cand-bars'>" + "".join(bars) + "</div>"
            + saving_block
            + "</div>"
        )
    return "".join(sections)


def _drawer_latency_cost_html(cost_summary: dict[str, Any]) -> str:
    if not cost_summary or not cost_summary.get("total_usd"):
        return (
            "<div class='drawer-card'>Стоимость не записана. Включено если провайдер возвращает "
            "<code>usage.cost</code> (OpenRouter с usage accounting).</div>"
        )
    total = cost_summary["total_usd"]
    by_model_rows = "".join(
        "<tr><td>" + _esc(item["model"]) + "</td><td class='num'>${:.6f}".format(float(item["cost_usd"])) + "</td></tr>"
        for item in (cost_summary.get("by_model") or [])
    )
    by_role_rows = "".join(
        "<tr><td>" + _esc(item["role"]) + "</td><td class='num'>${:.6f}".format(float(item["cost_usd"])) + "</td></tr>"
        for item in (cost_summary.get("by_role") or [])
    )
    return (
        "<div class='drawer-card'>"
        "<div class='drawer-card-title'>Total cost</div>"
        "<div style='font-size:24px;font-weight:700;color:#0f172a;font-family:JetBrains Mono,monospace'>"
        "${:.6f}</div>".format(total)
        + "<div class='drawer-small'>сумма по всем LLM-вызовам в прогоне</div>"
        + "</div>"
        + "<div class='drawer-card'>"
        + "<div class='drawer-card-title'>By model</div>"
        + "<table class='lat-drawer-table'><thead><tr><th>model</th><th>cost USD</th></tr></thead>"
        + "<tbody>" + by_model_rows + "</tbody></table>"
        + "</div>"
        + "<div class='drawer-card'>"
        + "<div class='drawer-card-title'>By role</div>"
        + "<table class='lat-drawer-table'><thead><tr><th>role</th><th>cost USD</th></tr></thead>"
        + "<tbody>" + by_role_rows + "</tbody></table>"
        + "</div>"
    )


def _drawer_latency_rag_html(run: TestRun, rag_summary: dict[str, Any]) -> str:
    """Полная разбивка RAG-таймингов по индексам."""
    sections: list[str] = []
    sections.append(
        "<div class='drawer-card'>"
        "<div class='drawer-card-title'>Сводка</div>"
        "<div class='drawer-kv'>"
        "<div><span>RAG cold calls</span>" + str(rag_summary.get("rag_cold_count", 0)) + "</div>"
        "<div><span>RAG warm calls</span>" + str(rag_summary.get("rag_warm_count", 0)) + "</div>"
        "<div><span>RAG total wall</span>" + _fmt_sec_short(rag_summary.get("rag_total_sec")) + "</div>"
        "<div><span>EXPLAIN wall</span>" + _fmt_sec_short(rag_summary.get("explain_total_sec")) + "</div>"
        "</div></div>"
    )
    # Per-node breakdown.
    rows_html: list[str] = []
    for event in _events(run.trace):
        details = event.get("details") or {}
        if not isinstance(details, dict):
            continue
        rag_timings = details.get("rag_timings") or {}
        if not isinstance(rag_timings, dict) or not rag_timings:
            continue
        node = str(event.get("node") or "—")
        for key, timing in rag_timings.items():
            if not isinstance(timing, dict):
                continue
            elapsed = timing.get("elapsed_sec") or 0
            hit = "warm" if timing.get("cache_hit") else "cold"
            rows_html.append(
                "<tr>"
                "<td>" + _esc(node) + "</td>"
                "<td>" + _esc(key) + "</td>"
                "<td class='" + ("ok" if timing.get("cache_hit") else "warn") + "'>" + hit + "</td>"
                "<td class='num'>" + _fmt_sec_short(elapsed) + "</td>"
                "</tr>"
            )
    if rows_html:
        sections.append(
            "<div class='drawer-card'>"
            "<div class='drawer-card-title'>Per-call timing</div>"
            "<table class='lat-drawer-table'>"
            "<thead><tr><th>node</th><th>call</th><th>cache</th><th>wall</th></tr></thead>"
            "<tbody>" + "".join(rows_html) + "</tbody></table>"
            "<div class='drawer-small' style='margin-top:8px'>"
            "Cold = реальный encode+FAISS (или Postgres cosine после миграции). "
            "Warm = lru_cache hit на ту же задачу/SQL в этом процессе."
            "</div>"
            "</div>"
        )
    else:
        sections.append("<div class='drawer-card'>RAG-таймингов в трассе нет.</div>")
    return "".join(sections)


def _drawer_latency_explain_html(run: TestRun) -> str:
    """EXPLAIN sub-timings: connect/setup/execute/fetch."""
    for event in _events(run.trace):
        if event.get("node") != "explain_sandbox":
            continue
        details = event.get("details") or {}
        if not isinstance(details, dict):
            continue
        sub = details.get("sub_timings") or {}
        if not isinstance(sub, dict) or not sub:
            return (
                "<div class='drawer-card'>EXPLAIN был пропущен или sub_timings не записаны.</div>"
            )
        rows_html: list[str] = []
        total = float(sub.get("total_sec") or 0)
        for phase in ("connect_sec", "setup_sec", "execute_sec", "fetch_sec"):
            val = float(sub.get(phase) or 0)
            pct = round(val / total * 100, 1) if total > 0 else 0.0
            rows_html.append(
                "<tr>"
                "<td>" + phase.replace("_sec", "") + "</td>"
                "<td class='num'>" + _fmt_sec_short(val) + "</td>"
                "<td class='num'>" + str(pct) + "%</td>"
                "</tr>"
            )
        return (
            "<div class='drawer-card'>"
            "<div class='drawer-card-title'>EXPLAIN sandbox phases</div>"
            "<table class='lat-drawer-table'>"
            "<thead><tr><th>phase</th><th>wall</th><th>share</th></tr></thead>"
            "<tbody>" + "".join(rows_html) + "</tbody></table>"
            "<div class='drawer-small' style='margin-top:8px'>"
            "Connect — psycopg2.connect (включая DSN handshake). Setup — SET TRANSACTION READ ONLY + statement_timeout. "
            "Execute — собственно EXPLAIN sql. Fetch — cursor.fetchall()."
            "</div>"
            "</div>"
        )
    return "<div class='drawer-card'>EXPLAIN-узел не выполнился в этом прогоне.</div>"


def _drawer_latency_retries_html(llm_calls: list[dict[str, Any]]) -> str:
    """Развернутый retry_log по всем вызовам."""
    sections: list[str] = []
    has_any = False
    for row in llm_calls:
        # retry_log хранится в исходной структуре llm_call/candidate, мы передаём его сюда
        # отдельно как row["retry_log"] нужно — но у нас в llm_call_rows нет полного лога.
        # Возьмём через response_headers indirection не получится. Используем retry_count.
        retries = row.get("retry_count") or 0
        if not retries:
            continue
        has_any = True
        node = _esc(row.get("node") or "-")
        if row.get("scheduling") and row.get("scheduling") != "single":
            node += "·" + _esc(row.get("candidate_index"))
        sections.append(
            "<div class='drawer-card'>"
            "<div class='drawer-card-head'>"
            "<div class='drawer-card-title'>" + node + "</div>"
            "<div class='drawer-small'>" + _esc(row.get("model") or "") + "</div>"
            "</div>"
            "<div class='drawer-kv'>"
            "<div><span>Retries</span>" + str(retries) + "</div>"
            "<div><span>Backoff total</span>" + _fmt_sec_short(row.get("retry_total_wait_sec")) + "</div>"
            "<div><span>Walltime</span>" + _fmt_sec_short(row.get("walltime_sec")) + "</div>"
            "<div><span>Provider</span>" + _esc(row.get("provider") or row.get("backend") or "-") + "</div>"
            "</div></div>"
        )
    if not has_any:
        return (
            "<div class='drawer-card'>"
            "<div class='drawer-card-title'>No retries</div>"
            "<div class='drawer-small'>SDK auto-retry отключён (max_retries=0), наш явный retry-loop не сработал ни разу. "
            "Это значит, что walltime — чистое время вызова без скрытого backoff.</div>"
            "</div>"
        )
    return "".join(sections)


def _normalized_token_usage(run: TestRun) -> dict[str, Any] | None:
    usage_items: list[dict[str, Any]] = []
    for event in _events(run.trace):
        details = event.get("details") or {}
        if not isinstance(details, dict):
            continue
        for item in details.get("candidates") or []:
            if isinstance(item, dict):
                usage = item.get("usage") or item.get("response_usage")
                if isinstance(usage, dict):
                    usage_items.append(usage)
        call = details.get("llm_call")
        if isinstance(call, dict) and isinstance(call.get("usage"), dict):
            usage_items.append(call["usage"])

    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    cost_usd = 0.0
    for item in usage_items:
        try:
            input_tokens += int(item.get("prompt_tokens") or item.get("input_tokens") or 0)
            output_tokens += int(item.get("completion_tokens") or item.get("output_tokens") or 0)
            total_tokens += int(item.get("total_tokens") or item.get("tokens") or 0)
            cost_usd += float(item.get("cost_usd") or item.get("cost") or 0.0)
        except (TypeError, ValueError):
            continue
    if not total_tokens:
        total_tokens = input_tokens + output_tokens
    if input_tokens or output_tokens or total_tokens or cost_usd:
        return {
            "input": input_tokens or None,
            "output": output_tokens or None,
            "total": total_tokens or None,
            "cost_usd": round(cost_usd, 6) if cost_usd else None,
        }
    return None


def _event_level(event: dict[str, Any]) -> str:
    details = event.get("details") or {}
    outputs = event.get("outputs") or {}
    if isinstance(details, dict) and details.get("error"):
        return "error"
    if isinstance(outputs, dict):
        if outputs.get("ok") is False:
            return "error"
        if outputs.get("decision") == "abstain":
            return "warning"
        try:
            if int(outputs.get("vuln_count") or 0) > 0:
                return "warning"
        except (TypeError, ValueError):
            pass
        if outputs.get("approved") is False:
            return "warning"
    return "info"


def _events_stream(run: TestRun) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, event in enumerate(_events(run.trace), 1):
        node = str(event.get("node") or "event")
        rows.append(
            {
                "index": index,
                "time": _time_only(event.get("started_at")),
                "level": _event_level(event),
                "name": node.replace("_", "."),
                "duration": _fmt_sec(event.get("duration_sec")),
                "drawer_key": "event-" + str(index - 1),
            }
        )
    return rows


def _time_only(value: Any) -> str:
    if isinstance(value, str) and "T" in value:
        return value.split("T", 1)[1].split("+", 1)[0].split("Z", 1)[0]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    return "unknown"


def _step_status(events: list[dict[str, Any]]) -> str:
    levels = {_event_level(event) for event in events}
    if "error" in levels:
        return "failed"
    if "warning" in levels:
        return "warning"
    return "success"


def _timeline_rounds(run: TestRun) -> dict[str, list[dict[str, Any]]]:
    rounds: dict[str, list[dict[str, Any]]] = {"generate": [], "audit": []}
    current_iteration = 0
    for event in _events(run.trace):
        node = str(event.get("node") or "")
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        if node == "generate":
            try:
                current_iteration = int(details.get("iteration") or current_iteration + 1)
            except (TypeError, ValueError):
                current_iteration += 1
            sec = _float(event.get("duration_sec"))
            candidates = details.get("candidates") if isinstance(details.get("candidates"), list) else []
            candidate_seconds = [
                round(_float((item or {}).get("walltime_sec") or (item or {}).get("latency_sec")), 3)
                for item in candidates[:4]
                if isinstance(item, dict)
            ]
            subtitle_parts = []
            candidate_count = int(details.get("candidate_count") or 0)
            if candidate_count:
                subtitle_parts.append(f"{candidate_count} SQL-кандидата")
            if candidate_seconds:
                subtitle_parts.append(" + ".join(_fmt_sec(item) for item in candidate_seconds))
            rounds["generate"].append(
                {
                    "number": current_iteration,
                    "sec": round(sec, 3),
                    "duration": _fmt_sec(sec),
                    "status": "completed",
                    "subtitle": " · ".join(subtitle_parts) or "генерация SQL",
                    "detail": {
                        "kind": "generate",
                        "iteration": current_iteration,
                        "candidates": [_timeline_candidate(item, idx, details) for idx, item in enumerate(candidates)],
                        "selector_scores": details.get("selector_scores") or [],
                        "selected_index": details.get("selected_index"),
                        "business_requirements": details.get("business_requirements") or [],
                        "selector_reason": details.get("selector_reason") or "",
                    },
                }
            )
            continue
        if node == "audit" and current_iteration:
            sec = _float(event.get("duration_sec"))
            findings = details.get("merged_findings") or details.get("findings") or []
            labels: list[str] = []
            for item in findings:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("vuln_class") or item.get("label") or "").strip()
                if label and label not in labels:
                    labels.append(label)
            rounds["audit"].append(
                {
                    "number": current_iteration,
                    "sec": round(sec, 3),
                    "duration": _fmt_sec(sec),
                    "status": "completed",
                    "subtitle": "риски: " + ", ".join(labels[:3]) if labels else "проверка ответа",
                    "detail": {
                        "kind": "audit",
                        "iteration": current_iteration,
                        "prompt_user": details.get("prompt_user") or (details.get("llm_call") or {}).get("prompt_user"),
                        "response_raw": details.get("response_raw") or (details.get("llm_call") or {}).get("response"),
                        "merged_findings": findings,
                        "classifier_output": details.get("classifier_output") or {},
                        "approved": details.get("approved"),
                        "overall_risk_score": details.get("overall_risk_score"),
                        "security_risk_score": details.get("security_risk_score"),
                        "quality_risk_score": details.get("quality_risk_score"),
                        "summary": details.get("summary"),
                        "business_requirements": details.get("business_requirements") or [],
                        "business_alignment_findings": details.get("business_alignment_findings") or [],
                    },
                }
            )
    return {key: value for key, value in rounds.items() if value}


def _timeline_candidate(item: Any, index: int, details: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"index": index}
    event_details = details or {}
    prompt_meta = item.get("prompt_meta") if isinstance(item.get("prompt_meta"), dict) else {}
    if not prompt_meta:
        prompt_meta = event_details.get("prompt_meta") if isinstance(event_details.get("prompt_meta"), dict) else {}
    return {
        "index": item.get("candidate_index", item.get("index", index)),
        "sql": item.get("sql") or "",
        "prompt_user": item.get("prompt_user") or event_details.get("prompt_user") or "",
        "prompt_system": item.get("prompt_system") or event_details.get("prompt_system") or "",
        "prompt_system_meta": {
            "prompt_id": prompt_meta.get("prompt_id"),
            "prompt_type": prompt_meta.get("prompt_type"),
            "prompt_version": prompt_meta.get("prompt_version"),
            "prompt_sha256": prompt_meta.get("prompt_sha256"),
            "prompt_source": prompt_meta.get("prompt_source"),
        },
        "response_raw": item.get("response") or item.get("response_raw") or "",
        "temperature": item.get("temperature"),
        "temperature_applied": item.get("temperature_applied"),
        "temperature_note": item.get("temperature_note"),
        "backend": item.get("backend"),
        "model": item.get("model"),
        "walltime_sec": item.get("walltime_sec") or item.get("latency_sec"),
        "usage": item.get("usage") or {},
        "selector_score": item.get("selector_score"),
        "selector_reason": item.get("selector_reason")
        or ((item.get("selector_score") or {}).get("selector_reason") if isinstance(item.get("selector_score"), dict) else ""),
        "business_requirements": item.get("business_requirements")
        or ((item.get("selector_score") or {}).get("business_requirements") if isinstance(item.get("selector_score"), dict) else [])
        or [],
        "business_alignment_findings": item.get("business_alignment_findings")
        or ((item.get("selector_score") or {}).get("business_alignment_findings") if isinstance(item.get("selector_score"), dict) else [])
        or [],
        "selected": bool(item.get("selected_by_selector") or item.get("selected")),
    }


def _timeline_steps(run: TestRun) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, event in enumerate(_events(run.trace), 1):
        node = str(event.get("node") or "event")
        if node not in groups:
            groups[node] = {
                "key": node,
                "label": _STEP_LABELS.get(node, node.replace("_", " ").title()),
                "sec": 0.0,
                "events": [],
                "color": _STEP_COLORS.get(node, "#64748b"),
            }
            order.append(node)
        try:
            sec = float(event.get("duration_sec") or 0)
        except (TypeError, ValueError):
            sec = 0.0
        groups[node]["sec"] += sec
        groups[node]["events"].append(
            {
                "index": index,
                "node": node,
                "started_at": event.get("started_at"),
                "duration_sec": sec,
                "level": _event_level(event),
                "outcome": _event_outcome(event),
                "outputs": _safe(event.get("outputs") or {}),
            }
        )

    steps = []
    max_sec = max((groups[key]["sec"] for key in order), default=0.0)
    rounds_by_step = _timeline_rounds(run)
    for key in order:
        item = groups[key]
        sec = round(float(item["sec"]), 3)
        steps.append(
            {
                "key": key,
                "label": item["label"],
                "sec": sec,
                "duration": _fmt_sec(sec),
                "status": _step_status(item["events"]),
                "active": max_sec > 0 and item["sec"] == max_sec,
                "color": item["color"],
                "drawer_key": "timeline-" + key,
                "events": item["events"],
                "rounds": rounds_by_step.get(key, []),
            }
        )
    return steps


def _duration_segments(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum(float(item.get("sec") or 0) for item in steps)
    rows: list[dict[str, Any]] = []
    for item in steps:
        sec = float(item.get("sec") or 0)
        pct = round(sec / total * 100.0, 2) if total > 0 else 0.0
        rows.append(
            {
                "label": item["label"],
                "duration": item["duration"],
                "percent": pct,
                "color": item["color"],
                "drawer_key": item["drawer_key"],
            }
        )
    return rows


def _sql_lines(sql: str) -> list[str]:
    if not sql:
        return [""]
    return sql.splitlines() or [sql]


def _diff_view(diffs: list[dict[str, str]], final_sql: str) -> dict[str, Any]:
    if not diffs:
        return {
            "left_title": "v1 (Rejected)",
            "right_title": "v2 (Final)",
            "left_lines": [{"text": "Diff is unavailable for this run.", "tone": "muted"}],
            "right_lines": [{"text": line, "tone": "add"} for line in _sql_lines(final_sql)[:12]],
        }
    text = diffs[-1]["text"]
    left: list[dict[str, str]] = []
    right: list[dict[str, str]] = []
    for line in text.splitlines():
        if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
            continue
        if line.startswith("-"):
            left.append({"text": line, "tone": "del"})
        elif line.startswith("+"):
            right.append({"text": line, "tone": "add"})
        elif line.strip():
            left.append({"text": "  " + line, "tone": "muted"})
            right.append({"text": "  " + line, "tone": "muted"})
    return {
        "left_title": "v1 (Rejected)",
        "right_title": "v2 (Final)",
        "left_lines": left[:14] or [{"text": "No removed lines.", "tone": "muted"}],
        "right_lines": right[:14] or [{"text": line, "tone": "add"} for line in _sql_lines(final_sql)[:12]],
    }


def _ast_preview(ast_data: dict[str, Any]) -> dict[str, Any]:
    lines: list[dict[str, Any]] = []

    def walk(value: Any, depth: int) -> None:
        if len(lines) >= 14 or depth > 5:
            return
        if isinstance(value, dict):
            for key, child in list(value.items())[:4]:
                label = str(key)
                if not isinstance(child, (dict, list)):
                    label += ": " + str(child)
                lines.append({"depth": depth, "label": label[:90]})
                walk(child, depth + 1)
                if len(lines) >= 14:
                    return
        elif isinstance(value, list):
            for index, child in enumerate(value[:3]):
                lines.append({"depth": depth, "label": "[" + str(index) + "]"})
                walk(child, depth + 1)
                if len(lines) >= 14:
                    return

    walk(ast_data.get("tree") if isinstance(ast_data, dict) else ast_data, 0)
    return {"lines": lines, "rows": len(lines)}


def _rag_summary(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        hits = block.get("hits") or []
        rows.append(
            {
                "title": block.get("title", ""),
                "node": block.get("node", ""),
                "count": len(hits),
                "top": hits[:3],
                "drawer_key": "rag-" + str(index),
            }
        )
    return rows


def _metric_cards(
    run: TestRun,
    risk_score: float | None,
    policy: dict[str, Any],
    business: dict[str, Any],
    pii: dict[str, Any],
    explain_stats: dict[str, str],
    tokens: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    risk_value = "unknown" if risk_score is None else f"{risk_score:g} / 10"
    token_value = "unknown" if not tokens else _fmt_count(tokens.get("total"))
    token_sub = (
        "No usage in trace"
        if not tokens
        else "In "
        + _fmt_count(tokens.get("input"))
        + " / Out "
        + _fmt_count(tokens.get("output"))
    )
    return [
        {
            "id": "risk",
            "label": "Risk Score",
            "value": risk_value,
            "sub": _risk_level(risk_score),
            "tone": "ok"
            if (risk_score is None or risk_score <= 3)
            else "warn"
            if risk_score <= 6
            else "bad",
            "drawer_key": "metric-risk",
        },
        {
            "id": "policy",
            "label": "Policy Checks",
            "value": policy["label"],
            "sub": policy["sub"],
            "tone": "ok" if policy.get("passed") == policy.get("total") else "warn",
            "drawer_key": "metric-policy",
        },
        {
            "id": "business",
            "label": "Business alignment",
            "value": business["label"],
            "sub": business["sub"],
            "tone": business["tone"],
            "drawer_key": "metric-business",
        },
        {
            "id": "pii",
            "label": "PII Risk",
            "value": pii["label"],
            "sub": pii["sub"],
            "tone": "ok" if pii["label"] == "Low" else "bad",
            "drawer_key": "metric-pii",
        },
        {
            "id": "rows",
            "label": "Rows (est.)",
            "value": explain_stats["rows"],
            "sub": "From EXPLAIN" if explain_stats["rows"] != "unknown" else "No estimate",
            "tone": "neutral",
            "drawer_key": "metric-rows",
        },
        {
            "id": "cost",
            "label": "Cost (est.)",
            "value": explain_stats["cost"],
            "sub": "Planner cost" if explain_stats["cost"] != "unknown" else "No estimate",
            "tone": "neutral",
            "drawer_key": "metric-cost",
        },
        {
            "id": "tokens",
            "label": "Tokens",
            "value": token_value,
            "sub": token_sub,
            "tone": "neutral",
            "drawer_key": "metric-tokens",
        },
    ]


def _metadata_rows(
    run: TestRun,
    report_id: str,
    trace_id: str,
    status: str,
    tokens: dict[str, Any] | None,
) -> list[list[dict[str, str]]]:
    meta = _metadata(run)
    model_version = str(meta.get("generator_model_key") or run.model_key or "unknown")
    model = str(
        meta.get("generator_model")
        or run.llm_generator_model
        or run.model_label
        or "unknown"
    )
    token_text = "unknown"
    if tokens:
        token_text = (
            "Total "
            + _fmt_count(tokens.get("total"))
            + " / Input "
            + _fmt_count(tokens.get("input"))
            + " / Output "
            + _fmt_count(tokens.get("output"))
        )
    rows = [
        [
            {"label": "Report ID", "value": report_id},
            {"label": "Run ID", "value": run.run_id},
            {"label": "Pipeline", "value": "ai-sql-security-pipeline"},
            {
                "label": "Environment",
                "value": os.environ.get("BOT_REPORT_ENV")
                or os.environ.get("APP_ENV")
                or os.environ.get("ENVIRONMENT")
                or "unknown",
            },
            {"label": "Request ID", "value": trace_id},
            {"label": "User / Service", "value": run.user_name or str(run.user_id)},
        ],
        [
            {"label": "Model", "value": model},
            {"label": "Model Version", "value": model_version},
            {"label": "Started At", "value": _fmt_time(run.started_at)},
            {"label": "Completed At", "value": _fmt_time(run.finished_at)},
            {"label": "Duration", "value": _fmt_sec(_trace_duration_sec(run))},
            {"label": "Status", "value": _status_text(status)},
            {"label": "Tokens", "value": token_text},
        ],
    ]
    prompt_items = _prompt_meta_summary(run.trace)
    if prompt_items:
        rows.append(prompt_items[:7])
    return rows


def _prompt_meta_summary(trace: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in prompt_trace.summarize_trace(trace).get("unique") or []:
        if not isinstance(item, dict):
            continue
        prompt_id = str(item.get("prompt_id") or "")
        prompt_type = str(item.get("prompt_type") or "")
        if not prompt_id or not prompt_type:
            continue
        version = item.get("prompt_version")
        source = str(item.get("prompt_source") or "unknown")
        value = prompt_id
        value += " / v" + str(version) if version is not None else " / legacy"
        rows.append({"label": "Prompt " + prompt_type, "value": value + " / " + source})
    return rows


def _attach_prompt_entries(
    timeline_steps: list[dict[str, Any]], prompt_timeline: dict[str, Any]
) -> list[dict[str, Any]]:
    by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in prompt_timeline.get("items") or []:
        if isinstance(item, dict):
            by_node[str(item.get("node") or "event")].append(_safe(item))
    for step in timeline_steps:
        step["prompt_entries"] = by_node.get(str(step.get("key") or ""), [])
    return timeline_steps


def _drawer_items(run: TestRun, data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {
        "summary": {
            "title": "Summary Details",
            "kind": "json",
            "value": {
                "verdict": data["verdict"],
                "status_label": data["status_label"],
                "needs_human": data["needs_human"],
                "human_reason": data["human_reason"],
                "risk_items": data["risk_items"],
            },
        },
        "final-sql": {"title": "Final SQL", "kind": "text", "value": data["final_sql"]},
        "diff": {
            "title": "Iteration Diff",
            "kind": "text",
            "value": "\n\n".join(item["text"] for item in data["diffs"])
            or "Diff is unavailable.",
        },
        "ast": {"title": "AST JSON", "kind": "json", "value": data["ast_data"]},
        "explain": {
            "title": "EXPLAIN JSON",
            "kind": "json",
            "value": data["explain_json"],
        },
        "audit-log": {
            "title": "Audit Log",
            "kind": "text",
            "value": data["audit_log"] or "Audit log is empty.",
        },
        "metric-risk": {
            "title": "Risk Score Evidence",
            "kind": "json",
            "value": {"risk_items": data["risk_items"], "score": data["risk_score"]},
        },
        "metric-policy": {
            "title": "Policy Check Evidence",
            "kind": "json",
            "value": data["policy_checks"],
        },
        "metric-business": {
            "title": "Business alignment",
            "kind": "json",
            "value": data["business_alignment"],
        },
        "metric-pii": {"title": "PII Risk Evidence", "kind": "json", "value": data["pii_risk"]},
        "metric-rows": {
            "title": "Rows Estimate Evidence",
            "kind": "text",
            "value": data["explain_text"] or "No EXPLAIN text.",
        },
        "metric-cost": {
            "title": "Cost Estimate Evidence",
            "kind": "text",
            "value": data["explain_text"] or "No EXPLAIN text.",
        },
        "metric-tokens": {
            "title": "Token Usage Evidence",
            "kind": "json",
            "value": data["token_usage"] or {"status": "unknown"},
        },
    }
    # Phase 0.7-v2 — Latency drawer items с pre-rendered HTML.
    llm_calls = data.get("llm_call_rows") or []
    candidate_groups = data.get("candidate_groups") or []
    cost_summary = data.get("cost_summary") or {}
    rag_summary = data.get("rag_timings_summary") or {}
    items["latency-calls"] = {
        "title": "Per-LLM-call breakdown",
        "subtitle": "wall-clock per call · tokens · cost · retries",
        "kind": "html",
        "value": _drawer_latency_calls_html(llm_calls),
    }
    items["latency-candidates"] = {
        "title": "Generate candidates",
        "subtitle": "parallel asyncio.gather · per-candidate wall",
        "kind": "html",
        "value": _drawer_latency_candidates_html(candidate_groups),
    }
    items["latency-cost"] = {
        "title": "Cost summary",
        "subtitle": "USD by model and role",
        "kind": "html",
        "value": _drawer_latency_cost_html(cost_summary),
    }
    items["latency-rag"] = {
        "title": "RAG sub-timings",
        "subtitle": "cold vs warm cache · 4 indices",
        "kind": "html",
        "value": _drawer_latency_rag_html(run, rag_summary),
    }
    items["latency-explain"] = {
        "title": "EXPLAIN sub-timings",
        "subtitle": "connect · setup · execute · fetch",
        "kind": "html",
        "value": _drawer_latency_explain_html(run),
    }
    items["latency-retries"] = {
        "title": "Retry log",
        "subtitle": "явный retry-loop (SDK auto-retry отключён)",
        "kind": "html",
        "value": _drawer_latency_retries_html(llm_calls),
    }

    for step in data.get("timeline_steps") or []:
        node = step.get("key")
        for round_item in step.get("rounds") or []:
            key = f"round-{node}-{round_item.get('number')}"
            detail = round_item.get("detail") or {}
            items[key] = {
                "title": f"{step.get('label') or node} · round {round_item.get('number')}",
                "subtitle": round_item.get("duration") or "",
                "kind": "round",
                "value": detail or round_item,
            }

    prompt_entries = [
        item for item in (data.get("prompt_timeline") or {}).get("items") or [] if isinstance(item, dict)
    ]
    prompt_by_event = {str(item.get("event_key") or ""): item for item in prompt_entries}
    for item in prompt_entries:
        key = str(item.get("key") or "")
        if not key:
            continue
        items[key] = {
            "title": str(item.get("title") or "Prompt exchange"),
            "subtitle": _prompt_drawer_subtitle(item),
            "kind": "prompt",
            "value": _safe(item),
        }

    for index, event in enumerate(data["events"]):
        event_data = {key: value for key, value in event.items() if not key.endswith("_json")}
        prompt_entry = prompt_by_event.get("event-" + str(index))
        if prompt_entry:
            items["event-" + str(index)] = {
                "title": "Event " + str(index + 1) + ": " + str(event.get("node", "event")),
                "subtitle": _prompt_drawer_subtitle(prompt_entry),
                "kind": "prompt",
                "value": _safe(prompt_entry),
            }
            continue
        items["event-" + str(index)] = {
            "title": "Event " + str(index + 1) + ": " + str(event.get("node", "event")),
            "kind": "json",
            "value": _safe(event_data),
        }
    for step in data["timeline_steps"]:
        items[step["drawer_key"]] = {
            "title": step["label"],
            "kind": "json",
            "value": {
                "step": step["label"],
                "duration": step["duration"],
                "events": step["events"],
                "prompt_entries": step.get("prompt_entries") or [],
            },
        }
    for index, block in enumerate(data["rag_blocks"]):
        items["rag-" + str(index)] = {
            "title": str(block.get("title") or "RAG hits"),
            "kind": "json",
            "value": _safe(block),
        }
    public = {key: value for key, value in data.items() if key != "drawer_items"}
    items["report-json"] = {"title": "Report JSON", "kind": "json", "value": _safe(public)}
    return items


def _prompt_drawer_subtitle(item: dict[str, Any]) -> str:
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    prompt_type = str(meta.get("prompt_type") or item.get("prompt_type") or item.get("node") or "prompt")
    prompt_id = str(meta.get("prompt_id") or item.get("prompt_id") or "")
    version = meta.get("prompt_version", item.get("prompt_version"))
    source = str(meta.get("prompt_source") or item.get("prompt_source") or "trace")
    parts = [prompt_type]
    if version is not None:
        parts.append("v" + str(version))
    elif prompt_id:
        parts.append("legacy")
    if source:
        parts.append(source)
    return " / ".join(parts)


def build_report_data(run: TestRun) -> dict[str, Any]:
    sql = _final_sql(run)
    sql_lines = _sql_lines(sql)
    timing_rows = _timing_rows(run)
    total_event_sec = round(sum(float(row["sec"]) for row in timing_rows), 3)
    ast_data = _ast_data(sql)
    trace_id = _metadata(run).get("trace_id") or run.trace.get("request_id") or run.run_id
    report_id = "rpt_" + str(run.run_id)
    verdict = _verdict(run)
    status = _status_label(run, verdict)
    explain_text = _explain_text(run)
    explain_json = _explain_json(run, explain_text)
    diffs = _diffs(run)
    findings = _findings(run)
    final_findings = _final_findings(run) or findings
    risk_score = _risk_score(run)
    policy = _policy_checks(run)
    business = _business_alignment(run, findings)
    pii = _pii_risk(final_findings)
    explain_stats = _explain_stats(explain_text)
    tokens = _token_usage(run)
    prompt_timeline = prompt_trace.build_prompt_trace(run.trace)
    timeline_steps = _attach_prompt_entries(_timeline_steps(run), prompt_timeline)
    rag_blocks = _rag_blocks(run)
    # Phase 0.7 — Latency breakdown
    llm_calls = _llm_call_rows(run)
    candidate_groups = _candidate_groups(llm_calls)
    cost_summary = _cost_summary(llm_calls)
    rag_timings_summary = _rag_timings_summary(run)
    latency_overview = _latency_overview(
        run, llm_calls, candidate_groups, cost_summary, rag_timings_summary
    )
    data = {
        "report_id": report_id,
        "request_id": trace_id,
        "pipeline_name": "ai-sql-security-pipeline",
        "environment": os.environ.get("BOT_REPORT_ENV")
        or os.environ.get("APP_ENV")
        or os.environ.get("ENVIRONMENT")
        or "unknown",
        "status_label": status,
        "status_text": _status_text(status),
        "verdict_label": _verdict_label(status),
        "verdict_tone": "ok"
        if status == "success"
        else "warn"
        if status == "abstain"
        else "bad",
        "verdict": verdict,
        "duration_sec": _duration_sec(run),
        "trace_duration_sec": _trace_duration_sec(run),
        "trace_duration_label": _fmt_sec(_trace_duration_sec(run)),
        "total_event_sec": total_event_sec,
        "total_event_label": _fmt_sec(total_event_sec),
        "iterations_used": run.system_result.get("iterations_used"),
        "needs_human": bool(_metadata(run).get("needs_human")),
        "human_reason": _metadata(run).get("human_reason", ""),
        "final_sql": sql,
        "sql_lines": sql_lines,
        "sql_line_count": len(sql_lines),
        "risk_items": _risk_items(run),
        "risk_score": risk_score,
        "risk_level": _risk_level(risk_score),
        "policy_checks": policy,
        "business_alignment": business,
        "pii_risk": pii,
        "token_usage": tokens,
        "node_rows": timing_rows,
        "timing_rows": timing_rows,
        "llm_call_rows": llm_calls,
        "candidate_groups": candidate_groups,
        "cost_summary": cost_summary,
        "rag_timings_summary": rag_timings_summary,
        "latency_overview": latency_overview,
        "timeline_steps": timeline_steps,
        "prompt_timeline": prompt_timeline,
        "prompt_summary": prompt_timeline.get("summary") or {},
        "sql_event_specs": _sql_event_specs(),
        "duration_segments": _duration_segments(timeline_steps),
        "rag_blocks": rag_blocks,
        "rag_summary": _rag_summary(rag_blocks),
        "diffs": diffs,
        "diff_view": _diff_view(diffs, sql),
        "ast_data": ast_data,
        "ast_text": _json_text(ast_data),
        "ast_preview": _ast_preview(ast_data),
        "explain_text": explain_text,
        "explain_json": explain_json,
        "explain_stats": explain_stats,
        "audit_log": _clip(str(run.system_result.get("audit_log") or "")),
        "events": [
            {
                **event,
                "inputs_json": _json_text(event.get("inputs") or {}),
                "outputs_json": _json_text(event.get("outputs") or {}),
                "details_json": _json_text(event.get("details") or {}),
            }
            for event in _events(run.trace)
        ],
        "events_stream": _events_stream(run),
        "trace_id": trace_id,
        "python_version": platform.python_version(),
        "rendered_at": datetime.now(timezone.utc).isoformat(),
    }
    data["metadata_rows"] = _metadata_rows(run, report_id, str(trace_id), status, tokens)
    data["metric_cards"] = _metric_cards(run, risk_score, policy, business, pii, explain_stats, tokens)
    data["drawer_items"] = _drawer_items(run, data)
    return data


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(("html", "xml")),
    )


def render(test_run: TestRun) -> str:
    """Вернуть standalone HTML по одному TestRun."""
    template = _env().get_template("test_report.html")
    return template.render(
        run=test_run,
        report=build_report_data(test_run),
        active_section="chat",
        audits_enabled=True,
    )


def _safe_name(run_id: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", run_id).strip("._")
    return name or "report"


def save_report(html: str, run_id: str) -> Path:
    """Сохранить отчет в BOT_REPORT_DIR и вернуть путь к файлу."""
    folder = Path(os.environ.get("BOT_REPORT_DIR", "data/bot/reports"))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (_safe_name(run_id) + ".html")
    path.write_text(html, encoding="utf-8")
    return path


def upload_to_php_server(
    html_path: Path,
    user_id: int,
    run_id: str,
) -> str | None:
    """
    Залить HTML-отчет на внешний PHP-сервер и вернуть URL.

    Сейчас функция-заглушка: local mode возвращает None, php_upload
    поднимает явный NotImplementedError. Контракт endpoint описан в ТЗ
    .cursor/!tmp/!TZ/2026-05-17/7-test_bot_telegram_spec.md раздел 13.
    """
    del html_path, user_id, run_id
    mode = os.environ.get("BOT_REPORT_DELIVERY_MODE", "local").strip().lower()
    if mode != "php_upload":
        return None
    raise NotImplementedError(
        "PHP upload не реализован. Поставьте BOT_REPORT_DELIVERY_MODE=local."
    )
