"""
Мета-аудитор петли обучения (Phase 2 плана).

Не часть продуктового пути. Отдельный оффлайн-контур: cron берёт
pipeline_runs из Store WHERE meta_audited=false, на каждом запускает
Claude CLI Opus 4.7 локально, получает структурированный JSON-урок и
кладёт его в benchmark.rag_embeddings(index_name='solutions') с
эмбеддингом multilingual-e5-small.

В следующий раз генератор на похожей задаче подтянет урок через
rag_adapter.get_solutions_context() — это закрывает «как система
учится между прогонами без правок кода».

Контракт baseline1.py не трогаем.

Запуск:
    python -m app.meta_auditor --trace-id <id>            # один trace
    python scripts/run_meta_audit.py --limit 20            # cron: до 20 свежих

Зависимости (локально):
    claude CLI (npm i -g @anthropic-ai/claude-code) с авторизацией.
    multilingual-e5-small (sentence-transformers) — тот же, что в RAG.
    psycopg2-binary для Postgres.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

from app import llm_provider, prompt_registry

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# Системный промпт Claude Opus 4.7 для мета-аудита. Жёсткая JSON-схема
# чтобы парсинг не падал; запрет на выдумывание (если данных нет — пишем
# unknown). Адаптация конкурентского prompt (meta_auditor/opus_reviewer.py)
# под наш набор узлов и метрик.
SYSTEM_PROMPT = """\
Ты старший эксперт по безопасности SQL и архитектуре LLM-агентов.

Тебе передают компактный лог одного прогона нашей системы:
- Generator пишет SQL по тексту задачи.
- Classifier (Stage 0-5) и sql_guard детектят риски детерминистски.
- LLM-Auditor проверяет SQL семантически.
- Они итерируют до 5 раз пока auditor не одобрит или не исчерпан бюджет.

Задача мета-анализа:
1. Назвать тип задачи (simple_select | join | aggregation | window_function |
   cte | plpgsql | mixed | unknown).
2. Описать паттерны ошибок генератора (если есть).
3. Дать вердикт по аудитору (correct | too_strict | too_lenient |
   partially_correct | unknown).
4. Сформулировать правильный подход к задаче (1-3 предложения).
5. Дать конкретный действенный урок для будущего генератора (2-4 предложения,
   начинать с действия: "Для задач типа X нужно ...").
6. Сформировать searchable_text (5-8 предложений) — это поле уйдёт в RAG
   и будет найдено при семантически похожих задачах в будущем.

Верни СТРОГО ОДИН JSON-объект без markdown-обёртки. Схема:

{
  "task_type": str,
  "generator_errors": [str, ...],
  "auditor_verdict": str,
  "auditor_notes": str,
  "correct_sql_approach": str,
  "lesson_for_generator": str,
  "searchable_text": str
}

Если данных недостаточно — пиши "unknown" в строке, [] в списке. Не выдумывай
имена таблиц или классы уязвимостей которых нет в логе.
"""


@dataclass
class MetaAuditResult:
    """Что вернул Claude + что мы дополнительно знаем о прогоне."""
    trace_id: str
    task_description: str
    task_type: str
    generator_errors: list[str]
    auditor_verdict: str
    auditor_notes: str
    correct_sql_approach: str
    lesson_for_generator: str
    searchable_text: str
    iterations_used: int
    approved: bool
    reviewer_model: str

    def to_metadata(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "task_description": self.task_description,
            "generator_errors": self.generator_errors,
            "auditor_verdict": self.auditor_verdict,
            "auditor_notes": self.auditor_notes,
            "correct_sql_approach": self.correct_sql_approach,
            "lesson_for_generator": self.lesson_for_generator,
            "iterations_used": self.iterations_used,
            "approved": self.approved,
            "reviewer_model": self.reviewer_model,
        }


# ── Embedding singleton ──────────────────────────────────────────────────────

_EMBED_MODEL = None


def _get_embedder():
    """Ленивая загрузка multilingual-e5-small. Тот же, что в RAG-адаптере."""
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        from sentence_transformers import SentenceTransformer
        _EMBED_MODEL = SentenceTransformer("intfloat/multilingual-e5-small")
    return _EMBED_MODEL


def _embed(text: str) -> list[float]:
    """Эмбедд под passage-протокол e5 (prefix 'passage: ' для документов)."""
    model = _get_embedder()
    vec = model.encode(
        ["passage: " + text],
        normalize_embeddings=True,
    )[0]
    return [float(x) for x in vec]


# ── Postgres ────────────────────────────────────────────────────────────────

def _bench_dsn() -> str:
    """DSN benchmark Postgres. Берётся из BENCHMARK_DSN или дефолт."""
    dsn = os.environ.get("BENCHMARK_DSN", "").strip()
    if dsn:
        return dsn
    user = os.environ.get("BENCH_USER", "bench")
    password = os.environ.get("BENCH_PASSWORD", "bench")
    host = os.environ.get("BENCH_PG_HOST", "127.0.0.1")
    port = os.environ.get("BENCH_PG_PORT", "15432")
    db = os.environ.get("BENCH_DB", "bench")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _load_run_from_store(trace_id: str) -> dict[str, Any]:
    """Чтение pipeline_run + llm_calls + findings из benchmark.* по trace_id."""
    with psycopg2.connect(_bench_dsn()) as conn:
        conn.set_session(readonly=True)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT trace_id, case_id, model_key, decision, approved,
                       needs_human, iterations_used, overall_risk_score,
                       duration_sec, final_sql_text AS final_sql,
                       generator_model, auditor_model, meta_audited
                FROM benchmark.pipeline_runs
                WHERE trace_id = %s
                """,
                (trace_id,),
            )
            run = cur.fetchone()
            if run is None:
                raise RuntimeError(
                    "trace_id " + trace_id + " не найден в benchmark.pipeline_runs"
                )
            cur.execute(
                """
                SELECT node, iteration, role, model, provider_header, provider,
                       walltime_sec, prompt_tokens, completion_tokens,
                       reasoning_tokens, cached_tokens, cost_usd, retries_count
                FROM benchmark.llm_calls
                WHERE trace_id = %s
                ORDER BY id
                """,
                (trace_id,),
            )
            llm_calls = cur.fetchall()
            cur.execute(
                """
                SELECT node, label, severity, risk_score, confidence, detector,
                       evidence_span
                FROM benchmark.findings
                WHERE trace_id = %s
                ORDER BY id
                """,
                (trace_id,),
            )
            findings = cur.fetchall()
            cur.execute(
                """
                SELECT COALESCE(
                    payload_jsonb->>'task',
                    payload_jsonb->'trace'->>'task',
                    payload_jsonb->'client_meta'->'case'->>'user_task',
                    payload_jsonb->'case'->>'user_task'
                ) AS task
                FROM benchmark.raw_payloads
                WHERE trace_id = %s
                """,
                (trace_id,),
            )
            row = cur.fetchone()
            task = (row or {}).get("task") or ""
    return {
        "run": dict(run),
        "task": task,
        "llm_calls": [dict(r) for r in llm_calls],
        "findings": [dict(r) for r in findings],
    }


def _mark_meta_audited(trace_id: str) -> None:
    """Поставить флаг meta_audited=true чтобы cron не разбирал дважды."""
    with psycopg2.connect(_bench_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE benchmark.pipeline_runs SET meta_audited = TRUE WHERE trace_id = %s",
                (trace_id,),
            )
        conn.commit()


def _save_lesson(result: MetaAuditResult) -> int:
    """Эмбедд searchable_text и INSERT в benchmark.rag_embeddings(solutions)."""
    text = result.searchable_text or result.lesson_for_generator
    if not text:
        return 0
    vec = _embed(text)
    metadata = result.to_metadata()
    metadata["trace_id"] = result.trace_id
    with psycopg2.connect(_bench_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO benchmark.rag_embeddings
                    (index_name, text, metadata, embedding, source_trace_id)
                VALUES (%s, %s, %s::jsonb, %s, %s)
                RETURNING id
                """,
                (
                    "solutions",
                    text,
                    json.dumps(metadata, ensure_ascii=False),
                    vec,
                    result.trace_id,
                ),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
    return int(new_id)


# ── Compact input для Claude ─────────────────────────────────────────────────

def _build_user_prompt(payload: dict[str, Any]) -> str:
    """Сжать pipeline_run + llm_calls + findings в компактный текст."""
    run = payload["run"]
    parts: list[str] = []
    parts.append("=== ЗАДАЧА ПОЛЬЗОВАТЕЛЯ ===")
    parts.append((payload.get("task") or "<unknown>").strip())
    parts.append("")
    parts.append("=== ПРОГОН ===")
    parts.append(
        "trace_id={tid} iterations={it} approved={ap} risk={rs}"
        .format(
            tid=run["trace_id"],
            it=run["iterations_used"],
            ap=run["approved"],
            rs=run.get("overall_risk_score"),
        )
    )
    parts.append("decision={d} needs_human={nh}".format(
        d=run.get("decision"), nh=run.get("needs_human")
    ))
    parts.append("generator_model={g} auditor_model={a}".format(
        g=run.get("generator_model"), a=run.get("auditor_model")
    ))
    parts.append("")
    parts.append("=== FINAL SQL ===")
    parts.append(run.get("final_sql") or "<empty>")
    parts.append("")
    parts.append("=== LLM ВЫЗОВЫ ===")
    for call in payload.get("llm_calls", [])[:30]:
        parts.append(
            "[{n}/{it}] {r} {m} via {prov} | walltime={w}s, in={pt}, out={ct}, reasoning={rt}, cost=${cu}, retries={rc}"
            .format(
                n=call.get("node"),
                it=call.get("iteration"),
                r=call.get("role"),
                m=call.get("model"),
                prov=call.get("provider_header") or call.get("provider"),
                w=call.get("walltime_sec"),
                pt=call.get("prompt_tokens"),
                ct=call.get("completion_tokens"),
                rt=call.get("reasoning_tokens") or 0,
                cu=call.get("cost_usd") or 0,
                rc=call.get("retries_count") or 0,
            )
        )
    parts.append("")
    parts.append("=== FINDINGS (правила + LLM-аудит) ===")
    for finding in payload.get("findings", [])[:40]:
        parts.append(
            "[{n}] {lab} severity={sev} risk={rs} conf={cf} detector={d} evidence={ev}"
            .format(
                n=finding.get("node"),
                lab=finding.get("label"),
                sev=finding.get("severity"),
                rs=finding.get("risk_score"),
                cf=finding.get("confidence"),
                d=finding.get("detector"),
                ev=(finding.get("evidence_span") or "")[:120],
            )
        )
    parts.append("")
    parts.append("Верни JSON по схеме из системной инструкции.")
    return "\n".join(parts)


# ── Claude CLI ───────────────────────────────────────────────────────────────

def _claude_cli(system: str, user: str, *, model: str, timeout_sec: int) -> str:
    """
    Вызов `claude -p "<user>" --append-system-prompt "<system>" --output-format json`.

    Возвращает текстовый ответ модели (поле result в JSON, или сам JSON если
    плагин вернул plain text).
    """
    cli = os.environ.get("ANTHROPIC_CLI_PATH", "claude")
    binary = shutil.which(cli) or cli
    cmd = [
        binary,
        "-p",
        user,
        "--append-system-prompt",
        system,
        "--output-format",
        "json",
    ]
    if model:
        cmd.extend(["--model", model])
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "claude CLI exit={c}: {err}".format(c=proc.returncode, err=proc.stderr.strip()[:500])
        )
    raw = proc.stdout.strip()
    # claude CLI с --output-format json возвращает {"type": "result", "result": "<text>"}
    try:
        wrap = json.loads(raw)
        if isinstance(wrap, dict) and "result" in wrap:
            return str(wrap["result"]).strip()
    except json.JSONDecodeError:
        pass
    return raw


def _parse_response(text: str) -> dict[str, Any]:
    """Распарсить JSON-объект из ответа модели. Markdown-обёртку срезает."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        for prefix in ("json\n", "JSON\n"):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
                break
    # Если модель добавила prose до/после JSON — берём первый {...} блок.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if 0 <= start < end:
        cleaned = cleaned[start: end + 1]
    return json.loads(cleaned)


def _build_case_quality_payload(payload: dict[str, Any]) -> dict[str, Any]:
    run = dict(payload.get("run") or {})
    findings = payload.get("findings") or []
    return {
        "user_task": payload.get("task") or "",
        "generated_sql": run.get("final_sql") or "",
        "audit_findings": findings,
        "decision": run.get("decision"),
        "approved": run.get("approved"),
        "iterations_used": run.get("iterations_used"),
        "llm_calls": payload.get("llm_calls") or [],
        "trace_id": run.get("trace_id"),
        "case_id": run.get("case_id"),
        "model_key": run.get("model_key"),
    }


def _validate_case_quality(data: dict[str, Any]) -> None:
    scores = data.get("sub_scores")
    if not isinstance(scores, dict):
        raise ValueError("sub_scores must be object")
    required = {
        "sql_correctness",
        "security",
        "intent_fidelity",
        "schema_usage",
        "rag_facts_used",
        "decision_rationale",
        "performance",
        "robustness",
        "retry_efficiency",
    }
    missing = sorted(required - set(scores))
    if missing:
        raise ValueError("missing sub_scores: " + ", ".join(missing))
    for name in required:
        value = int(scores.get(name))
        if value < 0 or value > 10:
            raise ValueError(name + " must be in 0..10")
        scores[name] = value
    patch = data.get("patch_suggestion")
    if not isinstance(patch, dict):
        raise ValueError("patch_suggestion must be object")
    if patch.get("target_area") not in {
        "generator_prompt",
        "auditor_prompt",
        "faiss_corpus",
        "schema_overlay",
        "sql_guard_rule",
        "none",
    }:
        raise ValueError("invalid patch_suggestion.target_area")
    if patch.get("severity") not in {"P0", "P1", "P2", "P3"}:
        raise ValueError("invalid patch_suggestion.severity")


def _quality_client(backend: str, model: str) -> llm_provider.LLMClient:
    key = backend.replace("-", "_")
    if key == "claude_cli":
        key = "anthropic_cli"
    role = "generator" if key == "codex_cli" else "direct"
    return llm_provider._build_direct_client(key, model, role=role)  # type: ignore[attr-defined]


def _usage_cost(usage: dict[str, Any]) -> float:
    return round(
        int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0) / 1000 * 0.005
        + int(usage.get("completion_tokens") or usage.get("output_tokens") or 0) / 1000 * 0.030
        + int(usage.get("cached_tokens") or 0) / 1000 * 0.0005,
        6,
    )


def review_case_quality(trace_id: str, backend: str = "codex_cli", model: str = "gpt-5.5") -> dict[str, Any]:
    """Return strict 9-score case quality JSON plus reviewer metadata."""
    payload = _load_run_from_store(trace_id)
    judge_input = _build_case_quality_payload(payload)
    prompt = prompt_registry.get_default("case_quality_judge_system")
    client = _quality_client(backend, model)
    started = time.perf_counter()
    response = client.invoke(
        str(prompt.get("text") or ""),
        json.dumps(judge_input, ensure_ascii=False, indent=2, default=str),
        temperature=0,
        response_format={"type": "json_object"},
    )
    elapsed = time.perf_counter() - started
    try:
        parsed = _parse_response(response.text)
        _validate_case_quality(parsed)
        status = "ok"
        error_text = None
    except (json.JSONDecodeError, ValueError) as exc:
        parsed = {
            "sub_scores": {},
            "patch_suggestion": {
                "target_area": "none",
                "severity": "P3",
                "title": "Reviewer parse error",
                "details": str(exc),
                "patch_hint": "",
                "examples": {},
            },
        }
        status = "parse_error"
        error_text = str(exc)
    usage = response.usage_norm or {}
    return {
        "sub_scores": parsed["sub_scores"],
        "patch_suggestion": parsed["patch_suggestion"],
        "reviewer_backend": backend,
        "reviewer_model": model,
        "reviewer_prompt_id": prompt.get("prompt_id") or prompt.get("id") or "case_quality_judge_system",
        "reviewer_prompt_version": str(prompt.get("prompt_version") or prompt.get("version") or ""),
        "reviewer_prompt_sha256": prompt.get("prompt_sha256") or prompt.get("sha256"),
        "reviewer_latency_ms": int(elapsed * 1000),
        "reviewer_walltime_sec": round(float(response.walltime_sec or elapsed), 3),
        "reviewer_tokens_in": usage.get("prompt_tokens") or usage.get("input_tokens"),
        "reviewer_tokens_out": usage.get("completion_tokens") or usage.get("output_tokens"),
        "reviewer_cached_tokens": usage.get("cached_tokens") or 0,
        "reviewer_cost_usd": usage.get("cost_usd") or _usage_cost(usage),
        "reviewer_raw_jsonb": {"response": response.text, "parsed": parsed, "usage": usage},
        "reviewer_status": status,
        "reviewer_error_text": error_text,
    }


# Контрактные enum-ы из SYSTEM_PROMPT. Любое расширение требует синхронной
# правки промпта и down-stream потребителей в benchmark.rag_embeddings(solutions).
_TASK_TYPES = {
    "simple_select",
    "join",
    "aggregation",
    "window_function",
    "cte",
    "plpgsql",
    "mixed",
    "unknown",
}
_AUDITOR_VERDICTS = {
    "correct",
    "too_strict",
    "too_lenient",
    "partially_correct",
    "unknown",
}


def _validate_response(data: dict[str, Any]) -> None:
    """
    Жёсткая валидация JSON-схемы ответа meta-аудитора.

    Падает с ValueError если модель пропустила обязательное поле или
    нарушила тип/enum. Контракт: silent default 'unknown' маскирует
    деградацию качества модели и пишет мусор в solutions RAG — лучше
    пере-запустить trace через cron.
    """
    if not isinstance(data, dict):
        raise ValueError(
            "meta-audit response must be JSON object, got " + type(data).__name__
        )
    required_str = (
        "task_type",
        "auditor_verdict",
        "auditor_notes",
        "correct_sql_approach",
        "lesson_for_generator",
        "searchable_text",
    )
    for key in required_str:
        if key not in data:
            raise ValueError("meta-audit response: missing required key '" + key + "'")
        if not isinstance(data[key], str):
            raise ValueError(
                "meta-audit response: '" + key + "' must be string, got "
                + type(data[key]).__name__
            )
    if "generator_errors" not in data:
        raise ValueError("meta-audit response: missing required key 'generator_errors'")
    if not isinstance(data["generator_errors"], list):
        raise ValueError(
            "meta-audit response: 'generator_errors' must be list, got "
            + type(data["generator_errors"]).__name__
        )
    for i, err in enumerate(data["generator_errors"]):
        if not isinstance(err, str):
            raise ValueError(
                "meta-audit response: generator_errors[" + str(i) + "] must be string, got "
                + type(err).__name__
            )
    if data["task_type"] not in _TASK_TYPES:
        raise ValueError(
            "meta-audit response: task_type='" + str(data["task_type"])
            + "' not in " + ",".join(sorted(_TASK_TYPES))
        )
    if data["auditor_verdict"] not in _AUDITOR_VERDICTS:
        raise ValueError(
            "meta-audit response: auditor_verdict='" + str(data["auditor_verdict"])
            + "' not in " + ",".join(sorted(_AUDITOR_VERDICTS))
        )


# ── Главные функции ─────────────────────────────────────────────────────────

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_TIMEOUT = int(os.environ.get("META_AUDIT_TIMEOUT_SEC", "120"))


def review_trace(
    trace_id: str,
    *,
    model: str = DEFAULT_MODEL,
    timeout_sec: int = DEFAULT_TIMEOUT,
) -> MetaAuditResult:
    """Полный цикл: load → claude CLI → parse → MetaAuditResult."""
    payload = _load_run_from_store(trace_id)
    user_prompt = _build_user_prompt(payload)
    response_text = _claude_cli(
        SYSTEM_PROMPT, user_prompt, model=model, timeout_sec=timeout_sec
    )
    try:
        data = _parse_response(response_text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            "не удалось распарсить JSON от claude CLI: " + str(exc)
            + "\n--- raw response (first 400 chars) ---\n" + response_text[:400]
        ) from exc
    try:
        _validate_response(data)
    except ValueError as exc:
        raise RuntimeError(
            "ответ claude CLI не прошёл schema-валидацию: " + str(exc)
            + "\n--- raw response (first 400 chars) ---\n" + response_text[:400]
        ) from exc
    return MetaAuditResult(
        trace_id=trace_id,
        task_description=payload.get("task") or "",
        task_type=str(data.get("task_type") or "unknown"),
        generator_errors=list(data.get("generator_errors") or []),
        auditor_verdict=str(data.get("auditor_verdict") or "unknown"),
        auditor_notes=str(data.get("auditor_notes") or ""),
        correct_sql_approach=str(data.get("correct_sql_approach") or ""),
        lesson_for_generator=str(data.get("lesson_for_generator") or ""),
        searchable_text=str(data.get("searchable_text") or ""),
        iterations_used=int(payload["run"].get("iterations_used") or 0),
        approved=bool(payload["run"].get("approved")),
        reviewer_model=model,
    )


def review_and_save(
    trace_id: str,
    *,
    model: str = DEFAULT_MODEL,
    timeout_sec: int = DEFAULT_TIMEOUT,
) -> tuple[MetaAuditResult, int]:
    """Review + сохранение lesson в rag_embeddings + флаг meta_audited."""
    result = review_trace(trace_id, model=model, timeout_sec=timeout_sec)
    embedding_id = _save_lesson(result)
    _mark_meta_audited(trace_id)
    return result, embedding_id


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 2 meta-auditor")
    parser.add_argument("--trace-id", required=True, help="trace_id из benchmark.pipeline_runs")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout-sec", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--dry-run", action="store_true", help="не сохранять в Postgres")
    args = parser.parse_args()

    if args.dry_run:
        result = review_trace(args.trace_id, model=args.model, timeout_sec=args.timeout_sec)
        print(json.dumps(result.to_metadata(), ensure_ascii=False, indent=2))
        return 0

    result, eid = review_and_save(
        args.trace_id, model=args.model, timeout_sec=args.timeout_sec
    )
    print("trace_id=" + result.trace_id)
    print("task_type=" + result.task_type)
    print("lesson_id=" + str(eid))
    print("lesson_for_generator: " + result.lesson_for_generator[:240])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
