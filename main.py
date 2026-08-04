"""Консольний та графічний запуск HydroBulletin."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

from hydrobulletin.archive import archive_summary, initialize_archive
from hydrobulletin.batch import run_batch_import
from hydrobulletin.models import HydroObservation
from hydrobulletin.pipeline import PipelineResult
from hydrobulletin.regions import REGIONS
from hydrobulletin.sources import SUPPORTED_MESSAGE_TYPES, DataSourceError
from hydrobulletin.stations import ALL_STATIONS, METEO_STATIONS, STATIONS_BY_INDEX
from hydrobulletin.workflow import (
    DEFAULT_HYDROLOGIST,
    WorkflowRequest,
    WorkflowResult,
    execute_workflow,
)


DEFAULT_DATE = "12.07.2026"
PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_FILE = PROJECT_DIR / "demo_data" / "week3" / "12.07.2026_ZRUR52.txt"
DEFAULT_ARCHIVE_DB = PROJECT_DIR / "archive" / "database" / "hydro_archive.sqlite"
DEFAULT_RAW_ROOT = PROJECT_DIR / "archive" / "raw"
DEFAULT_TEMPLATES = PROJECT_DIR / "templates" / "bulletins"
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "week3"
DEFAULT_MAPPING = PROJECT_DIR / "config" / "precipitation_mapping.json"


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
            "HydroBulletin: джерело → raw → декодування → QC → SQLite → DOCX."
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
        "--env-file",
        type=Path,
        default=PROJECT_DIR / ".env",
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
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Папка сформованих DOCX.",
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
    print(f"{result.message_type}: {state}; джерело: {result.source_type}")
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
            f"{meteo.import_result.inserted_observations} нових значень."
        )

    print()
    print("КОНТРОЛЬ ЯКОСТІ")
    print("-" * 36)
    print(f"Перевірено значень: {result.quality_summary.checked}")
    for status, count in sorted(result.quality_summary.counts.items()):
        print(f"{status}: {count}")

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

    for warning in result.warnings:
        print(f"Зауваження: {warning}", file=sys.stderr)

    print()
    print("ПІДСУМОК АРХІВУ")
    print("-" * 36)
    for key in (
        "stations",
        "imports",
        "observations",
        "products",
        "product_observations",
    ):
        print(f"{key}: {result.archive_counts.get(key, 0)}")


def _workflow_request(args: argparse.Namespace, *, source_mode: str | None = None):
    message_types = (
        tuple(args.online_types)
        if args.source == "online"
        else (args.message_type,)
    )
    return WorkflowRequest(
        bulletin_date=args.date,
        source_mode=source_mode or args.source,
        message_types=message_types,
        local_file=args.file,
        meteo_file=args.meteo_file,
        db_path=args.archive_db,
        raw_root=args.raw_root,
        templates_dir=args.templates_dir,
        output_dir=args.output_dir,
        mapping_path=args.mapping,
        env_path=args.env_file,
        region_keys=tuple(args.regions),
        hydrologist=args.hydrologist,
        include_meteo=not args.no_meteo,
        create_bulletins=not args.no_bulletins,
    )


def _run_batch(args: argparse.Namespace) -> WorkflowResult:
    batch = run_batch_import(
        args.batch_folder,
        raw_root=args.raw_root,
        db_path=args.archive_db,
        all_stations=ALL_STATIONS,
        hydro_stations_by_index=STATIONS_BY_INDEX,
        meteo_stations_by_index={
            station.index: station for station in METEO_STATIONS
        },
    )
    print(
        f"Пакетний імпорт: {batch.processed_files} файлів, "
        f"помилок: {len(batch.errors)}."
    )
    for error in batch.errors:
        print(f"Зауваження: {error.path}: {error.message}", file=sys.stderr)

    result = execute_workflow(
        _workflow_request(args, source_mode="database")
    )
    if not batch.errors:
        return result
    return WorkflowResult(
        result.hydro_imports,
        result.meteo_import,
        result.quality_summary,
        result.bulletins,
        result.warnings
        + tuple(f"{item.path}: {item.message}" for item in batch.errors),
        result.archive_counts,
    )


def main() -> int:
    configure_console()
    args = build_parser().parse_args()

    if args.gui:
        try:
            from hydrobulletin.gui import launch_gui

            launch_gui(PROJECT_DIR)
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
        print(f"Версія схеми: 3")
        return 0

    print("HYDROBULLETIN — РОБОЧИЙ СЦЕНАРІЙ ТИЖНЯ 3")
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
