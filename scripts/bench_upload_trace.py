from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    args = _args()
    trace = json.loads(args.trace.read_text(encoding="utf-8"))
    payload = _payload(args, trace)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    response = _post(args.url.rstrip("/") + "/v1/ingest/run", args.token, body)
    print(
        json.dumps(
            {
                "request_sha256": hashlib.sha256(body).hexdigest(),
                "request_size_bytes": len(body),
                "response": response,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--benchmark-run-id", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--model-key", required=True)
    parser.add_argument("--llm-mode", default="")
    parser.add_argument("--no-report-data", action="store_true")
    return parser.parse_args()


def _payload(args: argparse.Namespace, trace: dict[str, Any]) -> dict[str, Any]:
    system_result = _dict(trace.get("result"))
    metadata = _dict(system_result.get("metadata"))
    trace_id = str(metadata.get("trace_id") or trace.get("request_id") or args.trace.stem)
    llm_mode = args.llm_mode or str(metadata.get("mode") or "unknown")
    payload = {
        "trace_id": trace_id,
        "benchmark_run_id": args.benchmark_run_id,
        "dataset_id": args.dataset_id,
        "dataset_version": args.dataset_version,
        "case_id": args.case_id,
        "model_key": args.model_key,
        "llm_mode": llm_mode,
        "system_result": system_result,
        "trace": trace,
        "client_meta": {
            "runner_version": "0.1.0",
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    if not args.no_report_data:
        report = _report_data(trace_id, args, trace, system_result, llm_mode)
        if report:
            payload["report_data"] = report
    return payload


def _report_data(
    trace_id: str,
    args: argparse.Namespace,
    trace: dict[str, Any],
    system_result: dict[str, Any],
    llm_mode: str,
) -> dict[str, Any] | None:
    try:
        from app.test_report import TestRun, build_report_data
    except Exception:
        return None
    run = TestRun(
        run_id=trace_id,
        user_id=0,
        user_name="benchmark",
        task=str(trace.get("task") or ""),
        model_key=args.model_key,
        model_label=args.model_key,
        llm_mode=llm_mode,
        llm_generator_model=args.model_key,
        started_at=_dt(trace.get("started_at")),
        finished_at=_dt(trace.get("finished_at")),
        system_result=system_result,
        trace=trace,
    )
    return build_report_data(run)


def _post(url: str, token: str, body: bytes) -> dict[str, Any]:
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise SystemExit("HTTP " + str(exc.code) + ": " + text) from exc


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dt(value: Any) -> datetime:
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.now(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
