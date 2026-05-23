from __future__ import annotations

import hashlib
from typing import Any


PROMPT_NODES = {"generate", "audit", "review", "quality_review", "classifier_judge", "prompt_check"}


def build_prompt_trace(trace: dict[str, Any]) -> dict[str, Any]:
    events = trace.get("events") if isinstance(trace.get("events"), list) else []
    entries: list[dict[str, Any]] = []
    last_retrieve: dict[str, Any] | None = None
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        event = {**event, "_index": index}
        node = str(event.get("node") or "event")
        details = _dict(event.get("details"))
        if node == "retrieve":
            last_retrieve = event
        if not _has_prompt(details):
            continue
        entries.append(_entry_from_event(index, event, last_retrieve))
    return {
        "trace_id": trace.get("request_id"),
        "task": trace.get("task"),
        "items": entries,
        "summary": summarize_entries(entries),
    }


def summarize_trace(trace: dict[str, Any]) -> dict[str, Any]:
    return build_prompt_trace(trace)["summary"]


def summarize_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in entries:
        meta = _dict(item.get("meta"))
        prompt_type = str(meta.get("prompt_type") or item.get("prompt_type") or "")
        prompt_id = str(meta.get("prompt_id") or item.get("prompt_id") or "")
        version = meta.get("prompt_version", item.get("prompt_version"))
        version_text = "" if version is None else str(version)
        key = (prompt_type, prompt_id, version_text)
        if key in seen:
            continue
        seen.add(key)
        label = prompt_type or str(item.get("node") or "prompt")
        if version is not None:
            label += " v" + str(version)
        elif prompt_id:
            label += " legacy"
        unique.append(
            {
                "prompt_type": prompt_type,
                "prompt_id": prompt_id,
                "prompt_version": version,
                "prompt_sha256": meta.get("prompt_sha256") or item.get("prompt_sha256"),
                "prompt_source": meta.get("prompt_source") or item.get("prompt_source") or "trace",
                "label": label,
            }
        )
    label = ", ".join(item["label"] for item in unique[:4])
    if len(unique) > 4:
        label += " +" + str(len(unique) - 4)
    return {"count": len(entries), "unique": unique, "label": label}


def _entry_from_event(index: int, event: dict[str, Any], retrieve_event: dict[str, Any] | None) -> dict[str, Any]:
    node = str(event.get("node") or "event")
    details = _dict(event.get("details"))
    system_prompt = _prompt_text(details, "prompt_system") or _prompt_text(details, "prompt")
    user_prompt = _prompt_text(details, "prompt_user")
    meta = _prompt_meta(details, node, system_prompt)
    parts = _prompt_parts(system_prompt, user_prompt, node, meta)
    source_bundle = _source_bundle(event, retrieve_event)
    return {
        "key": "prompt-" + str(index),
        "event_key": "event-" + str(index),
        "event_index": index + 1,
        "node": node,
        "title": _title(node, index + 1, meta),
        "started_at": event.get("started_at"),
        "duration_sec": event.get("duration_sec"),
        "iteration": _iteration(event, details),
        "meta": meta,
        "prompt_type": meta.get("prompt_type"),
        "prompt_id": meta.get("prompt_id"),
        "prompt_version": meta.get("prompt_version"),
        "prompt_sha256": meta.get("prompt_sha256"),
        "prompt_source": meta.get("prompt_source"),
        "prompt_request_sha256": details.get("prompt_request_sha256"),
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "parts": parts,
        "sources": source_bundle,
        "raw": _compact_event(event),
    }


def _has_prompt(details: dict[str, Any]) -> bool:
    if isinstance(details.get("prompt_system"), str) or isinstance(details.get("prompt_user"), str):
        return True
    call = _dict(details.get("llm_call"))
    return isinstance(call.get("prompt"), str) or isinstance(call.get("prompt_user"), str)


def _prompt_text(details: dict[str, Any], key: str) -> str:
    value = details.get(key)
    if isinstance(value, str):
        return value
    call = _dict(details.get("llm_call"))
    call_key = "prompt" if key in {"prompt_system", "prompt"} else key
    value = call.get(call_key)
    return value if isinstance(value, str) else ""


def _prompt_meta(details: dict[str, Any], node: str, system_prompt: str) -> dict[str, Any]:
    meta = _dict(details.get("prompt_meta"))
    if not meta:
        meta = _dict(_dict(details.get("llm_call")).get("prompt_meta"))
    if not meta:
        prompt_type = _legacy_prompt_type(node)
        meta = {
            "prompt_id": "legacy:" + prompt_type,
            "prompt_type": prompt_type,
            "prompt_version": None,
            "prompt_sha256": _sha(system_prompt) if system_prompt else "",
            "prompt_source": "trace_legacy",
        }
    out = dict(meta)
    for key in ["prompt_id", "prompt_type", "prompt_version", "prompt_sha256", "prompt_source", "fallback_reason"]:
        if key not in out and key in details:
            out[key] = details.get(key)
    return out


def _legacy_prompt_type(node: str) -> str:
    if node == "audit":
        return "auditor_system"
    if node in {"review", "quality_review"}:
        return "quality_reviewer_system"
    if node == "classifier_judge":
        return "semantic_judge_system"
    if node == "prompt_check":
        return "prompt_check_judge_system"
    return "generator_system"


def _prompt_parts(system_prompt: str, user_prompt: str, node: str, meta: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    if system_prompt:
        parts.append(
            {
                "kind": "system",
                "label": "System prompt",
                "text": system_prompt,
                "source": _system_source(meta),
                "tooltip": "Versioned system prompt selected by prompt registry.",
                "tone": "system",
            }
        )
    for part in _split_user_prompt(user_prompt):
        part["tone"] = _tone(part["kind"])
        part["tooltip"] = _tooltip(part["kind"], node)
        parts.append(part)
    return parts


def _split_user_prompt(text: str) -> list[dict[str, Any]]:
    text = text or ""
    if not text.strip():
        return []
    headings = _find_headings(text)
    if not headings:
        return [{"kind": "user", "label": "User prompt", "text": text, "source": "assembled runtime prompt"}]
    parts: list[dict[str, Any]] = []
    if headings[0][0] > 0 and text[: headings[0][0]].strip():
        parts.append(_part("Context preface", text[: headings[0][0]], "solutions"))
    for idx, (start, label) in enumerate(headings):
        end = headings[idx + 1][0] if idx + 1 < len(headings) else len(text)
        section = text[start:end].strip()
        parts.append(_part(label, section, _kind_for_label(label)))
    return parts


def _find_headings(text: str) -> list[tuple[int, str]]:
    labels = [
        "Задача аналитика:",
        "Контекст из памяти (релевантные таблицы и шаблоны PostgreSQL):",
        "Контекст из памяти:",
        "Предыдущий SQL:",
        "Замечания аудитора (учитывай, но не повторяй буквально):",
        "SQL для проверки:",
        "Возможные классы рисков из памяти безопасности:",
        "Список чувствительных полей в схеме (таблица: колонки):",
        "Результаты быстрых детерминированных правил, которые уже отработали:",
    ]
    found: list[tuple[int, str]] = []
    for label in labels:
        pos = text.find(label)
        if pos >= 0:
            found.append((pos, label.rstrip(":")))
    found.extend((pos, line.strip("= ").strip()) for pos, line in _section_markers(text))
    found.sort(key=lambda item: item[0])
    dedup: list[tuple[int, str]] = []
    last = -1
    for pos, label in found:
        if pos == last:
            continue
        dedup.append((pos, label))
        last = pos
    return dedup


def _section_markers(text: str) -> list[tuple[int, str]]:
    markers: list[tuple[int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("===") and stripped.endswith("===") and len(stripped) < 140:
            markers.append((offset, stripped))
        offset += len(line)
    return markers


def _part(label: str, text: str, kind: str) -> dict[str, Any]:
    return {"kind": kind, "label": label, "text": text, "source": _source_for_kind(kind)}


def _kind_for_label(label: str) -> str:
    low = label.lower()
    if "задача" in low:
        return "task"
    if "контекст" in low or "релевантные таблицы" in low:
        return "rag_context"
    if "предыдущий sql" in low:
        return "previous_sql"
    if "замечания аудитора" in low:
        return "audit_feedback"
    if "sql для проверки" in low:
        return "sql_under_audit"
    if "классы рисков" in low:
        return "security_rag"
    if "чувствительных" in low:
        return "sensitive_fields"
    if "быстрых детерминированных" in low:
        return "guard_findings"
    if "уроки" in low or "solutions" in low:
        return "solutions"
    return "user"


def _source_for_kind(kind: str) -> str:
    return {
        "task": "user request",
        "rag_context": "RAG table/schema context",
        "solutions": "similar-task lessons",
        "previous_sql": "previous generator output",
        "audit_feedback": "previous auditor feedback",
        "sql_under_audit": "SQL candidate under audit",
        "security_rag": "security RAG memory",
        "sensitive_fields": "schema sensitivity map",
        "guard_findings": "deterministic guard/classifier output",
    }.get(kind, "assembled runtime prompt")


def _tooltip(kind: str, node: str) -> str:
    base = _source_for_kind(kind)
    if kind in {"rag_context", "solutions"}:
        return base + "; see Sources tab for retrieved chunks and index metadata."
    if kind in {"security_rag", "sensitive_fields"}:
        return base + "; generated for auditor prompt at runtime."
    if kind == "task":
        return "Original user task passed to " + node + "."
    return base + "."


def _tone(kind: str) -> str:
    if kind in {"system"}:
        return "system"
    if kind in {"rag_context", "solutions", "security_rag"}:
        return "context"
    if kind in {"task", "sql_under_audit"}:
        return "task"
    if kind in {"previous_sql", "audit_feedback", "guard_findings", "sensitive_fields"}:
        return "feedback"
    return "user"


def _source_bundle(event: dict[str, Any], retrieve_event: dict[str, Any] | None) -> dict[str, Any]:
    details = _dict(event.get("details"))
    retrieve_details = _dict(retrieve_event.get("details")) if isinstance(retrieve_event, dict) else {}
    return {
        "retrieve_event_index": None if retrieve_event is None else "event-" + str(_safe_index(retrieve_event)),
        "generation_context_chars": len(str(details.get("generation_context") or retrieve_details.get("generation_context") or "")),
        "rag_generation_hits": retrieve_details.get("rag_generation_hits") or details.get("rag_generation_hits") or [],
        "rag_sources": retrieve_details.get("rag_sources") or details.get("rag_sources") or {},
        "security_hits": details.get("rag_security_hits") or [],
        "rag_timings": details.get("rag_timings") or {},
    }


def _safe_index(event: dict[str, Any]) -> int:
    try:
        return int(event.get("_index") or 0)
    except (TypeError, ValueError):
        return 0


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "node": event.get("node"),
        "started_at": event.get("started_at"),
        "finished_at": event.get("finished_at"),
        "duration_sec": event.get("duration_sec"),
        "inputs": event.get("inputs") or {},
        "outputs": event.get("outputs") or {},
        "details": event.get("details") or {},
    }


def _iteration(event: dict[str, Any], details: dict[str, Any]) -> int | None:
    for block in (details, _dict(event.get("inputs")), _dict(event.get("outputs"))):
        value = block.get("iteration")
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _title(node: str, index: int, meta: dict[str, Any]) -> str:
    prompt_type = str(meta.get("prompt_type") or node)
    version = meta.get("prompt_version")
    title = str(index) + ". " + node.replace("_", ".") + " · " + prompt_type
    if version is not None:
        title += " v" + str(version)
    return title


def _system_source(meta: dict[str, Any]) -> str:
    prompt_id = str(meta.get("prompt_id") or "unknown")
    version = meta.get("prompt_version")
    source = str(meta.get("prompt_source") or "registry")
    if version is not None:
        return prompt_id + " / v" + str(version) + " / " + source
    return prompt_id + " / " + source


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
