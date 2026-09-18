from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "Asia/Kolkata"


def get_timezone(timezone_name: str | None = None) -> ZoneInfo:
    name = timezone_name or os.getenv("TIMEZONE", DEFAULT_TIMEZONE)
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def ensure_timezone(dt: datetime | None = None, timezone_name: str | None = None) -> datetime:
    tz = get_timezone(timezone_name)
    if dt is None:
        return datetime.now(tz)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).astimezone(tz)
    return dt.astimezone(tz)


def now_local(timezone_name: str | None = None) -> datetime:
    return ensure_timezone(timezone_name=timezone_name)


def now_local_iso(timezone_name: str | None = None) -> str:
    return now_local(timezone_name).isoformat()
