from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any


name = "anthropic_cli"


def invoke(
    system: str,
    user: str,
    *,
    timeout_sec: int,
    max_tokens: int,
    model: str,
    retry_attempts: int = 1,
    retry_backoff_sec: float = 0.0,
) -> dict[str, Any]:
    del max_tokens
    attempts = max(int(retry_attempts), 1)
    last_error = ""
    for attempt in range(attempts):
        try:
            return _invoke_once(system, user, timeout_sec=timeout_sec, model=model)
        except FileNotFoundError:
            # PATH miss — постоянная ошибка, retry бессмыслен.
            raise
        except RuntimeError as exc:
            last_error = str(exc)
            if attempt >= attempts - 1:
                raise
            time.sleep(max(retry_backoff_sec, 0.0) * (2 ** attempt))
    raise RuntimeError(
        "claude CLI failed after " + str(attempts) + " attempts: " + last_error
    )


def _invoke_once(
    system: str,
    user: str,
    *,
    timeout_sec: int,
    model: str,
) -> dict[str, Any]:
    binary = os.environ.get("ANTHROPIC_CLI_PATH", "claude")
    cmd = [binary, "-p", user, "--append-system-prompt", system, "--output-format", "json"]
    if model:
        cmd.extend(["--model", model])
    started = time.perf_counter()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("claude CLI timeout after " + str(timeout_sec) + " seconds") from exc
    latency = round(time.perf_counter() - started, 3)
    if result.returncode != 0:
        raise RuntimeError("claude CLI exited with code " + str(result.returncode) + ": " + result.stderr[-500:])
    raw = parse_claude_json(result.stdout)
    text = extract_text(raw)
    usage = extract_usage(raw)
    return {
        "text": text,
        "raw": raw | {"stderr": result.stderr},
        "usage": usage,
        "latency_sec": latency,
    }


def parse_claude_json(stdout: str) -> dict[str, Any]:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("claude CLI returned non-JSON output") from exc
    if not isinstance(data, dict):
        raise RuntimeError("claude CLI JSON output is not an object")
    return data


def extract_text(raw: dict[str, Any]) -> str:
    result = raw.get("result")
    if isinstance(result, str):
        return result.strip()
    content = raw.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(part for part in parts if part).strip()
    message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
    msg_content = message.get("content")
    if isinstance(msg_content, str):
        return msg_content.strip()
    return ""


def extract_usage(raw: dict[str, Any]) -> dict[str, Any] | None:
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    if not usage and "total_cost_usd" not in raw:
        return None
    out = dict(usage)
    input_tokens = int(out.get("input_tokens") or out.get("prompt_tokens") or 0)
    output_tokens = int(out.get("output_tokens") or out.get("completion_tokens") or 0)
    total_tokens = int(out.get("total_tokens") or input_tokens + output_tokens)
    if input_tokens and "prompt_tokens" not in out:
        out["prompt_tokens"] = input_tokens
    if output_tokens and "completion_tokens" not in out:
        out["completion_tokens"] = output_tokens
    if total_tokens:
        out["total_tokens"] = total_tokens
    if "total_cost_usd" in raw and "cost_usd" not in out:
        out["cost_usd"] = raw.get("total_cost_usd")
    return out or None
