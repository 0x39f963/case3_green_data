from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib import error, request


name = "openrouter"


def invoke(
    system: str,
    user: str,
    *,
    timeout_sec: int,
    max_tokens: int,
    model: str,
    retry_attempts: int = 3,
    retry_backoff_sec: float = 1.0,
) -> dict[str, Any]:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    if not model:
        raise RuntimeError("--reviewer-model is required for openrouter backend")
    base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    started = time.perf_counter()
    raw = post_with_retry(
        base_url + "/chat/completions",
        body,
        api_key,
        timeout_sec=timeout_sec,
        attempts=retry_attempts,
        backoff_sec=retry_backoff_sec,
    )
    latency = round(time.perf_counter() - started, 3)
    choices = raw.get("choices") or []
    text = ""
    if choices:
        text = str(((choices[0] or {}).get("message") or {}).get("content") or "")
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
    return {"text": text.strip(), "raw": raw, "usage": usage, "latency_sec": latency}


def post_with_retry(
    url: str,
    body: dict[str, Any],
    api_key: str,
    *,
    timeout_sec: int,
    attempts: int,
    backoff_sec: float,
) -> dict[str, Any]:
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last_error = ""
    attempts = max(int(attempts), 1)
    for attempt in range(attempts):
        req = request.Request(
            url,
            data=encoded,
            method="POST",
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=timeout_sec) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            last_error = "HTTP " + str(exc.code) + ": " + text[-500:]
            if exc.code != 429 and not (500 <= exc.code < 600):
                raise RuntimeError("openrouter request failed: " + last_error) from exc
        except error.URLError as exc:
            last_error = str(exc)

        if attempt < attempts - 1:
            time.sleep(max(backoff_sec, 0.0) * (2 ** attempt))
    raise RuntimeError("openrouter request failed after " + str(attempts) + " attempts: " + last_error)
