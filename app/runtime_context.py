"""Small runtime context block for prompts."""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TZ = "Europe/Moscow"


def build_runtime_context(now: datetime | None = None) -> str:
    """Return current date/time facts for LLM prompts."""
    tz_name = os.environ.get("APP_TIMEZONE") or os.environ.get("TZ") or DEFAULT_TZ
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz_name = DEFAULT_TZ
        tz = ZoneInfo(DEFAULT_TZ)

    item = now or datetime.now(tz)
    if item.tzinfo is None:
        item = item.replace(tzinfo=tz)
    else:
        item = item.astimezone(tz)

    offset = item.strftime("%z")
    offset_text = offset[:3] + ":" + offset[3:] if offset else ""
    return (
        "Текущая дата и время выполнения pipeline:\n"
        + "- timezone: " + tz_name + "\n"
        + "- now: " + item.strftime("%Y-%m-%d %H:%M:%S") + "\n"
        + "- utc_offset: " + offset_text + "\n"
        + "- current_year: " + item.strftime("%Y") + "\n"
        + "- current_month: " + item.strftime("%m") + "\n"
        + "- current_day: " + item.strftime("%d") + "\n"
        + "Правило: для относительных периодов не выдумывай старые фиксированные даты. "
        + "\"за последние 30 дней\" = rolling interval от текущей даты; "
        + "\"за прошлый месяц\" = предыдущий календарный месяц; "
        + "\"за текущий месяц\"/\"за месяц\" без уточнения = текущий календарный месяц."
    )
