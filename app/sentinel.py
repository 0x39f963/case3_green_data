"""
Policy-label sentinel detection.

Генератор обучен выдавать sentinel-SQL в двух случаях:
  SELECT 'REFUSAL_REQUIRED' AS reason, '...' AS message;
  SELECT 'INSUFFICIENT_CONTEXT' AS reason, '...' AS missing;

Этот модуль превращает такой SQL в структурированный policy-label, чтобы
финализатор pipeline не отдавал бизнес-SQL в публичный ответ и формировал
человеко-читаемое refusal/insufficient-context сообщение.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


REFUSAL_REQUIRED = "refusal_required"
INSUFFICIENT_CONTEXT = "insufficient_context"


@dataclass(frozen=True)
class Sentinel:
    kind: str
    message: str


_SENTINEL_REASON_RE = re.compile(
    r"^\s*(?:SELECT\s+)?'?(?P<reason>REFUSAL_REQUIRED|INSUFFICIENT_CONTEXT)'?\s+AS\s+reason\b",
    re.IGNORECASE | re.DOTALL,
)
_SENTINEL_MESSAGE_RE = re.compile(
    r",\s*'(?P<msg>[^']{0,500})'\s+AS\s+(?:message|missing)\b",
    re.IGNORECASE | re.DOTALL,
)


def detect(sql: str) -> Sentinel | None:
    """Вернуть Sentinel, если SQL — это policy-sentinel; иначе None."""
    if not sql:
        return None
    reason_match = _SENTINEL_REASON_RE.search(sql)
    if reason_match is None:
        return None
    reason = reason_match.group("reason").upper()
    kind = REFUSAL_REQUIRED if reason == "REFUSAL_REQUIRED" else INSUFFICIENT_CONTEXT
    message_match = _SENTINEL_MESSAGE_RE.search(sql)
    message = (message_match.group("msg") if message_match else "").strip()
    return Sentinel(kind=kind, message=message)


def is_sentinel(sql: str) -> bool:
    return detect(sql) is not None
