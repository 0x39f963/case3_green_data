from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from benchmark_service import db


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "bench" / "logs"
_LOCK = threading.Lock()
_PROCS: dict[str, subprocess.Popen[bytes]] = {}


class JudgeStartError(RuntimeError):
    pass


def start_judge_job(benchmark_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    run = db.get_benchmark_run(benchmark_run_id)
    if not run:
        raise JudgeStartError("Benchmark run not found: " + benchmark_run_id)
    config = run.get("config_jsonb") or {}
    backend = str(payload.get("backend") or payload.get("smart_judge_backend") or config.get("smart_judge_backend") or "codex_cli")
    if backend == "off":
        raise JudgeStartError("smart-judge backend is off")
    model = str(payload.get("model") or payload.get("smart_judge_model") or config.get("smart_judge_model") or "gpt-5.5")
    job_id = _job_id(benchmark_run_id)
    cmd = _build_cmd(benchmark_run_id, payload, backend, model)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / (job_id + ".log")

    with _LOCK:
        old = _PROCS.get(benchmark_run_id)
        if old and old.poll() is None:
            old_config = (db.get_benchmark_run(benchmark_run_id) or {}).get("config_jsonb") or {}
            return {
                "job_id": old_config.get("judge_job_id") or benchmark_run_id,
                "pid": old.pid,
                "status": "running",
                "log_path": old_config.get("judge_log_path"),
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
            db.update_judge_run_status(
                benchmark_run_id,
                "start_failed",
                backend=backend,
                model=model,
                job_id=job_id,
                log_path=str(log_path),
                running_workers=0,
                error_text=str(exc),
            )
            raise JudgeStartError(str(exc)) from exc
        _PROCS[benchmark_run_id] = proc

    db.update_judge_run_status(
        benchmark_run_id,
        "running",
        backend=backend,
        model=model,
        job_id=job_id,
        log_path=str(log_path),
        running_workers=int(payload.get("workers") or payload.get("smart_judge_workers") or 1),
    )
    return {"job_id": job_id, "pid": proc.pid, "status": "running", "log_path": str(log_path)}


def get_judge_job_status(benchmark_run_id: str) -> dict[str, Any]:
    reap_finished()
    with _LOCK:
        proc = _PROCS.get(benchmark_run_id)
    run = db.get_benchmark_run(benchmark_run_id)
    config = (run or {}).get("config_jsonb") or {}
    if not proc:
        return {
            "job_id": config.get("judge_job_id"),
            "status": config.get("judge_status") or "unknown",
            "pid": None,
            "return_code": config.get("judge_return_code"),
            "log_path": config.get("judge_log_path"),
        }
    code = proc.poll()
    return {
        "job_id": config.get("judge_job_id"),
        "status": "running" if code is None else "exited",
        "pid": proc.pid,
        "return_code": code,
        "log_path": config.get("judge_log_path"),
    }


def abort_judge_job(benchmark_run_id: str) -> dict[str, Any]:
    with _LOCK:
        proc = _PROCS.get(benchmark_run_id)
    if proc and proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        db.update_judge_run_status(benchmark_run_id, "aborted", running_workers=0)
        return {"status": "aborted", "pid": proc.pid}
    db.update_judge_run_status(benchmark_run_id, "aborted", running_workers=0)
    return {"status": "aborted", "pid": None}


def reap_finished() -> None:
    with _LOCK:
        items = list(_PROCS.items())
    for run_id, proc in items:
        code = proc.poll()
        if code is None:
            continue
        config = (db.get_benchmark_run(run_id) or {}).get("config_jsonb") or {}
        current_status = str(config.get("judge_status") or "")
        status = current_status if code == 0 and current_status else "runtime_error"
        if code == 0 and status == "running":
            status = "completed"
        db.update_judge_run_status(run_id, status, running_workers=0, error_text=None if code == 0 else "judge process exited " + str(code))
        with _LOCK:
            _PROCS.pop(run_id, None)


def reconcile_orphan_jobs() -> int:
    """Сбросить judge-job-ы, потерявшие subprocess или зависшие в partial.

    Два failure mode'а:
    1. status=running/queued/waiting, но нет в `_PROCS` — orphan после restart.
    2. status=partial с running_workers=0 — все worker-ы умерли, оставив
       queue зависшим. UI крутит «Сейчас выполняется: Judge-audit» вечно.
    Оба переводятся в `interrupted` со внятным error_text. UI получает
    возможность перезапустить job вручную.
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
        status = str(cfg.get("judge_status") or "")
        run_id = str(run.get("benchmark_run_id") or "")
        if not run_id or run_id in tracked:
            continue
        if status in {"running", "queued", "waiting"}:
            reason = "orphan judge job: process not tracked after container restart"
        elif status == "partial" and int(cfg.get("judge_running_workers") or 0) == 0 and int(cfg.get("judge_total_missing") or 0) > 0:
            reason = "judge partial with 0 active workers (workers died mid-job)"
        else:
            continue
        try:
            db.update_judge_run_status(
                run_id,
                "interrupted",
                running_workers=0,
                error_text=reason,
            )
            reset += 1
        except Exception:
            continue
    return reset


def _build_cmd(benchmark_run_id: str, payload: dict[str, Any], backend: str, model: str) -> list[str]:
    cmd = [
        sys.executable,
        "scripts/bench_judge_existing_run.py",
        "--benchmark-run-id",
        benchmark_run_id,
        "--backend",
        backend,
        "--model",
        model,
        "--workers",
        str(int(payload.get("workers") or payload.get("smart_judge_workers") or 1)),
        "--chunk-size",
        str(int(payload.get("chunk_size") or payload.get("smart_judge_chunk_size") or 10)),
    ]
    if payload.get("missing_only", True):
        cmd.append("--missing-only")
    limit = int(payload.get("limit") or 0)
    if limit > 0:
        cmd.extend(["--limit", str(limit)])
    fallback_backend = str(payload.get("fallback_backend") or "")
    fallback_model = str(payload.get("fallback_model") or "")
    if fallback_backend:
        cmd.extend(["--fallback-backend", fallback_backend])
    if fallback_model:
        cmd.extend(["--fallback-model", fallback_model])
    if payload.get("status_on_error"):
        cmd.extend(["--status-on-error", str(payload["status_on_error"])])
    if payload.get("codex_reasoning_effort"):
        cmd.extend(["--codex-reasoning-effort", str(payload["codex_reasoning_effort"])])
    if payload.get("watch"):
        cmd.append("--watch")
    if payload.get("poll_sec"):
        cmd.extend(["--poll-sec", str(float(payload["poll_sec"]))])
    return cmd


def _job_id(benchmark_run_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in benchmark_run_id)
    return safe + "_judge_" + str(int(time.time()))
