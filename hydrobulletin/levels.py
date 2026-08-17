"""Оперативна вибірка для Панелі рівнів."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from .archive import query_observations
from .models import Station
from .quality import MISSING, worst_quality_status


@dataclass(frozen=True)
class LevelPanelRow:
    station_index: str
    station_name: str
    morning_level: float | None
    previous_evening_level: float | None
    daily_change: float | None
    quality_status: str
    quality_message: str
    level_observation_id: int | None
    change_observation_id: int | None
    level_correction_id: int | None
    change_correction_id: int | None


def _parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%d.%m.%Y")
    except ValueError as exc:
        raise ValueError("Дата має бути у форматі ДД.ММ.РРРР.") from exc


def _number(row: dict[str, object] | None) -> float | None:
    if row is None or row["value"] is None:
        return None
    return float(row["value"])


def build_level_panel_rows(
    db_path: Path,
    bulletin_date: str,
    stations: Iterable[Station],
) -> tuple[LevelPanelRow, ...]:
    """Читає 08:00, попередні 20:00, зміну, QC і активні правки."""

    selected_date = _parse_date(bulletin_date)
    morning_at = selected_date.replace(hour=8, minute=0, second=0, microsecond=0)
    evening_at = morning_at - timedelta(hours=12)
    station_list = tuple(stations)
    records = query_observations(
        db_path,
        start_date=evening_at.strftime("%d.%m.%Y"),
        end_date=morning_at.strftime("%d.%m.%Y"),
        station_indexes=tuple(station.index for station in station_list),
        parameter_codes=("WATER_LEVEL", "DAILY_CHANGE"),
    )

    indexed: dict[tuple[str, str, datetime], dict[str, object]] = {}
    for record in records:
        key = (
            str(record["station_index"]),
            str(record["parameter_code"]),
            datetime.fromisoformat(str(record["observed_at"])),
        )
        indexed[key] = record

    result: list[LevelPanelRow] = []
    for station in station_list:
        morning = indexed.get((station.index, "WATER_LEVEL", morning_at))
        evening = indexed.get((station.index, "WATER_LEVEL", evening_at))
        change = indexed.get((station.index, "DAILY_CHANGE", morning_at))
        present = [item for item in (morning, evening, change) if item is not None]
        statuses = [str(item["quality_status"]) for item in present]
        missing: list[str] = []
        if morning is None:
            missing.append("рівень 08:00")
        if change is None:
            missing.append("добова зміна")
        quality_status = worst_quality_status(statuses)
        messages = [
            str(item["quality_message"])
            for item in present
            if str(item["quality_message"]).strip()
        ]
        if missing:
            quality_status = MISSING
            messages.append("Відсутні: " + ", ".join(missing) + ".")

        result.append(
            LevelPanelRow(
                station.index,
                station.name,
                _number(morning),
                _number(evening),
                _number(change),
                quality_status,
                " ".join(dict.fromkeys(messages)),
                None if morning is None else int(morning["observation_id"]),
                None if change is None else int(change["observation_id"]),
                None
                if morning is None or morning.get("correction_id") is None
                else int(morning["correction_id"]),
                None
                if change is None or change.get("correction_id") is None
                else int(change["correction_id"]),
            )
        )
    return tuple(result)
