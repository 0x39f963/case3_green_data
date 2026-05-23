"""
Phase 5 — Agentic tool-loop helper.

run_with_tools() реализует цикл "модель пишет → tool_calls → выполняем
→ result обратно в messages → модель пишет дальше → ... → текст без
tool_calls = финальный ответ".

Логика OpenAI/OpenRouter tool spec:
    assistant message содержит tool_calls = [{"id", "function": {"name", "arguments"}}]
    мы выполняем dispatch и отвечаем role="tool", tool_call_id=..., content=json
    цикл продолжается пока assistant.message.tool_calls пуст ИЛИ
    достигли max_steps.

Безопасность:
- max_steps cap (default 5). Это hard cap. Иначе зацикливание.
- ToolDispatcher уже изолирует exceptions внутри tools.
- Каждый шаг логирует tool_calls + tool_results в trace.event.details
  через возвращаемую структуру steps[].

Не блокирующий: если client.invoke() поднимает ProviderUnavailable —
exception всплывает наверх как раньше. Tool-loop не пытается ловить
ошибки провайдера.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app import tools as tools_module
from app.llm_provider import LLMClient, LLMResponse


DEFAULT_MAX_STEPS = 5
TOOL_RESULT_CHAR_LIMIT = 12000


def _head_tail(text: str, limit: int = TOOL_RESULT_CHAR_LIMIT) -> str:
    if len(text) <= limit:
        return text
    head = max(limit - 1000, 0)
    tail = min(1000, limit)
    return text[:head] + "\n\n[truncated]\n\n" + text[-tail:]


def _ordered_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _required_tools(final_text: str, called_tools: list[str]) -> list[str]:
    required = ["check_hallucination", "get_sensitive_fields", "explain_query"]
    upper = final_text.upper()
    if " JOIN " in upper or "get_approved_joins" in called_tools:
        required.append("get_approved_joins")
    return required


def _compliance(
    *,
    final_text: str,
    called_tools: list[str],
    last_results: dict[str, Any],
    stop_reason: str,
) -> dict[str, Any]:
    called = _ordered_unique(called_tools)
    required = _required_tools(final_text, called)
    missing = [name for name in required if name not in called]
    bad: list[str] = []

    check = last_results.get("check_hallucination")
    if isinstance(check, dict) and check.get("ok") is not True:
        bad.append("check_hallucination_failed")

    explain = last_results.get("explain_query")
    if isinstance(explain, dict):
        skipped = explain.get("skipped") is True
        if explain.get("ok") is not True and not skipped:
            bad.append("explain_query_failed")

    reasons = list(missing)
    reasons.extend(bad)
    if stop_reason == "no_tools_called":
        reasons.append("no_tools_called")
    elif stop_reason == "no_tool_call_loop":
        reasons.append("cycle_detected")
    elif stop_reason == "max_steps":
        reasons.append("max_steps")

    return {
        "required_tools": required,
        "called_tools": called,
        "missing_tools": missing,
        "last_results": last_results,
        "ok": not reasons,
        "degraded_reason": "; ".join(_ordered_unique(reasons)),
    }


def run_with_tools(
    client: LLMClient,
    system: str,
    user: str,
    *,
    tool_specs: list[dict[str, Any]] | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    temperature: float | None = None,
    tool_choice: str | dict | None = "auto",
) -> dict[str, Any]:
    """
    Запустить tool-loop до финального ответа.

    Returns:
        {
            "final_text": str,           # финальный ответ модели (SQL)
            "steps": [...],               # детали каждого шага
            "tool_calls_total": int,
            "tool_results_total": int,
            "responses": [LLMResponse],   # все ответы модели в порядке
            "elapsed_sec": float,
            "stop_reason": str,           # "final_text" | "max_steps" | "no_tool_call_loop"
        }
    """
    specs = tool_specs if tool_specs is not None else tools_module.ALL_SPECS

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    steps: list[dict[str, Any]] = []
    responses: list[LLMResponse] = []
    called_tools: list[str] = []
    last_results: dict[str, Any] = {}
    tool_calls_total = 0
    tool_results_total = 0
    stop_reason = "max_steps"
    started = time.perf_counter()

    for step_idx in range(max_steps):
        response = client.invoke(
            system,
            user,
            temperature=temperature,
            tools=specs,
            tool_choice=tool_choice,
            messages_override=messages,
        )
        responses.append(response)

        # Сохраняем assistant-message ВСЕГДА (даже если есть tool_calls).
        # OpenAI требует assistant с tool_calls перед tool-result-сообщениями.
        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if response.text:
            assistant_msg["content"] = response.text
        if response.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                    },
                }
                for call in response.tool_calls
            ]
        # OpenAI spec не любит пустые content+tool_calls — добавим null content
        if "content" not in assistant_msg and "tool_calls" not in assistant_msg:
            assistant_msg["content"] = ""
        messages.append(assistant_msg)

        step_log: dict[str, Any] = {
            "step": step_idx + 1,
            "model_text": response.text,
            "tool_calls": [],
            "walltime_sec": response.walltime_sec,
        }

        if not response.tool_calls:
            # финальный ответ
            stop_reason = "final_text"
            steps.append(step_log)
            break

        tool_calls_total += len(response.tool_calls)

        # Выполнить каждый tool_call и положить tool-result обратно в messages.
        for call in response.tool_calls:
            name = call["name"]
            args = call["arguments"]
            t_tool = time.perf_counter()
            try:
                result = tools_module.dispatch(name, args)
            except Exception as exc:  # noqa: BLE001
                result = {"error": str(exc), "exception_class": exc.__class__.__name__}
            called_tools.append(name)
            last_results[name] = result
            tool_elapsed = round(time.perf_counter() - t_tool, 3)
            tool_results_total += 1
            content = _head_tail(json.dumps(result, ensure_ascii=False))
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": content,
            })
            step_log["tool_calls"].append({
                "id": call["id"],
                "name": name,
                "arguments": args,
                "result": result,
                "elapsed_sec": tool_elapsed,
            })

        steps.append(step_log)

        # Защита от циклов: если две последние итерации только вызывали
        # один и тот же tool с теми же args — выходим.
        if step_idx >= 1:
            prev = steps[-2]
            this = steps[-1]
            if (
                prev.get("tool_calls")
                and this.get("tool_calls")
                and [(c["name"], json.dumps(c["arguments"], sort_keys=True)) for c in prev["tool_calls"]]
                == [(c["name"], json.dumps(c["arguments"], sort_keys=True)) for c in this["tool_calls"]]
            ):
                stop_reason = "no_tool_call_loop"
                break

    elapsed = round(time.perf_counter() - started, 3)
    final_text = responses[-1].text if responses else ""
    if stop_reason == "final_text" and tool_calls_total == 0:
        stop_reason = "no_tools_called"
    tool_compliance = _compliance(
        final_text=final_text,
        called_tools=called_tools,
        last_results=last_results,
        stop_reason=stop_reason,
    )
    return {
        "final_text": final_text,
        "steps": steps,
        "tool_calls_total": tool_calls_total,
        "tool_results_total": tool_results_total,
        "responses": responses,
        "elapsed_sec": elapsed,
        "stop_reason": stop_reason,
        "tool_compliance": tool_compliance,
        "messages_final": messages,
    }


__all__ = ["run_with_tools", "DEFAULT_MAX_STEPS"]
