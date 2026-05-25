"""
Phase 4.1 — Execution Accuracy metric для request-level датасета.

Идея конкурента (marussiakuz1/case3-rag/validation/evaluate.py): не
просто проверять recall меток риска, а фактически выполнять
generated_sql и reference_sql на тестовой Postgres под read-only ролью
и сравнивать множества возвращённых строк. Это сильная метрика для
бизнеса: «модель ответила на вопрос правильно?» вместо «модель
правильно классифицировала?».

Использует:
- v0.2 adversarial_sql_requests.jsonl как источник пар
  (user_task, safe_rewrite). Берём только expected_decision in
  {approve, revise} — там safe_rewrite сейчас служит reference SQL.
- FastAPI /run для генерации (тот же контур, что бот) — чтобы
  Phase 0 тайминги и Phase 2 уроки тоже подключались.
- POSTGRES_AUDIT_DSN (демо-БД с тестовыми данными) под read-only
  ролью + statement_timeout для выполнения.

Phase 4.2 — retry-on-hallucination:
если `app.sql_guard.HALLUCINATED_TABLE` находится в findings первого
прогона, повторяем /run до 2 раз с feedback. Это даёт справедливую EA
на семействах с правильной задачей, где модель ошиблась в имени
таблицы единожды.

Запуск:
    python scripts/eval_execution_accuracy.py --limit 5 --max-iterations 3

CLI-результат: EA по family + per-case JSONL + один summary.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_DATASET = ROOT / "data" / "bench" / "requests" / "adversarial_sql_requests_v0_2.jsonl"
RESULTS_DIR = ROOT / "data" / "eval" / "execution_accuracy"
DEFAULT_SQL_TIMEOUT_MS = 5000
HALLUCINATION_LABELS = ("HALLUCINATED_TABLE", "HALLUCINATED_COLUMN")


class ConfigError(RuntimeError):
    pass


def _api_run(
    api_url: str, task: str, *, llm_mode: str | None, model_key: str | None,
    max_iterations: int, timeout_sec: int,
) -> dict[str, Any]:
    """Один POST /run, возвращает SystemResult dict."""
    body: dict[str, Any] = {"task": task, "max_iterations": max_iterations}
    if llm_mode:
        body["llm_mode"] = llm_mode
    if model_key:
        body["llm_generator_model"] = model_key
    req = request.Request(
        api_url.rstrip("/") + "/run",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout_sec) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _connect_target(dsn: str, *, timeout: int):
    """Псевдо-readonly: SET TRANSACTION READ ONLY + statement_timeout."""
    return psycopg2.connect(dsn, connect_timeout=timeout)


def _execute_safely(conn, sql: str, timeout_ms: int) -> tuple[list[tuple], str | None]:
    """Выполнить SQL в READ ONLY tx и забрать строки. Откат гарантирован
    в finally — иначе следующий вызов не сможет SET TRANSACTION READ ONLY
    (текущая транзакция уже идёт после удачного fetch)."""
    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY;")
            cur.execute("SET statement_timeout TO %s;", (timeout_ms,))
            cur.execute(_substitute_placeholders(sql))
            rows = cur.fetchall()
        return rows, None
    except psycopg2.Error as exc:
        return [], str(exc).strip()
    finally:
        try:
            conn.rollback()
        except psycopg2.Error:
            pass


def _execute_isolated(
    dsn: str, sql: str, *, timeout_ms: int, timeout_sec: int,
) -> tuple[list[tuple], str | None]:
    """Открыть отдельное подключение, выполнить sql, закрыть. Полная
    изоляция между generated и reference SQL: ни один запрос не наследует
    state другого."""
    if not sql:
        return [], "empty_sql"
    conn = _connect_target(dsn, timeout=timeout_sec)
    try:
        return _execute_safely(conn, sql, timeout_ms)
    finally:
        conn.close()


_INT_HINTS = (
    "_id", "_count", "_num", "_qty", "_year", "_month",
    "_day", "_total", "_amount", "_age", "_score",
)
_DATE_HINTS = ("_at", "_date", "_on", "_dt")
_TIMESTAMP_HINTS = ("_time", "_timestamp")
_UUID_HINTS = ("_uuid", "uuid_")
_BOOL_HINTS = ("is_", "has_", "_flag", "_enabled", "_active")


def _placeholder_value_for(column_hint: str) -> str:
    """Угадать литерал по имени колонки слева от $N.

    Без полного schema lookup это эвристика по суффиксам имени. Для
    неопознанных случаев — NULL: PostgreSQL принимает в любом типе и
    запрос остаётся синтаксически валидным (вернёт 0 строк, но не упадёт
    с CAST-ошибкой как было c подстановкой '1' в text/uuid колонки)."""
    if not column_hint:
        return "NULL"
    lower = column_hint.lower()
    if lower == "id" or any(lower.endswith(h) for h in _INT_HINTS):
        return "1"
    if any(lower.startswith(h) for h in _BOOL_HINTS):
        return "FALSE"
    if any(lower.endswith(h) for h in _UUID_HINTS):
        return "'00000000-0000-0000-0000-000000000000'::uuid"
    if any(lower.endswith(h) for h in _TIMESTAMP_HINTS):
        return "NOW()"
    if any(lower.endswith(h) for h in _DATE_HINTS):
        return "CURRENT_DATE"
    if "email" in lower:
        return "'test@example.com'"
    if "phone" in lower:
        return "'+70000000000'"
    return "'test'"


_PLACEHOLDER_CTX_RE = re.compile(
    r"(?P<col>[A-Za-z_][A-Za-z_0-9]*)\s*(?:=|<>|!=|<=|>=|<|>|\sLIKE\s|\sILIKE\s)\s*\$(?P<idx>\d+)",
    re.IGNORECASE,
)


def _substitute_placeholders(sql: str) -> str:
    """Заменить $1, $2 на литералы, угадывая тип по колонке слева.

    Раньше всё $N → 1, что ломало text/uuid/date колонки (PostgreSQL
    отбивал CAST). Теперь шаг 1: ищем `<column> <op> $N` и подбираем
    литерал по эвристике на имени колонки. Шаг 2: оставшиеся `$N`
    (IN-листы, BETWEEN без явного оператора, и т.д.) превращаются в NULL —
    любой тип принимает, запрос не падает с CAST."""
    def _replace(match: re.Match) -> str:
        col = match.group("col")
        literal = _placeholder_value_for(col)
        return match.group(0).replace("$" + match.group("idx"), literal)

    out = _PLACEHOLDER_CTX_RE.sub(_replace, sql)
    return re.sub(r"\$\d+", "NULL", out)


def _canonicalize_cell(cell: Any) -> str:
    """Каноническая строковая форма ячейки. jsonb/list/Decimal/datetime
    приводим к стабильному виду — иначе одинаковые dict в разном порядке
    ключей сравнивались как разные."""
    if isinstance(cell, dict) or isinstance(cell, list):
        return json.dumps(cell, sort_keys=True, default=str, ensure_ascii=False)
    return repr(cell)


def _canonicalize_row(row: Any) -> tuple[str, ...]:
    try:
        return tuple(_canonicalize_cell(cell) for cell in row)
    except TypeError:
        return (_canonicalize_cell(row),)


def _rows_match(a: list[tuple], b: list[tuple]) -> bool:
    """Сравнение строк без порядка с канонизацией jsonb/dict/list/Decimal.

    Для hashable-рядов остаётся быстрый set-путь. Если хоть один ряд
    содержит dict/list — переходим на канонизованный мультисет
    (sorted(canon_row) == sorted(canon_row))."""
    try:
        return set(a) == set(b)
    except TypeError:
        canon_a = sorted(_canonicalize_row(r) for r in a)
        canon_b = sorted(_canonicalize_row(r) for r in b)
        return canon_a == canon_b


def _hallucinated_findings(result: dict[str, Any]) -> list[str]:
    """Извлечь HALLUCINATED_TABLE/COLUMN из последней iteration audit."""
    iters = result.get("iterations_log") or []
    if not iters:
        return []
    last = iters[-1]
    vulns = (last.get("audit_result") or {}).get("vulnerabilities") or []
    labels = [str(v.get("vuln_class") or "") for v in vulns]
    return [lab for lab in labels if lab in HALLUCINATION_LABELS]


def _eval_one(
    row: dict[str, Any],
    *,
    api_url: str,
    target_dsn: str,
    llm_mode: str | None,
    model_key: str | None,
    max_iterations: int,
    hallucination_retries: int,
    timeout_sec: int,
    sql_timeout_ms: int,
) -> dict[str, Any]:
    """Прогнать один кейс. Возвращает строку для results.jsonl."""
    started = time.perf_counter()
    task = row["user_task"]
    reference_sql = row.get("safe_rewrite") or ""
    case_id = row.get("id") or row.get("case_id")

    result_payload: dict[str, Any] | None = None
    hallucinated = False
    retries_done = 0
    try:
        result_payload = _api_run(
            api_url, task,
            llm_mode=llm_mode, model_key=model_key,
            max_iterations=max_iterations, timeout_sec=timeout_sec,
        )
        for _attempt in range(hallucination_retries):
            labels = _hallucinated_findings(result_payload or {})
            if not labels:
                break
            hallucinated = True
            retries_done += 1
            retry_task = (
                task
                + "\n\n[Подсказка после ошибки]: "
                + ", ".join(labels)
                + ". Используй ТОЛЬКО таблицы из контекста схемы."
            )
            result_payload = _api_run(
                api_url, retry_task,
                llm_mode=llm_mode, model_key=model_key,
                max_iterations=max_iterations, timeout_sec=timeout_sec,
            )
    except (error.URLError, OSError, json.JSONDecodeError) as exc:
        return {
            "case_id": case_id,
            "family": row.get("family"),
            "expected_decision": row.get("expected_decision"),
            "error": "api_error: " + str(exc)[:200],
            "ea": 0.0,
            "elapsed_sec": round(time.perf_counter() - started, 3),
        }

    generated_sql = (result_payload or {}).get("final_sql") or ""

    # Каждый SQL — в отдельном соединении: между gen и ref не должно быть
    # transaction-state leak (предыдущий fetch держал tx открытой, и
    # SET TRANSACTION READ ONLY на втором SQL падал).
    rows_gen, err_gen = (
        _execute_isolated(target_dsn, generated_sql,
                          timeout_ms=sql_timeout_ms, timeout_sec=timeout_sec)
        if generated_sql else ([], "empty generated_sql")
    )
    rows_ref, err_ref = (
        _execute_isolated(target_dsn, reference_sql,
                          timeout_ms=sql_timeout_ms, timeout_sec=timeout_sec)
        if reference_sql else ([], "empty reference_sql")
    )

    if err_gen and err_ref:
        ea = 0.0
        match = False
        note = "both_failed gen={} ref={}".format(err_gen[:60], err_ref[:60])
    elif err_gen:
        ea = 0.0
        match = False
        note = "gen_failed: " + err_gen[:120]
    elif err_ref:
        ea = 0.0
        match = False
        note = "ref_failed: " + err_ref[:120]
    else:
        match = _rows_match(rows_gen, rows_ref)
        ea = 1.0 if match else 0.0
        note = "gen={} rows · ref={} rows".format(len(rows_gen), len(rows_ref))

    return {
        "case_id": case_id,
        "family": row.get("family"),
        "expected_decision": row.get("expected_decision"),
        "task_excerpt": task[:120],
        "ea": ea,
        "match": match,
        "hallucinated": hallucinated,
        "hallucination_retries": retries_done,
        "generated_sql": generated_sql,
        "reference_sql": reference_sql,
        "gen_rows": len(rows_gen),
        "ref_rows": len(rows_ref),
        "note": note,
        "approved": bool((result_payload or {}).get("approved")),
        "iterations_used": (result_payload or {}).get("iterations_used"),
        "trace_id": ((result_payload or {}).get("metadata") or {}).get("trace_id"),
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 4.1+4.2 — Execution Accuracy на v0.2 dataset")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--api-url", default="http://127.0.0.1:18000")
    parser.add_argument("--postgres-dsn", default=os.environ.get("POSTGRES_AUDIT_DSN", ""))
    parser.add_argument("--llm-mode", default=None)
    parser.add_argument("--model-key", default=None)
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--hallucination-retries", type=int, default=2,
                        help="Phase 4.2 — retry-on-hallucination до N раз")
    parser.add_argument("--timeout-sec", type=int, default=600)
    parser.add_argument("--sql-timeout-ms", type=int, default=DEFAULT_SQL_TIMEOUT_MS)
    parser.add_argument("--limit", type=int, default=None,
                        help="ограничить число approve/revise кейсов (для smoke)")
    parser.add_argument("--family", action="append", default=[],
                        help="фильтр по family (multi)")
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args()

    if not args.postgres_dsn:
        raise ConfigError("--postgres-dsn или POSTGRES_AUDIT_DSN обязателен.")

    rows = [json.loads(l) for l in args.dataset.read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r.get("expected_decision") in ("approve", "revise") and r.get("safe_rewrite")]
    if args.family:
        rows = [r for r in rows if r.get("family") in args.family]
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print(json.dumps({"status": "no_rows", "filtered": 0}), flush=True)
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    run_id = "ea_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    results_path = args.out_dir / (run_id + ".jsonl")
    summary_path = args.out_dir / (run_id + "_summary.json")

    print("Phase 4.1/4.2 EA · rows=" + str(len(rows)) + " · api=" + args.api_url, flush=True)
    started = time.time()
    per_family: dict[str, list[float]] = defaultdict(list)
    hallucination_total = 0
    rows_done: list[dict[str, Any]] = []

    with results_path.open("w", encoding="utf-8") as fh:
        for idx, row in enumerate(rows, start=1):
            case_id = row.get("id") or row.get("case_id")
            print(
                "[{:>3}/{:<3}] {} ({}) ... ".format(idx, len(rows), case_id, row.get("family")),
                end="", flush=True,
            )
            res = _eval_one(
                row,
                api_url=args.api_url,
                target_dsn=args.postgres_dsn,
                llm_mode=args.llm_mode,
                model_key=args.model_key,
                max_iterations=args.max_iterations,
                hallucination_retries=args.hallucination_retries,
                timeout_sec=args.timeout_sec,
                sql_timeout_ms=args.sql_timeout_ms,
            )
            fh.write(json.dumps(res, ensure_ascii=False) + "\n")
            rows_done.append(res)
            per_family[res.get("family") or "unknown"].append(float(res.get("ea") or 0))
            if res.get("hallucinated"):
                hallucination_total += 1
            print(
                "EA={:.0f}  {}  t={:.1f}s{}".format(
                    res.get("ea") or 0,
                    (res.get("note") or "")[:80],
                    res.get("elapsed_sec") or 0,
                    "  ↻retry={}".format(res.get("hallucination_retries"))
                    if res.get("hallucination_retries") else "",
                ),
                flush=True,
            )

    elapsed = time.time() - started
    overall_ea = sum(r.get("ea", 0) for r in rows_done) / max(len(rows_done), 1)
    per_family_summary = {
        family: {
            "n": len(values),
            "ea": round(sum(values) / max(len(values), 1), 4),
        }
        for family, values in per_family.items()
    }
    summary = {
        "run_id": run_id,
        "dataset": str(args.dataset),
        "rows_total": len(rows_done),
        "ea_overall": round(overall_ea, 4),
        "ea_target": 0.7,
        "ea_passed": overall_ea >= 0.7,
        "per_family": per_family_summary,
        "hallucination_total": hallucination_total,
        "hallucination_rate": round(hallucination_total / max(len(rows_done), 1), 4),
        "elapsed_sec": round(elapsed, 2),
        "results_path": str(results_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 60)
    print("Overall EA: {:.4f}  ({:.0%})  target 0.70  {}".format(
        overall_ea, overall_ea, "PASS" if overall_ea >= 0.7 else "MISS"
    ))
    for family, stats in sorted(per_family_summary.items()):
        print("  {:<26} n={:<3} EA={:.3f}".format(family, stats["n"], stats["ea"]))
    print("Hallucinations: {}/{} ({:.0%})".format(
        hallucination_total, len(rows_done),
        hallucination_total / max(len(rows_done), 1),
    ))
    print("Summary: " + str(summary_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
