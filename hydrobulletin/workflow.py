"""Єдиний прикладний сценарій третього тижня HydroBulletin."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .archive import archive_summary, initialize_archive
from .bulletins import BulletinResult, generate_bulletins
from .meteorology import (
    MeteoPipelineResult,
    load_precipitation_mapping,
    run_meteo_import_pipeline,
)
from .pipeline import PipelineResult, run_import_pipeline
from .quality import QualitySummary, run_initial_quality_control
from .regions import REGIONS, resolve_regions
from .sources import (
    ArchiveDataSource,
    DataSourceError,
    FallbackDataSource,
    LocalFileSource,
    OnlineDataSource,
    OnlineMeteoDataSource,
    OnlineSourceSettings,
    TextDataSource,
)
from .stations import (
    ALL_STATIONS,
    METEO_STATIONS,
    STATIONS_BY_INDEX,
)


SOURCE_MODES = ("auto", "local", "online", "archive", "database")
DEFAULT_HYDROLOGIST = "Євген КОЗИРЄВ"


@dataclass(frozen=True)
class WorkflowRequest:
    """Усі змінні одного запуску без залежності від CLI або Tkinter."""

    bulletin_date: str
    source_mode: str
    message_types: tuple[str, ...]
    db_path: Path
    raw_root: Path
    templates_dir: Path
    output_dir: Path
    mapping_path: Path
    region_keys: tuple[str, ...] = tuple(region.key for region in REGIONS)
    hydrologist: str = DEFAULT_HYDROLOGIST
    local_file: Path | None = None
    meteo_file: Path | None = None
    env_path: Path | None = None
    include_meteo: bool = True
    create_bulletins: bool = True


@dataclass(frozen=True)
class WorkflowResult:
    """Результат, однаково придатний для консолі та мінімального GUI."""

    hydro_imports: tuple[PipelineResult, ...]
    meteo_import: MeteoPipelineResult | None
    quality_summary: QualitySummary
    bulletins: tuple[BulletinResult, ...]
    warnings: tuple[str, ...]
    archive_counts: dict[str, int] = field(default_factory=dict)


def _validate_request(request: WorkflowRequest) -> None:
    try:
        datetime.strptime(request.bulletin_date, "%d.%m.%Y")
    except ValueError as exc:
        raise ValueError("Дата має бути у форматі ДД.ММ.РРРР.") from exc

    if request.source_mode not in SOURCE_MODES:
        raise ValueError(
            f"Невідомий режим {request.source_mode}. "
            f"Доступні: {', '.join(SOURCE_MODES)}."
        )
    if not request.message_types:
        raise ValueError("Потрібно вибрати хоча б один тип гідроповідомлення.")
    if not request.region_keys:
        raise ValueError("Потрібно вибрати хоча б один регіон бюлетеня.")


def _load_online_connection(
    request: WorkflowRequest,
    warnings: list[str],
):
    try:
        return OnlineSourceSettings().load_connection(request.env_path)
    except DataSourceError as exc:
        if request.source_mode == "online":
            raise
        warnings.append(f"Онлайн-джерело пропущено: {exc}")
        return None


def _auto_hydro_source(
    request: WorkflowRequest,
    message_type: str,
    connection,
) -> FallbackDataSource:
    sources: list[TextDataSource] = []
    if connection is not None:
        sources.append(
            OnlineDataSource(connection, request.bulletin_date, message_type)
        )
    if request.local_file is not None:
        sources.append(LocalFileSource(request.local_file))
    sources.append(
        ArchiveDataSource(
            request.raw_root,
            request.bulletin_date,
            message_type,
        )
    )
    return FallbackDataSource(tuple(sources))


def _hydro_sources(
    request: WorkflowRequest,
    connection,
) -> list[tuple[str, TextDataSource]]:
    message_types = tuple(dict.fromkeys(item.upper() for item in request.message_types))
    if request.source_mode == "database":
        return []

    if request.source_mode == "local":
        if request.local_file is None:
            raise ValueError("Для локального режиму потрібно вибрати TXT-файл.")
        return [(message_types[0], LocalFileSource(request.local_file))]

    if request.source_mode == "online":
        return [
            (
                message_type,
                OnlineDataSource(
                    connection,
                    request.bulletin_date,
                    message_type,
                ),
            )
            for message_type in message_types
        ]

    if request.source_mode == "archive":
        return [
            (
                message_type,
                ArchiveDataSource(
                    request.raw_root,
                    request.bulletin_date,
                    message_type,
                ),
            )
            for message_type in message_types
        ]

    return [
        (
            message_type,
            _auto_hydro_source(request, message_type, connection),
        )
        for message_type in message_types
    ]


def _meteo_source(
    request: WorkflowRequest,
    connection,
) -> TextDataSource | None:
    if request.source_mode == "database" or not request.include_meteo:
        return None
    if request.source_mode == "local":
        return (
            LocalFileSource(request.meteo_file)
            if request.meteo_file is not None
            else None
        )
    if request.source_mode == "online":
        return OnlineMeteoDataSource(
            connection,
            request.bulletin_date,
            tuple(station.index for station in METEO_STATIONS),
        )
    if request.source_mode == "archive":
        return ArchiveDataSource(
            request.raw_root,
            request.bulletin_date,
            "SYNOP",
        )

    sources: list[TextDataSource] = []
    if connection is not None:
        sources.append(
            OnlineMeteoDataSource(
                connection,
                request.bulletin_date,
                tuple(station.index for station in METEO_STATIONS),
            )
        )
    if request.meteo_file is not None:
        sources.append(LocalFileSource(request.meteo_file))
    sources.append(
        ArchiveDataSource(
            request.raw_root,
            request.bulletin_date,
            "SYNOP",
        )
    )
    return FallbackDataSource(tuple(sources))


def execute_workflow(request: WorkflowRequest) -> WorkflowResult:
    """Виконує імпорт, QC, читання SQLite та генерацію вибраних DOCX."""

    _validate_request(request)
    warnings: list[str] = []
    selected_regions = resolve_regions(request.region_keys)
    initialize_archive(request.db_path, ALL_STATIONS)

    connection = None
    if request.source_mode in {"auto", "online"}:
        connection = _load_online_connection(request, warnings)

    hydro_results: list[PipelineResult] = []
    for message_type, source in _hydro_sources(request, connection):
        hydro_results.append(
            run_import_pipeline(
                source,
                bulletin_date=request.bulletin_date,
                message_type=message_type,
                raw_root=request.raw_root,
                db_path=request.db_path,
                stations=ALL_STATIONS,
                stations_by_index=STATIONS_BY_INDEX,
                source_type=request.source_mode,
                source_name=getattr(source, "source_name", request.source_mode),
            )
        )

    meteo_result: MeteoPipelineResult | None = None
    meteo_source = _meteo_source(request, connection)
    if meteo_source is not None:
        try:
            meteo_result = run_meteo_import_pipeline(
                meteo_source,
                bulletin_date=request.bulletin_date,
                raw_root=request.raw_root,
                db_path=request.db_path,
                all_stations=ALL_STATIONS,
                meteo_stations_by_index={
                    station.index: station for station in METEO_STATIONS
                },
                source_type=request.source_mode,
                source_name=getattr(
                    meteo_source,
                    "source_name",
                    request.source_mode,
                ),
            )
        except (DataSourceError, FileNotFoundError, OSError, ValueError) as exc:
            warnings.append(f"SYNOP-опади не імпортовано: {exc}")
    elif request.include_meteo and request.source_mode == "local":
        warnings.append(
            "SYNOP-файл не вибрано: у бюлетені буде використано "
            "гідрологічні опади або позначку про відсутність даних."
        )

    quality_summary = run_initial_quality_control(
        request.db_path,
        request.bulletin_date,
    )
    bulletin_results: tuple[BulletinResult, ...] = ()
    if request.create_bulletins:
        mapping = load_precipitation_mapping(request.mapping_path)
        bulletin_results = generate_bulletins(
            request.db_path,
            selected_regions,
            bulletin_date=request.bulletin_date,
            hydrologist=request.hydrologist.strip() or DEFAULT_HYDROLOGIST,
            templates_dir=request.templates_dir,
            output_dir=request.output_dir,
            precipitation_mapping=mapping,
        )

    return WorkflowResult(
        tuple(hydro_results),
        meteo_result,
        quality_summary,
        bulletin_results,
        tuple(warnings),
        archive_summary(request.db_path),
    )
