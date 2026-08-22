"""Імпорт SYNOP-опадів і мапінг «гідропост ↔ метеостанція»."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence

from .archive import ImportResult, archive_raw_text, import_observations, initialize_archive
from .decoder import normalize_token
from .models import HydroObservation, Station
from .quality import QualitySummary, run_initial_quality_control
from .sources import TextDataSource
from .timeutils import ukraine_local_to_utc


@dataclass(frozen=True)
class SynopRecord:
    """Один строк синоптичного повідомлення в UTC."""

    section: str
    station_name: str
    observed_at_utc: datetime
    station_index: str
    groups: tuple[str, ...]
    raw_record: str


@dataclass(frozen=True)
class MeteoPipelineResult:
    raw_path: Path
    observations: tuple[HydroObservation, ...]
    import_result: ImportResult
    source_type: str
    source_name: str
    quality_summary: QualitySummary


def parse_synop_records(raw_text: str) -> list[SynopRecord]:
    """Виділяє станцію, UTC-час і кодові групи з тексту SM/SI."""

    records: list[SynopRecord] = []
    section = ""
    station_name = ""
    current_dt: datetime | None = None
    current_lines: list[str] = []
    expect_station_name = False

    def flush() -> None:
        nonlocal current_dt, current_lines
        if current_dt is None or not current_lines:
            current_dt = None
            current_lines = []
            return

        code_text = " ".join(current_lines)
        groups = tuple(
            normalized
            for part in code_text.split()
            if (normalized := normalize_token(part))
        )
        if groups and re.fullmatch(r"\d{5}", groups[0]):
            records.append(
                SynopRecord(
                    section=section,
                    station_name=station_name,
                    observed_at_utc=current_dt,
                    station_index=groups[0],
                    groups=groups,
                    raw_record=code_text,
                )
            )
        current_dt = None
        current_lines = []

    prepared = raw_text.replace("\r", "\n")
    prepared = re.sub(
        r"=\s*(?=(?:SM|SI)\s+Синоптичне)",
        "=\n",
        prepared,
    )
    prepared = re.sub(
        r"=\s*(?=\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})",
        "=\n",
        prepared,
    )

    for raw_line in prepared.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        header = re.match(r"^(SM|SI)\s+.*?:\s*(.*?)\s*$", line)
        split_header = re.match(r"^(SM|SI)\s+Синоптичне\s+зведення", line)
        if header:
            flush()
            section = header.group(1)
            station_name = header.group(2).strip()
            expect_station_name = not bool(station_name)
            continue
        if split_header:
            flush()
            section = split_header.group(1)
            station_name = ""
            expect_station_name = True
            continue
        if expect_station_name:
            if line != ":":
                station_name = line
                expect_station_name = False
            continue

        match = re.match(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})$", line)
        if match:
            flush()
            current_dt = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            continue
        if current_dt is not None:
            current_lines.append(line)

    flush()
    return records


def decode_synop_precip_amount(rrr: int) -> float | None:
    """Декодує поле ``RRR`` групи SYNOP ``6RRRtR`` у міліметри.

    Код 990 (сліди опадів) зберігається як 0,0 мм у числовому полі; коди
    991–999 відповідають 0,1–0,9 мм. Значення 989 є нижньою межею
    «989 мм або більше», тому його числове представлення дорівнює 989,0.
    """

    if rrr == 990:
        return 0.0
    if 991 <= rrr <= 999:
        return (rrr - 990) / 10.0
    if 0 <= rrr <= 989:
        return float(rrr)
    return None


def synop_precip_period_hours(group: str) -> int | None:
    normalized = normalize_token(group)
    if not (
        len(normalized) == 5
        and normalized.startswith("6")
        and normalized[1:].isdigit()
    ):
        return None
    return {1: 6, 2: 12, 3: 18, 4: 24}.get(int(normalized[4]))


def synop_precip_groups(record: SynopRecord) -> list[tuple[float, int, str]]:
    """Повертає лише групи ``6RRRtR`` із секції опадів."""

    result: list[tuple[float, int, str]] = []
    after_555 = False
    for position, group in enumerate(record.groups):
        normalized = normalize_token(group)
        if normalized == "555":
            after_555 = True
            continue
        if not (
            len(normalized) == 5
            and normalized.startswith("6")
            and normalized[1:].isdigit()
        ):
            continue
        if not after_555 and position < 6:
            continue

        amount = decode_synop_precip_amount(int(normalized[1:4]))
        period = synop_precip_period_hours(normalized)
        if amount is not None and period is not None:
            result.append((amount, period, normalized))
    return result


def synop_precipitation_indicator(record: SynopRecord) -> int | None:
    """Повертає ``iR`` з групи ``iRiXhVV`` або ``None``.

    ``iR = 3`` означає, що групу опадів пропущено через нульову кількість;
    ``iR = 4`` — що кількість опадів недоступна. Сам індикатор не замінює
    наявну групу ``6RRRtR``, а пояснює лише її відсутність.
    """

    if len(record.groups) < 2:
        return None
    indicator_group = normalize_token(record.groups[1])
    if len(indicator_group) != 5 or not indicator_group.isdigit():
        return None
    indicator = int(indicator_group[0])
    return indicator if 0 <= indicator <= 4 else None


def _precip_amount(record: SynopRecord | None, period_hours: int) -> float | None:
    if record is None:
        return None
    for amount, period, _group in synop_precip_groups(record):
        if period == period_hours:
            return amount
    if synop_precipitation_indicator(record) == 3:
        return 0.0
    return None


def daily_precipitation(
    records: Sequence[SynopRecord],
    bulletin_date: str,
) -> float | None:
    """Обчислює суму за локальний інтервал 09:00–09:00.

    Спочатку використовуються дві 12-годинні суми. За їх відсутності кожна
    половина доби складається з двох 6-годинних сум.
    """

    try:
        bulletin = datetime.strptime(bulletin_date, "%d.%m.%Y")
    except ValueError as exc:
        raise ValueError("Дата має бути у форматі ДД.ММ.РРРР.") from exc

    by_time = {record.observed_at_utc: record for record in records}
    previous_day = bulletin - timedelta(days=1)

    def at_local(local_dt: datetime) -> SynopRecord | None:
        return by_time.get(ukraine_local_to_utc(local_dt))

    def half_day(
        end_local: datetime,
        middle_local: datetime,
    ) -> float | None:
        amount_12 = _precip_amount(at_local(end_local), 12)
        if amount_12 is not None:
            return amount_12

        amounts_6 = tuple(
            _precip_amount(at_local(local_dt), 6)
            for local_dt in (middle_local, end_local)
        )
        if any(amount is None for amount in amounts_6):
            return None
        return sum(float(amount) for amount in amounts_6 if amount is not None)

    first = half_day(
        previous_day.replace(hour=21, minute=0, second=0, microsecond=0),
        previous_day.replace(hour=15, minute=0, second=0, microsecond=0),
    )
    second = half_day(
        bulletin.replace(hour=9, minute=0, second=0, microsecond=0),
        bulletin.replace(hour=3, minute=0, second=0, microsecond=0),
    )
    if first is None or second is None:
        return None
    return round(first + second, 1)


def records_by_station(
    records: Sequence[SynopRecord],
) -> dict[str, list[SynopRecord]]:
    grouped: dict[str, list[SynopRecord]] = {}
    for record in records:
        grouped.setdefault(record.station_index, []).append(record)
    for items in grouped.values():
        items.sort(key=lambda item: item.observed_at_utc)
    return grouped


def decode_meteo_precipitation(
    raw_text: str,
    bulletin_date: str,
    stations_by_index: Mapping[str, Station],
    *,
    source_type: str,
    source_file: str,
) -> list[HydroObservation]:
    """Перетворює SYNOP на спільну модель із параметром PRECIPITATION."""

    records = parse_synop_records(raw_text)
    grouped = records_by_station(records)
    observed_at = datetime.strptime(bulletin_date, "%d.%m.%Y").replace(hour=9)
    result: list[HydroObservation] = []

    for index, station in stations_by_index.items():
        station_records = grouped.get(index, [])
        amount = daily_precipitation(station_records, bulletin_date)
        if amount is None:
            continue
        raw_record = "\n".join(record.raw_record for record in station_records)
        result.append(
            HydroObservation(
                index=index,
                station_name=station.name,
                level=None,
                change=None,
                evening_level=None,
                raw_record=raw_record,
                quality_status="NOT_CHECKED",
                precipitation_mm=amount,
                observed_at=observed_at,
                source_type=source_type,
                source_file=source_file,
            )
        )
    return result


def load_precipitation_mapping(path: Path) -> dict[str, str]:
    """Читає і перевіряє налаштовуваний JSON-мапінг."""

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Файл мапінгу опадів не знайдено: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Мапінг опадів має бути JSON-об'єктом.")

    result: dict[str, str] = {}
    for hydro_index, meteo_index in data.items():
        hydro_text = str(hydro_index).strip()
        meteo_text = str(meteo_index).strip()
        if not re.fullmatch(r"\d{5}", hydro_text):
            raise ValueError(f"Некоректний індекс гідропоста: {hydro_index}")
        if not re.fullmatch(r"\d{5}", meteo_text):
            raise ValueError(f"Некоректний індекс метеостанції: {meteo_index}")
        result[hydro_text] = meteo_text
    return result


def run_meteo_import_pipeline(
    source: TextDataSource,
    *,
    bulletin_date: str,
    raw_root: Path,
    db_path: Path,
    all_stations: Sequence[Station],
    meteo_stations_by_index: Mapping[str, Station],
    source_type: str,
    source_name: str,
) -> MeteoPipelineResult:
    """Зберігає raw SYNOP, декодує опади й записує їх до спільного SQLite."""

    raw_text = source.load_text()
    if not raw_text.strip():
        raise ValueError("Джерело SYNOP повернуло порожній текст.")

    resolved_type = str(getattr(source, "source_type", source_type))
    resolved_name = str(getattr(source, "source_name", source_name))
    raw_path = archive_raw_text(raw_root, bulletin_date, "SYNOP", raw_text)
    try:
        source_file = raw_path.relative_to(Path(raw_root).parent).as_posix()
    except ValueError:
        source_file = str(raw_path)

    observations = tuple(
        decode_meteo_precipitation(
            raw_text,
            bulletin_date,
            meteo_stations_by_index,
            source_type=resolved_type,
            source_file=source_file,
        )
    )
    initialize_archive(db_path, all_stations)
    imported = import_observations(
        db_path,
        observations,
        source_name=resolved_name,
        source_type=resolved_type,
        message_type="SYNOP",
        bulletin_date=bulletin_date,
        raw_path=source_file,
        raw_text=raw_text,
    )
    quality = run_initial_quality_control(db_path, bulletin_date)
    return MeteoPipelineResult(
        raw_path,
        observations,
        imported,
        resolved_type,
        resolved_name,
        quality,
    )
