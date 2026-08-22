"""Пакетний імпорт папки з уже збереженими повідомленнями."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from .meteorology import MeteoPipelineResult, run_meteo_import_pipeline
from .models import Station
from .pipeline import PipelineResult, run_import_pipeline
from .sources import LocalFileSource


_ARCHIVE_NAME = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_"
    r"(?P<message>ZRUR52|ZRUR53|ZRUR71|SYNOP)(?:_\d+)?\.txt$",
    re.IGNORECASE,
)
_LOCAL_NAME = re.compile(
    r"^(?P<date>\d{2}\.\d{2}\.\d{4})[_-]"
    r"(?P<message>ZRUR52|ZRUR53|ZRUR71|SYNOP)(?:[_-]\d+)?\.txt$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BatchFile:
    path: Path
    bulletin_date: str
    message_type: str


@dataclass(frozen=True)
class BatchError:
    path: Path
    message: str


@dataclass(frozen=True)
class BatchImportResult:
    hydrological: tuple[PipelineResult, ...]
    meteorological: tuple[MeteoPipelineResult, ...]
    errors: tuple[BatchError, ...]

    @property
    def processed_files(self) -> int:
        return len(self.hydrological) + len(self.meteorological)


def _metadata_from_name(path: Path) -> BatchFile | None:
    match = _ARCHIVE_NAME.fullmatch(path.name)
    if match:
        parsed = datetime.strptime(match.group("date"), "%Y-%m-%d")
        return BatchFile(
            path,
            parsed.strftime("%d.%m.%Y"),
            match.group("message").upper(),
        )

    match = _LOCAL_NAME.fullmatch(path.name)
    if match:
        parsed = datetime.strptime(match.group("date"), "%d.%m.%Y")
        return BatchFile(
            path,
            parsed.strftime("%d.%m.%Y"),
            match.group("message").upper(),
        )
    return None


def discover_batch_files(folder: Path) -> tuple[BatchFile, ...]:
    """Знаходить підтримувані TXT-файли та сортує їх хронологічно."""

    folder = Path(folder)
    if not folder.exists():
        raise FileNotFoundError(f"Папку пакетного імпорту не знайдено: {folder}")
    if not folder.is_dir():
        raise NotADirectoryError(f"Це не папка: {folder}")

    files = [
        metadata
        for path in folder.rglob("*.txt")
        if (metadata := _metadata_from_name(path)) is not None
    ]
    message_order = {
        "ZRUR52": 0,
        "ZRUR53": 1,
        "ZRUR71": 2,
        "SYNOP": 3,
    }
    files.sort(
        key=lambda item: (
            datetime.strptime(item.bulletin_date, "%d.%m.%Y"),
            message_order[item.message_type],
            item.path.name,
        )
    )
    return tuple(files)


def run_batch_import(
    folder: Path,
    *,
    raw_root: Path,
    db_path: Path,
    all_stations: Sequence[Station],
    hydro_stations_by_index: Mapping[str, Station],
    meteo_stations_by_index: Mapping[str, Station],
    bulletin_date: str | None = None,
    include_meteo: bool = True,
) -> BatchImportResult:
    """Імпортує підтримувані файли, не зупиняючи пакет через один збій.

    ``bulletin_date`` обмежує пакет однією робочою датою. Це дає змогу GUI
    безпечно працювати з папкою, у якій зберігаються повідомлення за кілька
    днів, і не змішує їх у поточному запуску.
    """

    hydro_results: list[PipelineResult] = []
    meteo_results: list[MeteoPipelineResult] = []
    errors: list[BatchError] = []

    files = discover_batch_files(folder)
    if bulletin_date is not None:
        try:
            datetime.strptime(bulletin_date, "%d.%m.%Y")
        except ValueError as exc:
            raise ValueError("Дата має бути у форматі ДД.ММ.РРРР.") from exc
        files = tuple(
            item for item in files if item.bulletin_date == bulletin_date
        )

    for item in files:
        if item.message_type == "SYNOP" and not include_meteo:
            continue
        source = LocalFileSource(item.path)
        try:
            if item.message_type == "SYNOP":
                meteo_results.append(
                    run_meteo_import_pipeline(
                        source,
                        bulletin_date=item.bulletin_date,
                        raw_root=raw_root,
                        db_path=db_path,
                        all_stations=all_stations,
                        meteo_stations_by_index=meteo_stations_by_index,
                        source_type="batch_meteo",
                        source_name=str(item.path),
                    )
                )
            else:
                hydro_results.append(
                    run_import_pipeline(
                        source,
                        bulletin_date=item.bulletin_date,
                        message_type=item.message_type,
                        raw_root=raw_root,
                        db_path=db_path,
                        stations=all_stations,
                        stations_by_index=hydro_stations_by_index,
                        source_type="batch",
                        source_name=str(item.path),
                    )
                )
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(BatchError(item.path, str(exc)))

    return BatchImportResult(
        tuple(hydro_results),
        tuple(meteo_results),
        tuple(errors),
    )
