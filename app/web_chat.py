from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app import llm_provider, prompt_registry, prompt_trace
from app import test_report
from app import trace as trace_store
from app.llm_provider import LLMConfigError, ProviderUnavailable
from app.pipeline_service import (
    MAX_ITERATIONS_LIMIT,
    LatencyConfigError,
    PipelineTimeout,
    execute_run,
    provider_error_message,
)


router = APIRouter(tags=["web-chat"])

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = ROOT / "app" / "templates" / "web_chat.html"
TEMPLATES_DIR = ROOT / "app" / "templates"
ASSET_DIR = ROOT / "app" / "static" / "web"
SHARED_ASSET_DIR = ROOT / "app" / "static" / "shared"
_LOCK = threading.Lock()
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class ChatCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    source: str = Field(default="web", max_length=40)


class ChatRunRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=12000)
    llm_mode: str | None = None
    llm_generator_model: str | None = None
    openrouter_provider: str | None = None
    judge_openrouter_provider: str | None = None
    codex_reasoning_effort: str | None = None
    judge_backend: str | None = None
    prompt_check_enabled: bool | None = None
    prompt_check_backend: str | None = None
    prompt_check_model: str | None = None
    prompt_check_openrouter_provider: str | None = None
    max_iterations: int = Field(default=MAX_ITERATIONS_LIMIT, ge=1, le=MAX_ITERATIONS_LIMIT)
    profile: bool = False


class SystemPromptCreateRequest(BaseModel):
    prompt_type: str = Field(..., max_length=80)
    name: str = Field(..., min_length=1, max_length=200)
    text: str = Field(..., max_length=200000)
    created_by: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=4000)


class SystemPromptUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=200000)
    created_by: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=4000)


class SystemPromptCloneRequest(BaseModel):
    created_by: str | None = Field(default=None, max_length=120)


@router.get("/", response_class=HTMLResponse)
def web_index(request: Request) -> HTMLResponse:
    return _shell(request, "chat")


@router.get("/chat", response_class=HTMLResponse)
def web_chat(request: Request) -> HTMLResponse:
    return _shell(request, "chat")


@router.get("/chat/new", response_class=HTMLResponse)
def web_chat_new(request: Request) -> HTMLResponse:
    return _shell(request, "chat")


@router.get("/chat/{chat_id}", response_class=HTMLResponse)
def web_chat_detail(chat_id: str, request: Request) -> HTMLResponse:
    del chat_id
    return _shell(request, "chat")


@router.get("/history", response_class=HTMLResponse)
def web_history(request: Request) -> HTMLResponse:
    return _shell(request, "history")


@router.get("/settings/prompts", response_class=HTMLResponse)
def web_system_prompts(request: Request) -> HTMLResponse:
    return _shell(request, "prompts")


@router.get("/prompts/system", response_class=HTMLResponse)
def web_system_prompts_alias(request: Request) -> HTMLResponse:
    return _shell(request, "prompts")


@router.get("/web/assets/{name}")
def web_asset(name: str) -> FileResponse:
    allowed = {"web_chat.css", "web_chat.js"}
    if name not in allowed:
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(ASSET_DIR / name)


@router.get("/web/static/shared/{name}")
def shared_asset(name: str) -> FileResponse:
    allowed = {"design_system.css", "shell.js"}
    if name not in allowed:
        raise HTTPException(status_code=404, detail="asset not found")
    path = SHARED_ASSET_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(path)


@router.get("/runs/{trace_id}", response_class=HTMLResponse)
def run_detail_page(trace_id: str, request: Request):
    if not _ID_RE.fullmatch(trace_id):
        return templates.TemplateResponse(
            request,
            "run_detail_placeholder.html",
            {
                "trace_id": trace_id,
                "trace_json_url": "",
                "message": "Trace id is missing or invalid for this run.",
                "active_section": "chat",
                "audits_enabled": True,
            },
            status_code=200,
        )

    trace_path = Path(os.environ.get("TRACES_DIR", "data/traces")) / f"{trace_id}.json"
    if trace_path.exists():
        rendered = _render_trace_as_report(trace_id, trace_path)
        if rendered is not None:
            return HTMLResponse(rendered, headers={"Cache-Control": "no-store"})
        return templates.TemplateResponse(
            request,
            "run_detail_placeholder.html",
            {
                "trace_id": trace_id,
                "trace_json_url": f"/web/api/traces/{trace_id}",
                "active_section": "chat",
                "audits_enabled": True,
            },
        )

    reports_dir = Path(os.environ.get("BOT_REPORTS_DIR", "data/bot/reports"))
    html_path = reports_dir / f"{trace_id}.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")

    return templates.TemplateResponse(
        request,
        "run_detail_placeholder.html",
        {
            "trace_id": trace_id,
            "trace_json_url": "",
            "message": "Trace JSON and HTML report were not found for this run.",
            "active_section": "chat",
            "audits_enabled": True,
        },
        status_code=200,
    )


@router.get("/web/api/config")
def web_config() -> dict[str, Any]:
    try:
        mode = llm_provider.describe_current_mode()
        llm_provider.validate_current_config()
        mode_error = None
    except LLMConfigError as exc:
        mode = {}
        mode_error = str(exc)
    options = llm_provider.list_model_options()
    default_key = _default_option_key(options, mode)
    return {
        "service": "SQL Security Studio",
        "trace_viewer_url": os.environ.get("TRACE_VIEWER_URL", "http://localhost:8502"),
        "models": options,
        "openrouter_provider_catalog": llm_provider.openrouter_provider_catalog(),
        "default_model_key": default_key,
        "mode": mode,
        "mode_error": mode_error,
        "max_iterations_limit": MAX_ITERATIONS_LIMIT,
        "judge_backends": llm_provider.list_judge_backend_options(),
        "default_judge_backend": llm_provider.current_judge_backend_key(),
        "prompt_check_backends": llm_provider.list_prompt_check_backend_options(),
        "default_prompt_check_backend": llm_provider.current_prompt_check_backend_key(),
        "generator_tool_mode_enabled": _env_bool("GENERATOR_TOOL_MODE", False),
    }


@router.get("/web/api/judge-backends")
def web_judge_backends() -> dict[str, Any]:
    return {
        "items": llm_provider.list_judge_backend_options(),
        "default_judge_backend": llm_provider.current_judge_backend_key(),
    }


@router.get("/web/api/prompt-check-backends")
def web_prompt_check_backends() -> dict[str, Any]:
    return {
        "items": llm_provider.list_prompt_check_backend_options(),
        "default_prompt_check_backend": llm_provider.current_prompt_check_backend_key(),
    }


@router.get("/web/api/system-prompts")
def list_system_prompts(prompt_type: str | None = None) -> dict[str, Any]:
    try:
        items = prompt_registry.list_prompts(prompt_type=prompt_type)
    except prompt_registry.PromptRegistryError as exc:
        raise _prompt_http_error(exc) from exc
    return {"items": items, "total": len(items), "prompt_types": list(prompt_registry.PROMPT_TYPES)}


@router.get("/web/api/system-prompts/{prompt_id}")
def get_system_prompt(prompt_id: str) -> dict[str, Any]:
    try:
        return {"prompt": prompt_registry.get_prompt(prompt_id)}
    except prompt_registry.PromptRegistryError as exc:
        raise _prompt_http_error(exc) from exc


@router.post("/web/api/system-prompts")
def create_system_prompt(req: SystemPromptCreateRequest) -> dict[str, Any]:
    try:
        prompt = prompt_registry.create_prompt(
            prompt_type=req.prompt_type,
            name=req.name,
            text=req.text,
            created_by=req.created_by,
            notes=req.notes,
        )
    except prompt_registry.PromptRegistryError as exc:
        raise _prompt_http_error(exc) from exc
    return {"prompt": prompt}


@router.post("/web/api/system-prompts/{prompt_id}/clone")
def clone_system_prompt(prompt_id: str, req: SystemPromptCloneRequest | None = None) -> dict[str, Any]:
    try:
        prompt = prompt_registry.clone_prompt(prompt_id, created_by=req.created_by if req else None)
    except prompt_registry.PromptRegistryError as exc:
        raise _prompt_http_error(exc) from exc
    return {"prompt": prompt}


@router.patch("/web/api/system-prompts/{prompt_id}")
def update_system_prompt(prompt_id: str, req: SystemPromptUpdateRequest) -> dict[str, Any]:
    try:
        prompt = prompt_registry.update_prompt(
            prompt_id,
            name=req.name,
            text=req.text,
            notes=req.notes,
            created_by=req.created_by,
        )
    except prompt_registry.PromptRegistryError as exc:
        raise _prompt_http_error(exc) from exc
    return {"prompt": prompt}


@router.post("/web/api/system-prompts/{prompt_id}/save-as-default")
def save_system_prompt_as_default(prompt_id: str, req: SystemPromptUpdateRequest) -> dict[str, Any]:
    try:
        prompt = prompt_registry.save_as_default_version(
            prompt_id,
            name=req.name,
            text=req.text,
            notes=req.notes,
            created_by=req.created_by,
        )
    except prompt_registry.PromptRegistryError as exc:
        raise _prompt_http_error(exc) from exc
    return {"prompt": prompt}


@router.post("/web/api/system-prompts/{prompt_id}/activate")
def activate_system_prompt(prompt_id: str) -> dict[str, Any]:
    try:
        prompt = prompt_registry.activate_prompt(prompt_id)
    except prompt_registry.PromptRegistryError as exc:
        raise _prompt_http_error(exc) from exc
    return {"prompt": prompt}


@router.post("/web/api/system-prompts/{prompt_id}/make-default")
def make_default_system_prompt(prompt_id: str) -> dict[str, Any]:
    try:
        prompt = prompt_registry.make_default(prompt_id)
    except prompt_registry.PromptRegistryError as exc:
        raise _prompt_http_error(exc) from exc
    return {"prompt": prompt}


@router.post("/web/api/system-prompts/{prompt_id}/archive")
def archive_system_prompt(prompt_id: str) -> dict[str, Any]:
    try:
        prompt = prompt_registry.archive_prompt(prompt_id)
    except prompt_registry.PromptRegistryError as exc:
        raise _prompt_http_error(exc) from exc
    return {"prompt": prompt}


def _default_option_key(options: list[dict[str, Any]], mode: dict[str, Any]) -> str:
    """Pick the option that matches current backend + generator model key."""
    contour = str(mode.get("mode") or "").strip()
    model_key = str(mode.get("generator_model_key") or "").strip()
    for item in options:
        if item.get("llm_mode") == contour and item.get("llm_generator_model") == model_key:
            return str(item.get("key") or "")
    for item in options:
        if item.get("llm_mode") == contour:
            return str(item.get("key") or "")
    return str(options[0].get("key") or "") if options else ""


@router.get("/web/api/traces/{trace_id}")
def web_trace_json(trace_id: str) -> FileResponse:
    if not _ID_RE.fullmatch(trace_id):
        raise HTTPException(status_code=400, detail="invalid trace_id")
    path = Path(os.environ.get("TRACES_DIR", "data/traces")) / f"{trace_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="trace not found")
    return FileResponse(path, media_type="application/json")


@router.get("/web/api/traces/{trace_id}/prompts")
def web_trace_prompts(trace_id: str) -> dict[str, Any]:
    trace = _load_trace_payload(trace_id)
    return {"prompt_trace": prompt_trace.build_prompt_trace(trace)}


@router.get("/web/api/chats/{chat_id}/progress")
def chat_progress(chat_id: str) -> dict[str, Any]:
    """Live pipeline progress для horizontal/vertical timeline в UI.

    Web-UI на /chat поллит этот endpoint пока pending-assistant активен.
    Источник правды — partial trace JSON (Trace.flush_partial пишет после
    каждого узла LangGraph). Возвращаем minimal timeline_steps в формате
    test_report.html (label, duration, status, active, drawer_key) +
    флаг complete + текущий active step.
    """
    chat = _read_chat(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    trace_id = str(chat.get("pending_trace_id") or "")
    if not trace_id:
        # Финальный run уже завершился — отдаём timeline по последнему
        # ассистент-сообщению, если у него есть trace_id.
        for msg in reversed(chat.get("messages") or []):
            if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("trace_id"):
                trace_id = str(msg["trace_id"])
                break
    if not trace_id or not _ID_RE.fullmatch(trace_id):
        return {"trace_id": "", "steps": [], "active_step": "", "complete": True, "partial": False}

    trace_path = Path(os.environ.get("TRACES_DIR", "data/traces")) / f"{trace_id}.json"
    if not trace_path.exists():
        return {
            "trace_id": trace_id,
            "steps": [],
            "active_step": "",
            "complete": False,
            "partial": False,
            "stage": "starting",
        }
    try:
        trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"trace_id": trace_id, "steps": [], "active_step": "", "complete": False, "partial": False}

    result = trace_data.get("result") if isinstance(trace_data.get("result"), dict) else {}
    meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    started = _parse_iso(trace_data.get("started_at"))
    finished = _parse_iso(trace_data.get("finished_at")) or started
    try:
        test_run = test_report.TestRun(
            run_id=trace_id,
            user_id=0,
            user_name="web",
            task=str(trace_data.get("task") or ""),
            model_key=str(meta.get("generator_model_key") or ""),
            model_label=str(meta.get("generator_model") or ""),
            llm_mode=str(meta.get("mode") or ""),
            llm_generator_model=str(meta.get("generator_model_key") or ""),
            started_at=started or datetime.now(timezone.utc),
            finished_at=finished or datetime.now(timezone.utc),
            system_result=result,
            trace=trace_data,
        )
        steps = test_report._timeline_steps(test_run)
    except Exception:  # noqa: BLE001
        steps = []

    is_partial = bool(trace_data.get("partial"))
    complete = (not is_partial) and bool(trace_data.get("finished_at"))
    stale_error: dict[str, Any] | None = None
    if is_partial and chat.get("pending_trace_id") == trace_id and _partial_trace_is_stale(trace_path):
        complete = True
        is_partial = False
        stale_error = {
            "type": "InterruptedRun",
            "code": "stale_after_restart",
            "message": "Pipeline run looks stale: no trace updates after the hard latency budget.",
        }
    # Если есть pending_trace_id, всё ещё считаем не завершённым с точки
    # зрения UI; финал зафиксирует /web/api/chats/{id}.
    if chat.get("pending_trace_id") == trace_id and stale_error is None:
        complete = False
    active_step = ""
    if not complete and steps:
        active_step = steps[-1].get("key") or ""

    public_steps = [
        {
            "key": step.get("key"),
            "label": step.get("label"),
            "duration": step.get("duration"),
            "sec": step.get("sec"),
            "status": step.get("status"),
            "active": (step.get("key") == active_step) if not complete else bool(step.get("active")),
            "color": step.get("color"),
            "drawer_key": step.get("drawer_key"),
            "event_count": len(step.get("events") or []),
        }
        for step in steps
    ]
    return {
        "trace_id": trace_id,
        "steps": public_steps,
        "iterations": _iteration_breakdown(trace_data),
        "active_step": active_step,
        "complete": complete,
        "partial": is_partial,
        "error": trace_data.get("error") or stale_error,
    }


def _iteration_breakdown(trace_data: dict[str, Any]) -> list[dict[str, Any]]:
    events = trace_data.get("events") if isinstance(trace_data.get("events"), list) else []
    out: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for event in events:
        if not isinstance(event, dict):
            continue
        node = str(event.get("node") or "")
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        if node == "generate":
            iteration = int(details.get("iteration") or len(out) + 1)
            current = {"iteration": iteration, "steps": [], "sec": 0.0}
            out.append(current)
        if current is None:
            continue
        if node not in {"retrieve", "generate", "sql_guard", "explain_sandbox", "audit", "decide", "revise"}:
            continue
        sec = _float(event.get("duration_sec"))
        current["sec"] = round(float(current.get("sec") or 0.0) + sec, 3)
        step = {"node": node, "sec": sec, "duration": _duration_label(sec)}
        if node == "generate":
            step["candidate_count"] = int(details.get("candidate_count") or 0)
            candidates = details.get("candidates") if isinstance(details.get("candidates"), list) else []
            step["candidate_seconds"] = [
                round(_float((item or {}).get("walltime_sec") or (item or {}).get("latency_sec")), 3)
                for item in candidates[:4]
                if isinstance(item, dict)
            ]
            step["candidates"] = [_public_candidate(item, idx, details) for idx, item in enumerate(candidates)]
            step["detail"] = {
                "kind": "generate",
                "iteration": iteration,
                "candidates": step["candidates"],
                "selector_scores": details.get("selector_scores") or [],
                "selected_index": details.get("selected_index"),
            }
        elif node == "audit":
            step["detail"] = {
                "kind": "audit",
                "iteration": current.get("iteration"),
                "prompt_user": details.get("prompt_user") or (details.get("llm_call") or {}).get("prompt_user"),
                "response_raw": details.get("response_raw") or (details.get("llm_call") or {}).get("response"),
                "merged_findings": details.get("merged_findings") or details.get("findings") or [],
                "classifier_output": details.get("classifier_output") or {},
                "stage4": {
                    "backend": details.get("judge_backend"),
                    "model": details.get("judge_model"),
                    "decision": details.get("judge_decision"),
                    "latency_sec": details.get("judge_latency_sec"),
                    "prompt_id": details.get("stage4_prompt_id") or details.get("judge_prompt_id"),
                    "prompt_version": details.get("stage4_prompt_version") or details.get("judge_prompt_version"),
                    "prompt_sha256": details.get("stage4_prompt_sha256") or details.get("judge_prompt_sha256"),
                },
                "approved": details.get("approved"),
                "overall_risk_score": details.get("overall_risk_score"),
                "security_risk_score": details.get("security_risk_score"),
                "quality_risk_score": details.get("quality_risk_score"),
                "summary": details.get("summary"),
            }
        current["steps"].append(step)
    return out[-8:]


def _public_candidate(item: Any, index: int, details: dict[str, Any] | None = None) -> dict[str, Any]:
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
        "selected": bool(item.get("selected_by_selector") or item.get("selected")),
    }


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _duration_label(sec: float) -> str:
    return f"{sec:.3f}s" if sec < 1 else f"{sec:.2f}s"


@router.get("/web/api/chats")
def list_chats(limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    items = [_summary(item) for item in _read_all()]
    return {"items": items[:limit], "total": len(items)}


@router.post("/web/api/chats")
def create_chat(req: ChatCreateRequest) -> dict[str, Any]:
    chat = _new_chat(title=req.title, source=req.source)
    _write_chat(chat)
    return {"chat": chat, "summary": _summary(chat)}


@router.get("/web/api/chats/{chat_id}")
def get_chat(chat_id: str) -> dict[str, Any]:
    chat = _read_chat(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return {"chat": chat, "summary": _summary(chat)}


@router.post("/web/api/chats/{chat_id}/messages")
async def run_chat_message(chat_id: str, req: ChatRunRequest) -> dict[str, Any]:
    chat = _read_chat(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")

    now = _now()
    task = req.task.strip()
    pending_trace_id = trace_store._build_request_id()
    user_msg = {
        "id": _msg_id(),
        "role": "user",
        "text": task,
        "created_at": now,
    }
    pending_assistant_msg = {
        "id": _msg_id(),
        "role": "assistant",
        "created_at": now,
        "status": "running",
        "pending": True,
        "trace_id": pending_trace_id,
        "summary": {"status": "running", "trace_id": pending_trace_id},
    }
    chat["messages"].append(user_msg)
    chat["messages"].append(pending_assistant_msg)
    chat["status"] = "running"
    chat["pending_trace_id"] = pending_trace_id
    chat["updated_at"] = now
    chat["title"] = chat.get("title") or _title_from_task(task)
    _write_chat(chat)

    try:
        with trace_store.request_id_override(pending_trace_id):
            result = await execute_run(
                task=task,
                llm_mode=req.llm_mode,
                llm_generator_model=req.llm_generator_model,
                openrouter_provider=req.openrouter_provider,
                judge_openrouter_provider=req.judge_openrouter_provider,
                codex_reasoning_effort=req.codex_reasoning_effort,
                judge_backend=req.judge_backend,
                prompt_check_enabled=req.prompt_check_enabled,
                prompt_check_backend=req.prompt_check_backend,
                prompt_check_model=req.prompt_check_model,
                prompt_check_openrouter_provider=req.prompt_check_openrouter_provider,
                max_iterations=req.max_iterations,
                profile=req.profile,
            )
        assistant_msg = _assistant_success(result, req)
        # Если pipeline по какой-то причине переписал trace_id, всё равно
        # используем pre-allocated id, чтобы UI'шный progress polling работал.
        assistant_msg.setdefault("trace_id", pending_trace_id)
        chat["status"] = assistant_msg["status"]
        chat["last_result"] = assistant_msg["summary"]
    except (LLMConfigError, LatencyConfigError) as exc:
        assistant_msg = _assistant_error("config_error", str(exc))
        assistant_msg["trace_id"] = pending_trace_id
        chat["status"] = "failed"
    except ProviderUnavailable as exc:
        assistant_msg = _assistant_error("provider_unavailable", provider_error_message(exc))
        assistant_msg["trace_id"] = pending_trace_id
        chat["status"] = "failed"
    except PipelineTimeout as exc:
        assistant_msg = _assistant_error("timeout", str(exc))
        assistant_msg["trace_id"] = pending_trace_id
        chat["status"] = "failed"
    except Exception as exc:  # noqa: BLE001
        assistant_msg = _assistant_error("pipeline_error", str(exc))
        assistant_msg["trace_id"] = pending_trace_id
        chat["status"] = "failed"

    # Уберём pending-сообщение и заменим на финальное.
    chat["messages"] = [m for m in chat["messages"] if not m.get("pending")]
    chat["messages"].append(assistant_msg)
    chat["last_result"] = assistant_msg.get("summary") or {}
    chat["pending_trace_id"] = None
    chat["updated_at"] = _now()
    _write_chat(chat)
    return {"chat": chat, "summary": _summary(chat), "message": assistant_msg}


def _render_trace_as_report(trace_id: str, trace_path: Path) -> str | None:
    """Render a JSON trace on-the-fly through the canonical test_report template.

    Phase 7 (2026-05-21): вместо pre-generated HTML отчёта в
    data/bot/reports/, конструируем TestRun из trace.json и передаём
    в test_report.render() — рендер тот же, что и Telegram-бот делает,
    но генерируется per-request без кеширования на диске. Если трейс
    кривой или у render'а не хватает данных, возвращаем None и роут
    падает на placeholder.
    """
    try:
        data = json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}

    started = _parse_iso(data.get("started_at"))
    finished = _parse_iso(data.get("finished_at")) or started

    try:
        test_run = test_report.TestRun(
            run_id=str(data.get("request_id") or trace_id),
            user_id=int(data.get("user_id") or 0),
            user_name=str(data.get("user_name") or "web"),
            task=str(data.get("task") or ""),
            model_key=str(meta.get("generator_model_key") or ""),
            model_label=str(meta.get("generator_model") or meta.get("generator_model_key") or ""),
            llm_mode=str(meta.get("mode") or ""),
            llm_generator_model=str(meta.get("generator_model_key") or ""),
            started_at=started or datetime.now(timezone.utc),
            finished_at=finished or datetime.now(timezone.utc),
            system_result=result,
            trace=data,
        )
        return test_report.render(test_run)
    except Exception:  # noqa: BLE001
        # Любая ошибка рендера — graceful fallback на placeholder,
        # чтобы UI не упал в 500 из-за неожиданной формы трейса.
        return None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        # `2026-05-17T21:34:04.891294+00:00` — fromisoformat умеет
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _shell(request: Request, active_section: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "web_chat.html",
        {
            "active_section": active_section,
            "asset_version": _asset_version(),
            "audits_enabled": True,
        },
    )


def _asset_version() -> str:
    paths = [ASSET_DIR / "web_chat.css", ASSET_DIR / "web_chat.js", TEMPLATE_PATH]
    version = 0
    for path in paths:
        try:
            version = max(version, int(path.stat().st_mtime))
        except OSError:
            continue
    return str(version)


def _web_dir() -> Path:
    path = Path(os.environ.get("WEB_CHAT_DIR", "data/web_chats"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _chat_path(chat_id: str) -> Path:
    if not _ID_RE.fullmatch(chat_id):
        raise HTTPException(status_code=404, detail="chat not found")
    return _web_dir() / (chat_id + ".json")


def _new_chat(title: str | None = None, source: str = "web") -> dict[str, Any]:
    now = _now()
    return {
        "chat_id": uuid.uuid4().hex[:12],
        "title": (title or "").strip(),
        "source": source,
        "status": "draft",
        "created_at": now,
        "updated_at": now,
        "messages": [],
        "last_result": {},
    }


def _read_chat(chat_id: str) -> dict[str, Any] | None:
    path = _chat_path(chat_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _read_all() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for file in sorted(_web_dir().glob("*.json"), reverse=True):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            items.append(data)
    items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return items


def _write_chat(chat: dict[str, Any]) -> None:
    chat_id = str(chat.get("chat_id") or "")
    path = _chat_path(chat_id)
    with _LOCK:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(chat, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def _assistant_success(result: dict[str, Any], req: ChatRunRequest) -> dict[str, Any]:
    meta = _dict(result.get("metadata"))
    return {
        "id": _msg_id(),
        "role": "assistant",
        "created_at": _now(),
        "status": _status_from_result(result),
        "result": result,
        "trace_id": meta.get("trace_id"),
        "llm_mode": req.llm_mode,
        "llm_generator_model": req.llm_generator_model,
        "openrouter_provider": req.openrouter_provider,
        "judge_openrouter_provider": req.judge_openrouter_provider,
        "codex_reasoning_effort": req.codex_reasoning_effort,
        "judge_backend": req.judge_backend,
        "prompt_check_enabled": req.prompt_check_enabled,
        "prompt_check_backend": req.prompt_check_backend,
        "prompt_check_model": req.prompt_check_model,
        "prompt_check_openrouter_provider": req.prompt_check_openrouter_provider,
        "summary": _result_summary(result),
    }


def _assistant_error(code: str, message: str) -> dict[str, Any]:
    return {
        "id": _msg_id(),
        "role": "assistant",
        "created_at": _now(),
        "status": "failed",
        "error": {"code": code, "message": message},
        "summary": {"status": "failed", "error": message},
    }


def _summary(chat: dict[str, Any]) -> dict[str, Any]:
    last = _dict(chat.get("last_result"))
    messages = chat.get("messages") if isinstance(chat.get("messages"), list) else []
    task = ""
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "user":
            task = str(msg.get("text") or "")
    prompt_summary = _prompt_summary_for_trace(str(last.get("trace_id") or ""))
    return {
        "chat_id": chat.get("chat_id"),
        "title": chat.get("title") or _title_from_task(task) or "New conversation",
        "source": chat.get("source") or "web",
        "status": chat.get("status") or last.get("status") or "draft",
        "task": task,
        "updated_at": chat.get("updated_at"),
        "created_at": chat.get("created_at"),
        "trace_id": last.get("trace_id"),
        "approved": last.get("approved"),
        "duration_sec": last.get("duration_sec"),
        "model": last.get("model") or chat.get("model_key"),
        "risk": last.get("risk"),
        "prompt_summary": prompt_summary,
        "prompt_summary_label": prompt_summary.get("label", ""),
    }


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    meta = _dict(result.get("metadata"))
    status = _status_from_result(result)
    return {
        "status": status,
        "approved": result.get("approved"),
        "trace_id": meta.get("trace_id"),
        "duration_sec": meta.get("duration_sec"),
        "model": meta.get("generator_model") or meta.get("generator_model_key"),
        "risk": result.get("overall_risk_score"),
        "iterations_used": result.get("iterations_used"),
        "final_sql": result.get("final_sql"),
        # Insight 7: surface policy_label / refusal_message / risk split so chat
        # UI may explain abstain decisions without forcing the user to open trace.
        "metadata": {
            "policy_label": meta.get("policy_label"),
            "refusal_message": meta.get("refusal_message"),
            "security_risk_score": meta.get("security_risk_score"),
            "quality_risk_score": meta.get("quality_risk_score"),
            "banned_identifiers": list(meta.get("banned_identifiers") or []),
            "human_reason": meta.get("human_reason"),
            "decision": meta.get("decision"),
        },
        "candidate_metrics": _extract_candidate_metrics(meta.get("trace_id")),
    }


def _extract_candidate_metrics(trace_id: str | None) -> list[dict[str, Any]]:
    """Вытащить per-iteration candidate metrics для UI temperature stats."""
    if not trace_id:
        return []
    try:
        payload = _load_trace_payload(trace_id)
    except HTTPException:
        return []
    out: list[dict[str, Any]] = []
    for ev in payload.get("events", []) or []:
        if ev.get("node") != "generate":
            continue
        iteration = (ev.get("outputs") or {}).get("iteration") or (ev.get("inputs") or {}).get("iteration")
        details = ev.get("details") or {}
        for cand in details.get("candidates") or []:
            if not isinstance(cand, dict):
                continue
            score = cand.get("selector_score") or {}
            out.append({
                "iteration": iteration,
                "candidate_index": cand.get("candidate_index"),
                "selected": bool(cand.get("selected_by_selector")),
                "temperature": cand.get("temperature"),
                "finding_count": score.get("finding_count"),
                "broken": bool(score.get("broken")),
                "labels": score.get("labels") or [],
            })
    return out


def _load_trace_payload(trace_id: str) -> dict[str, Any]:
    if not _ID_RE.fullmatch(trace_id):
        raise HTTPException(status_code=400, detail="invalid trace_id")
    path = Path(os.environ.get("TRACES_DIR", "data/traces")) / f"{trace_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="trace not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="trace is not readable") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="trace payload is not an object")
    return data


def _prompt_summary_for_trace(trace_id: str) -> dict[str, Any]:
    if not trace_id or not _ID_RE.fullmatch(trace_id):
        return {"count": 0, "unique": [], "label": ""}
    try:
        return prompt_trace.summarize_trace(_load_trace_payload(trace_id))
    except HTTPException:
        return {"count": 0, "unique": [], "label": ""}


def _status_from_result(result: dict[str, Any]) -> str:
    if not result:
        return "failed"
    meta = _dict(result.get("metadata"))
    if meta.get("error"):
        return "failed"
    if result.get("approved") is True:
        return "approved"
    return "needs_review"


def _title_from_task(task: str) -> str:
    text = " ".join(task.strip().split())
    return text[:80]


def _load_models() -> list[dict[str, Any]]:
    path = Path(os.environ.get("BOT_MODELS_CONFIG", "deploy/bot_models.json"))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    models = data.get("models")
    return models if isinstance(models, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _prompt_http_error(exc: prompt_registry.PromptRegistryError) -> HTTPException:
    if isinstance(exc, prompt_registry.PromptNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, prompt_registry.PromptConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, prompt_registry.PromptRegistryUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


def _partial_trace_is_stale(path: Path) -> bool:
    """Detect partial traces left behind by app restarts or killed requests."""
    try:
        age_sec = (datetime.now(timezone.utc).timestamp() - path.stat().st_mtime)
    except OSError:
        return False
    hard_sec = _env_int("LATENCY_HARD_SEC", 600)
    grace_sec = _env_int("WEB_CHAT_STALE_GRACE_SEC", 60)
    return age_sec > hard_sec + grace_sec


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _msg_id() -> str:
    return "msg_" + uuid.uuid4().hex[:12]
