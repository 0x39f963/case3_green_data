"""
Реализация SQLGenerator из контрактов заказчика.

Собирает приглашение из шаблонов app/prompts и зовет языковую модель
через единую точку llm_provider. На первой итерации - короткий промпт,
на повторных добавляем предыдущий SQL и сводку аудитора. После вызова
self.last_call экспонирует все: промпты, ответ модели, контекст RAG.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import sys
import os
from pathlib import Path
from typing import Any

_TASK3_ROOT = Path(__file__).resolve().parent.parent / "TASK-3"
if str(_TASK3_ROOT) not in sys.path:
    sys.path.insert(0, str(_TASK3_ROOT))

from baseline1 import SQLGenerator as _BaseSQLGenerator, AuditResult  # noqa: E402

from app import llm_provider, prompt_registry, rag_adapter, runtime_context  # noqa: E402

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    """Прочитать текст приглашения. Файлы маленькие, читаем каждый раз без кеша."""
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _strip_markdown(text: str) -> str:
    """
    Снять обертку ```sql ... ``` если модель ее добавила.
    Маленькие модели любят оборачивать SQL в markdown, нам это мешает
    при подаче запроса в EXPLAIN-песочницу.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        for prefix in ("sql\n", "SQL\n", "postgresql\n"):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
                break
        cleaned = cleaned.strip()
    return cleaned


def _summarize_audit(audit: AuditResult | None) -> str:
    """
    Короткая сводка от аудитора для следующей итерации генератора.
    Длинный технический отчет в приглашение не пихаем - маленькие модели
    путаются. Берем итоговый текст и до трех топ-уязвимостей.
    """
    if audit is None:
        return ""
    parts: list[str] = []
    if audit.summary:
        parts.append(audit.summary)
    if audit.vulnerabilities:
        top = sorted(audit.vulnerabilities, key=lambda v: v.risk_score, reverse=True)[:3]
        for vuln in top:
            parts.append("- " + vuln.vuln_class + ": " + vuln.description + " " + vuln.recommendation)
    return "\n".join(parts).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _generator_temperature() -> float:
    raw = os.environ.get("LLM_GENERATOR_TEMPERATURE", "0.3").strip()
    try:
        value = float(raw)
    except ValueError:
        return 0.3
    return value if math.isfinite(value) else 0.3


def _generator_temperatures(multi: bool = True) -> tuple[list[float], dict[str, str] | None]:
    """Build generator temperature schedule from env with visible safe fallback."""
    if not multi:
        return [0.1], None

    fallback = _generator_temperature()
    raw = os.environ.get("LLM_GENERATOR_TEMPERATURES", "").strip()
    if not raw:
        return [0.3, 0.6], None

    values: list[float] = []
    error = ""
    for part in raw.split(","):
        token = part.strip()
        if not token:
            error = "empty value"
            break
        try:
            value = float(token)
        except ValueError:
            error = "non-numeric value"
            break
        if not math.isfinite(value) or value < 0 or value > 2:
            error = "temperature out of range 0..2"
            break
        values.append(value)

    if error or not values:
        return [fallback, fallback], {
            "type": "temperature_config_error",
            "env": "LLM_GENERATOR_TEMPERATURES",
            "value": raw,
            "message": "Invalid LLM_GENERATOR_TEMPERATURES (" + (error or "empty list")
            + "); fallback to LLM_GENERATOR_TEMPERATURE.",
        }
    return values, None


def _temperature_applied_for_backend(backend: str) -> bool:
    return backend != "anthropic_cli"


def _candidate_parallel_supported(client: Any, parallel: bool, temperatures: list[float]) -> bool:
    if not parallel or len(temperatures) <= 1 or not hasattr(client, "invoke_async"):
        return False
    supported_fn = getattr(client, "candidate_parallel_supported", None)
    if callable(supported_fn) and not bool(supported_fn()):
        return False
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return True
    return False


def _candidate_max_parallel(client: Any, supported: bool, temperatures: list[float]) -> int:
    max_fn = getattr(client, "candidate_max_parallel", None)
    if callable(max_fn):
        try:
            return max(1, int(max_fn()))
        except (TypeError, ValueError):
            return 1
    return len(temperatures) if supported else 1


def _prompt_sha256(system_prompt: str, user_prompt: str) -> str:
    return hashlib.sha256((system_prompt + "\n\0\n" + user_prompt).encode("utf-8")).hexdigest()


def _prompt_trace_fields(record: prompt_registry.PromptRecord, user_prompt: str) -> dict[str, Any]:
    meta = record.meta
    return {
        "prompt_system": record.text,
        "prompt_user": user_prompt,
        "prompt_meta": meta,
        **meta,
        "prompt_request_sha256": _prompt_sha256(record.text, user_prompt),
    }


def _generate_candidates(
    client: Any,
    system_prompt: str,
    user_prompt: str,
    temperatures: list[float],
    parallel: bool,
) -> list[Any]:
    """
    Сгенерировать N кандидатов от LLM. Если parallel=True и клиент
    поддерживает invoke_async() — гоним через asyncio.gather (t = max).
    Иначе fallback на sequential (t = sum). Возвращает list[LLMResponse]
    в том же порядке, что и запросы.
    """
    if not temperatures:
        return []

    if _candidate_parallel_supported(client, parallel, temperatures):
        async def _run_one(temperature: float) -> Any:
            return await client.invoke_async(
                system_prompt, user_prompt, temperature=temperature
            )

        async def _run_all() -> list[Any]:
            return await asyncio.gather(*(_run_one(temp) for temp in temperatures))

        return asyncio.run(_run_all())

    return [
        client.invoke(system_prompt, user_prompt, temperature=temperature)
        for temperature in temperatures
    ]


class SQLGenerator(_BaseSQLGenerator):
    """
    Реализация генератора. Сохраняет сигнатуру generate() как требует baseline.

    После каждого вызова self.last_call содержит полные тексты промптов,
    использованный контекст из памяти Марины, сырой ответ модели и
    финальный SQL после очистки markdown.
    """

    def __init__(self, db_schema: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(db_schema=db_schema, **kwargs)
        self.last_call: dict[str, Any] = {}

    def _generate_with_tools(
        self,
        client: Any,
        task_description: str,
        regular_system_prompt: str,
        regular_user_prompt: str,
    ) -> str:
        """
        Phase 5 — agentic tool-loop generation.

        Использует app/tool_loop.run_with_tools с системным промптом
        generator_system_tools.txt. Модель пишет SQL, обязательно
        проходит через check_hallucination → get_approved_joins →
        get_sensitive_fields → explain_query, и только после этого
        возвращает финальный SQL. Multi-candidate здесь не работает —
        итерации делает сама модель внутри loop.

        regular_system_prompt/user_prompt передан для совместимости с
        текущим SQLGenerator.generate; tool-mode использует свой
        системный промпт generator_system_tools.txt, а user-промпт
        собирается из задачи + контекста.
        """
        from app import tool_loop, tools

        tools_record = prompt_registry.get_default_prompt("generator_tool_mode_system")
        tools_system = tools_record.text
        tools_meta = tools_record.meta
        max_steps = _env_int("GENERATOR_TOOL_MAX_STEPS", tool_loop.DEFAULT_MAX_STEPS)
        # user-prompt компактнее обычного: задача + schema-контекст без
        # extra инструкций (они уже в tools_system).
        user_prompt = (
            "Задача аналитика:\n" + task_description
            + "\n\n--- доступный контекст (схема и шаблоны) ---\n"
            + (regular_user_prompt or "")
        )

        result = tool_loop.run_with_tools(
            client,
            tools_system,
            user_prompt,
            tool_specs=tools.ALL_SPECS,
            max_steps=max_steps,
            temperature=_generator_temperature(),
            tool_choice="auto",
        )

        # Tool-mode даёт последовательность LLM-вызовов вместо
        # параллельных кандидатов. tool_llm_calls -- canonical поле для
        # benchmark store/ingest. candidates оставляем legacy-дубликатом
        # для untracked app/test_report.py, который пока читает только
        # details.candidates[*] для latency drawer.
        tool_llm_calls = []
        for step_idx, response in enumerate(result["responses"]):
            usage = response.usage_norm or llm_provider.extract_usage(response.raw)
            generation_id = (response.raw or {}).get("id")
            temp = _generator_temperature()
            temp_applied = _temperature_applied_for_backend(response.backend)
            temp_note = {} if temp_applied else {"temperature_note": "backend ignores temperature"}
            tool_llm_calls.append({
                "response": response.text,
                "prompt_meta": tools_meta,
                **tools_meta,
                "backend": response.backend,
                "model": response.model,
                "candidate_index": step_idx,
                "temperature": temp,
                "temperature_applied": temp_applied,
                **temp_note,
                "usage": usage,
                "response_usage": usage,
                "generation_id": generation_id,
                "response_generation_id": generation_id,
                "provider": (usage or {}).get("provider"),
                "latency_ms": int(response.walltime_sec * 1000) if response.walltime_sec else None,
                "walltime_sec": response.walltime_sec,
                "retry_log": response.retry_log,
                "response_headers": response.response_headers,
                "step": step_idx + 1,
                "tool_calls": response.tool_calls,
                "scheduling": "tool_loop",
            })

        sql = _strip_markdown(result["final_text"])
        candidate_details = []
        for item in tool_llm_calls:
            step = int(item.get("step") or 0)
            candidate_details.append({
                **item,
                "sql": sql if step == len(tool_llm_calls) else "",
                "legacy_tool_loop_candidate": True,
            })
        self.last_call = {
            **_prompt_trace_fields(tools_record, user_prompt),
            "generation_context": "",
            "allowed_objects": "",
            "response_raw": [r.text for r in result["responses"]],
            "response_sql": [sql],
            "candidates": candidate_details,
            "candidate_count": len(candidate_details),
            "multi_candidate": False,
            "scheduling": "tool_loop",
            "node_walltime_sec": result["elapsed_sec"],
            "temperature": _generator_temperature(),
            "temperature_schedule": [_generator_temperature()],
            "backend": result["responses"][0].backend if result["responses"] else "",
            "model": result["responses"][0].model if result["responses"] else "",
            "tool_mode": True,
            "tool_mode_degraded": False,
            "tool_loop_steps": result["steps"],
            "tool_llm_calls": tool_llm_calls,
            "tool_calls_total": result["tool_calls_total"],
            "tool_results_total": result["tool_results_total"],
            "tool_loop_stop_reason": result["stop_reason"],
            "stop_reason": result["stop_reason"],
            "tool_compliance": result["tool_compliance"],
        }
        del regular_system_prompt  # quiet linter
        return sql

    def generate(
        self,
        task_description: str,
        sql_history: list[str] | None = None,
        audit_feedback: AuditResult | None = None,
        iteration: int = 1,
        generation_context: str | None = None,
        allowed_objects: str = "",
        solutions_context: str = "",
        banned_identifiers: list[str] | None = None,
        intent_block: str = "",
    ) -> str | list[str]:
        """
        Сгенерировать SQL под текстовую задачу.

        Первая итерация собирает приглашение из задачи и контекста памяти.
        Повторные добавляют предыдущий SQL и сводку аудитора, чтобы
        модель видела, что именно нужно переписать. Возвращает строку SQL.

        solutions_context — Phase 2 петля обучения: уроки из похожих
        задач (rag_adapter.get_solutions_context). Идёт ТОЛЬКО на первой
        итерации; на ревизии уже есть конкретный audit_feedback, дополнять
        промпт ещё одной обобщённой подсказкой не нужно.
        """
        context = generation_context or rag_adapter.get_generation_context(task_description)
        runtime_block = runtime_context.build_runtime_context()
        system_record = prompt_registry.get_default_prompt("generator_system")
        system_prompt = system_record.text
        system_meta = system_record.meta

        banned_list = sorted(set(banned_identifiers or []))
        banned_block = (
            "BANNED_IDENTIFIERS: " + ", ".join(banned_list)
            + "\n(Эти идентификаторы запрещены полностью: ни в SELECT, ни в FROM/JOIN, ни в WHERE, ни как alias.)"
            if banned_list
            else "BANNED_IDENTIFIERS: (нет)"
        )

        intent_line = intent_block or "INTENT: unknown"
        if iteration <= 1 or not sql_history:
            template = _load_prompt("generator_user_first.txt")
            try:
                user_prompt = template.format(
                    task=task_description,
                    generation_context=context,
                    allowed_objects=allowed_objects,
                    solutions_context=solutions_context or "",
                    intent_block=intent_line,
                    runtime_context=runtime_block,
                )
            except KeyError:
                user_prompt = (
                    intent_line + "\n"
                    + template.format(
                        task=task_description,
                        generation_context=context,
                        allowed_objects=allowed_objects,
                        solutions_context=solutions_context or "",
                        runtime_context=runtime_block,
                    )
                )
        else:
            template = _load_prompt("generator_user_revision.txt")
            try:
                user_prompt = template.format(
                    iteration=iteration,
                    prior_sql=sql_history[-1],
                    audit_summary=_summarize_audit(audit_feedback) or "Без подробностей.",
                    generation_context=context,
                    allowed_objects=allowed_objects,
                    banned_identifiers=banned_block,
                    intent_block=intent_line,
                    runtime_context=runtime_block,
                )
            except KeyError:
                user_prompt = (
                    intent_line + "\n"
                    + template.format(
                        iteration=iteration,
                        prior_sql=sql_history[-1],
                        audit_summary=_summarize_audit(audit_feedback) or "Без подробностей.",
                        generation_context=context,
                        allowed_objects=allowed_objects,
                        banned_identifiers=banned_block,
                        runtime_context=runtime_block,
                    )
                )

        client = llm_provider.get_llm("generator")

        # Phase 5 — tool-mode (agentic loop). За фичефлагом, default off.
        # Если включён, модель использует tools и итерирует пока не
        # вернёт SQL без tool_calls. Multi-candidate и параллельность
        # в tool-mode не применимы (модель сама себя корректирует).
        tool_mode_requested = _env_bool("GENERATOR_TOOL_MODE", False)
        tool_mode_supported = bool(getattr(client, "supports_tools", False))
        if tool_mode_requested and tool_mode_supported and hasattr(client, "invoke"):
            return self._generate_with_tools(client, task_description, system_prompt, user_prompt)

        multi = _env_bool("LLM_MULTI_CANDIDATE", True)
        temperatures, temperature_error = _generator_temperatures(multi)
        parallel = multi and len(temperatures) > 1 and _env_bool("LLM_PARALLEL_CANDIDATES", True)
        parallel_supported = _candidate_parallel_supported(client, parallel, temperatures)
        effective_scheduling = "parallel" if parallel_supported else "sequential"
        candidate_backend = str(getattr(client, "backend", "") or "")
        candidate_max_parallel = _candidate_max_parallel(client, parallel_supported, temperatures)

        # Phase 0.3 — multi-candidate параллельно через asyncio.gather на
        # AsyncOpenAI клиенте. Это превращает t(узла generate) из t1+t2 в
        # max(t1, t2) и снимает половину 20-30с патологии, если узкое
        # место в sequential кандидатах. Для клиентов без реального async
        # support или если флаг выключен — fallback на последовательный вызов.
        responses = _generate_candidates(
            client, system_prompt, user_prompt, temperatures, parallel
        )
        candidates = [_strip_markdown(item.text) for item in responses]
        sql = candidates if multi else candidates[0]
        candidate_details = []
        for idx, (response, candidate_sql, temperature) in enumerate(zip(responses, candidates, temperatures)):
            usage = response.usage_norm or llm_provider.extract_usage(response.raw)
            generation_id = (response.raw or {}).get("id")
            temp_applied = _temperature_applied_for_backend(response.backend)
            temp_note = {} if temp_applied else {"temperature_note": "backend ignores temperature"}
            candidate_details.append(
                {
                    "candidate_index": idx,
                    "prompt_meta": system_meta,
                    **system_meta,
                    "sql": candidate_sql,
                    "response": response.text,
                    "backend": response.backend,
                    "model": response.model,
                    "temperature": temperature,
                    "temperature_applied": temp_applied,
                    **temp_note,
                    "usage": usage,
                    "response_usage": usage,
                    "generation_id": generation_id,
                    "response_generation_id": generation_id,
                    "provider": (usage or {}).get("provider"),
                    "latency_ms": int(response.walltime_sec * 1000) if response.walltime_sec else None,
                    "walltime_sec": response.walltime_sec,
                    "retry_log": response.retry_log,
                    "response_headers": response.response_headers,
                }
            )

        # Walltime узла: при parallel это max, при sequential — sum.
        # node_walltime_sec вычисляется здесь чтобы test_report мог
        # сравнить с trace.event.duration_sec и подсветить overhead графа.
        walltimes = [r.walltime_sec for r in responses if r.walltime_sec is not None]
        if effective_scheduling == "parallel" and len(responses) > 1:
            node_walltime_sec = max(walltimes) if walltimes else 0.0
        else:
            node_walltime_sec = sum(walltimes)

        self.last_call = {
            **_prompt_trace_fields(system_record, user_prompt),
            "generation_context": context,
            "allowed_objects": allowed_objects,
            "response_raw": [item.text for item in responses],
            "response_sql": candidates,
            "candidates": candidate_details,
            "candidate_count": len(candidates),
            "multi_candidate": multi,
            "scheduling": effective_scheduling,
            "candidate_parallel_requested": parallel,
            "candidate_parallel_supported": parallel_supported,
            "candidate_parallel_backend": candidate_backend or (responses[0].backend if responses else ""),
            "candidate_effective_scheduling": effective_scheduling,
            "candidate_max_parallel": candidate_max_parallel,
            "node_walltime_sec": round(node_walltime_sec, 3) if node_walltime_sec else 0.0,
            "temperature": temperatures[0] if temperatures else None,
            "temperature_schedule": temperatures,
            "backend": responses[0].backend if responses else "",
            "model": responses[0].model if responses else "",
            "iteration": iteration,
        }
        if temperature_error:
            self.last_call["temperature_config_error"] = temperature_error
        if tool_mode_requested and not tool_mode_supported:
            self.last_call.update({
                "tool_mode": False,
                "tool_mode_degraded": True,
                "degraded_reason": "client_without_tool_support",
            })

        return sql
