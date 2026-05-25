from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any

from fastapi import HTTPException, Request


_BUCKETS: dict[str, tuple[float, float]] = {}
PLACEHOLDER_TOKEN = "change-me-32-chars-min"
MIN_TOKEN_LEN = 32


def api_error(status_code: int, code: str, message: str, details: Any | None = None) -> None:
    detail: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        detail["details"] = details
    raise HTTPException(status_code=status_code, detail=detail)


def require_token(request: Request) -> bool:
    configured = _configured_token()
    if not configured:
        api_error(500, "auth_not_configured", "BENCHMARK_INGEST_TOKEN is not configured.")

    header = request.headers.get("authorization", "").strip()
    if not header:
        api_error(401, "missing_token", "Authorization header is required.")
    prefix = "Bearer "
    if not header.startswith(prefix):
        api_error(401, "missing_token", "Bearer authorization is required.")

    token = header[len(prefix):].strip()
    if not hmac.compare_digest(token, configured):
        api_error(403, "bad_token", "Authorization token is invalid.")
    if not _take_token(token):
        api_error(429, "rate_limited", "Rate limit exceeded.")
    return True


def validate_startup_auth() -> None:
    token = _configured_token()
    if not token:
        raise RuntimeError("auth_not_configured: BENCHMARK_INGEST_TOKEN is not configured.")
    if token == PLACEHOLDER_TOKEN:
        raise RuntimeError("auth_not_configured: placeholder BENCHMARK_INGEST_TOKEN is rejected.")
    if len(token) < MIN_TOKEN_LEN:
        raise RuntimeError("auth_not_configured: BENCHMARK_INGEST_TOKEN must be at least 32 characters.")


def max_body_bytes() -> int:
    raw = os.environ.get("BENCHMARK_MAX_BODY_MB", "25").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 25
    return max(value, 1) * 1024 * 1024


def _take_token(token: str) -> bool:
    limit = _rate_limit()
    if limit <= 0:
        return True

    now = time.monotonic()
    token_id = hashlib.sha256(token.encode("utf-8")).hexdigest()
    current, updated = _BUCKETS.get(token_id, (float(limit), now))
    refill = (now - updated) * (float(limit) / 60.0)
    current = min(float(limit), current + refill)
    if current < 1.0:
        _BUCKETS[token_id] = (current, now)
        return False
    _BUCKETS[token_id] = (current - 1.0, now)
    return True


def _rate_limit() -> int:
    raw = os.environ.get("BENCHMARK_RATE_LIMIT_PER_MIN", "60").strip()
    try:
        return int(raw)
    except ValueError:
        return 60


def _configured_token() -> str:
    return os.environ.get("BENCHMARK_INGEST_TOKEN", "").strip()
