from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from uuid import uuid4
from pathlib import Path
from typing import Any

from benchmark_service import db


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "bench" / "logs"
_LOCK = threading.Lock()
_PROCS: dict[str, subprocess.Popen[bytes]] = {}


class AnalysisStartError(RuntimeError):
    pass


def start_analysis_job(benchmark_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    run = db.get_benchmark_run(benchmark_run_id)
    if not run:
        raise AnalysisStartError("Benchmark run not found: " + benchmark_run_id)
    backend = str(payload.get("backend") or "codex_cli")
    model = str(payload.get("model") or "gpt-5.5")
    job_id = _job_id(benchmark_run_id)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / (job_id + ".log")
    cmd = _build_cmd(benchmark_run_id, payload, backend, model, job_id, log_path)

    with _LOCK:
        old = _PROCS.get(benchmark_run_id)
        if old and old.poll() is None:
            config = (db.get_benchmark_run(benchmark_run_id) or {}).get("config_jsonb") or {}
            return {
                "job_id": config.get("analysis_job_id") or job_id,
                "pid": old.pid,
                "status": "running",
                "log_path": config.get("analysis_log_path"),
            }
        log = log_path.open("ab")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=log,
                stderr=subprocess.STDOUT,
                env=os.environ.copy(),
                start_new_session=True,
            )
        except Exception as exc:
            log.close()
            db.update_analysis_run_status(benchmark_run_id, "start_failed", job_id=job_id, log_path=str(log_path), error_text=str(exc))
            raise AnalysisStartError(str(exc)) from exc
        _PROCS[benchmark_run_id] = proc

    db.update_analysis_run_status(benchmark_run_id, "running", job_id=job_id, log_path=str(log_path))
    return {"job_id": job_id, "pid": proc.pid, "status": "running", "log_path": str(log_path)}


def get_analysis_job_status(benchmark_run_id: str) -> dict[str, Any]:
    reap_finished()
    with _LOCK:
        proc = _PROCS.get(benchmark_run_id)
    run = db.get_benchmark_run(benchmark_run_id)
    config = (run or {}).get("config_jsonb") or {}
    if not proc:
        return {
            "job_id": config.get("analysis_job_id"),
            "status": config.get("analysis_status") or "unknown",
            "pid": None,
            "return_code": config.get("analysis_return_code"),
            "log_path": config.get("analysis_log_path"),
        }
    code = proc.poll()
    return {
        "job_id": config.get("analysis_job_id"),
        "status": "running" if code is None else "exited",
        "pid": proc.pid,
        "return_code": code,
        "log_path": config.get("analysis_log_path"),
    }


def abort_analysis_job(benchmark_run_id: str) -> dict[str, Any]:
    with _LOCK:
        proc = _PROCS.get(benchmark_run_id)
    if proc and proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        db.update_analysis_run_status(benchmark_run_id, "aborted")
        return {"status": "aborted", "pid": proc.pid}
    db.update_analysis_run_status(benchmark_run_id, "aborted")
    return {"status": "aborted", "pid": None}


def reap_finished() -> None:
    with _LOCK:
        items = list(_PROCS.items())
    for run_id, proc in items:
        code = proc.poll()
        if code is None:
            continue
        config = (db.get_benchmark_run(run_id) or {}).get("config_jsonb") or {}
        current_status = str(config.get("analysis_status") or "")
        if current_status == "aborted":
            status = "aborted"
            error_text = None
        elif code == 0:
            status = "completed"
            error_text = None
        else:
            status = "failed"
            error_text = "analysis process exited " + str(code)
        db.update_analysis_run_status(run_id, status, error_text=error_text)
        with _LOCK:
            _PROCS.pop(run_id, None)


def reconcile_orphan_jobs() -> int:
    """Помечаем как `interrupted` любой analysis job, который в БД числится
    как `running`/`queued`/`waiting`, но не отслеживается в `_PROCS`.

    Это происходит после рестарта контейнера: subprocess был убит при
    `docker compose up -d`, in-memory state потерян, а БД продолжает
    показывать «running». Без reconcile UI висит на этом статусе вечно.
    Возвращает количество сброшенных job-ов.
    """
    try:
        runs = (db.list_benchmark_runs(limit=500).get("items") or [])
    except Exception:
        return 0
    reset = 0
    with _LOCK:
        tracked = set(_PROCS.keys())
    for run in runs:
        cfg = run.get("config_jsonb") or {}
        status = str(cfg.get("analysis_status") or "")
        if status not in {"running", "queued", "waiting"}:
            continue
        run_id = str(run.get("benchmark_run_id") or "")
        if not run_id or run_id in tracked:
            continue
        try:
            db.update_analysis_run_status(
                run_id,
                "interrupted",
                error_text="orphan analysis job: process not tracked after container restart",
            )
            reset += 1
        except Exception:
            continue
    return reset


def _build_cmd(
    benchmark_run_id: str,
    payload: dict[str, Any],
    backend: str,
    model: str,
    job_id: str,
    log_path: Path,
) -> list[str]:
    cmd = [
        sys.executable,
        "scripts/bench_analyze_judge_reports.py",
        "--benchmark-run-id",
        benchmark_run_id,
        "--backend",
        backend,
        "--model",
        model,
        "--job-id",
        job_id,
        "--log-path",
        str(log_path),
    ]
    if payload.get("missing_only", True):
        cmd.append("--missing-only")
    if payload.get("oracle_required"):
        cmd.append("--oracle-required")
    for trace_id in payload.get("trace_id") or []:
        cmd.extend(["--trace-id", str(trace_id)])
    limit = int(payload.get("limit") or 0)
    if limit > 0:
        cmd.extend(["--limit", str(limit)])
    if payload.get("status_on_error"):
        cmd.extend(["--status-on-error", str(payload["status_on_error"])])
    if payload.get("codex_reasoning_effort"):
        cmd.extend(["--codex-reasoning-effort", str(payload["codex_reasoning_effort"])])
    return cmd


def _job_id(benchmark_run_id: str) -> str:
    del benchmark_run_id
    return str(uuid4())
