"""Часові правила для переведення локальних строків України в UTC."""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta


def last_sunday(year: int, month: int) -> datetime:
    last_day = datetime(year, month, calendar.monthrange(year, month)[1])
    return last_day - timedelta(days=(last_day.weekday() + 1) % 7)


def ukraine_utc_offset_hours(local_dt: datetime) -> int:
    """Повертає історично очікуване зміщення UTC+2/UTC+3."""

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
    return local_dt - timedelta(hours=ukraine_utc_offset_hours(local_dt))

