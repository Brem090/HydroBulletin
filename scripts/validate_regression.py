"""Перевірка імпорту та матеріалів HydroBulletin на синтетичних даних."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import subprocess
import sys
import time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from docx import Document


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from hydrobulletin.bulletins import build_bulletin_rows
from hydrobulletin.regions import REGIONS

DEMO_DIR = PROJECT_DIR / "demo_data" / "regression"
PRECIPITATION_MAPPING_PATH = PROJECT_DIR / "config" / "precipitation_mapping.json"
DATE_TEXT = "12.07.2026"
EXPECTED_WORD_FIELDS = 501
EXPECTED_COUNTS = {
    "stations": 81,
    "imports": 3,
    "observations": 150,
    "corrections": 0,
    "reference_extremes": 70,
    "products": 6,
    "product_observations": 93,
}


def _scalar(connection: sqlite3.Connection, query: str) -> int:
    row = connection.execute(query).fetchone()
    if row is None:
        raise RuntimeError(f"Запит не повернув результат: {query}")
    return int(row[0])


def _full_command(work_dir: Path, output_name: str = "results") -> list[str]:
    return [
        sys.executable,
        str(PROJECT_DIR / "main.py"),
        "--batch-folder",
        str(DEMO_DIR),
        "--date",
        DATE_TEXT,
        "--archive-db",
        str(work_dir / "hydro_archive.sqlite"),
        "--raw-root",
        str(work_dir / "raw"),
        "--output-dir",
        str(work_dir / output_name),
        "--map",
        "--level-chart",
        "--discharge-chart",
        "--chart-station",
        "79726",
        "--start-date",
        "11.07.2026",
        "--end-date",
        DATE_TEXT,
    ]


def _offline_command(work_dir: Path, mode: str) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_DIR / "main.py"),
        "--source",
        mode,
        "--date",
        DATE_TEXT,
        "--archive-db",
        str(work_dir / "hydro_archive.sqlite"),
        "--raw-root",
        str(work_dir / "raw"),
        "--output-dir",
        str(work_dir / f"offline_{mode}"),
        "--map",
        "--level-chart",
        "--discharge-chart",
        "--chart-station",
        "79726",
        "--start-date",
        "11.07.2026",
        "--end-date",
        DATE_TEXT,
    ]
    return command


def _run(command: list[str]) -> tuple[float, str]:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        details = "\n".join(
            part.strip()
            for part in (completed.stdout, completed.stderr)
            if part.strip()
        )
        raise RuntimeError(
            f"HydroBulletin завершився з кодом {completed.returncode}.\n{details}"
        )
    return elapsed, completed.stdout


def _product_files(output_root: Path) -> list[Path]:
    return sorted(
        path
        for path in output_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".docx", ".png"}
    )



def _format_number(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return ""
    numeric = float(value)
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.{digits}f}".rstrip("0").rstrip(".").replace(".", ",")


def _format_change(value: float | None) -> str:
    if value is None:
        return ""
    text = _format_number(value, 1)
    return f"+{text}" if value > 0 else text


def _format_precipitation(value: float | None, state: str) -> str:
    if value is None:
        return ""
    if state == "NO_RAIN":
        return ""
    if value == 0.0:
        return "0,0" if state == "TRACE" else ""
    if -1.0 < value < 1.0:
        return f"{value:.1f}".replace(".", ",")
    rounded = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return str(int(rounded))


def _format_ice_and_temperature(item: Any) -> str:
    parts: list[str] = []
    if item.ice_phenomena:
        parts.append(str(item.ice_phenomena))
    if item.ice_thickness is not None:
        parts.append(f"{_format_number(item.ice_thickness, 1)} см")
    if parts:
        return ", ".join(parts)
    return _format_number(item.water_temperature, 1)


def _normalize_cell_text(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def _load_precipitation_mapping() -> dict[str, str]:
    try:
        payload = json.loads(PRECIPITATION_MAPPING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Не вдалося прочитати карту SYNOP-опадів: {PRECIPITATION_MAPPING_PATH}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Карта SYNOP-опадів має містити JSON-об'єкт.")
    return {str(key): str(value) for key, value in payload.items()}


def _iter_tables(container: Any):
    seen_tables: set[object] = set()

    def walk(owner: Any):
        for table in owner.tables:
            table_key = table._tbl
            if table_key in seen_tables:
                continue
            seen_tables.add(table_key)
            yield table

            seen_cells: set[object] = set()
            for row in table.rows:
                for cell in row.cells:
                    cell_key = cell._tc
                    if cell_key in seen_cells:
                        continue
                    seen_cells.add(cell_key)
                    yield from walk(cell)

    yield from walk(container)


def _find_official_bulletin_table(document: Any) -> Any:
    for table in _iter_tables(document):
        if not table.rows or len(table.columns) < 10:
            continue
        header = _normalize_cell_text(table.rows[0].cells[0].text)
        if "Річка-пункт" in header:
            return table
    raise RuntimeError("У сформованому DOCX не знайдено офіційної таблиці бюлетеня.")


def _verify_word_fields(
    db_path: Path,
    product_files: list[Path],
) -> dict[str, Any]:
    """Звіряє 501 змінне поле трьох Word-бюлетенів із даними SQLite."""

    docx_files = {
        path.name: path
        for path in product_files
        if path.suffix.lower() == ".docx"
    }
    precipitation_mapping = _load_precipitation_mapping()

    checked = 0
    matched = 0
    mismatches: list[dict[str, Any]] = []
    by_region: dict[str, dict[str, int]] = {}

    for region in REGIONS:
        expected_name = region.output_name(DATE_TEXT)
        path = docx_files.get(expected_name)
        if path is None:
            raise RuntimeError(f"Не знайдено контрольний Word-бюлетень: {expected_name}")

        rows = build_bulletin_rows(
            db_path,
            region,
            DATE_TEXT,
            precipitation_mapping,
        )
        document = Document(str(path))
        table = _find_official_bulletin_table(document)
        expected_row_count = len(rows) + 2
        if len(table.rows) != expected_row_count:
            raise RuntimeError(
                f"{path.name}: очікується {expected_row_count} рядків таблиці, "
                f"отримано {len(table.rows)}."
            )

        region_checked = 0
        region_matched = 0

        for position, item in enumerate(rows, start=2):
            target = table.rows[position]
            expected_fields: list[tuple[int, str, str]] = [
                (1, "level", _format_number(item.level, 1)),
                (2, "change", _format_change(item.change)),
                (
                    3,
                    "precipitation",
                    _format_precipitation(
                        item.precipitation,
                        item.precipitation_state,
                    ),
                ),
                (9, "water_temperature_or_ice", _format_ice_and_temperature(item)),
            ]

            for column, field_name, value in (
                (6, "maximum_level", item.maximum_level),
                (7, "average_level", item.average_level),
                (8, "minimum_level", item.minimum_level),
            ):
                if value is not None:
                    expected_fields.append((column, field_name, _format_number(value, 0)))

            for column, field_name, expected in expected_fields:
                actual = _normalize_cell_text(target.cells[column].text)
                expected_normalized = _normalize_cell_text(expected)
                checked += 1
                region_checked += 1
                if actual == expected_normalized:
                    matched += 1
                    region_matched += 1
                    continue
                mismatches.append(
                    {
                        "region": region.key,
                        "station_index": item.station_index,
                        "station_name": item.station_name,
                        "field": field_name,
                        "expected": expected_normalized,
                        "actual": actual,
                    }
                )

        by_region[region.key] = {
            "checked": region_checked,
            "matched": region_matched,
        }

    if checked != EXPECTED_WORD_FIELDS:
        raise RuntimeError(
            "Кількість контрольованих Word-полів змінилася: "
            f"очікується {EXPECTED_WORD_FIELDS}, отримано {checked}."
        )
    if mismatches:
        first = mismatches[0]
        raise RuntimeError(
            "Word-звірка не пройдена: "
            f"{matched}/{checked}. Перша розбіжність: "
            f"{first['region']} / {first['station_index']} / {first['field']}: "
            f"очікується «{first['expected']}», отримано «{first['actual']}»."
        )

    return {
        "expected": EXPECTED_WORD_FIELDS,
        "checked": checked,
        "matched": matched,
        "mismatched": len(mismatches),
        "by_region": by_region,
    }

def _archive_report(db_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        counts = {
            table: _scalar(connection, f"SELECT COUNT(*) FROM {table}")
            for table in EXPECTED_COUNTS
        }
        integrity_row = connection.execute("PRAGMA integrity_check").fetchone()
        integrity = str(integrity_row[0]) if integrity_row is not None else ""
        foreign_key_violations = len(
            connection.execute("PRAGMA foreign_key_check").fetchall()
        )
        duplicate_import_keys = _scalar(
            connection,
            """
            SELECT COUNT(*) FROM (
                SELECT source_key FROM imports
                GROUP BY source_key HAVING COUNT(*) > 1
            )
            """,
        )
        duplicate_observation_keys = _scalar(
            connection,
            """
            SELECT COUNT(*) FROM (
                SELECT station_index, observed_at, parameter_code
                FROM observations
                GROUP BY station_index, observed_at, parameter_code
                HAVING COUNT(*) > 1
            )
            """,
        )
        duplicate_product_links = _scalar(
            connection,
            """
            SELECT COUNT(*) FROM (
                SELECT product_id, observation_id
                FROM product_observations
                GROUP BY product_id, observation_id HAVING COUNT(*) > 1
            )
            """,
        )
        linked_rows = _scalar(connection, "SELECT COUNT(*) FROM product_observations")
        provenance_rows = _scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM product_observations AS po
            JOIN products AS p ON p.product_id = po.product_id
            JOIN observations AS o ON o.observation_id = po.observation_id
            JOIN imports AS i ON i.import_id = o.import_id
            """,
        )
        quality_rows = connection.execute(
            """
            SELECT quality_status, COUNT(*) AS amount
            FROM observations
            GROUP BY quality_status
            ORDER BY quality_status
            """
        ).fetchall()
        quality_counts = {str(row[0]): int(row[1]) for row in quality_rows}
        selected_quality_rows = connection.execute(
            """
            SELECT quality_status, COUNT(*) AS amount
            FROM observations
            WHERE date(observed_at) = '2026-07-12'
            GROUP BY quality_status
            ORDER BY quality_status
            """
        ).fetchall()
        selected_quality = {
            str(row[0]): int(row[1]) for row in selected_quality_rows
        }

        raw_matches = 0
        raw_total = 0
        import_rows = connection.execute(
            "SELECT raw_path, source_hash FROM imports ORDER BY import_id"
        ).fetchall()
        for row in import_rows:
            raw_total += 1
            raw_path = Path(str(row[0]))
            if not raw_path.is_absolute():
                raw_path = db_path.parent / raw_path
            if raw_path.exists():
                digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
                if digest == str(row[1]):
                    raw_matches += 1
    finally:
        connection.close()

    return {
        "counts": counts,
        "integrity_check": integrity,
        "foreign_key_violations": foreign_key_violations,
        "duplicate_import_keys": duplicate_import_keys,
        "duplicate_observation_keys": duplicate_observation_keys,
        "duplicate_product_links": duplicate_product_links,
        "provenance_complete": f"{provenance_rows}/{linked_rows}",
        "raw_sha256_matches": f"{raw_matches}/{raw_total}",
        "quality_counts_all_dates": quality_counts,
        "quality_counts_2026_07_12": selected_quality,
    }


def _assert_control_result(
    archive: dict[str, Any],
    product_files: list[Path],
) -> None:
    if archive["counts"] != EXPECTED_COUNTS:
        raise RuntimeError(
            f"Неочікувані кількості SQLite: {archive['counts']}"
        )
    if archive["integrity_check"] != "ok":
        raise RuntimeError("PRAGMA integrity_check не повернув ok.")
    if archive["foreign_key_violations"] != 0:
        raise RuntimeError("Виявлено порушення зовнішніх ключів SQLite.")
    if any(
        archive[key] != 0
        for key in (
            "duplicate_import_keys",
            "duplicate_observation_keys",
            "duplicate_product_links",
        )
    ):
        raise RuntimeError("Виявлено дублікати у контрольній SQLite-базі.")
    if archive["provenance_complete"] != "93/93":
        raise RuntimeError("Ланцюг походження продуктів неповний.")
    if archive["raw_sha256_matches"] != "3/3":
        raise RuntimeError("Контрольна сума raw-файлу не збігається.")
    if len(product_files) != 6:
        raise RuntimeError(
            f"Очікувалося 6 DOCX/PNG, але знайдено {len(product_files)}."
        )
    if sum(path.suffix.lower() == ".docx" for path in product_files) != 3:
        raise RuntimeError("Очікувалося три Word-бюлетені.")
    if sum(path.suffix.lower() == ".png" for path in product_files) != 3:
        raise RuntimeError("Очікувалося три PNG-візуалізації.")


def validate(work_dir: Path, samples: int) -> dict[str, Any]:
    """Запускає наскрізну, повторну, офлайн- та часову перевірки."""

    work_dir.mkdir(parents=True, exist_ok=False)
    first_seconds, _ = _run(_full_command(work_dir))
    product_files = _product_files(work_dir / "results")
    archive_first = _archive_report(work_dir / "hydro_archive.sqlite")
    _assert_control_result(archive_first, product_files)
    word_fields = _verify_word_fields(
        work_dir / "hydro_archive.sqlite",
        product_files,
    )

    repeat_seconds, _ = _run(_full_command(work_dir))
    archive_repeat = _archive_report(work_dir / "hydro_archive.sqlite")
    product_files_repeat = _product_files(work_dir / "results")
    _assert_control_result(archive_repeat, product_files_repeat)
    _verify_word_fields(
        work_dir / "hydro_archive.sqlite",
        product_files_repeat,
    )
    idempotent = archive_first["counts"] == archive_repeat["counts"]
    if not idempotent:
        raise RuntimeError("Повторний запуск змінив кількість записів SQLite.")

    archive_seconds, _ = _run(_offline_command(work_dir, "archive"))
    database_seconds, _ = _run(_offline_command(work_dir, "database"))
    archive_offline_files = _product_files(work_dir / "offline_archive")
    database_offline_files = _product_files(work_dir / "offline_database")
    if len(archive_offline_files) != 6 or len(database_offline_files) != 6:
        raise RuntimeError("Офлайн-режим не створив усі шість продуктів.")

    benchmark_seconds: list[float] = []
    for sample_number in range(1, samples + 1):
        sample_dir = work_dir / f"benchmark_{sample_number}"
        sample_dir.mkdir()
        elapsed, _ = _run(_full_command(sample_dir))
        benchmark_seconds.append(elapsed)

    return {
        "status": "OK",
        "work_dir": str(work_dir),
        "first_run_seconds": round(first_seconds, 3),
        "repeat_run_seconds": round(repeat_seconds, 3),
        "idempotent": idempotent,
        "offline_archive_seconds": round(archive_seconds, 3),
        "offline_database_seconds": round(database_seconds, 3),
        "benchmark": {
            "samples_seconds": [round(item, 3) for item in benchmark_seconds],
            "mean_seconds": round(statistics.mean(benchmark_seconds), 3),
            "median_seconds": round(statistics.median(benchmark_seconds), 3),
            "minimum_seconds": round(min(benchmark_seconds), 3),
            "maximum_seconds": round(max(benchmark_seconds), 3),
            "population_stdev_seconds": round(
                statistics.pstdev(benchmark_seconds),
                3,
            ),
        },
        "products": [str(path) for path in product_files],
        "offline_archive_product_count": len(archive_offline_files),
        "offline_database_product_count": len(database_offline_files),
        "archive": archive_first,
        "word_fields": word_fields,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Відтворювана регресійна перевірка HydroBulletin."
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help=(
            "Нова папка результатів; за замовчуванням створюється "
            "у validation_results/ у корені проєкту."
        ),
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=3,
        help="Кількість свіжих запусків для вимірювання часу (типово: 3).",
    )
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples має бути не менше 1")

    if args.work_dir is not None:
        work_dir = args.work_dir
    else:
        validation_root = PROJECT_DIR / "validation_results"
        validation_root.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        work_dir = validation_root / f"regression_{timestamp}"
        suffix = 1
        while work_dir.exists():
            work_dir = validation_root / f"regression_{timestamp}_{suffix}"
            suffix += 1

    work_dir = work_dir.resolve()

    try:
        report = validate(work_dir, args.samples)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        print(f"ПОМИЛКА: {exc}", file=sys.stderr)
        return 1

    report_path = work_dir / "regression_validation.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("HydroBulletin — регресійна перевірка: OK")
    print(f"Звіт: {report_path}")
    print(
        "Word-звірка: "
        f"{report['word_fields']['matched']}/{report['word_fields']['checked']}"
    )
    print(
        "Середній час свіжого запуску: "
        f"{report['benchmark']['mean_seconds']} с"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
