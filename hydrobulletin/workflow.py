"""Єдиний прикладний сценарій HydroBulletin."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from .archive import archive_summary, initialize_archive
from .batch import run_batch_import
from .bulletins import BulletinResult, generate_bulletins
from .charts import ChartResult, create_charts
from .extremes import seed_extremes_from_templates
from .maps import MapResult, create_lviv_map, map_output_name
from .meteorology import (
    MeteoPipelineResult,
    load_precipitation_mapping,
    run_meteo_import_pipeline,
)
from .output_paths import dated_output_dir
from .pipeline import PipelineResult, run_import_pipeline
from .quality import QualitySummary, run_initial_quality_control
from .regions import REGIONS, resolve_regions
from .sources import (
    ArchiveDataSource,
    DataSourceError,
    FallbackDataSource,
    GCST_SOURCE_AUTO,
    GCST_SOURCE_MODES,
    LocalFileSource,
    OnlineConnection,
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


SOURCE_MODES = ("auto", "local", "online", "archive", "batch", "database")
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
    batch_folder: Path | None = None
    batch_all_dates: bool = False
    env_path: Path | None = None
    gcst_source_mode: str = GCST_SOURCE_AUTO
    include_meteo: bool = True
    create_bulletins: bool = True
    create_map: bool = False
    map_template_path: Path | None = None
    font_path: Path | None = None
    chart_station_index: str | None = None
    chart_start_date: str | None = None
    chart_end_date: str | None = None
    create_level_chart: bool = False
    create_discharge_chart: bool = False


@dataclass(frozen=True)
class WorkflowResult:
    """Результат, однаково придатний для консолі та GUI."""

    hydro_imports: tuple[PipelineResult, ...]
    meteo_import: MeteoPipelineResult | None
    quality_summary: QualitySummary
    bulletins: tuple[BulletinResult, ...]
    warnings: tuple[str, ...]
    archive_counts: dict[str, int] = field(default_factory=dict)
    map_result: MapResult | None = None
    charts: tuple[ChartResult, ...] = ()


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
    if request.gcst_source_mode not in GCST_SOURCE_MODES:
        raise ValueError(
            f"Невідомий серверний режим {request.gcst_source_mode}. "
            f"Доступні: {', '.join(GCST_SOURCE_MODES)}."
        )
    if request.source_mode != "database" and not request.message_types:
        raise ValueError("Потрібно вибрати хоча б один тип гідроповідомлення.")
    if request.source_mode == "batch" and request.batch_folder is None:
        raise ValueError("Для пакетного режиму потрібно вибрати папку TXT-файлів.")
    if request.create_bulletins and not request.region_keys:
        raise ValueError("Потрібно вибрати хоча б один регіон бюлетеня.")

    if request.create_map:
        if request.map_template_path is None:
            raise ValueError("Не вказано шаблон карти Львівської області.")
        if request.font_path is None:
            raise ValueError("Не вказано шрифт для карти Львівської області.")

    if request.create_level_chart or request.create_discharge_chart:
        if not request.chart_station_index:
            raise ValueError("Потрібно вибрати гідрологічний пост для графіка.")
        if not request.chart_start_date or not request.chart_end_date:
            raise ValueError("Потрібно вказати початок і кінець періоду графіка.")
        try:
            start = datetime.strptime(request.chart_start_date, "%d.%m.%Y")
            end = datetime.strptime(request.chart_end_date, "%d.%m.%Y")
        except ValueError as exc:
            raise ValueError(
                "Дати періоду мають бути у форматі ДД.ММ.РРРР."
            ) from exc
        if start > end:
            raise ValueError("Початкова дата не може бути пізнішою за кінцеву.")


def _load_online_connections(
    request: WorkflowRequest,
    warnings: list[str],
) -> tuple[OnlineConnection, ...]:
    try:
        return OnlineSourceSettings().load_gcst_connections(
            request.gcst_source_mode,
            request.env_path,
        )
    except DataSourceError as exc:
        if request.source_mode == "online":
            raise
        warnings.append(f"Онлайн-джерело пропущено: {exc}")
        return ()


def _online_hydro_source(
    request: WorkflowRequest,
    message_type: str,
    connections: Sequence[OnlineConnection],
) -> TextDataSource:
    sources = tuple(
        OnlineDataSource(connection, request.bulletin_date, message_type)
        for connection in connections
    )
    if not sources:
        raise DataSourceError(
            "Немає налаштованого онлайн-підключення до ГЦСТ."
        )
    if len(sources) == 1:
        return sources[0]
    return FallbackDataSource(sources)


def _auto_hydro_source(
    request: WorkflowRequest,
    message_type: str,
    connections: Sequence[OnlineConnection],
) -> FallbackDataSource:
    sources: list[TextDataSource] = [
        OnlineDataSource(connection, request.bulletin_date, message_type)
        for connection in connections
    ]
    sources.append(
        ArchiveDataSource(
            request.raw_root,
            request.bulletin_date,
            message_type,
        )
    )
    return FallbackDataSource(tuple(sources))


def _hydro_source(
    request: WorkflowRequest,
    message_type: str,
    connections: Sequence[OnlineConnection],
) -> TextDataSource:
    if request.source_mode == "local":
        if request.local_file is None:
            raise ValueError(
                "Для локального режиму потрібно вибрати TXT-файл."
            )
        return LocalFileSource(request.local_file)

    if request.source_mode == "online":
        return _online_hydro_source(request, message_type, connections)

    if request.source_mode == "archive":
        return ArchiveDataSource(
            request.raw_root,
            request.bulletin_date,
            message_type,
        )

    return _auto_hydro_source(request, message_type, connections)


def _meteo_source(
    request: WorkflowRequest,
    connections: Sequence[OnlineConnection],
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
        online_sources = tuple(
            OnlineMeteoDataSource(
                connection,
                request.bulletin_date,
                tuple(station.index for station in METEO_STATIONS),
            )
            for connection in connections
        )
        if not online_sources:
            raise DataSourceError(
                "Немає налаштованого онлайн-підключення до ГЦСТ."
            )
        if len(online_sources) == 1:
            return online_sources[0]
        return FallbackDataSource(online_sources)
    if request.source_mode == "archive":
        return ArchiveDataSource(
            request.raw_root,
            request.bulletin_date,
            "SYNOP",
        )

    sources: list[TextDataSource] = []
    sources.extend(
        (
            OnlineMeteoDataSource(
                connection,
                request.bulletin_date,
                tuple(station.index for station in METEO_STATIONS),
            )
            for connection in connections
        )
    )
    sources.append(
        ArchiveDataSource(
            request.raw_root,
            request.bulletin_date,
            "SYNOP",
        )
    )
    return FallbackDataSource(tuple(sources))


def _import_hydro_messages(
    request: WorkflowRequest,
    connections: tuple[OnlineConnection, ...],
    report: Callable[[str], None],
) -> tuple[list[PipelineResult], tuple[OnlineConnection, ...]]:
    """Імпортує вибрані ZRUR і повертає фактичний порядок підключень."""

    message_types = tuple(
        dict.fromkeys(item.upper() for item in request.message_types)
    )
    if request.source_mode == "local":
        message_types = message_types[:1]
    if request.source_mode == "database":
        message_types = ()

    results: list[PipelineResult] = []
    active_connections = connections
    for message_type in message_types:
        report(f"Отримую та імпортую {message_type}.")
        source = _hydro_source(request, message_type, active_connections)
        result = run_import_pipeline(
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
        results.append(result)

        if (
            request.gcst_source_mode == GCST_SOURCE_AUTO
            and len(active_connections) > 1
            and active_connections[-1].base_url in result.source_name
        ):
            active_connections = (active_connections[-1],)

    return results, active_connections


def _import_meteo(
    request: WorkflowRequest,
    connections: tuple[OnlineConnection, ...],
    warnings: list[str],
    report: Callable[[str], None],
) -> MeteoPipelineResult | None:
    """Імпортує SYNOP, залишаючи його відсутність явним зауваженням."""

    source = _meteo_source(request, connections)
    if source is None:
        if request.include_meteo and request.source_mode == "local":
            warnings.append(
                "SYNOP-файл не вибрано: у бюлетені буде використано "
                "гідрологічні опади або позначку про відсутність даних."
            )
        return None

    report("Отримую та імпортую SYNOP-опади.")
    try:
        return run_meteo_import_pipeline(
            source,
            bulletin_date=request.bulletin_date,
            raw_root=request.raw_root,
            db_path=request.db_path,
            all_stations=ALL_STATIONS,
            meteo_stations_by_index={
                station.index: station for station in METEO_STATIONS
            },
            source_type=request.source_mode,
            source_name=getattr(source, "source_name", request.source_mode),
        )
    except (DataSourceError, FileNotFoundError, OSError, ValueError) as exc:
        warnings.append(f"SYNOP-опади не імпортовано: {exc}")
        return None


def _import_batch_folder(
    request: WorkflowRequest,
    warnings: list[str],
    report: Callable[[str], None],
) -> tuple[list[PipelineResult], MeteoPipelineResult | None]:
    """Імпортує вибрану дату або весь датований пакет і перевіряє ZRUR."""

    if request.batch_folder is None:
        raise ValueError("Для пакетного режиму потрібно вибрати папку TXT-файлів.")

    if request.batch_all_dates:
        report("Імпортую всі датовані TXT-файли з вибраної папки.")
    else:
        report(f"Імпортую TXT-файли за {request.bulletin_date} з вибраної папки.")
    batch = run_batch_import(
        request.batch_folder,
        raw_root=request.raw_root,
        db_path=request.db_path,
        all_stations=ALL_STATIONS,
        hydro_stations_by_index=STATIONS_BY_INDEX,
        meteo_stations_by_index={
            station.index: station for station in METEO_STATIONS
        },
        bulletin_date=None if request.batch_all_dates else request.bulletin_date,
        include_meteo=request.include_meteo,
    )
    warnings.extend(f"{item.path}: {item.message}" for item in batch.errors)

    imported_types = {item.message_type for item in batch.hydrological}
    required_types = (
        set(request.message_types)
        if request.create_bulletins or request.create_map
        else set()
    )
    missing_types = sorted(required_types - imported_types)
    if required_types and not imported_types:
        raise FileNotFoundError(
            "У вибраній папці за вказану дату не знайдено жодного "
            "гідрологічного TXT-файлу."
        )
    if missing_types:
        warnings.append(
            "За вибрану дату не знайдено "
            + ", ".join(missing_types)
            + ": відповідні рядки матеріалів можуть не мати даних."
        )

    meteo_result = batch.meteorological[-1] if batch.meteorological else None
    if request.include_meteo and meteo_result is None:
        warnings.append(
            "SYNOP-файл за вибрану дату не знайдено: у бюлетені буде "
            "використано гідрологічні опади або позначку про відсутність даних."
        )
    return list(batch.hydrological), meteo_result


def execute_workflow(
    request: WorkflowRequest,
    progress: Callable[[str], None] | None = None,
) -> WorkflowResult:
    """Виконує імпорт, QC і формування вибраних службових матеріалів."""

    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    report("Перевіряю параметри запуску.")
    _validate_request(request)
    warnings: list[str] = []
    selected_regions = resolve_regions(request.region_keys)
    report("Готую SQLite-архів і довідники.")
    initialize_archive(request.db_path, ALL_STATIONS)
    seeded_extremes = seed_extremes_from_templates(
        request.db_path,
        REGIONS,
        request.templates_dir,
    )
    if seeded_extremes:
        report(f"Перенесено довідникових записів екстремумів: {seeded_extremes}.")

    connections: tuple[OnlineConnection, ...] = ()
    if request.source_mode in {"auto", "online"}:
        report("Перевіряю налаштування основного сервера та дзеркала ГЦСТ.")
        connections = _load_online_connections(request, warnings)

    if request.source_mode == "batch":
        hydro_results, meteo_result = _import_batch_folder(
            request,
            warnings,
            report,
        )
    else:
        hydro_results, connections = _import_hydro_messages(
            request,
            connections,
            report,
        )
        meteo_result = _import_meteo(
            request,
            connections,
            warnings,
            report,
        )

    report("Виконую первинний контроль якості.")
    quality_summary = run_initial_quality_control(
        request.db_path,
        request.bulletin_date,
    )
    bulletin_results: tuple[BulletinResult, ...] = ()
    if request.create_bulletins:
        report(f"Створюю Word-бюлетені: {len(selected_regions)}.")
        mapping = load_precipitation_mapping(request.mapping_path)
        bulletin_output_dir = dated_output_dir(
            request.output_dir,
            request.bulletin_date,
        )
        bulletin_results = generate_bulletins(
            request.db_path,
            selected_regions,
            bulletin_date=request.bulletin_date,
            hydrologist=request.hydrologist.strip() or DEFAULT_HYDROLOGIST,
            templates_dir=request.templates_dir,
            output_dir=bulletin_output_dir,
            precipitation_mapping=mapping,
        )

    map_result: MapResult | None = None
    if request.create_map:
        report("Створюю гідрологічну карту Львівської області.")
        map_template_path = request.map_template_path
        font_path = request.font_path
        if map_template_path is None or font_path is None:
            raise RuntimeError(
                "Для карти мають бути задані шаблон і файл шрифту."
            )
        map_output_dir = dated_output_dir(
            request.output_dir,
            request.bulletin_date,
        )
        map_result = create_lviv_map(
            request.db_path,
            bulletin_date=request.bulletin_date,
            template_path=map_template_path,
            font_path=font_path,
            output_path=map_output_dir
            / map_output_name(request.bulletin_date),
        )

    chart_results: tuple[ChartResult, ...] = ()
    if request.create_level_chart or request.create_discharge_chart:
        selected_chart_count = int(request.create_level_chart) + int(
            request.create_discharge_chart
        )
        report(f"Створюю архівні графіки: {selected_chart_count}.")
        chart_output_dir = dated_output_dir(
            request.output_dir,
            request.chart_end_date or request.bulletin_date,
        )
        chart_results = create_charts(
            request.db_path,
            station_index=request.chart_station_index or "",
            start_date=request.chart_start_date or "",
            end_date=request.chart_end_date or "",
            output_dir=chart_output_dir,
            font_path=request.font_path,
            include_levels=request.create_level_chart,
            include_discharge=request.create_discharge_chart,
        )

    report("Створення матеріалів завершено.")
    return WorkflowResult(
        tuple(hydro_results),
        meteo_result,
        quality_summary,
        bulletin_results,
        tuple(warnings),
        archive_summary(request.db_path),
        map_result,
        chart_results,
    )
