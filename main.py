"""Консольний запуск першого етапу HydroBulletin."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

from hydrobulletin.archive import archive_summary, initialize_archive
from hydrobulletin.decoder import decode_codes
from hydrobulletin.models import HydroObservation
from hydrobulletin.sources import LocalFileSource
from hydrobulletin.stations import LVIV_STATIONS, STATIONS_BY_INDEX

DEFAULT_DATE = "12.07.2026"
DEFAULT_FILE = Path(__file__).parent / "demo_data" / "sample_codes.txt"
DEFAULT_ARCHIVE_DB = Path(__file__).parent / "archive" / "database" / "hydro_archive.sqlite"


def configure_console() -> None:
    """Намагається ввімкнути UTF-8 для коректного українського тексту."""

    try:
        stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
        if callable(stdout_reconfigure):
            stdout_reconfigure(encoding="utf-8")

        stderr_reconfigure = getattr(sys.stderr, "reconfigure", None)
        if callable(stderr_reconfigure):
            stderr_reconfigure(encoding="utf-8")
    except OSError:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Демонстрація першого етапу HydroBulletin: локальний файл, "
            "декодування рівнів і підготовка SQLite-архіву."
        )
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_FILE,
        help=f"Шлях до тестового файла (типово: {DEFAULT_FILE})",
    )
    parser.add_argument(
        "--date",
        default=DEFAULT_DATE,
        help=f"Дата бюлетеня у форматі ДД.ММ.РРРР (типово: {DEFAULT_DATE})",
    )
    parser.add_argument(
        "--init-archive",
        action="store_true",
        help="Створити порожню SQLite-базу архіву та записати довідник постів.",
    )
    parser.add_argument(
        "--archive-db",
        type=Path,
        default=DEFAULT_ARCHIVE_DB,
        help=f"Шлях до SQLite-бази (типово: {DEFAULT_ARCHIVE_DB})",
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
        "Зміна за добу",
        "Рівень 20:00 попер. доби",
        "Статус",
    )
    prepared = [
        (
            item.index,
            item.station_name,
            item.level_text,
            item.change_text,
            item.evening_level_text,
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


def main() -> int:
    configure_console()
    args = build_parser().parse_args()

    try:
        source = LocalFileSource(args.file)
        raw_text = source.load_text()
        observations = decode_codes(raw_text, args.date, STATIONS_BY_INDEX)
    except (OSError, ValueError) as exc:
        print(f"Помилка: {exc}", file=sys.stderr)
        return 2

    print("HYDROBULLETIN — ДЕМОНСТРАЦІЯ ТИЖНЯ 1")
    print("=" * 52)
    print(f"Дата бюлетеня: {args.date}")
    print(f"Джерело: локальний файл {args.file}")
    print(f"Знайдено записів: {len(observations)}")
    print()
    print(render_table(observations))

    if args.init_archive:
        try:
            db_path = initialize_archive(args.archive_db, LVIV_STATIONS)
            summary = archive_summary(db_path)
        except OSError as exc:
            print(f"Помилка створення архіву: {exc}", file=sys.stderr)
            return 2

        print()
        print("ПІДГОТОВКА ЛОКАЛЬНОГО АРХІВУ")
        print("-" * 36)
        print(f"SQLite-база: {db_path}")
        print(f"Постів у довіднику: {summary['stations']}")
        print(f"Імпортів: {summary['imports']}")
        print(f"Спостережень: {summary['observations']}")
        print("Примітка: автоматичне наповнення архіву буде реалізовано на тижні 2.")

    print()
    print("Використано синтетичні демонстраційні дані без службових доступів.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
