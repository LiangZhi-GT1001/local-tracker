from __future__ import annotations

from datetime import datetime, time, timedelta


def local_tz():
    return datetime.now().astimezone().tzinfo


def to_local_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    tz = local_tz()
    return value.astimezone(tz) if tz else value


def format_local_datetime(value: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return to_local_datetime(value).strftime(fmt)


def parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def format_local_iso(value: str, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    if not value:
        return "-"
    dt = parse_iso_datetime(value)
    if dt is None:
        return value[:19].replace("T", " ")
    return format_local_datetime(dt, fmt)


def local_period_start(period: str, now: datetime | None = None) -> datetime:
    tz = local_tz()
    current = now or datetime.now(tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    elif tz:
        current = current.astimezone(tz)

    if period == "daily":
        return current.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "weekly":
        monday = current.date() - timedelta(days=current.weekday())
        return datetime.combine(monday, time.min, tzinfo=tz)
    if period == "monthly":
        return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"Unsupported period: {period}")
