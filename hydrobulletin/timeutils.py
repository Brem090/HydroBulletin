"""Переведення локального часу України в UTC за базою часових зон IANA."""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


try:
    UKRAINE_TIMEZONE: ZoneInfo | None = ZoneInfo("Europe/Kyiv")
except ZoneInfoNotFoundError:  # Захисний резерв для Python без пакета tzdata.
    UKRAINE_TIMEZONE = None


def last_sunday(year: int, month: int) -> datetime:
    last_day = datetime(year, month, calendar.monthrange(year, month)[1])
    return last_day - timedelta(days=(last_day.weekday() + 1) % 7)


def ukraine_utc_offset_hours(local_dt: datetime) -> int:
    """Повертає чинне для заданого локального моменту зміщення від UTC."""

    if local_dt.tzinfo is not None:
        raise ValueError("Очікується локальний час без tzinfo.")
    if UKRAINE_TIMEZONE is not None:
        offset = local_dt.replace(tzinfo=UKRAINE_TIMEZONE).utcoffset()
        if offset is not None:
            return round(offset.total_seconds() / 3600)

    # Резервне правило потрібне лише тоді, коли у середовищі немає IANA tzdata.
    summer_start = last_sunday(local_dt.year, 3).replace(
        hour=3,
        minute=0,
        second=0,
        microsecond=0,
    )
    summer_end = last_sunday(local_dt.year, 10).replace(
        hour=4,
        minute=0,
        second=0,
        microsecond=0,
    )
    return 3 if summer_start <= local_dt < summer_end else 2


def ukraine_local_to_utc(local_dt: datetime) -> datetime:
    """Перетворює наївний локальний час на наївний UTC-час для SQLite."""

    if local_dt.tzinfo is not None:
        raise ValueError("Очікується локальний час без tzinfo.")
    if UKRAINE_TIMEZONE is not None:
        return (
            local_dt.replace(tzinfo=UKRAINE_TIMEZONE)
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )
    return local_dt - timedelta(hours=ukraine_utc_offset_hours(local_dt))
