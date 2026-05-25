from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.benchmark_client import BenchmarkClient, BenchmarkClientError  # noqa: E402
from app import prompt_registry  # noqa: E402


PROMPT_DIR = ROOT / "app" / "prompts"
SCHEMA_PATH = ROOT / "data" / "bench" / "audits" / "reviewer_output.schema.json"
AUDIT_DIR = ROOT / "data" / "bench" / "audits"
TARGET_AREAS = {
    "generator_prompt",
    "auditor_prompt",
    "faiss_generation_pg_pattern",
    "faiss_generation_pg_docs",
    "faiss_generation_schema",
    "faiss_security_vuln_class",
    "faiss_security_vuln_example",
    "schema_overlay",
    "sql_guard_rule",
    "dataset_case",
    "other",
}
SEVERITY_WEIGHT = {"P0": 3, "P1": 2, "P2": 1}


class ConfigError(RuntimeError):
    pass


def main() -> int:
    try:
        args = parse_args()
        out_dir = args.out_dir or (AUDIT_DIR / args.benchmark_run_id)
        input_dir = out_dir / "inputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        input_dir.mkdir(parents=True, exist_ok=True)
        system_record = prompt_registry.get_default_prompt("quality_reviewer_system")
        system_prompt = system_record.text
        user_template = read_prompt("bench_reviewer_user.txt")
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        prompt_version = resolve_prompt_version(args, system_prompt, user_template)
        assert_no_secret(system_prompt + "\n" + user_template, args)
        client = BenchmarkClient(args.store_url, args.store_token, timeout_sec=30, retries=args.store_retry_attempts)
        client.health()
        targets = fetch_targets(client, args)
    except (ConfigError, OSError, ValueError, json.JSONDecodeError) as exc:
        print_json({"status": "CONFIG_ERROR", "error": str(exc)})
        return 2
    except BenchmarkClientError as exc:
        print_json({"status": "STORE_UNREACHABLE", "error": str(exc), "status_code": exc.status_code})
        return 3

    validator = Draft7Validator(schema)
    reviews_path = out_dir / "reviews.jsonl"
    had_error = False
    cost_total = 0.0
    # Phase 3 P0 — reviewer matrix. Каждый backend обходит весь список
    # targets независимо. Один и тот же trace получит несколько строк в
    # audit_reviews с разным reviewer_backend; UNIQUE constraint в Store
    # на (trace_id, backend, model, prompt_version) разводит их.
    per_backend_stats: list[dict[str, Any]] = []

    # Compact-input одинаковый для всех backend'ов — пишем один раз.
    inputs_cached: dict[str, dict[str, Any]] = {}
    for item in targets:
        trace_id = str(item.get("trace_id") or "")
        if not trace_id:
            continue
        try:
            compact = build_compact_input(item, args.store_url, args.max_prompt_chars)
        except ConfigError as exc:
            print_json({"status": "CONFIG_ERROR", "trace_id": trace_id, "error": str(exc)})
            return 2
        write_json(input_dir / (trace_id + ".json"), compact)
        inputs_cached[trace_id] = compact

    if args.dry_run:
        suggestions_path = write_suggestions(out_dir, args.benchmark_run_id, prompt_version, args.write_suggestions)
        print_json({
            "status": "DRY_RUN",
            "benchmark_run_id": args.benchmark_run_id,
            "reviewer_prompt_version": prompt_version,
            "reviewer_backends": args.reviewer_backends_list,
            "targets": len(targets),
            "inputs_dir": str(input_dir),
            "reviews_path": str(reviews_path),
            "suggestions_path": str(suggestions_path),
        })
        return 0

    for backend, model in zip(args.reviewer_backends_list, args.reviewer_models_list):
        # Подменяем args.reviewer_backend/reviewer_model на время одного
        # backend-прохода. Все downstream-функции (review_trace,
        # build_store_payload, existing_reviews) читают эти поля как single.
        args.reviewer_backend = backend
        args.reviewer_model = model
        existing = existing_reviews(client, args, prompt_version) if args.resume else set()
        processed = 0
        skipped = 0
        backend_errors = 0
        for item in targets:
            trace_id = str(item.get("trace_id") or "")
            if not trace_id:
                continue
            if trace_id in existing:
                skipped += 1
                continue
            compact = inputs_cached.get(trace_id)
            if compact is None:
                continue
            processed += 1
            review, raw_resp = review_trace(args, system_prompt, user_template, compact, validator)
            review = ensure_step_scores(review, compact)
            store_payload = build_store_payload(item, review, raw_resp, args, prompt_version)
            append_jsonl(reviews_path, {
                "input_path": str(input_dir / (trace_id + ".json")),
                "reviewer_backend": backend,
                "reviewer_model": model,
                **store_payload,
            })
            client.upsert_audit_review(store_payload)
            verdict_err = store_payload["review"]["verdict"] == "error"
            if verdict_err:
                backend_errors += 1
                had_error = True
            cost_total += float(store_payload["review"].get("reviewer_cost_usd") or 0.0)
            if args.max_cost_usd is not None and cost_total > args.max_cost_usd:
                per_backend_stats.append({
                    "backend": backend, "model": model,
                    "processed": processed, "skipped": skipped, "errors": backend_errors,
                })
                write_suggestions(out_dir, args.benchmark_run_id, prompt_version, args.write_suggestions)
                print_json({
                    "status": "COST_BUDGET",
                    "per_backend": per_backend_stats,
                    "cost_usd": cost_total,
                })
                return 4
        per_backend_stats.append({
            "backend": backend, "model": model,
            "processed": processed, "skipped": skipped, "errors": backend_errors,
        })

    suggestions_path = write_suggestions(out_dir, args.benchmark_run_id, prompt_version, args.write_suggestions)
    print_json(
        {
            "status": "PARTIAL_ERROR" if had_error else "OK",
            "benchmark_run_id": args.benchmark_run_id,
            "reviewer_prompt_version": prompt_version,
            "reviewer_backends": args.reviewer_backends_list,
            "targets": len(targets),
            "per_backend": per_backend_stats,
            "cost_usd": round(cost_total, 6),
            "inputs_dir": str(input_dir),
            "reviews_path": str(reviews_path),
            "suggestions_path": str(suggestions_path),
        }
    )
    return 1 if had_error else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review stored benchmark pipeline runs and save audit feedback.")
    parser.add_argument("--store-url", default="http://localhost:18080")
    parser.add_argument("--store-token", default=os.environ.get("BENCHMARK_INGEST_TOKEN", ""))
    parser.add_argument("--benchmark-run-id", required=True)
    parser.add_argument("--trace-id", action="append", default=[])
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--family", action="append", default=[])
    parser.add_argument("--reviewer-backend", choices=["codex_cli", "anthropic_cli", "openrouter"], default="codex_cli")
    parser.add_argument(
        "--reviewer-backends",
        default="",
        help=(
            "Comma-separated list of reviewer backends for consensus matrix "
            "(Phase 3 P0). Each backend writes a separate audit_reviews row "
            "with unique (trace_id, reviewer_backend, model, prompt_version). "
            "Overrides --reviewer-backend if provided. "
            "Example: --reviewer-backends anthropic_cli,codex_cli"
        ),
    )
    parser.add_argument("--reviewer-model", default="gpt-5.5")
    parser.add_argument(
        "--reviewer-models",
        default="",
        help=(
            "Comma-separated reviewer model per backend, в том же порядке "
            "что и --reviewer-backends. Если не указан — используется "
            "--reviewer-model для всех. Например: "
            "--reviewer-models claude-opus-4-7,gpt-5.5"
        ),
    )
    parser.add_argument("--max-prompt-chars", type=int, default=30000)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write-suggestions", type=Path, default=None)
    parser.add_argument("--retry-on-invalid-json", type=int, default=1)
    parser.add_argument("--max-cost-usd", type=float, default=None)
    parser.add_argument("--export-patches", action="store_true")
    parser.add_argument("--prompt-version", default="")
    parser.add_argument("--prompt-version-file", type=Path, default=None)
    parser.add_argument("--reviewer-retry-attempts", type=int, default=3)
    parser.add_argument("--reviewer-retry-backoff-sec", type=float, default=1.0)
    parser.add_argument("--store-retry-attempts", type=int, default=3)
    parser.add_argument("--timeout-sec", type=int, default=900)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()
    if not args.store_token:
        raise ConfigError("--store-token or BENCHMARK_INGEST_TOKEN is required.")
    if args.limit < 1:
        raise ConfigError("--limit must be >= 1.")
    if args.concurrency != 1:
        raise ConfigError("--concurrency > 1 belongs to P2 and is not enabled.")
    if args.max_prompt_chars < 1000:
        raise ConfigError("--max-prompt-chars must be >= 1000.")
    if args.retry_on_invalid_json < 0:
        raise ConfigError("--retry-on-invalid-json must be >= 0.")
    if args.reviewer_retry_attempts < 1:
        raise ConfigError("--reviewer-retry-attempts must be >= 1.")
    if args.reviewer_retry_backoff_sec < 0:
        raise ConfigError("--reviewer-retry-backoff-sec must be >= 0.")
    if args.store_retry_attempts < 1:
        raise ConfigError("--store-retry-attempts must be >= 1.")
    if args.prompt_version and args.prompt_version_file:
        raise ConfigError("--prompt-version and --prompt-version-file are mutually exclusive.")
    if args.export_patches:
        raise ConfigError("--export-patches belongs to P1 patch export and is not enabled in this runner.")
    # Phase 3 P0 — reviewer matrix. Если задан --reviewer-backends, парсим
    # его как список; иначе один backend из --reviewer-backend.
    allowed_backends = {"codex_cli", "anthropic_cli", "openrouter"}
    if args.reviewer_backends.strip():
        backends = [b.strip() for b in args.reviewer_backends.split(",") if b.strip()]
        # Защита от --reviewer-backends ",,, " — split даёт [] и матрица
        # запускается на нуле reviewers, не отбивая ошибку.
        if not backends:
            raise ConfigError(
                "--reviewer-backends parsed to empty list. Provide at least one of "
                + ", ".join(sorted(allowed_backends))
            )
        for b in backends:
            if b not in allowed_backends:
                raise ConfigError(
                    "--reviewer-backends contains unknown backend '" + b
                    + "'. Allowed: " + ", ".join(sorted(allowed_backends))
                )
        args.reviewer_backends_list = backends
    else:
        args.reviewer_backends_list = [args.reviewer_backend]
    # Соответствующие модели per-backend.
    if args.reviewer_models.strip():
        models = [m.strip() for m in args.reviewer_models.split(",") if m.strip()]
        if not models:
            raise ConfigError(
                "--reviewer-models parsed to empty list. Provide at least one model."
            )
        if len(models) != len(args.reviewer_backends_list):
            raise ConfigError(
                "--reviewer-models count ({}) != --reviewer-backends count ({})"
                .format(len(models), len(args.reviewer_backends_list))
            )
        args.reviewer_models_list = models
    else:
        args.reviewer_models_list = [args.reviewer_model] * len(args.reviewer_backends_list)
    return args


def fetch_targets(client: BenchmarkClient, args: argparse.Namespace) -> list[dict[str, Any]]:
    data = client.audit_targets(
        args.benchmark_run_id,
        trace_ids=args.trace_id,
        case_ids=args.case_id,
        families=args.family,
        limit=args.limit,
    )
    items = [item for item in data.get("items") or [] if isinstance(item, dict)]
    if not items:
        raise ConfigError("No audit targets found for benchmark_run_id=" + args.benchmark_run_id)
    return items


def existing_reviews(client: BenchmarkClient, args: argparse.Namespace, prompt_version: str) -> set[str]:
    data = client.audit_reviews(
        benchmark_run_id=args.benchmark_run_id,
        reviewer_backend=args.reviewer_backend,
        reviewer_model=args.reviewer_model,
        reviewer_prompt_version=prompt_version,
        limit=500,
    )
    return {
        str(item.get("trace_id"))
        for item in data.get("items") or []
        if item.get("trace_id") and item.get("verdict") != "error"
    }


def build_compact_input(item: dict[str, Any], store_url: str, max_chars: int) -> dict[str, Any]:
    payload = item.get("payload_jsonb") if isinstance(item.get("payload_jsonb"), dict) else {}
    trace = payload.get("trace") if isinstance(payload.get("trace"), dict) else {}
    result = payload.get("system_result") if isinstance(payload.get("system_result"), dict) else {}
    meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    case = case_meta(item, payload)
    steps = compact_steps(trace)
    compact = {
        "case": {
            "case_id": item.get("case_id") or payload.get("case_id"),
            "family": case.get("family") or item.get("family"),
            "language": case.get("language") or item.get("language"),
            "user_task": case.get("user_task") or case.get("task") or item.get("task") or trace.get("task"),
            "expected_labels": case.get("expected_labels") or case.get("risk_labels") or item.get("expected_labels") or [],
            "expected_decision": raw_expected_decision(case, item),
            "expected_runtime_decision": expected_runtime_decision(case, item),
            "schema_scope": case.get("schema_scope") or [],
            "safe_rewrite": case.get("safe_rewrite") or item.get("safe_rewrite"),
            "evidence_span": case.get("evidence_span") or item.get("evidence_span") or [],
        },
        "model": {
            "model_key": item.get("model_key") or payload.get("model_key"),
            "backend": meta.get("generator_backend"),
            "provider": meta.get("generator_provider"),
            "llm_mode": payload.get("llm_mode") or item.get("llm_mode") or meta.get("mode"),
        },
        "pipeline": {
            "decision": meta.get("decision") or item.get("decision") or "unknown",
            "approved": bool(result.get("approved")),
            "iterations_used": result.get("iterations_used"),
            "final_sql": result.get("final_sql") or (trace.get("result") or {}).get("final_sql"),
            "candidates": compact_candidates(trace),
            "steps": steps,
            "guard_findings": findings_for_node(trace, "sql_guard"),
            "audit_findings": findings_for_node(trace, "audit"),
            "explain": explain_info(trace),
        },
        "prompts_used": prompt_refs(),
        "trace_ref": {"trace_id": item.get("trace_id"), "store_url": store_url.rstrip("/")},
        "budget": {"max_prompt_chars": max_chars},
    }
    return fit_prompt_budget(compact, max_chars)


def case_meta(item: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    client_meta = payload.get("client_meta") if isinstance(payload.get("client_meta"), dict) else {}
    case = client_meta.get("case") if isinstance(client_meta.get("case"), dict) else {}
    if case:
        return case
    return {
        "family": item.get("family"),
        "language": item.get("language"),
        "task": item.get("task"),
        "expected_decision": item.get("expected_decision"),
        "expected_runtime_decision": item.get("expected_runtime_decision"),
        "expected_runtime_decision_alternatives": item.get("expected_runtime_decision_alternatives"),
        "expected_labels": item.get("expected_labels"),
        "safe_rewrite": item.get("safe_rewrite"),
        "evidence_span": item.get("evidence_span"),
    }


def raw_expected_decision(case: dict[str, Any], item: dict[str, Any]) -> str:
    return str(case.get("expected_decision") or item.get("expected_decision") or "")


def expected_runtime_decision(case: dict[str, Any], item: dict[str, Any]) -> str:
    return str(
        case.get("expected_runtime_decision")
        or item.get("expected_runtime_decision")
        or case.get("expected_decision")
        or item.get("expected_decision")
        or ""
    )


def compact_steps(trace: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events(trace):
        node = str(event.get("node") or "")
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        outputs = event.get("outputs") if isinstance(event.get("outputs"), dict) else {}
        item: dict[str, Any] = {
            "node": node,
            "duration_sec": event.get("duration_sec"),
            "status_inferred": infer_status(node, outputs, details),
            "outputs": outputs,
        }
        if node == "retrieve":
            item["top_generation_hits"] = top_hits(details.get("rag_generation_hits"))
        elif node == "audit":
            item["top_security_hits"] = top_hits(details.get("rag_security_hits"))
            item["parse_error"] = details.get("parse_error")
        elif node in {"prompt_check", "sql_guard"}:
            item["findings"] = details.get("findings") or []
        elif node == "explain_sandbox":
            item["error"] = details.get("error")
        out.append(item)
    return out


def infer_status(node: str, outputs: dict[str, Any], details: dict[str, Any]) -> str:
    if node == "explain_sandbox" and outputs.get("ok") is False:
        return "major_issue" if not outputs.get("skipped") else "minor_issue"
    if details.get("parse_error"):
        return "major_issue"
    return "ok"


def compact_candidates(trace: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events(trace):
        if event.get("node") != "generate":
            continue
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        inputs = event.get("inputs") if isinstance(event.get("inputs"), dict) else {}
        items = details.get("candidates") or details.get("generate_candidates") or []
        for cand in items if isinstance(items, list) else []:
            if isinstance(cand, dict):
                out.append({"iteration": inputs.get("iteration"), "sql": cand.get("sql") or "", "usage": cand.get("usage") or {}})
            elif isinstance(cand, str):
                out.append({"iteration": inputs.get("iteration"), "sql": cand, "usage": {}})
    return out


def findings_for_node(trace: dict[str, Any], node: str) -> list[Any]:
    out: list[Any] = []
    for event in events(trace):
        if event.get("node") != node:
            continue
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        if node == "audit":
            out.extend(details.get("merged_findings") or [])
        else:
            out.extend(details.get("findings") or [])
    return out


def explain_info(trace: dict[str, Any]) -> dict[str, Any]:
    for event in events(trace):
        if event.get("node") == "explain_sandbox":
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            outputs = event.get("outputs") if isinstance(event.get("outputs"), dict) else {}
            return {"ok": outputs.get("ok"), "skipped": outputs.get("skipped"), "error": details.get("error"), "plan_text": details.get("plan")}
    return {"ok": None, "skipped": None, "error": "explain_sandbox node not found", "plan_text": None}


def top_hits(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("content") or item.get("text") or "")
        copy = {key: item.get(key) for key in ("score", "source", "table_name", "vuln_class", "doc_id", "pattern_id", "id")}
        copy["content_excerpt"] = text[:500]
        out.append(copy)
        if len(out) >= 5:
            break
    return out


def prompt_refs() -> dict[str, Any]:
    gen = read_prompt("generator_system.txt")
    aud = read_prompt("auditor_system.txt")
    return {
        "generator_system_sha256": sha256_text(gen),
        "auditor_system_sha256": sha256_text(aud),
        "generator_system_excerpt": gen[:1500],
        "auditor_system_excerpt": aud[:1500],
    }


def fit_prompt_budget(data: dict[str, Any], max_chars: int) -> dict[str, Any]:
    truncations: list[str] = []
    data["review_meta"] = {"truncations": truncations, "context_chars_used": 0}

    def current_size() -> int:
        return len(json.dumps(data, ensure_ascii=False, sort_keys=True))

    if current_size() > max_chars:
        data["pipeline"]["candidates"] = data["pipeline"]["candidates"][:1]
        truncations.append("candidates")
    if current_size() > max_chars:
        data["pipeline"]["audit_findings"] = data["pipeline"]["audit_findings"][:5]
        truncations.append("audit_findings")
    if current_size() > max_chars:
        data["pipeline"]["guard_findings"] = data["pipeline"]["guard_findings"][:5]
        truncations.append("guard_findings")
    if current_size() > max_chars:
        for step in data["pipeline"]["steps"]:
            step.pop("top_generation_hits", None)
            step.pop("top_security_hits", None)
        truncations.append("hits")
    if current_size() > max_chars:
        data["prompts_used"]["generator_system_excerpt"] = data["prompts_used"]["generator_system_excerpt"][:300]
        data["prompts_used"]["auditor_system_excerpt"] = data["prompts_used"]["auditor_system_excerpt"][:300]
        truncations.append("excerpts")
    if current_size() > max_chars:
        for step in data["pipeline"]["steps"]:
            for key in list(step.keys()):
                if key not in {"node", "duration_sec", "status_inferred"}:
                    step.pop(key, None)
        truncations.append("step_details")
    if current_size() > max_chars:
        data["pipeline"]["guard_findings"] = []
        data["pipeline"]["audit_findings"] = []
        data["pipeline"]["candidates"] = []
        explain = data["pipeline"].get("explain") or {}
        data["pipeline"]["explain"] = {
            "ok": explain.get("ok"),
            "skipped": explain.get("skipped"),
            "error": explain.get("error"),
        }
        truncations.append("pipeline_details")
    if current_size() > max_chars:
        data["prompts_used"] = {
            "generator_system_sha256": data["prompts_used"].get("generator_system_sha256"),
            "auditor_system_sha256": data["prompts_used"].get("auditor_system_sha256"),
        }
        truncations.append("prompt_excerpts_removed")
    if current_size() > max_chars:
        data["case"] = {
            "case_id": data["case"].get("case_id"),
            "family": data["case"].get("family"),
            "user_task": str(data["case"].get("user_task") or "")[:300],
            "expected_decision": data["case"].get("expected_decision"),
            "expected_runtime_decision": data["case"].get("expected_runtime_decision"),
        }
        data["model"] = {"model_key": data["model"].get("model_key")}
        data["pipeline"] = {
            "decision": data["pipeline"].get("decision"),
            "approved": data["pipeline"].get("approved"),
            "final_sql": str(data["pipeline"].get("final_sql") or "")[:500],
            "steps": [
                {"node": step.get("node"), "status_inferred": step.get("status_inferred")}
                for step in data["pipeline"].get("steps", [])
                if isinstance(step, dict)
            ],
        }
        truncations.append("minimum_trace")

    limit = 1000
    while current_size() > max_chars and limit >= 50:
        shrink_strings(data, limit)
        truncations.append("strings_" + str(limit))
        limit = limit // 2
    data["review_meta"]["context_chars_used"] = current_size()
    if data["review_meta"]["context_chars_used"] > max_chars:
        raise ConfigError("compact input cannot fit max_prompt_chars=" + str(max_chars))
    return data


def shrink_strings(value: Any, limit: int) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and len(item) > limit:
                value[key] = item[:limit] + "\n[truncated]"
            else:
                shrink_strings(item, limit)
    elif isinstance(value, list):
        for item in value:
            shrink_strings(item, limit)


def review_trace(
    args: argparse.Namespace,
    system_prompt: str,
    user_template: str,
    compact: dict[str, Any],
    validator: Draft7Validator,
) -> tuple[dict[str, Any], dict[str, Any]]:
    user = user_template.format(compact_input=json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True))
    assert_no_secret(user, args)
    last_text = ""
    last_error = ""
    backend = load_backend(args.reviewer_backend)
    for attempt in range(args.retry_on_invalid_json + 1):
        try:
            raw_resp = backend.invoke(
                system_prompt,
                user,
                timeout_sec=args.timeout_sec,
                max_tokens=args.max_tokens,
                model=args.reviewer_model,
                retry_attempts=args.reviewer_retry_attempts,
                retry_backoff_sec=args.reviewer_retry_backoff_sec,
            )
        except Exception as exc:
            return error_review(compact, str(exc)), {"text": last_text, "raw": {"error": str(exc)}, "usage": None, "latency_sec": 0.0}
        try:
            last_text = str(raw_resp.get("text") or "")
            parsed = parse_model_json(last_text)
            errors = sorted(validator.iter_errors(parsed), key=lambda item: list(item.path))
            if errors:
                raise ValueError("; ".join(str(err.message) for err in errors[:5]))
            return parsed, raw_resp
        except Exception as exc:
            last_error = str(exc)
            if attempt >= args.retry_on_invalid_json:
                break
            user = user + "\n\nPrevious response was invalid JSON or schema-invalid: " + last_error + "\nRespond again strictly per schema."
    return error_review(compact, last_error), {"text": last_text, "raw": {"error": last_error}, "usage": None, "latency_sec": 0.0}


def load_backend(name: str):
    return importlib.import_module("scripts._bench_audit.backends." + name)


def parse_model_json(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = value.strip("`")
        if value.lower().startswith("json"):
            value = value[4:]
        value = value.strip()
    first = value.find("{")
    last = value.rfind("}")
    if first >= 0 and last >= first:
        value = value[first : last + 1]
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("reviewer response is not a JSON object")
    return data


def error_review(compact: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "verdict": "error",
        "sql_correctness": {"class": "unknown", "confidence": 0.0, "evidence": ["reviewer_error: " + error[:500]]},
        "step_scores": [
            {
                "node": str(step.get("node") or "unknown"),
                "status": "unknown",
                "score": 0,
                "evidence": "Reviewer did not return valid JSON: " + error[:500],
                "fix_hint": "",
            }
            for step in compact.get("pipeline", {}).get("steps", [])
            if isinstance(step, dict)
        ],
        "suggestions": [],
        "review_meta": {
            "reviewer_self_assessment": "reviewer_error",
            "truncations": compact.get("review_meta", {}).get("truncations", []),
            "context_chars_used": int(compact.get("review_meta", {}).get("context_chars_used") or 0),
        },
    }


def ensure_step_scores(review: dict[str, Any], compact: dict[str, Any]) -> dict[str, Any]:
    nodes = [str(step.get("node") or "") for step in compact.get("pipeline", {}).get("steps", []) if isinstance(step, dict)]
    scores = [row for row in review.get("step_scores") or [] if isinstance(row, dict)]
    by_node = {str(row.get("node") or ""): row for row in scores}
    for node in nodes:
        if node and node not in by_node:
            scores.append(
                {
                    "node": node,
                    "status": "unknown",
                    "score": 0,
                    "evidence": "Node was present in trace but reviewer did not return a score.",
                    "fix_hint": "",
                }
            )
    review["step_scores"] = scores
    meta = review.get("review_meta") if isinstance(review.get("review_meta"), dict) else {}
    meta.setdefault("truncations", compact.get("review_meta", {}).get("truncations", []))
    meta["context_chars_used"] = int(compact.get("review_meta", {}).get("context_chars_used") or 0)
    review["review_meta"] = meta
    return review


def build_store_payload(
    item: dict[str, Any],
    review: dict[str, Any],
    raw_resp: dict[str, Any],
    args: argparse.Namespace,
    prompt_version: str,
) -> dict[str, Any]:
    trace_id = str(item.get("trace_id"))
    review_id = "rev_" + sha256_text("|".join([trace_id, args.reviewer_backend, args.reviewer_model, prompt_version]))[:24]
    usage = raw_resp.get("usage") if isinstance(raw_resp.get("usage"), dict) else None
    correctness = review.get("sql_correctness") if isinstance(review.get("sql_correctness"), dict) else {}
    suggestions = normalize_suggestions(review_id, review.get("suggestions") or [])
    return {
        "review": {
            "review_id": review_id,
            "trace_id": trace_id,
            "case_id": item.get("case_id"),
            "model_key": item.get("model_key"),
            "benchmark_run_id": item.get("benchmark_run_id"),
            "reviewer_backend": args.reviewer_backend,
            "reviewer_model": args.reviewer_model,
            "reviewer_prompt_version": prompt_version,
            "verdict": review.get("verdict") if review.get("verdict") in {"pass", "fail", "needs_review"} else "error",
            "reviewer_latency_sec": raw_resp.get("latency_sec"),
            "reviewer_tokens_total": usage_total_tokens(usage),
            "reviewer_cost_usd": usage_cost_usd(usage),
            "raw_response_jsonb": {"review": review, "raw": raw_resp.get("raw"), "text": raw_resp.get("text")},
        },
        "step_scores": normalize_step_scores(review_id, review.get("step_scores") or []),
        "sql_correctness": {
            "review_id": review_id,
            "class": correctness.get("class") or "unknown",
            "confidence": correctness.get("confidence"),
            "explanation": "; ".join(str(item) for item in correctness.get("evidence") or []),
            "expected_vs_actual_jsonb": {"evidence": correctness.get("evidence") or []},
        },
        "suggestions": suggestions,
    }


def normalize_step_scores(review_id: str, rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "review_id": review_id,
                "node": str(row.get("node") or "unknown"),
                "status": row.get("status") if row.get("status") in {"ok", "minor_issue", "major_issue", "unknown"} else "unknown",
                "score": int(row.get("score") or 0),
                "evidence": str(row.get("evidence") or ""),
                "fix_hint": str(row.get("fix_hint") or ""),
            }
        )
    return out


def normalize_suggestions(review_id: str, rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        target = str(row.get("target_area") or "other")
        if target not in TARGET_AREAS:
            target = "other"
        severity = str(row.get("severity") or "P2")
        if severity not in SEVERITY_WEIGHT:
            severity = "P2"
        title = str(row.get("title") or "")[:120]
        details = str(row.get("details") or "")[:2000]
        patch_hint = str(row.get("patch_hint") or "")[:2000]
        linked_node = str(row.get("linked_node") or "unknown")
        digest = sha256_text("|".join([target, severity, title, details, patch_hint, linked_node]))
        item_id = sha256_text(review_id + "|" + digest)
        out.append(
            {
                "suggestion_id": "sug_" + item_id[:24],
                "review_id": review_id,
                "target_area": target,
                "severity": severity,
                "title": title,
                "details": details,
                "patch_hint": patch_hint,
                "linked_node": linked_node,
                "content_sha256": digest,
            }
        )
    return out


def write_suggestions(out_dir: Path, run_id: str, prompt_version: str, target_path: Path | None = None) -> Path:
    reviews_path = out_dir / "reviews.jsonl"
    suggestions: list[dict[str, Any]] = []
    reviews = 0
    if reviews_path.exists():
        for row in read_jsonl(reviews_path):
            reviews += 1
            suggestions.extend(row.get("suggestions") or [])
    clusters: dict[tuple[str, str], dict[str, Any]] = {}
    for item in suggestions:
        key = (str(item.get("target_area")), normalize_text(str(item.get("title") or item.get("details") or "")))
        current = clusters.setdefault(
            key,
            {
                "cluster_id": "c_" + sha256_text("|".join(key))[:12],
                "target_area": key[0],
                "severity": item.get("severity"),
                "exemplar": item.get("title"),
                "count": 0,
                "example_trace_ids": [],
                "score": 0,
            },
        )
        current["count"] += 1
        current["score"] += SEVERITY_WEIGHT.get(str(item.get("severity")), 1)
    top = sorted(clusters.values(), key=lambda item: (-int(item["score"]), -int(item["count"]), str(item["target_area"])))
    by_sev = Counter(str(item.get("severity")) for item in suggestions)
    by_area = Counter(str(item.get("target_area")) for item in suggestions)
    payload = {
        "benchmark_run_id": run_id,
        "reviewer_prompt_version": prompt_version,
        "totals": {"reviews": reviews, "suggestions": len(suggestions), "by_severity": dict(sorted(by_sev.items()))},
        "top": top,
        "by_target_area": dict(sorted(by_area.items())),
    }
    path = target_path or (out_dir / "suggestions.json")
    write_json(path, payload)
    return path


def read_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def resolve_prompt_version(args: argparse.Namespace, system: str, user_template: str) -> str:
    if args.prompt_version_file:
        value = args.prompt_version_file.read_text(encoding="utf-8").strip()
        if not value:
            raise ConfigError("--prompt-version-file is empty.")
        return value
    return args.prompt_version or prompt_hash(system, user_template)


def prompt_hash(system: str, user_template: str) -> str:
    return sha256_text(system + "\n----\n" + user_template)


def usage_total_tokens(usage: dict[str, Any] | None) -> int | None:
    if not usage:
        return None
    total = int(usage.get("total_tokens") or 0)
    if total > 0:
        return total
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total = prompt + completion
    return total if total > 0 else None


def usage_cost_usd(usage: dict[str, Any] | None) -> float | None:
    if not usage:
        return None
    for key in ("cost_usd", "cost", "total_cost_usd"):
        if key in usage and usage.get(key) is not None:
            return float(usage.get(key) or 0.0)
    return None


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def events(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in trace.get("events") or [] if isinstance(item, dict)]


def normalize_text(text: str) -> str:
    return " ".join(text.lower().replace("`", " ").split())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                data = json.loads(line)
                if isinstance(data, dict):
                    rows.append(data)
    return rows


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(item, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def print_json(item: dict[str, Any]) -> None:
    print(json.dumps(item, ensure_ascii=False, indent=2, sort_keys=True))


def assert_no_secret(text: str, args: argparse.Namespace) -> None:
    values = [
        os.environ.get("OPENROUTER_API_KEY", ""),
        os.environ.get("BENCHMARK_INGEST_TOKEN", ""),
        args.store_token,
    ]
    for value in values:
        value = str(value or "").strip()
        if len(value) >= 8 and value in text:
            raise ConfigError("reviewer prompt contains a configured secret")


if __name__ == "__main__":
    raise SystemExit(main())
