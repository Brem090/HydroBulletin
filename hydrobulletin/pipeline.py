"""Конвеєр імпорту: джерело → raw-архів → декодер → SQLite."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence

from .archive import (
    ImportResult,
    archive_raw_text,
    import_observations,
    initialize_archive,
)
from .decoder import decode_codes
from .models import HydroObservation, Station
from .quality import QualitySummary, run_initial_quality_control
from .sources import TextDataSource


@dataclass(frozen=True)
class PipelineResult:
    message_type: str
    raw_path: Path
    observations: tuple[HydroObservation, ...]
    import_result: ImportResult
    source_type: str
    source_name: str
    quality_summary: QualitySummary | None = None


def run_import_pipeline(
    source: TextDataSource,
    *,
    bulletin_date: str,
    message_type: str,
    raw_root: Path,
    db_path: Path,
    stations: Sequence[Station],
    stations_by_index: Mapping[str, Station],
    source_type: str,
    source_name: str,
    apply_quality_control: bool = True,
) -> PipelineResult:
    """Отримує повідомлення, зберігає raw-файл і записує вимірювання."""

    raw_text = source.load_text()
    if not raw_text.strip():
        raise ValueError("Джерело даних повернуло порожній текст.")

    resolved_source_type = str(getattr(source, "source_type", source_type))
    resolved_source_name = str(getattr(source, "source_name", source_name))
    if resolved_source_type == "auto":
        resolved_source_type = source_type
    if resolved_source_name == "автоматичне джерело":
        resolved_source_name = source_name

    raw_path = archive_raw_text(raw_root, bulletin_date, message_type, raw_text)
    try:
        source_file = raw_path.relative_to(Path(raw_root).parent).as_posix()
    except ValueError:
        source_file = str(raw_path)

    observations = tuple(
        decode_codes(
            raw_text,
            bulletin_date,
            dict(stations_by_index),
            source_type=resolved_source_type,
            source_file=source_file,
        )
    )
    initialize_archive(db_path, stations)
    result = import_observations(
        db_path,
        observations,
        source_name=resolved_source_name,
        source_type=resolved_source_type,
        message_type=message_type,
        bulletin_date=bulletin_date,
        raw_path=source_file,
        raw_text=raw_text,
    )
    quality_summary: QualitySummary | None = None
    if apply_quality_control:
        bulletin = datetime.strptime(bulletin_date, "%d.%m.%Y")
        previous_date = (bulletin - timedelta(days=1)).strftime("%d.%m.%Y")
        run_initial_quality_control(db_path, previous_date)
        quality_summary = run_initial_quality_control(db_path, bulletin_date)

    return PipelineResult(
        message_type.upper(),
        raw_path,
        observations,
        result,
        resolved_source_type,
        resolved_source_name,
        quality_summary,
    )
