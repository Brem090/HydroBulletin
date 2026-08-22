"""Перевірка повного експлуатаційного сценарію на реальних raw-файлах."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document
from PIL import Image


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from hydrobulletin.batch import discover_batch_files  # noqa: E402
from hydrobulletin.decoder import decode_codes  # noqa: E402
from hydrobulletin.meteorology import (  # noqa: E402
    decode_meteo_precipitation,
    parse_synop_records,
    synop_precip_groups,
)
from hydrobulletin.regions import REGIONS  # noqa: E402
from hydrobulletin.sources import LocalFileSource  # noqa: E402
from hydrobulletin.stations import (  # noqa: E402
    HYDRO_STATIONS,
    METEO_STATIONS,
    STATIONS_BY_INDEX,
)


PREVIOUS_DATE = "07.08.2026"
DEFAULT_DATE = "08.08.2026"
FOLLOWING_DATE = "09.08.2026"
CHART_STATION_INDEX = "81015"
DEFAULT_INPUT_FOLDER = PROJECT_DIR / "demo_data" / "full_private"
REQUIRED_MESSAGE_TYPES = ("ZRUR52", "ZRUR71", "SYNOP")
HYDRO_MESSAGE_TYPES = ("ZRUR52", "ZRUR71")
MANUAL_SECONDS = {
    "Львівський бюлетень": 9 * 60 + 59,
    "Івано-Франківський бюлетень": 4 * 60 + 34,
    "Ліві притоки Дністра": 6 * 60 + 13,
    "Карта": 7 * 60 + 29,
}
MANUAL_COMPARABLE_TOTAL = sum(MANUAL_SECONDS.values())


@dataclass(frozen=True)
class SelectedInput:
    message_type: str
    path: Path
    sha256: str
    semantic_sha256: str
    duplicate_names: tuple[str, ...]
    ignored_variant_names: tuple[str, ...] = ()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_payload(path: Path, message_type: str, date_text: str) -> object:
    raw_text = LocalFileSource(path).load_text()
    if message_type == "SYNOP":
        return [
            (
                record.section,
                record.station_index,
                record.observed_at_utc.isoformat(timespec="seconds"),
                record.groups,
            )
            for record in parse_synop_records(raw_text)
        ]

    return [
        (
            item.index,
            item.level,
            item.change,
            item.evening_level,
            item.water_temperature_c,
            item.precipitation_mm,
            item.discharge_m3_s,
            item.ice_phenomena,
            item.ice_thickness_cm,
            item.observed_at.isoformat(timespec="seconds")
            if item.observed_at is not None
            else "",
        )
        for item in decode_codes(
            raw_text,
            date_text,
            STATIONS_BY_INDEX,
            source_type="validation",
            source_file=path.name,
        )
    ]


def _semantic_sha256(path: Path, message_type: str, date_text: str) -> str:
    payload = json.dumps(
        _semantic_payload(path, message_type, date_text),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _preferred_file(path: Path) -> tuple[bool, str]:
    has_numeric_suffix = bool(re.search(r"_\d+\.txt$", path.name, re.IGNORECASE))
    return has_numeric_suffix, path.name.lower()


def _candidate_score(
    path: Path,
    message_type: str,
    date_text: str,
) -> tuple[int, int, int]:
    """Оцінює відповідність вмісту даті, не покладаючись на суфікс файла."""

    raw_text = LocalFileSource(path).load_text()
    if message_type == "SYNOP":
        target_date = datetime.strptime(date_text, "%d.%m.%Y").date()
        records = parse_synop_records(raw_text)
        target_records = [
            record
            for record in records
            if record.observed_at_utc.date() == target_date
        ]
        known_indexes = {station.index for station in METEO_STATIONS}
        target_stations = {
            record.station_index
            for record in target_records
            if record.station_index in known_indexes
        }
        return len(target_stations), len(target_records), len(records)

    observations = decode_codes(
        raw_text,
        date_text,
        STATIONS_BY_INDEX,
        source_type="validation",
        source_file=path.name,
    )
    expected_indexes = {station.index for station in HYDRO_STATIONS}
    found_indexes = {
        observation.index
        for observation in observations
        if observation.index in expected_indexes
    }
    return len(found_indexes), len(observations), 0


def select_operational_inputs(
    folder: Path,
    date_text: str,
    required_message_types: tuple[str, ...] = REQUIRED_MESSAGE_TYPES,
) -> tuple[SelectedInput, ...]:
    """Вибирає найповніший варіант кожного типу для заданої дати.

    Семантично рівнозначні файли вважаються дублікатами. Якщо однойменні
    варіанти відрізняються, перевагу отримує вміст із найбільшим фактичним
    покриттям потрібної дати. Однаковий найкращий результат двох різних
    варіантів вимагає ручного вибору.
    """

    candidates = [
        item
        for item in discover_batch_files(folder)
        if item.bulletin_date == date_text
    ]
    selected: list[SelectedInput] = []
    for message_type in required_message_types:
        files = sorted(
            (item.path for item in candidates if item.message_type == message_type),
            key=_preferred_file,
        )
        if not files:
            raise FileNotFoundError(
                f"За {date_text} не знайдено файл {message_type} у папці {folder}."
            )

        signatures: dict[str, list[Path]] = {}
        for path in files:
            signature = _semantic_sha256(path, message_type, date_text)
            signatures.setdefault(signature, []).append(path)

        ranked: list[tuple[tuple[int, int, int], str, list[Path]]] = []
        for signature, equivalent_files in signatures.items():
            representative = min(equivalent_files, key=_preferred_file)
            ranked.append(
                (
                    _candidate_score(representative, message_type, date_text),
                    signature,
                    equivalent_files,
                )
            )
        best_score = max(item[0] for item in ranked)
        best = [item for item in ranked if item[0] == best_score]
        if len(best) != 1:
            variants = ", ".join(path.name for path in files)
            raise RuntimeError(
                f"Не вдалося однозначно вибрати {message_type} за {date_text} "
                f"({variants}). Оберіть правильну версію вручну."
            )

        _score, signature, equivalent_files = best[0]
        chosen = min(equivalent_files, key=_preferred_file)
        duplicates = tuple(
            path.name for path in equivalent_files if path != chosen
        )
        ignored_variants = tuple(
            path.name
            for _other_score, other_signature, other_files in ranked
            if other_signature != signature
            for path in other_files
        )
        selected.append(
            SelectedInput(
                message_type,
                chosen,
                _file_sha256(chosen),
                signature,
                duplicates,
                ignored_variants,
            )
        )
    return tuple(selected)


def input_coverage(
    selected: tuple[SelectedInput, ...],
    date_text: str,
) -> dict[str, Any]:
    """Повертає точне покриття гідропостів, метеостанцій і груп опадів."""

    by_type: dict[str, set[str]] = {}
    for item in selected:
        if item.message_type == "SYNOP":
            continue
        observations = decode_codes(
            LocalFileSource(item.path).load_text(),
            date_text,
            STATIONS_BY_INDEX,
            source_type="validation",
            source_file=item.path.name,
        )
        by_type[item.message_type] = {observation.index for observation in observations}

    combined = set().union(*by_type.values()) if by_type else set()
    region_coverage: dict[str, dict[str, Any]] = {}
    for region in REGIONS:
        expected = {station.index for station in region.stations}
        found = combined & expected
        region_coverage[region.key] = {
            "expected": len(expected),
            "found": len(found),
            "missing": sorted(expected - found),
        }

    synop_item = next(item for item in selected if item.message_type == "SYNOP")
    synop_text = LocalFileSource(synop_item.path).load_text()
    synop_records = parse_synop_records(synop_text)
    known_meteo = {station.index for station in METEO_STATIONS}
    record_stations = {record.station_index for record in synop_records}
    precip_groups = [
        group
        for record in synop_records
        for _amount, _period, group in synop_precip_groups(record)
        if record.station_index in known_meteo
    ]
    meteo_observations = decode_meteo_precipitation(
        synop_text,
        date_text,
        {station.index: station for station in METEO_STATIONS},
        source_type="validation",
        source_file=synop_item.path.name,
    )

    expected_hydro = {station.index for station in HYDRO_STATIONS}
    return {
        "hydrological": {
            "expected_unique_stations": len(expected_hydro),
            "found_unique_stations": len(combined),
            "missing_station_indexes": sorted(expected_hydro - combined),
            "by_message_type": {
                message_type: {
                    "station_count": len(indexes),
                    "station_indexes": sorted(indexes),
                }
                for message_type, indexes in sorted(by_type.items())
            },
            "regions": region_coverage,
        },
        "meteorological": {
            "record_count": len(synop_records),
            "expected_station_count": len(known_meteo),
            "found_station_count": len(record_stations & known_meteo),
            "missing_station_indexes": sorted(known_meteo - record_stations),
            "usable_6RRRtR_group_count": len(precip_groups),
            "usable_6RRRtR_groups": precip_groups,
            "daily_precipitation_station_count": len(meteo_observations),
            "daily_precipitation_values_mm": {
                observation.index: observation.precipitation_mm
                for observation in meteo_observations
            },
            "interpretation": (
                "SYNOP імпортовано, але добові опади не обчислюються без "
                "придатних груп 6RRRtR. Нульові значення не підставляються."
                if not meteo_observations
                else (
                    "Добові опади 09:00–09:00 обчислено для наявних "
                    "метеостанцій; iR=3 враховано як нуль, а не як пропуск."
                )
            ),
        },
    }


def _hydro_by_index(
    selected: tuple[SelectedInput, ...],
    date_text: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in selected:
        if item.message_type == "SYNOP":
            continue
        observations = decode_codes(
            LocalFileSource(item.path).load_text(),
            date_text,
            STATIONS_BY_INDEX,
            source_type="validation",
            source_file=item.path.name,
        )
        result.update({observation.index: observation for observation in observations})
    return result


def daily_change_transition(
    previous_inputs: tuple[SelectedInput, ...],
    current_inputs: tuple[SelectedInput, ...],
    previous_date: str,
    current_date: str,
) -> dict[str, Any]:
    """Звіряє кодовану зміну з різницею двох ранкових рівнів."""

    previous = _hydro_by_index(previous_inputs, previous_date)
    current = _hydro_by_index(current_inputs, current_date)
    consistent: list[str] = []
    unavailable: list[str] = []
    inconsistent: list[dict[str, Any]] = []
    expected_indexes = {station.index for station in HYDRO_STATIONS}

    for index in sorted(expected_indexes):
        previous_observation = previous.get(index)
        current_observation = current.get(index)
        if (
            previous_observation is None
            or current_observation is None
            or previous_observation.level is None
            or current_observation.level is None
            or current_observation.change is None
        ):
            unavailable.append(index)
            continue

        expected_change = current_observation.level - previous_observation.level
        coded_change = current_observation.change
        if abs(expected_change - coded_change) <= 1e-9:
            consistent.append(index)
            continue
        inconsistent.append(
            {
                "station_index": index,
                "station_name": STATIONS_BY_INDEX[index].name,
                "previous_level_cm": previous_observation.level,
                "current_level_cm": current_observation.level,
                "coded_change_cm": coded_change,
                "calculated_change_cm": expected_change,
            }
        )

    return {
        "previous_date": previous_date,
        "current_date": current_date,
        "expected_station_count": len(expected_indexes),
        "checkable_count": len(consistent) + len(inconsistent),
        "consistent_count": len(consistent),
        "inconsistent_count": len(inconsistent),
        "unavailable_station_indexes": unavailable,
        "inconsistent_details": inconsistent,
    }


def _stage_inputs(selected: tuple[SelectedInput, ...], folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=False)
    for item in selected:
        shutil.copy2(item.path, folder / item.path.name)
    return folder


def _batch_command(
    run_dir: Path,
    input_folder: Path,
    date_text: str,
    *,
    create_products: bool,
    include_charts: bool,
    include_meteo: bool,
    chart_start_date: str | None = None,
    chart_end_date: str | None = None,
    output_folder_name: str = "output",
) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_DIR / "main.py"),
        "--batch-folder",
        str(input_folder),
        "--date",
        date_text,
        "--archive-db",
        str(run_dir / "hydro_archive.sqlite"),
        "--raw-root",
        str(run_dir / "raw"),
        "--output-dir",
        str(run_dir / output_folder_name),
    ]
    if not create_products:
        command.append("--no-bulletins")
    else:
        command.append("--map")
    if not include_meteo:
        command.append("--no-meteo")
    if include_charts:
        command.extend(
            (
                "--level-chart",
                "--discharge-chart",
                "--chart-station",
                CHART_STATION_INDEX,
                "--start-date",
                chart_start_date or date_text,
                "--end-date",
                chart_end_date or date_text,
            )
        )
    return command


def _database_chart_command(
    run_dir: Path,
    *,
    date_text: str,
    start_date: str,
    end_date: str,
) -> list[str]:
    return [
        sys.executable,
        str(PROJECT_DIR / "main.py"),
        "--source",
        "database",
        "--date",
        date_text,
        "--archive-db",
        str(run_dir / "hydro_archive.sqlite"),
        "--raw-root",
        str(run_dir / "raw"),
        "--output-dir",
        str(run_dir / "three_day_output"),
        "--no-bulletins",
        "--no-meteo",
        "--level-chart",
        "--discharge-chart",
        "--chart-station",
        CHART_STATION_INDEX,
        "--start-date",
        start_date,
        "--end-date",
        end_date,
    ]


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
            value.strip()
            for value in (completed.stdout, completed.stderr)
            if value.strip()
        )
        raise RuntimeError(
            f"HydroBulletin завершився з кодом {completed.returncode}.\n{details}"
        )
    return elapsed, completed.stdout


def _scalar(connection: sqlite3.Connection, query: str) -> int:
    row = connection.execute(query).fetchone()
    if row is None:
        raise RuntimeError(f"Запит не повернув результат: {query}")
    return int(row[0])


def _archive_report(db_path: Path, date_text: str) -> dict[str, Any]:
    report_date = datetime.strptime(date_text, "%d.%m.%Y").strftime("%Y-%m-%d")
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        integrity_row = connection.execute("PRAGMA integrity_check").fetchone()
        quality_rows = connection.execute(
            """
            SELECT quality_status, COUNT(*)
            FROM observations
            WHERE date(observed_at) = date(?)
            GROUP BY quality_status ORDER BY quality_status
            """,
            (report_date,),
        ).fetchall()
        parameter_rows = connection.execute(
            """
            SELECT parameter_code, COUNT(*)
            FROM observations
            GROUP BY parameter_code ORDER BY parameter_code
            """
        ).fetchall()
        import_rows = connection.execute(
            """
            SELECT message_type, raw_path, source_hash
            FROM imports ORDER BY import_id
            """
        ).fetchall()
        flagged_rows = connection.execute(
            """
            SELECT o.station_index, s.station_name, o.parameter_code, o.value,
                   o.quality_status, o.quality_message
            FROM observations AS o
            JOIN stations AS s ON s.station_index = o.station_index
            WHERE date(o.observed_at) = date(?)
              AND o.quality_status NOT IN ('VALID', 'NOT_CHECKED')
            ORDER BY o.station_index, o.parameter_code
            """,
            (report_date,),
        ).fetchall()
        product_rows = connection.execute(
            """
            SELECT p.product_type, p.region_key, p.output_path, p.metadata_json,
                   COUNT(po.observation_id) AS linked_observations
            FROM products AS p
            LEFT JOIN product_observations AS po ON po.product_id = p.product_id
            GROUP BY p.product_id ORDER BY p.product_id
            """
        ).fetchall()

        raw_matches = 0
        for row in import_rows:
            raw_path = Path(str(row["raw_path"]))
            if not raw_path.is_absolute():
                raw_path = db_path.parent / raw_path
            if raw_path.exists() and _file_sha256(raw_path) == str(row["source_hash"]):
                raw_matches += 1

        products = [
            {
                "type": str(row["product_type"]),
                "region": str(row["region_key"]),
                "output_path": str(row["output_path"]),
                "linked_observations": int(row["linked_observations"]),
                "metadata": json.loads(str(row["metadata_json"])),
            }
            for row in product_rows
        ]
        import_counts = Counter(str(row["message_type"]) for row in import_rows)
        observations_on_report_date = connection.execute(
            "SELECT COUNT(*) FROM observations WHERE date(observed_at) = date(?)",
            (report_date,),
        ).fetchone()
        return {
            "report_date": date_text,
            "integrity_check": str(integrity_row[0]) if integrity_row else "",
            "foreign_key_violations": len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            ),
            "stations": _scalar(connection, "SELECT COUNT(*) FROM stations"),
            "imports": _scalar(connection, "SELECT COUNT(*) FROM imports"),
            "observations": _scalar(
                connection, "SELECT COUNT(*) FROM observations"
            ),
            "observations_on_report_date": (
                int(observations_on_report_date[0])
                if observations_on_report_date is not None
                else 0
            ),
            "reference_extremes": _scalar(
                connection, "SELECT COUNT(*) FROM reference_extremes"
            ),
            "products": len(products),
            "product_observations": _scalar(
                connection, "SELECT COUNT(*) FROM product_observations"
            ),
            "duplicate_observation_keys": _scalar(
                connection,
                """
                SELECT COUNT(*) FROM (
                    SELECT station_index, observed_at, parameter_code
                    FROM observations
                    GROUP BY station_index, observed_at, parameter_code
                    HAVING COUNT(*) > 1
                )
                """,
            ),
            "raw_sha256_matches": f"{raw_matches}/{len(import_rows)}",
            "imports_by_type": dict(sorted(import_counts.items())),
            "quality_on_report_date": {
                str(status): int(count) for status, count in quality_rows
            },
            "flagged_on_report_date": [dict(row) for row in flagged_rows],
            "parameters": {
                str(parameter): int(count) for parameter, count in parameter_rows
            },
            "product_details": products,
        }
    finally:
        connection.close()


def _product_files(output_root: Path) -> list[Path]:
    return sorted(
        path
        for path in output_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".docx", ".png"}
    )


def _verify_product_files(paths: list[Path]) -> None:
    for path in paths:
        if path.suffix.lower() == ".docx":
            document = Document(path)
            if not document.tables:
                raise RuntimeError(f"Word-файл не містить таблиць: {path}")
        elif path.suffix.lower() == ".png":
            with Image.open(path) as image:
                image.verify()


def _validate_run(
    archive: dict[str, Any],
    product_files: list[Path],
    *,
    include_charts: bool,
) -> None:
    expected_products = 6 if include_charts else 4
    expected_links = 415 if include_charts else 410
    expected_types = {"WORD_BULLETIN", "HYDRO_MAP"}
    if include_charts:
        expected_types.update(("LEVEL_CHART", "DISCHARGE_CHART"))
    product_types = {item["type"] for item in archive["product_details"]}

    exact_expectations = {
        "integrity_check": "ok",
        "foreign_key_violations": 0,
        "stations": 81,
        "imports": 5,
        "observations": 787,
        "observations_on_report_date": 344,
        "reference_extremes": 70,
        "products": expected_products,
        "product_observations": expected_links,
        "duplicate_observation_keys": 0,
        "raw_sha256_matches": "5/5",
        "quality_on_report_date": {"SUSPICIOUS": 1, "VALID": 343},
    }
    for key, expected in exact_expectations.items():
        if archive[key] != expected:
            raise RuntimeError(
                f"Неочікуване значення {key}: {archive[key]!r}; "
                f"очікувалося {expected!r}."
            )
    if archive["imports_by_type"] != {"SYNOP": 1, "ZRUR52": 2, "ZRUR71": 2}:
        raise RuntimeError(f"Неочікувані типи імпорту: {archive['imports_by_type']}")
    target_flags = archive["flagged_on_report_date"]
    if not (
        len(target_flags) == 1
        and target_flags[0]["station_index"] == "81197"
        and target_flags[0]["parameter_code"] == "PRECIPITATION"
        and target_flags[0]["quality_status"] == "SUSPICIOUS"
        and target_flags[0]["value"] == 152.0
    ):
        raise RuntimeError(f"Неочікувані прапорці QC за 08.08: {target_flags}")
    if product_types != expected_types:
        raise RuntimeError(f"Неочікуваний набір продуктів: {sorted(product_types)}")
    if len(product_files) != expected_products:
        raise RuntimeError(
            f"Створено {len(product_files)} файлів замість {expected_products}."
        )

    map_product = next(
        item for item in archive["product_details"] if item["type"] == "HYDRO_MAP"
    )
    if map_product["metadata"].get("plotted_stations") != 21:
        raise RuntimeError("На карту нанесено не всі 21 передбачені шаблоном пости.")
    if include_charts:
        level_chart = next(
            item for item in archive["product_details"] if item["type"] == "LEVEL_CHART"
        )
        discharge_chart = next(
            item
            for item in archive["product_details"]
            if item["type"] == "DISCHARGE_CHART"
        )
        if (
            level_chart["metadata"].get("available_points") != 3
            or level_chart["metadata"].get("missing_points") != 1
        ):
            raise RuntimeError("Дводенний графік рівнів має неочікувану повноту.")
        if (
            discharge_chart["metadata"].get("available_points") != 2
            or discharge_chart["metadata"].get("missing_points") != 0
        ):
            raise RuntimeError("Дводенний графік витрат має неочікувану повноту.")
    _verify_product_files(product_files)


def _validate_continuity_run(
    archive: dict[str, Any],
    three_day_products: list[Path],
) -> None:
    exact_expectations = {
        "integrity_check": "ok",
        "foreign_key_violations": 0,
        "stations": 81,
        "imports": 7,
        "observations": 1134,
        "observations_on_report_date": 277,
        "reference_extremes": 70,
        "products": 8,
        "product_observations": 423,
        "duplicate_observation_keys": 0,
        "raw_sha256_matches": "7/7",
        "quality_on_report_date": {"INCONSISTENT_CHANGE": 1, "VALID": 276},
    }
    for key, expected in exact_expectations.items():
        if archive[key] != expected:
            raise RuntimeError(
                f"Неочікуване значення триденного архіву {key}: "
                f"{archive[key]!r}; очікувалося {expected!r}."
            )
    if archive["imports_by_type"] != {"SYNOP": 1, "ZRUR52": 3, "ZRUR71": 3}:
        raise RuntimeError(
            f"Неочікувані типи триденного імпорту: {archive['imports_by_type']}"
        )

    flags = archive["flagged_on_report_date"]
    if not (
        len(flags) == 1
        and flags[0]["station_index"] == "81113"
        and flags[0]["parameter_code"] == "DAILY_CHANGE"
        and flags[0]["quality_status"] == "INCONSISTENT_CHANGE"
        and flags[0]["value"] == -1.0
        and "-11" in flags[0]["quality_message"]
    ):
        raise RuntimeError(f"Не виявлено очікувану неузгодженість 08→09: {flags}")

    if len(three_day_products) != 2:
        raise RuntimeError(
            f"Створено {len(three_day_products)} триденних графіків замість 2."
        )
    three_day_details = [
        item
        for item in archive["product_details"]
        if item["metadata"].get("start_date") == PREVIOUS_DATE
        and item["metadata"].get("end_date") == FOLLOWING_DATE
    ]
    if len(three_day_details) != 2:
        raise RuntimeError("У provenance не знайдено обидва триденні графіки.")
    by_type = {item["type"]: item for item in three_day_details}
    level = by_type.get("LEVEL_CHART", {}).get("metadata", {})
    discharge = by_type.get("DISCHARGE_CHART", {}).get("metadata", {})
    if level.get("available_points") != 5 or level.get("missing_points") != 1:
        raise RuntimeError("Триденний графік рівнів має неочікувану повноту.")
    if discharge.get("available_points") != 3 or discharge.get("missing_points") != 0:
        raise RuntimeError("Триденний графік витрат має неочікувану повноту.")
    _verify_product_files(three_day_products)


def performance_metrics(automatic_seconds: float) -> dict[str, float]:
    """Обчислює три показники без округлення проміжних значень."""

    if automatic_seconds <= 0:
        raise ValueError("Автоматичний час має бути додатним.")
    return {
        "manual_seconds": float(MANUAL_COMPARABLE_TOTAL),
        "automatic_median_seconds": round(automatic_seconds, 3),
        "saved_seconds": round(MANUAL_COMPARABLE_TOTAL - automatic_seconds, 3),
        "time_reduction_percent": round(
            (1 - automatic_seconds / MANUAL_COMPARABLE_TOTAL) * 100,
            2,
        ),
        "speedup_times": round(MANUAL_COMPARABLE_TOTAL / automatic_seconds, 2),
    }


def _benchmark_summary(samples: list[float]) -> dict[str, Any]:
    median_seconds = statistics.median(samples)
    return {
        "samples_seconds": [round(value, 3) for value in samples],
        "mean_seconds": round(statistics.mean(samples), 3),
        "median_seconds": round(median_seconds, 3),
        "minimum_seconds": round(min(samples), 3),
        "maximum_seconds": round(max(samples), 3),
        "population_stdev_seconds": round(statistics.pstdev(samples), 3),
        "comparison_with_manual": performance_metrics(median_seconds),
    }


def validate(
    input_folder: Path,
    work_dir: Path,
    date_text: str,
    samples: int,
) -> dict[str, Any]:
    """Перевіряє оперативний день 08.08 у зв'язній послідовності 07→09."""

    if date_text != DEFAULT_DATE:
        raise ValueError(
            "Поточний контрольний набір і точні очікувані кількості "
            f"зафіксовано для {DEFAULT_DATE}."
        )
    if work_dir.exists():
        raise FileExistsError(f"Папка результатів уже існує: {work_dir}")
    work_dir.mkdir(parents=True)
    previous_inputs = select_operational_inputs(
        input_folder,
        PREVIOUS_DATE,
        HYDRO_MESSAGE_TYPES,
    )
    target_inputs = select_operational_inputs(input_folder, date_text)
    following_inputs = select_operational_inputs(
        input_folder,
        FOLLOWING_DATE,
        HYDRO_MESSAGE_TYPES,
    )
    coverage = input_coverage(target_inputs, date_text)
    hydro_coverage = coverage["hydrological"]
    if hydro_coverage["missing_station_indexes"]:
        raise RuntimeError(
            "Не покрито всі потрібні гідропости: "
            + ", ".join(hydro_coverage["missing_station_indexes"])
        )
    for key, details in hydro_coverage["regions"].items():
        if details["missing"]:
            raise RuntimeError(
                f"Неповне покриття регіону {key}: {details['missing']}"
            )

    meteorological = coverage["meteorological"]
    if meteorological["daily_precipitation_station_count"] != len(METEO_STATIONS):
        raise RuntimeError(
            "Добові SYNOP-опади не обчислено для всіх 10 метеостанцій."
        )

    transition_to_target = daily_change_transition(
        previous_inputs,
        target_inputs,
        PREVIOUS_DATE,
        date_text,
    )
    if not (
        transition_to_target["checkable_count"] == 70
        and transition_to_target["consistent_count"] == 70
        and transition_to_target["inconsistent_count"] == 0
        and transition_to_target["unavailable_station_indexes"] == ["81261"]
    ):
        raise RuntimeError(
            f"Неочікуваний результат звірення 07→08: {transition_to_target}"
        )

    transition_to_following = daily_change_transition(
        target_inputs,
        following_inputs,
        date_text,
        FOLLOWING_DATE,
    )
    following_details = transition_to_following["inconsistent_details"]
    if not (
        transition_to_following["checkable_count"] == 70
        and transition_to_following["consistent_count"] == 69
        and transition_to_following["inconsistent_count"] == 1
        and transition_to_following["unavailable_station_indexes"] == ["81261"]
        and len(following_details) == 1
        and following_details[0]["station_index"] == "81113"
        and following_details[0]["coded_change_cm"] == -1
        and following_details[0]["calculated_change_cm"] == -11
    ):
        raise RuntimeError(
            f"Неочікуваний результат звірення 08→09: {transition_to_following}"
        )

    staged_previous = _stage_inputs(previous_inputs, work_dir / "input_07")
    staged_target = _stage_inputs(target_inputs, work_dir / "input_08")
    staged_following = _stage_inputs(following_inputs, work_dir / "input_09")

    def seed_previous_day(run_dir: Path) -> None:
        _run(
            _batch_command(
                run_dir,
                staged_previous,
                PREVIOUS_DATE,
                create_products=False,
                include_charts=False,
                include_meteo=False,
                output_folder_name="preload_output",
            )
        )

    comparable_samples: list[float] = []
    comparable_archive: dict[str, Any] | None = None
    comparable_products: list[Path] = []
    for sample_number in range(1, samples + 1):
        sample_dir = work_dir / f"comparable_{sample_number}"
        sample_dir.mkdir()
        seed_previous_day(sample_dir)
        elapsed, _stdout = _run(
            _batch_command(
                sample_dir,
                staged_target,
                date_text,
                create_products=True,
                include_charts=False,
                include_meteo=True,
                output_folder_name="current_output",
            )
        )
        archive = _archive_report(sample_dir / "hydro_archive.sqlite", date_text)
        products = _product_files(sample_dir / "current_output")
        _validate_run(archive, products, include_charts=False)
        comparable_samples.append(elapsed)
        if comparable_archive is None:
            comparable_archive = archive
            comparable_products = products

    extended_dir = work_dir / "extended_six_products"
    extended_dir.mkdir()
    seed_previous_day(extended_dir)
    extended_seconds, _stdout = _run(
        _batch_command(
            extended_dir,
            staged_target,
            date_text,
            create_products=True,
            include_charts=True,
            include_meteo=True,
            chart_start_date=PREVIOUS_DATE,
            chart_end_date=date_text,
            output_folder_name="current_output",
        )
    )
    extended_archive = _archive_report(
        extended_dir / "hydro_archive.sqlite",
        date_text,
    )
    extended_products = _product_files(extended_dir / "current_output")
    _validate_run(extended_archive, extended_products, include_charts=True)

    _run(
        _batch_command(
            extended_dir,
            staged_following,
            FOLLOWING_DATE,
            create_products=False,
            include_charts=False,
            include_meteo=False,
            output_folder_name="follow_output",
        )
    )
    _run(
        _database_chart_command(
            extended_dir,
            date_text=FOLLOWING_DATE,
            start_date=PREVIOUS_DATE,
            end_date=FOLLOWING_DATE,
        )
    )
    continuity_archive = _archive_report(
        extended_dir / "hydro_archive.sqlite",
        FOLLOWING_DATE,
    )
    three_day_products = _product_files(extended_dir / "three_day_output")
    _validate_continuity_run(continuity_archive, three_day_products)

    if comparable_archive is None:
        raise RuntimeError("Не виконано жодного порівнюваного запуску.")
    return {
        "status": "OK",
        "scenario_date": date_text,
        "scope": {
            "comparable": (
                "3 Word-бюлетені + гідрологічна карта; цей обсяг порівнюється "
                "з ручними вимірами користувача."
            ),
            "extended": (
                "Ті самі 4 матеріали + дводенні графіки рівнів і витрат. "
                "Графіки не входять до ручного порівняння, оскільки АРМГ "
                "також формує їх автоматично."
            ),
            "continuity": (
                "Після основного запуску імпортується 09.08, QC перевіряє "
                "перехід 08→09, а окремо створюються триденні графіки 07–09."
            ),
        },
        "environment": {
            "platform": platform.platform(),
            "python_version": sys.version.replace("\n", " "),
            "python_executable": sys.executable,
            "processor": platform.processor(),
        },
        "selected_inputs": [
            {
                "role": role,
                "date": selected_date,
                "message_type": item.message_type,
                "filename": item.path.name,
                "byte_count": item.path.stat().st_size,
                "sha256": item.sha256,
                "semantic_sha256": item.semantic_sha256,
                "ignored_semantic_duplicates": list(item.duplicate_names),
                "ignored_nonmatching_variants": list(item.ignored_variant_names),
            }
            for role, selected_date, items in (
                ("previous_level_base", PREVIOUS_DATE, previous_inputs),
                ("target_products", date_text, target_inputs),
                ("following_qc", FOLLOWING_DATE, following_inputs),
            )
            for item in items
        ],
        "coverage": coverage,
        "daily_change_checks": {
            "07_to_08": transition_to_target,
            "08_to_09": transition_to_following,
        },
        "manual_measurements": {
            "conditions": (
                "Виміри одного користувача на цьому самому наборі за "
                "08.08.2026 у спокійній обстановці; результат залежить від "
                "людини та умов."
            ),
            "items_seconds": MANUAL_SECONDS,
            "bulletins_subtotal_seconds": sum(
                value for name, value in MANUAL_SECONDS.items() if name != "Карта"
            ),
            "comparable_total_seconds": MANUAL_COMPARABLE_TOTAL,
        },
        "comparable_benchmark": _benchmark_summary(comparable_samples),
        "extended_six_products_seconds": round(extended_seconds, 3),
        "archive": comparable_archive,
        "extended_archive": extended_archive,
        "continuity_archive": continuity_archive,
        "comparable_products": [str(path) for path in comparable_products],
        "extended_products": [str(path) for path in extended_products],
        "three_day_products": [str(path) for path in three_day_products],
        "limitations": [
            "Час слід наводити лише з цього звіту, запущеного у Windows 10 "
            "на фактичному комп'ютері дипломника.",
            "Попередній день 07.08 завантажується до початку кожного заміру, "
            "тому підготовка контрольної бази не входить до автоматичного часу.",
            "Наступний день 09.08 імпортується після формування матеріалів за "
            "08.08 і не впливає на порівняння з ручним часом.",
            "Гідрологічний пост 81261 не має ранкового рівня у триденному наборі, "
            "тому для нього міждобове звірення неможливе.",
        ],
    }


def _summary_text(report: dict[str, Any]) -> str:
    benchmark = report["comparable_benchmark"]
    comparison = benchmark["comparison_with_manual"]
    coverage = report["coverage"]
    change_checks = report["daily_change_checks"]
    return "\n".join(
        (
            "HYDROBULLETIN — ПОВНИЙ ЕКСПЛУАТАЦІЙНИЙ СЦЕНАРІЙ",
            f"Статус: {report['status']}",
            f"Основна дата: {report['scenario_date']}",
            f"Послідовність рівнів: {PREVIOUS_DATE} → {DEFAULT_DATE} → {FOLLOWING_DATE}",
            f"Середовище: {report['environment']['platform']}",
            f"Python: {report['environment']['python_version']}",
            "",
            "ПОКРИТТЯ",
            "Гідропости: "
            f"{coverage['hydrological']['found_unique_stations']}/"
            f"{coverage['hydrological']['expected_unique_stations']}",
            "Метеостанції SYNOP: "
            f"{coverage['meteorological']['found_station_count']}/"
            f"{coverage['meteorological']['expected_station_count']}",
            "Придатні групи 6RRRtR: "
            f"{coverage['meteorological']['usable_6RRRtR_group_count']}",
            "Добові SYNOP-опади: "
            f"{coverage['meteorological']['daily_precipitation_station_count']}/10 станцій",
            "Звірення змін 07→08: "
            f"{change_checks['07_to_08']['consistent_count']}/"
            f"{change_checks['07_to_08']['checkable_count']} узгоджено",
            "Звірення змін 08→09: "
            f"{change_checks['08_to_09']['consistent_count']}/"
            f"{change_checks['08_to_09']['checkable_count']} узгоджено; "
            "1 неузгодженість виявлено",
            "",
            "ПОРІВНЮВАНИЙ ОБСЯГ: 3 БЮЛЕТЕНІ + КАРТА",
            f"Ручний час: {MANUAL_COMPARABLE_TOTAL} с (28 хв 15 с)",
            f"Автоматичні заміри: {benchmark['samples_seconds']}",
            f"Медіана: {benchmark['median_seconds']} с",
            f"Економія: {comparison['saved_seconds']} с",
            f"Скорочення часу: {comparison['time_reduction_percent']}%",
            f"Прискорення: {comparison['speedup_times']} раза",
            "",
            "РОЗШИРЕНИЙ ОБСЯГ: 3 БЮЛЕТЕНІ + КАРТА + 2 ГРАФІКИ",
            f"Час одного запуску: {report['extended_six_products_seconds']} с",
            "Графіки не порівнюються з ручним введенням.",
            "Триденний контроль: 5 точок рівня, 3 точки витрати; "
            "QC виявив зміну -1 см замість різниці -11 см.",
        )
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Перевірка повного сценарію HydroBulletin на реальних raw-файлах."
    )
    parser.add_argument(
        "--input-folder",
        type=Path,
        default=DEFAULT_INPUT_FOLDER,
        help=(
            "Папка із ZRUR52/ZRUR71 за 07–09.08.2026 та SYNOP за 08.08; "
            "можна залишити невідповідні демонстраційні варіанти й дублікати _2."
        ),
    )
    parser.add_argument("--date", default=DEFAULT_DATE)
    parser.add_argument(
        "--samples",
        type=int,
        default=5,
        help="Кількість незалежних запусків порівнюваного обсягу (типово: 5).",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Нова папка результатів; типово validation_results/дата-час.",
    )
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples має бути не менше 1")
    try:
        datetime.strptime(args.date, "%d.%m.%Y")
    except ValueError:
        parser.error("--date має бути у форматі ДД.ММ.РРРР")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work_dir = args.work_dir or PROJECT_DIR / "validation_results" / stamp
    try:
        report = validate(
            args.input_folder.resolve(),
            work_dir.resolve(),
            args.date,
            args.samples,
        )
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        print(f"ПОМИЛКА: {exc}", file=sys.stderr)
        return 1

    json_path = work_dir / "operational_validation.json"
    text_path = work_dir / "operational_validation.txt"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    text_path.write_text(_summary_text(report), encoding="utf-8")
    print(_summary_text(report), end="")
    print(f"JSON-звіт: {json_path}")
    print(f"Текстовий звіт: {text_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
