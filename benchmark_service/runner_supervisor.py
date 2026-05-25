from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
import re
from typing import Any
from urllib import error, request
import json

from benchmark_service import db


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "bench" / "logs"
DATASET_UPLOAD_DIR = ROOT / "data" / "eval" / "uploads"
_LOCK = threading.Lock()
_PROCS: dict[str, subprocess.Popen[bytes]] = {}
_POSTHOC_THREADS: dict[str, threading.Thread] = {}


class RunnerStartError(RuntimeError):
    pass


class JudgeStartError(RuntimeError):
    pass


class OracleStartError(RuntimeError):
    pass


class AnalysisStartError(RuntimeError):
    pass


def start_subprocess(payload: dict[str, Any], token: str) -> dict[str, Any]:
    run_id = str(payload.get("benchmark_run_id") or "")
    if not run_id:
        raise RunnerStartError("benchmark_run_id is required")
    cmd = _build_cmd(payload, token)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / (run_id + ".log")
    with _LOCK:
        old = _PROCS.get(run_id)
        if old and old.poll() is None:
            return {"pid": old.pid, "log_path": str(log_path), "status": "running"}
        log = log_path.open("ab")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=log,
                stderr=subprocess.STDOUT,
                env=_runner_env(token),
                start_new_session=True,
            )
        except Exception as exc:
            log.close()
            db.update_benchmark_run_status(run_id, "failed", runner_log_path=str(log_path), error=str(exc))
            raise RunnerStartError(str(exc)) from exc
        _PROCS[run_id] = proc
    db.update_benchmark_run_status(run_id, "running", runner_pid=proc.pid, runner_log_path=str(log_path))
    return {"pid": proc.pid, "log_path": str(log_path), "status": "running"}


def start_judge_subprocess(
    benchmark_run_id: str,
    payload: dict[str, Any],
    token: str,
    *,
    watch: bool = False,
) -> dict[str, Any]:
    backend = str(payload.get("backend") or payload.get("smart_judge_backend") or "")
    if not backend or backend == "off":
        return {"status": "disabled"}
    model = str(payload.get("model") or payload.get("smart_judge_model") or "gpt-5.5")
    body = {
        "backend": backend,
        "model": model,
        "workers": int(payload.get("workers") or payload.get("smart_judge_workers") or 1),
        "chunk_size": int(payload.get("chunk_size") or payload.get("smart_judge_chunk_size") or 10),
        "missing_only": bool(payload.get("missing_only", True)),
        "limit": int(payload.get("limit") or 0),
        "fallback_backend": payload.get("fallback_backend") or "claude_cli",
        "fallback_model": payload.get("fallback_model") or "claude-sonnet-4-6",
        "codex_reasoning_effort": payload.get("codex_reasoning_effort") or "",
        "status_on_error": payload.get("status_on_error") or "runtime_error",
        "watch": bool(payload.get("watch", watch)),
        "poll_sec": float(payload.get("poll_sec") or 5),
    }
    url = _judge_executor_url().rstrip("/") + "/web/api/benchmarks/runs/" + benchmark_run_id + "/judge/start"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer " + token,
        },
    )
    try:
        with request.urlopen(req, timeout=float(os.environ.get("BENCHMARK_JUDGE_START_TIMEOUT_SEC", "10"))) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        db.update_judge_run_status(benchmark_run_id, "start_failed", backend=backend, model=model, error_text=text)
        raise JudgeStartError("judge executor HTTP " + str(exc.code) + ": " + text) from exc
    except Exception as exc:
        db.update_judge_run_status(benchmark_run_id, "start_failed", backend=backend, model=model, error_text=str(exc))
        raise JudgeStartError(str(exc)) from exc


def start_oracle_subprocess(
    benchmark_run_id: str,
    payload: dict[str, Any],
    token: str,
) -> dict[str, Any]:
    body = {
        "oracle_types": payload.get("oracle_types") or [],
        "limit": int(payload.get("limit") or 0),
        "missing_only": bool(payload.get("missing_only", True)),
        "dataset_version": payload.get("dataset_version") or "1.1",
        "workers": int(payload.get("workers") or 1),
        "case_id": payload.get("case_id") or [],
        "status_on_error": payload.get("status_on_error") or "error",
    }
    url = _judge_executor_url().rstrip("/") + "/web/api/benchmarks/runs/" + benchmark_run_id + "/oracle/start"
    return _post_executor(url, body, token, OracleStartError, benchmark_run_id, "oracle")


def start_analysis_subprocess(
    benchmark_run_id: str,
    payload: dict[str, Any],
    token: str,
) -> dict[str, Any]:
    body = {
        "backend": payload.get("backend") or "codex_cli",
        "model": payload.get("model") or "gpt-5.5",
        "limit": int(payload.get("limit") or 0),
        "missing_only": bool(payload.get("missing_only", True)),
        "oracle_required": bool(payload.get("oracle_required", False)),
        "status_on_error": payload.get("status_on_error") or "runtime_error",
        "codex_reasoning_effort": payload.get("codex_reasoning_effort") or "",
    }
    url = _judge_executor_url().rstrip("/") + "/web/api/benchmarks/runs/" + benchmark_run_id + "/analysis/start"
    return _post_executor(url, body, token, AnalysisStartError, benchmark_run_id, "analysis")


def start_posthoc_chain(benchmark_run_id: str, payload: dict[str, Any], token: str) -> dict[str, Any]:
    with _LOCK:
        old = _POSTHOC_THREADS.get(benchmark_run_id)
        if old and old.is_alive():
            return {"status": "running", "thread": old.name}
        thread = threading.Thread(
            target=_posthoc_chain_worker,
            name="posthoc-" + benchmark_run_id[:48],
            args=(benchmark_run_id, dict(payload), token),
            daemon=True,
        )
        _POSTHOC_THREADS[benchmark_run_id] = thread
        thread.start()
    return {"status": "scheduled", "thread": thread.name}


def _posthoc_chain_worker(benchmark_run_id: str, payload: dict[str, Any], token: str) -> None:
    try:
        _wait_for_runner_terminal(benchmark_run_id)
        if payload.get("oracle_enabled", True):
            oracle_payload = {
                "oracle_types": payload.get("oracle_types") or [],
                "limit": 0,
                "missing_only": True,
                "dataset_version": payload.get("oracle_dataset_version") or payload.get("dataset_version") or "1.1",
                "workers": 1,
                "status_on_error": "error",
            }
            try:
                start_oracle_subprocess(benchmark_run_id, oracle_payload, token)
            except Exception:
                pass
            _wait_for_status(benchmark_run_id, "oracle_status", {"running"}, timeout_sec=60 * 60 * 6)
        if payload.get("analysis_enabled", True):
            smart_backend = str(payload.get("smart_judge_backend") or "")
            if smart_backend and smart_backend != "off":
                _wait_for_status(benchmark_run_id, "judge_status", {"running", "queued", "waiting"}, timeout_sec=60 * 60 * 8)
            analysis_payload = {
                "backend": payload.get("analysis_backend") or payload.get("smart_judge_backend") or "codex_cli",
                "model": payload.get("analysis_model") or payload.get("smart_judge_model") or "gpt-5.5",
                "limit": 0,
                "missing_only": True,
                "oracle_required": bool(payload.get("analysis_oracle_required", False)),
                "status_on_error": "runtime_error",
                "codex_reasoning_effort": payload.get("analysis_codex_reasoning_effort")
                or payload.get("codex_reasoning_effort")
                or "",
            }
            try:
                start_analysis_subprocess(benchmark_run_id, analysis_payload, token)
            except Exception:
                pass
    finally:
        with _LOCK:
            current = _POSTHOC_THREADS.get(benchmark_run_id)
            if current and current.name == threading.current_thread().name:
                _POSTHOC_THREADS.pop(benchmark_run_id, None)


def _wait_for_runner_terminal(benchmark_run_id: str, timeout_sec: int = 60 * 60 * 12) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        reap_finished()
        run = db.get_benchmark_run(benchmark_run_id) or {}
        status = str(run.get("status") or "")
        config = run.get("config_jsonb") or {}
        runner_status = str(config.get("runner_status") or "")
        if status in {"completed", "failed", "aborted"} or runner_status in {"completed", "failed", "aborted"}:
            return
        time.sleep(float(os.environ.get("BENCHMARK_POSTHOC_POLL_SEC", "5")))


def _wait_for_status(
    benchmark_run_id: str,
    config_key: str,
    running_values: set[str],
    *,
    timeout_sec: int,
) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        run = db.get_benchmark_run(benchmark_run_id) or {}
        config = run.get("config_jsonb") or {}
        value = str(config.get(config_key) or "")
        if value and value not in running_values:
            return
        time.sleep(float(os.environ.get("BENCHMARK_POSTHOC_POLL_SEC", "5")))


def _post_executor(
    url: str,
    body: dict[str, Any],
    token: str,
    error_cls: type[RuntimeError],
    benchmark_run_id: str,
    kind: str,
) -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer " + token,
        },
    )
    try:
        with request.urlopen(req, timeout=float(os.environ.get("BENCHMARK_JUDGE_START_TIMEOUT_SEC", "10"))) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        if kind == "oracle":
            db.update_oracle_run_status(benchmark_run_id, "start_failed", error_text=text)
        elif kind == "analysis":
            db.update_analysis_run_status(benchmark_run_id, "start_failed", error_text=text)
        raise error_cls(kind + " executor HTTP " + str(exc.code) + ": " + text) from exc
    except Exception as exc:
        if kind == "oracle":
            db.update_oracle_run_status(benchmark_run_id, "start_failed", error_text=str(exc))
        elif kind == "analysis":
            db.update_analysis_run_status(benchmark_run_id, "start_failed", error_text=str(exc))
        raise error_cls(str(exc)) from exc


def abort_subprocess(benchmark_run_id: str) -> dict[str, Any]:
    with _LOCK:
        proc = _PROCS.get(benchmark_run_id)
    if proc and proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        db.update_benchmark_run_status(benchmark_run_id, "aborted", runner_pid=proc.pid)
        return {"status": "aborted", "pid": proc.pid}
    db.update_benchmark_run_status(benchmark_run_id, "aborted")
    return {"status": "aborted", "pid": None}


def get_subprocess_status(benchmark_run_id: str) -> dict[str, Any]:
    with _LOCK:
        proc = _PROCS.get(benchmark_run_id)
    if not proc:
        run = db.get_benchmark_run(benchmark_run_id)
        config = (run or {}).get("config_jsonb") or {}
        return {
            "status": config.get("runner_status") or "unknown",
            "pid": config.get("runner_pid"),
            "return_code": config.get("runner_return_code"),
            "log_path": config.get("runner_log_path"),
            "error_text": config.get("runner_error") or _runner_error(benchmark_run_id),
        }
    code = proc.poll()
    return {
        "status": "running" if code is None else "exited",
        "pid": proc.pid,
        "return_code": code,
    }


def reap_finished() -> None:
    with _LOCK:
        items = list(_PROCS.items())
    for run_id, proc in items:
        code = proc.poll()
        if code is None:
            continue
        log_path = LOG_DIR / (run_id + ".log")
        config = (db.get_benchmark_run(run_id) or {}).get("config_jsonb") or {}
        current_status = str(config.get("runner_status") or "")
        next_status = "completed" if code == 0 else "failed"
        error_text = _runner_error(run_id) if code != 0 else None
        if current_status == "aborted":
            next_status = "aborted"
            error_text = None
        db.update_benchmark_run_status(
            run_id,
            next_status,
            runner_pid=proc.pid,
            runner_log_path=str(log_path),
            runner_return_code=code,
            error=error_text,
        )
        with _LOCK:
            _PROCS.pop(run_id, None)


def _build_cmd(payload: dict[str, Any], token: str) -> list[str]:
    models = payload.get("models") or payload.get("model_matrix") or []
    if isinstance(models, str):
        models = [models]
    run_id = str(payload["benchmark_run_id"])
    dataset_id = str(payload.get("dataset_id") or "")
    dataset_path = str(payload.get("dataset_path") or _dataset_path(dataset_id))
    cmd = [
        sys.executable,
        "scripts/bench_run_dataset.py",
        "--dataset",
        dataset_path,
        "--models",
        ",".join(str(item) for item in models),
        "--benchmark-run-id",
        run_id,
        "--api-url",
        os.environ.get("BENCHMARK_RUNNER_API_URL", "http://host.docker.internal:18002"),
        "--store-url",
        os.environ.get("BENCHMARK_RUNNER_STORE_URL", "http://127.0.0.1:8080"),
        "--store-token",
        token,
        "--isolation",
        str(payload.get("isolation_mode") or payload.get("isolation") or "production"),
        "--on-duplicate",
        "replace",
    ]
    schema_path = str(payload.get("schema_path") or _schema_path(dataset_path))
    if schema_path:
        cmd.extend(["--schema", schema_path])
    if _should_skip_schema(dataset_path):
        cmd.append("--skip-schema-validation")
    if payload.get("limit") is not None:
        cmd.extend(["--limit", str(int(payload["limit"]))])
    if payload.get("openrouter_providers"):
        import json

        cmd.extend(["--openrouter-providers", json.dumps(payload["openrouter_providers"], ensure_ascii=False)])
    if payload.get("codex_reasoning_effort"):
        cmd.extend(["--codex-reasoning-effort", str(payload["codex_reasoning_effort"])])
    for case_id in payload.get("case_ids_filter") or []:
        cmd.extend(["--case-id", str(case_id)])
    if payload.get("parent_run_id"):
        cmd.extend(["--parent-run-id", str(payload["parent_run_id"])])
    if payload.get("prompt_version_override"):
        cmd.extend(["--prompt-version-override", str(payload["prompt_version_override"])])
    if payload.get("prompt_check_enabled") is False:
        cmd.append("--prompt-check-disabled")
    if payload.get("prompt_check_backend"):
        cmd.extend(["--prompt-check-backend", str(payload["prompt_check_backend"])])
    if payload.get("prompt_check_model"):
        cmd.extend(["--prompt-check-model", str(payload["prompt_check_model"])])
    if payload.get("prompt_check_openrouter_provider"):
        cmd.extend(["--prompt-check-openrouter-provider", str(payload["prompt_check_openrouter_provider"])])
    backend = str(payload.get("smart_judge_backend") or "")
    if backend:
        cmd.extend(["--smart-judge-backend", backend])
    if payload.get("smart_judge_model"):
        cmd.extend(["--smart-judge-model", str(payload["smart_judge_model"])])
    if payload.get("smart_judge_chunk_size"):
        cmd.extend(["--smart-judge-chunk-size", str(int(payload["smart_judge_chunk_size"]))])
    if payload.get("smart_judge_workers"):
        cmd.extend(["--smart-judge-workers", str(int(payload["smart_judge_workers"]))])
    if backend and backend != "off":
        cmd.append("--smart-judge-external")
    return cmd


def _runner_env(token: str) -> dict[str, str]:
    env = os.environ.copy()
    env["BENCHMARK_INGEST_TOKEN"] = token
    return env


def _dataset_path(dataset_id: str) -> str:
    if dataset_id == "golden_v1_0":
        return "data/eval/golden_v1_0.jsonl"
    if dataset_id.startswith("golden"):
        return "data/eval/golden_v1_0.jsonl"
    return "data/bench/requests/adversarial_sql_requests_v0_2.jsonl"


def dataset_case_count(dataset_id: str, dataset_path: str | None = None) -> int | None:
    path = ROOT / (dataset_path or _dataset_path(dataset_id))
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return None


def _schema_path(dataset_path: str) -> str:
    if "data/eval/" in dataset_path:
        return "data/eval/dataset.schema.json"
    return "data/bench/requests/adversarial_sql_requests.schema.json"


def _should_skip_schema(dataset_path: str) -> bool:
    path = Path(dataset_path)
    return "golden_" in path.name or "uploads" in path.parts


def list_datasets() -> list[dict[str, Any]]:
    roots = [
        ROOT / "data" / "eval",
        DATASET_UPLOAD_DIR,
        ROOT / "data" / "bench" / "requests",
    ]
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("*.jsonl")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            case_count = _count_jsonl(path)
            dataset_id = _dataset_id_from_path(path)
            out.append(
                {
                    "dataset_id": dataset_id,
                    "label": dataset_id + (" · " + str(case_count) + " cases" if case_count is not None else ""),
                    "dataset_path": rel,
                    "case_count": case_count,
                    "kind": "upload" if DATASET_UPLOAD_DIR in path.parents else "builtin",
                }
            )
    return out


def save_uploaded_dataset(filename: str, content: str, dataset_id: str | None = None) -> dict[str, Any]:
    rows = _parse_dataset_content(content)
    safe_id = _safe_dataset_id(dataset_id or Path(filename).stem)
    if not safe_id:
        raise ValueError("dataset_id is empty after normalization")
    DATASET_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = DATASET_UPLOAD_DIR / (safe_id + ".jsonl")
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(_normalize_upload_row(row), ensure_ascii=False, sort_keys=True) + "\n")
    rel = path.relative_to(ROOT).as_posix()
    return {
        "dataset_id": safe_id,
        "label": safe_id + " · " + str(len(rows)) + " cases",
        "dataset_path": rel,
        "case_count": len(rows),
        "kind": "upload",
    }


def _parse_dataset_content(content: str) -> list[dict[str, Any]]:
    text = content.strip()
    if not text:
        raise ValueError("dataset content is empty")
    rows: list[dict[str, Any]]
    if text.startswith("["):
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("JSON dataset must be an array of objects")
        rows = parsed
    else:
        rows = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError("invalid JSONL at line " + str(line_no) + ": " + str(exc)) from exc
    if not rows:
        raise ValueError("dataset has no rows")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("each dataset row must be an object")
    return rows


def _normalize_upload_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    if "user_task" not in out and out.get("task"):
        out["user_task"] = out.get("task")
    if "task" not in out and out.get("user_task"):
        out["task"] = out.get("user_task")
    if not str(out.get("id") or "").strip():
        raise ValueError("every dataset row must contain id")
    if not str(out.get("user_task") or out.get("task") or "").strip():
        raise ValueError("every dataset row must contain task or user_task")
    return out


def _safe_dataset_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip().lower()).strip("._-")
    if not normalized:
        return ""
    if not normalized.startswith("custom_"):
        normalized = "custom_" + normalized
    return normalized[:80]


def _dataset_id_from_path(path: Path) -> str:
    stem = path.stem
    if stem == "golden_v1_0":
        return "golden_v1_0"
    if stem.endswith("_v1_0") and not stem.startswith("golden"):
        return stem.rsplit("_v", 1)[0]
    return stem


def _count_jsonl(path: Path) -> int | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return None


def _judge_executor_url() -> str:
    return os.environ.get(
        "BENCHMARK_JUDGE_EXECUTOR_URL",
        os.environ.get("BENCHMARK_RUNNER_API_URL", "http://host.docker.internal:18002"),
    )


def _runner_error(run_id: str) -> str | None:
    failed_path = ROOT / "data" / "bench" / "runs" / run_id / "failed.jsonl"
    try:
        lines = [line for line in failed_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return None
    if not lines:
        return None
    try:
        row = json.loads(lines[-1])
    except json.JSONDecodeError:
        return lines[-1][:2000]
    parts = []
    if row.get("case_id"):
        parts.append("case " + str(row["case_id"]))
    if row.get("model_key"):
        parts.append("model " + str(row["model_key"]))
    if row.get("http_status"):
        parts.append("HTTP " + str(row["http_status"]))
    if row.get("error_class"):
        parts.append(str(row["error_class"]))
    message = str(row.get("message") or "").strip()
    prefix = " · ".join(parts)
    return ((prefix + ": ") if prefix else "") + message[:1800]
