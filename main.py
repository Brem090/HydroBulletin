"""Консольний запуск HydroBulletin."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

from hydrobulletin.archive import archive_summary, initialize_archive
from hydrobulletin.models import HydroObservation
from hydrobulletin.pipeline import PipelineResult, run_import_pipeline
from hydrobulletin.sources import (
    SUPPORTED_MESSAGE_TYPES,
    DataSourceError,
    LocalFileSource,
    OnlineDataSource,
    OnlineSourceSettings,
)
from hydrobulletin.stations import LVIV_STATIONS, STATIONS_BY_INDEX

DEFAULT_DATE = "12.07.2026"
PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_FILE = PROJECT_DIR / "demo_data" / "sample_codes.txt"
DEFAULT_ARCHIVE_DB = PROJECT_DIR / "archive" / "database" / "hydro_archive.sqlite"
DEFAULT_RAW_ROOT = PROJECT_DIR / "archive" / "raw"


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
        description="HydroBulletin: джерело → raw-архів → декодування → SQLite."
    )
    parser.add_argument(
        "--source",
        choices=("local", "online"),
        default="local",
        help="Джерело даних: локальний файл або робочий сайт (типово: local).",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_FILE,
        help=f"Локальний файл для імпорту (типово: {DEFAULT_FILE}).",
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
        help="Тип локального повідомлення.",
    )
    parser.add_argument(
        "--online-types",
        nargs="+",
        choices=SUPPORTED_MESSAGE_TYPES,
        default=list(SUPPORTED_MESSAGE_TYPES),
        help="Типи повідомлень для онлайн-режиму.",
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
        "Статус",
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
            item.quality_status,
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
    import_result = result.import_result
    if import_result.duplicate_file and import_result.inserted_observations:
        state = "повторно оброблено — архів доповнено"
    elif import_result.duplicate_file:
        state = "повторний файл — нових даних немає"
    else:
        state = "новий імпорт"
    print()
    print(f"{result.message_type}: {state}")
    print(f"Raw-файл: {result.raw_path}")
    print(f"Розкодовано постів із довідника Львівщини: {len(result.observations)}")
    print(f"Нових вимірювань у SQLite: {import_result.inserted_observations}")
    if import_result.duplicate_observations:
        print(f"Повторних вимірювань пропущено: {import_result.duplicate_observations}")
    print()
    print(render_table(result.observations))


def main() -> int:
    configure_console()
    args = build_parser().parse_args()

    if args.init_archive:
        try:
            initialize_archive(args.archive_db, LVIV_STATIONS)
            summary = archive_summary(args.archive_db)
        except (OSError, sqlite3.Error) as exc:
            print(f"Помилка створення архіву: {exc}", file=sys.stderr)
            return 2
        print("HYDROBULLETIN — ІНІЦІАЛІЗАЦІЯ АРХІВУ")
        print(f"SQLite-база: {args.archive_db}")
        print(f"Постів у довіднику: {summary['stations']}")
        print(f"Імпортів: {summary['imports']}")
        print(f"Вимірювань: {summary['observations']}")
        return 0

    print("HYDROBULLETIN — ІМПОРТ ГІДРОЛОГІЧНИХ ДАНИХ")
    print("=" * 52)
    print(f"Дата бюлетеня: {args.date}")
    print(f"Режим: {args.source}")

    try:
        if args.source == "local":
            source = LocalFileSource(args.file)
            results = [
                run_import_pipeline(
                    source,
                    bulletin_date=args.date,
                    message_type=args.message_type,
                    raw_root=args.raw_root,
                    db_path=args.archive_db,
                    stations=LVIV_STATIONS,
                    stations_by_index=STATIONS_BY_INDEX,
                    source_type="local",
                    source_name=source.source_name,
                )
            ]
        else:
            connection = OnlineSourceSettings().load_connection(args.env_file)
            results = []
            for message_type in args.online_types:
                source = OnlineDataSource(connection, args.date, message_type)
                results.append(
                    run_import_pipeline(
                        source,
                        bulletin_date=args.date,
                        message_type=message_type,
                        raw_root=args.raw_root,
                        db_path=args.archive_db,
                        stations=LVIV_STATIONS,
                        stations_by_index=STATIONS_BY_INDEX,
                        source_type="online",
                        source_name=source.source_name,
                    )
                )
    except (DataSourceError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"Помилка: {exc}", file=sys.stderr)
        return 2

    for result in results:
        print_pipeline_result(result)

    summary = archive_summary(args.archive_db)
    print()
    print("ПІДСУМОК АРХІВУ")
    print("-" * 36)
    print(f"SQLite-база: {args.archive_db}")
    print(f"Імпортів: {summary['imports']}")
    print(f"Нормалізованих вимірювань: {summary['observations']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
