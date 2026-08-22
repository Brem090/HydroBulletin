"""Консольний та графічний запуск HydroBulletin."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

from hydrobulletin.archive import SCHEMA_VERSION, archive_summary, initialize_archive
from hydrobulletin.models import HydroObservation
from hydrobulletin.output_paths import MATERIALS_DIR_NAME
from hydrobulletin.pipeline import PipelineResult
from hydrobulletin.quality import quality_status_label
from hydrobulletin.regions import REGIONS, message_types_for_regions
from hydrobulletin.runtime import resolve_runtime_paths
from hydrobulletin.sources import (
    GCST_SOURCE_AUTO,
    GCST_SOURCE_MODES,
    SUPPORTED_MESSAGE_TYPES,
    DataSourceError,
)
from hydrobulletin.stations import ALL_STATIONS
from hydrobulletin.workflow import (
    DEFAULT_HYDROLOGIST,
    WorkflowRequest,
    WorkflowResult,
    execute_workflow,
)


DEFAULT_DATE = "12.07.2026"
RUNTIME_PATHS = resolve_runtime_paths(Path(__file__))
RESOURCE_DIR = RUNTIME_PATHS.resource_root
APPLICATION_DIR = RUNTIME_PATHS.data_root
# Спільний корінь ресурсів для CLI, GUI та регресійних сценаріїв.
PROJECT_DIR = RESOURCE_DIR
DEFAULT_FILE = (
    RESOURCE_DIR / "demo_data" / "regression" / "12.07.2026_ZRUR52.txt"
)
DEFAULT_ARCHIVE_DB = (
    APPLICATION_DIR / "archive" / "database" / "hydro_archive.sqlite"
)
DEFAULT_RAW_ROOT = APPLICATION_DIR / "archive" / "raw"
DEFAULT_TEMPLATES = RESOURCE_DIR / "templates" / "bulletins"
DEFAULT_MAP_TEMPLATE = (
    RESOURCE_DIR / "templates" / "maps" / "HydroMap_UHMC_Lviv_template_clean.png"
)
DEFAULT_FONT = RESOURCE_DIR / "resources" / "fonts" / "e-Ukraine-Regular.otf"
DEFAULT_OUTPUT = APPLICATION_DIR / MATERIALS_DIR_NAME
DEFAULT_MAPPING = RESOURCE_DIR / "config" / "precipitation_mapping.json"


def configure_console() -> None:
    """Намагається ввімкнути UTF-8 для коректного українського тексту."""

    try:
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(encoding="utf-8")
    except OSError:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "HydroBulletin: джерело → raw → декодування → QC → SQLite → "
            "DOCX/PNG."
        )
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Відкрити графічний інтерфейс HydroBulletin.",
    )
    parser.add_argument(
        "--source",
        choices=("auto", "local", "online", "archive", "database"),
        default="local",
        help=(
            "Джерело: автоматичний режим, файл, онлайн, raw-архів "
            "або лише наявна SQLite (типово: local)."
        ),
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_FILE,
        help=f"Локальний гідрологічний TXT (типово: {DEFAULT_FILE}).",
    )
    parser.add_argument(
        "--gcst-source",
        choices=GCST_SOURCE_MODES,
        default=GCST_SOURCE_AUTO,
        help=(
            "Сервер ГЦСТ: auto (основний, потім дзеркало), primary "
            "або mirror (типово: auto)."
        ),
    )
    parser.add_argument(
        "--meteo-file",
        type=Path,
        help="Окремий локальний SYNOP TXT з опадами.",
    )
    parser.add_argument(
        "--date",
        default=DEFAULT_DATE,
        help=f"Дата бюлетеня ДД.ММ.РРРР (типово: {DEFAULT_DATE}).",
    )
    parser.add_argument(
        "--message-type",
        choices=SUPPORTED_MESSAGE_TYPES,
        default="ZRUR52",
        help="Тип локального або архівного гідроповідомлення.",
    )
    parser.add_argument(
        "--online-types",
        nargs="+",
        choices=SUPPORTED_MESSAGE_TYPES,
        default=list(SUPPORTED_MESSAGE_TYPES),
        help="Типи повідомлень для онлайн-режиму.",
    )
    parser.add_argument(
        "--batch-folder",
        type=Path,
        help=(
            "Пакетно імпортувати TXT з назвами "
            "ДД.ММ.РРРР_ТИП.txt або РРРР-ММ-ДД_ТИП.txt."
        ),
    )
    parser.add_argument(
        "--regions",
        nargs="+",
        choices=tuple(region.key for region in REGIONS),
        default=[region.key for region in REGIONS],
        help="Регіони для DOCX: lviv, if, left_dnister.",
    )
    parser.add_argument(
        "--hydrologist",
        default=DEFAULT_HYDROLOGIST,
        help=f"Підпис у Word-бюлетені (типово: {DEFAULT_HYDROLOGIST}).",
    )
    parser.add_argument(
        "--no-meteo",
        action="store_true",
        help="Не намагатися імпортувати SYNOP-опади.",
    )
    parser.add_argument(
        "--no-bulletins",
        action="store_true",
        help="Завершити після імпорту та QC без створення DOCX.",
    )
    parser.add_argument(
        "--map",
        dest="create_map",
        action="store_true",
        help="Створити гідрологічну карту Львівської області.",
    )
    parser.add_argument(
        "--level-chart",
        action="store_true",
        help="Створити єдиний графік рівнів води за 08:00 і 20:00.",
    )
    parser.add_argument(
        "--discharge-chart",
        action="store_true",
        help="Створити графік витрат води.",
    )
    parser.add_argument(
        "--chart-station",
        default="81015",
        help="Індекс гідропоста для архівних графіків (типово: 81015).",
    )
    parser.add_argument(
        "--start-date",
        help="Початок періоду графіка ДД.ММ.РРРР (типово: --date).",
    )
    parser.add_argument(
        "--end-date",
        help="Кінець періоду графіка ДД.ММ.РРРР (типово: --date).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=APPLICATION_DIR / ".env",
        help="Файл конфігурації онлайн-доступу.",
    )
    parser.add_argument(
        "--archive-db",
        type=Path,
        default=DEFAULT_ARCHIVE_DB,
        help="Шлях до SQLite-бази.",
    )
    parser.add_argument(
        "--raw-root",
        type=Path,
        default=DEFAULT_RAW_ROOT,
        help="Коренева папка raw-архіву.",
    )
    parser.add_argument(
        "--templates-dir",
        type=Path,
        default=DEFAULT_TEMPLATES,
        help="Папка трьох шаблонів DOCX.",
    )
    parser.add_argument(
        "--map-template",
        type=Path,
        default=DEFAULT_MAP_TEMPLATE,
        help="PNG-шаблон карти Львівської області.",
    )
    parser.add_argument(
        "--font",
        type=Path,
        default=DEFAULT_FONT,
        help="Шрифт e-Ukraine для карти й графіків.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=(
            "Коренева папка сформованих DOCX і PNG. Підпапки року й "
            "місяця створюються автоматично."
        ),
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=DEFAULT_MAPPING,
        help="JSON-мапінг гідропостів на метеостанції.",
    )
    parser.add_argument(
        "--init-archive",
        action="store_true",
        help="Лише створити/оновити схему SQLite та завершити роботу.",
    )
    return parser


def render_table(observations: Iterable[HydroObservation]) -> str:
    rows = list(observations)
    if not rows:
        return "За вибрану дату записи підтримуваних постів не знайдено."

    headers = (
        "Індекс",
        "Гідрологічний пост",
        "Рівень 08:00",
        "Зміна",
        "Рівень 20:00",
        "T води",
        "Опади",
        "Витрата",
    )
    prepared = [
        (
            item.index,
            item.station_name,
            item.level_text,
            item.change_text,
            item.evening_level_text,
            item.temperature_text,
            item.precipitation_text,
            item.discharge_text,
        )
        for item in rows
    ]

    widths = [len(header) for header in headers]
    for row in prepared:
        for column, value in enumerate(row):
            widths[column] = max(widths[column], len(value))

    def format_row(row: tuple[str, ...]) -> str:
        return " | ".join(value.ljust(widths[i]) for i, value in enumerate(row))

    separator = "-+-".join("-" * width for width in widths)
    lines = [format_row(headers), separator]
    lines.extend(format_row(row) for row in prepared)
    return "\n".join(lines)


def print_pipeline_result(result: PipelineResult) -> None:
    imported = result.import_result
    state = (
        "повторний файл"
        if imported.duplicate_file
        else "новий імпорт"
    )
    print()
    print(
        f"{result.message_type}: {state}; джерело: "
        f"{result.source_type} ({result.source_name})"
    )
    print(f"Raw-файл: {result.raw_path}")
    print(f"Розкодовано постів: {len(result.observations)}")
    print(f"Нових значень у SQLite: {imported.inserted_observations}")
    if imported.duplicate_observations:
        print(f"Повторних значень пропущено: {imported.duplicate_observations}")
    print()
    print(render_table(result.observations))


def print_workflow_result(result: WorkflowResult) -> None:
    for imported in result.hydro_imports:
        print_pipeline_result(imported)

    if result.meteo_import is not None:
        meteo = result.meteo_import
        print()
        print(
            "SYNOP: "
            f"{len(meteo.observations)} метеостанцій, "
            f"{meteo.import_result.inserted_observations} нових значень; "
            f"джерело: {meteo.source_name}."
        )

    print()
    print("КОНТРОЛЬ ЯКОСТІ")
    print("-" * 36)
    print(f"Перевірено значень: {result.quality_summary.checked}")
    for status, count in sorted(result.quality_summary.counts.items()):
        print(f"{quality_status_label(status)}: {count}")

    if result.bulletins:
        print()
        print("WORD-БЮЛЕТЕНІ")
        print("-" * 36)
        for bulletin in result.bulletins:
            print(
                f"{bulletin.region_key}: {bulletin.output_path} "
                f"(використано значень: "
                f"{bulletin.product.linked_observations})"
            )

    if result.map_result is not None:
        print()
        print("ГІДРОЛОГІЧНА КАРТА")
        print("-" * 36)
        print(
            f"{result.map_result.output_path} "
            f"(нанесено постів: {result.map_result.plotted_stations})"
        )

    if result.charts:
        print()
        print("АРХІВНІ ГРАФІКИ")
        print("-" * 36)
        for chart in result.charts:
            print(
                f"{chart.output_path} "
                f"(значень: {chart.available_points}, "
                f"пропусків: {chart.missing_points})"
            )

    for warning in result.warnings:
        print(f"Зауваження: {warning}", file=sys.stderr)

    print()
    print("ПІДСУМОК АРХІВУ")
    print("-" * 36)
    for key in (
        "stations",
        "imports",
        "observations",
        "corrections",
        "reference_extremes",
        "products",
        "product_observations",
    ):
        print(f"{key}: {result.archive_counts.get(key, 0)}")


def _workflow_request(
    args: argparse.Namespace,
    *,
    source_mode: str | None = None,
) -> WorkflowRequest:
    effective_source = source_mode or args.source
    message_regions = tuple(args.regions)
    if args.create_map and "lviv" not in message_regions:
        message_regions += ("lviv",)
    if effective_source == "batch":
        message_types = message_types_for_regions(message_regions)
    elif args.source == "online":
        message_types = tuple(args.online_types)
    else:
        message_types = (args.message_type,)

    return WorkflowRequest(
        bulletin_date=args.date,
        source_mode=effective_source,
        message_types=message_types,
        local_file=args.file,
        meteo_file=args.meteo_file,
        batch_folder=args.batch_folder if effective_source == "batch" else None,
        batch_all_dates=effective_source == "batch",
        db_path=args.archive_db,
        raw_root=args.raw_root,
        templates_dir=args.templates_dir,
        output_dir=args.output_dir,
        mapping_path=args.mapping,
        env_path=args.env_file,
        gcst_source_mode=args.gcst_source,
        region_keys=tuple(args.regions),
        hydrologist=args.hydrologist,
        include_meteo=not args.no_meteo,
        create_bulletins=not args.no_bulletins,
        create_map=args.create_map,
        map_template_path=args.map_template,
        font_path=args.font,
        chart_station_index=args.chart_station,
        chart_start_date=args.start_date or args.date,
        chart_end_date=args.end_date or args.date,
        create_level_chart=args.level_chart,
        create_discharge_chart=args.discharge_chart,
    )


def _run_batch(args: argparse.Namespace) -> WorkflowResult:
    result = execute_workflow(_workflow_request(args, source_mode="batch"))
    print(
        "Пакетний імпорт папки: "
        f"{len(result.hydro_imports)} ZRUR-файлів, "
        f"SYNOP: {'так' if result.meteo_import is not None else 'ні'}. "
        f"Матеріали формуються за {args.date}."
    )
    return result


def main() -> int:
    configure_console()
    args = build_parser().parse_args()

    if args.gui:
        try:
            from hydrobulletin.gui import launch_gui

            launch_gui(RESOURCE_DIR, APPLICATION_DIR)
        except KeyboardInterrupt:
            print("Роботу GUI зупинено користувачем.", file=sys.stderr)
            return 130
        except Exception as exc:
            print(f"Не вдалося відкрити GUI: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.init_archive:
        try:
            initialize_archive(args.archive_db, ALL_STATIONS)
            summary = archive_summary(args.archive_db)
        except (OSError, sqlite3.Error) as exc:
            print(f"Помилка створення архіву: {exc}", file=sys.stderr)
            return 2
        print("HYDROBULLETIN — ІНІЦІАЛІЗАЦІЯ АРХІВУ")
        print(f"SQLite-база: {args.archive_db}")
        print(f"Постів і метеостанцій: {summary['stations']}")
        print(f"Версія схеми: {SCHEMA_VERSION}")
        return 0

    print("HYDROBULLETIN — ОПЕРАЦІЙНИЙ СЦЕНАРІЙ")
    print("=" * 58)
    print(f"Дата бюлетеня: {args.date}")
    print(f"Режим: {'batch' if args.batch_folder else args.source}")

    try:
        result = (
            _run_batch(args)
            if args.batch_folder is not None
            else execute_workflow(_workflow_request(args))
        )
    except (
        DataSourceError,
        FileNotFoundError,
        OSError,
        RuntimeError,
        ValueError,
        sqlite3.Error,
    ) as exc:
        print(f"Помилка: {exc}", file=sys.stderr)
        return 2

    print_workflow_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
