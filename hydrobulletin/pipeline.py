"""Конвеєр імпорту: джерело → raw-архів → декодер → SQLite."""

from __future__ import annotations

from dataclasses import dataclass
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
from .sources import TextDataSource


@dataclass(frozen=True)
class PipelineResult:
    message_type: str
    raw_path: Path
    observations: tuple[HydroObservation, ...]
    import_result: ImportResult


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
) -> PipelineResult:
    """Виконує один атомарний сценарій імпорту кодованого повідомлення."""

    raw_text = source.load_text()
    if not raw_text.strip():
        raise ValueError("Джерело даних повернуло порожній текст.")

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
            source_type=source_type,
            source_file=source_file,
        )
    )
    initialize_archive(db_path, stations)
    result = import_observations(
        db_path,
        observations,
        source_name=source_name,
        source_type=source_type,
        message_type=message_type,
        bulletin_date=bulletin_date,
        raw_path=source_file,
        raw_text=raw_text,
    )
    return PipelineResult(message_type.upper(), raw_path, observations, result)
