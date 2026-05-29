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


class OracleStartError(RuntimeError):
    pass


def start_oracle_job(benchmark_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    run = db.get_benchmark_run(benchmark_run_id)
    if not run:
        raise OracleStartError("Benchmark run not found: " + benchmark_run_id)
    job_id = _job_id(benchmark_run_id)
    cmd = _build_cmd(benchmark_run_id, payload)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / (job_id + ".log")

    with _LOCK:
        old = _PROCS.get(benchmark_run_id)
        if old and old.poll() is None:
            config = (db.get_benchmark_run(benchmark_run_id) or {}).get("config_jsonb") or {}
            return {
                "job_id": config.get("oracle_job_id") or job_id,
                "pid": old.pid,
                "status": "running",
                "log_path": config.get("oracle_log_path"),
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
            db.update_oracle_run_status(
                benchmark_run_id,
                "start_failed",
                job_id=job_id,
                log_path=str(log_path),
                running_workers=0,
                error_text=str(exc),
            )
            raise OracleStartError(str(exc)) from exc
        _PROCS[benchmark_run_id] = proc

    db.update_oracle_run_status(
        benchmark_run_id,
        "running",
        job_id=job_id,
        log_path=str(log_path),
        running_workers=1,
    )
    return {"job_id": job_id, "pid": proc.pid, "status": "running", "log_path": str(log_path)}


def get_oracle_job_status(benchmark_run_id: str) -> dict[str, Any]:
    reap_finished()
    with _LOCK:
        proc = _PROCS.get(benchmark_run_id)
    run = db.get_benchmark_run(benchmark_run_id)
    config = (run or {}).get("config_jsonb") or {}
    if not proc:
        return {
            "job_id": config.get("oracle_job_id"),
            "status": config.get("oracle_status") or "unknown",
            "pid": None,
            "return_code": config.get("oracle_return_code"),
            "log_path": config.get("oracle_log_path"),
        }
    code = proc.poll()
    return {
        "job_id": config.get("oracle_job_id"),
        "status": "running" if code is None else "exited",
        "pid": proc.pid,
        "return_code": code,
        "log_path": config.get("oracle_log_path"),
    }


def abort_oracle_job(benchmark_run_id: str) -> dict[str, Any]:
    with _LOCK:
        proc = _PROCS.get(benchmark_run_id)
    if proc and proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        db.update_oracle_run_status(benchmark_run_id, "aborted", running_workers=0)
        return {"status": "aborted", "pid": proc.pid}
    db.update_oracle_run_status(benchmark_run_id, "aborted", running_workers=0)
    return {"status": "aborted", "pid": None}


def reap_finished() -> None:
    with _LOCK:
        items = list(_PROCS.items())
    for run_id, proc in items:
        code = proc.poll()
        if code is None:
            continue
        config = (db.get_benchmark_run(run_id) or {}).get("config_jsonb") or {}
        current_status = str(config.get("oracle_status") or "")
        if current_status == "aborted":
            status = "aborted"
            error_text = None
        elif code == 0:
            counts = db.oracle_counts(run_id)
            status = "completed" if counts["missing_cases"] == 0 else "partial"
            error_text = None
        else:
            status = "failed"
            error_text = "oracle process exited " + str(code)
        db.update_oracle_run_status(run_id, status, running_workers=0, error_text=error_text)
        with _LOCK:
            _PROCS.pop(run_id, None)


def reconcile_orphan_jobs() -> int:
    """Сбросить oracle-job-ы, которые БД считает running, но процесса нет."""
    try:
        runs = (db.list_benchmark_runs(limit=500).get("items") or [])
    except Exception:
        return 0
    reset = 0
    with _LOCK:
        tracked = set(_PROCS.keys())
    for run in runs:
        cfg = run.get("config_jsonb") or {}
        status = str(cfg.get("oracle_status") or "")
        if status not in {"running", "queued", "waiting"}:
            continue
        run_id = str(run.get("benchmark_run_id") or "")
        if not run_id or run_id in tracked:
            continue
        try:
            db.update_oracle_run_status(
                run_id,
                "interrupted",
                running_workers=0,
                error_text="orphan oracle job: process not tracked after container restart",
            )
            reset += 1
        except Exception:
            continue
    return reset


def _build_cmd(benchmark_run_id: str, payload: dict[str, Any]) -> list[str]:
    golden = str(payload.get("golden") or "")
    if not golden and benchmark_run_id.startswith("golden_v2_"):
        golden = "data/eval/golden_v2.jsonl"
    elif not golden:
        golden = "data/eval/golden_dataset_v1_1.csv"
    cmd = [
        sys.executable,
        "scripts/bench_oracle_existing_run.py",
        "--benchmark-run-id",
        benchmark_run_id,
        "--dataset-version",
        str(payload.get("dataset_version") or "1.1"),
        "--golden",
        golden,
        "--ingest-store",
    ]
    if payload.get("missing_only", True):
        cmd.append("--missing-only")
    limit = int(payload.get("limit") or 0)
    if limit > 0:
        cmd.extend(["--limit", str(limit)])
    oracle_types = payload.get("oracle_types") or []
    if isinstance(oracle_types, list) and oracle_types:
        cmd.extend(["--oracle-types", ",".join(str(item) for item in oracle_types if item)])
    for case_id in payload.get("case_id") or []:
        cmd.extend(["--case-id", str(case_id)])
    if payload.get("status_on_error"):
        cmd.extend(["--status-on-error", str(payload["status_on_error"])])
    return cmd


def _job_id(benchmark_run_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in benchmark_run_id)
    return safe + "_oracle_" + str(int(time.time()))
