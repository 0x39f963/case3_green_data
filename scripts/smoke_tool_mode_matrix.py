from __future__ import annotations

import argparse
import json
import signal
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import llm_provider, tool_loop, tools  # noqa: E402


REPORT_PATH = ROOT / ".cursor/!tmp/!ARTEFACTS/2026-05-21/13-tool_mode_matrix_report.md"
TARGET_KEYS = {
    "local-qwen3-5-9b",
    "local-qwen3-8b",
    "local-qwen-coder-7b",
    "local-arctic-text2sql-7b",
    "or-qwen3-5-9b",
    "or-qwen3-235b-a22b-2507",
    "or-qwen3-32b",
    "or-gpt-5-4-mini",
    "or-gpt-5-4-nano",
    "or-gemini-3-1-flash-lite",
    "or-claude-haiku-4-5",
    "or-qwen3-coder-30b-a3b",
}


def main() -> int:
    args = parse_args()
    options = [
        item for item in llm_provider.list_model_options()
        if item.get("key") in TARGET_KEYS and item.get("backend") in {"openrouter", "local_openai"}
    ]
    if args.models:
        wanted = {part.strip() for part in args.models.split(",") if part.strip()}
        options = [item for item in options if item.get("key") in wanted]

    rows = []
    for item in options:
        print("[matrix] " + str(item.get("key") or ""), flush=True)
        if args.dry_run:
            rows.append(_row(item, "not_run", "unknown", "dry_run"))
            write_report(rows, REPORT_PATH)
            continue
        rows.append(run_one_with_timeout(item, args.timeout_sec))
        write_report(rows, REPORT_PATH)

    write_report(rows, REPORT_PATH)
    print(json.dumps({"report_path": str(REPORT_PATH), "rows": rows}, ensure_ascii=False, indent=2))
    failed = [row for row in rows if row["outcome"] == "provider_error"]
    return 2 if failed and args.fail_on_provider_error else 0


def run_one_with_timeout(item: dict[str, Any], timeout_sec: int) -> dict[str, Any]:
    if timeout_sec <= 0:
        return run_one(item)
    try:
        with _time_limit(timeout_sec):
            return run_one(item)
    except TimeoutError as exc:
        return _row(item, "provider_error", "unknown", str(exc))


def run_one(item: dict[str, Any]) -> dict[str, Any]:
    key = str(item.get("key") or "")
    try:
        with llm_provider.model_override(
            llm_mode=str(item.get("llm_mode") or ""),
            llm_generator_model=str(item.get("llm_generator_model") or ""),
        ):
            client = llm_provider.get_llm("generator")
            result = tool_loop.run_with_tools(
                client,
                "You must call the provided tool exactly once, then summarize the result.",
                "Call tool get_sensitive_fields for table employees and return the result.",
                tool_specs=[tools.GET_SENSITIVE_FIELDS_SPEC],
                max_steps=2,
                temperature=0.5,
                tool_choice="auto",
            )
    except Exception as exc:  # noqa: BLE001
        return _row(item, "provider_error", "unknown", exc.__class__.__name__ + ": " + str(exc)[:240])

    called = []
    for step in result.get("steps") or []:
        for call in step.get("tool_calls") or []:
            called.append(call.get("name"))
    if called == ["get_sensitive_fields"]:
        return _row(item, "passed", "verified", "")
    if not called:
        return _row(item, "model_didnt_call_tool", "unsupported", "")
    return _row(item, "wrong_tool_format", "unsupported", ",".join(str(name) for name in called))


@contextmanager
def _time_limit(timeout_sec: int):
    def _raise_timeout(signum: int, frame: Any) -> None:
        del signum, frame
        raise TimeoutError("timeout_sec=" + str(timeout_sec))

    old_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(timeout_sec))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, old_handler)


def _row(item: dict[str, Any], outcome: str, support: str, error: str) -> dict[str, Any]:
    return {
        "key": str(item.get("key") or ""),
        "backend": str(item.get("backend") or ""),
        "provider_model": str(item.get("provider_model") or ""),
        "outcome": outcome,
        "supports_tool_mode": support,
        "error": error,
    }


def write_report(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "Generated at: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S MSK"),
        "",
        "## Tool Mode Matrix Report",
        "",
        "| key | backend | provider_model | outcome | supports_tool_mode | error |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {key} | {backend} | {provider_model} | {outcome} | {supports_tool_mode} | {error} |".format(
                **{key: _cell(value) for key, value in row.items()}
            )
        )
    lines.extend(
        [
            "",
            "## Outcome Legend",
            "",
            "- passed: provider emitted exactly one `get_sensitive_fields` tool call.",
            "- model_didnt_call_tool: provider answered without a tool call.",
            "- wrong_tool_format: provider emitted another tool or malformed sequence.",
            "- provider_error: API, auth, quota, timeout, or local runtime error.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "/").replace("\n", " ")[:500]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live tool-calling smoke over runtime presets.")
    parser.add_argument("--models", default="", help="Comma-separated model option keys to test.")
    parser.add_argument("--dry-run", action="store_true", help="Write report structure without provider calls.")
    parser.add_argument("--timeout-sec", type=int, default=45, help="Per-model timeout; <=0 disables.")
    parser.add_argument("--fail-on-provider-error", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
