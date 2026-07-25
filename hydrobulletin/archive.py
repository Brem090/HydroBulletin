"""Raw-архів і нормалізоване SQLite-сховище HydroBulletin."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from .models import HydroObservation, Station, observation_measurements

SCHEMA_VERSION = "2"

BASE_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stations (
    station_index TEXT PRIMARY KEY,
    station_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS imports (
    import_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'local',
    message_type TEXT NOT NULL DEFAULT 'UNKNOWN',
    bulletin_date TEXT NOT NULL DEFAULT '',
    raw_path TEXT NOT NULL DEFAULT '',
    source_hash TEXT NOT NULL UNIQUE,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

OBSERVATIONS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_index TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    parameter_code TEXT NOT NULL,
    value REAL,
    unit TEXT NOT NULL,
    quality_status TEXT NOT NULL DEFAULT 'NOT_CHECKED',
    source_type TEXT NOT NULL DEFAULT 'unknown',
    source_file TEXT NOT NULL DEFAULT '',
    raw_record TEXT NOT NULL DEFAULT '',
    import_id INTEGER NOT NULL,
    FOREIGN KEY (station_index) REFERENCES stations(station_index),
    FOREIGN KEY (import_id) REFERENCES imports(import_id),
    UNIQUE (station_index, observed_at, parameter_code)
);

CREATE INDEX IF NOT EXISTS idx_observations_station_time
ON observations(station_index, observed_at);

CREATE INDEX IF NOT EXISTS idx_observations_parameter_time
ON observations(parameter_code, observed_at);
"""


@dataclass(frozen=True)
class ImportResult:
    """Підсумок однієї спроби запису файла до SQLite."""

    import_id: int
    duplicate_file: bool
    inserted_observations: int
    duplicate_observations: int


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _ensure_import_columns(connection: sqlite3.Connection) -> None:
    """Додає відсутні поля походження файла до старої схеми."""

    columns = _table_columns(connection, "imports")
    additions = {
        "source_type": "TEXT NOT NULL DEFAULT 'local'",
        "message_type": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "bulletin_date": "TEXT NOT NULL DEFAULT ''",
        "raw_path": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE imports ADD COLUMN {name} {definition}")


def _ensure_observations_schema(connection: sqlite3.Connection) -> None:
    """Створює схему v2 або переносить дані зі схеми v1."""

    columns = _table_columns(connection, "observations")
    if not columns:
        connection.executescript(OBSERVATIONS_SCHEMA_SQL)
        return
    if "parameter_code" in columns:
        connection.executescript(OBSERVATIONS_SCHEMA_SQL)
        return

    connection.execute("ALTER TABLE observations RENAME TO observations_week1")
    connection.executescript(OBSERVATIONS_SCHEMA_SQL)

    legacy_rows = connection.execute(
        """
        SELECT station_index, observation_at, level_cm, daily_change_cm,
               quality_status, import_id
        FROM observations_week1
        """
    ).fetchall()
    for (
        station_index,
        observed_at,
        level,
        change,
        quality_status,
        import_id,
    ) in legacy_rows:
        if import_id is None:
            continue
        for parameter_code, value in (
            ("WATER_LEVEL", level),
            ("DAILY_CHANGE", change),
        ):
            if value is None:
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO observations(
                    station_index, observed_at, parameter_code, value, unit,
                    quality_status, source_type, source_file, raw_record, import_id
                ) VALUES (?, ?, ?, ?, 'cm', ?, 'legacy', '', '', ?)
                """,
                (
                    station_index,
                    observed_at,
                    parameter_code,
                    value,
                    quality_status,
                    import_id,
                ),
            )

    connection.execute("DROP TABLE observations_week1")


def initialize_archive(db_path: Path, stations: Iterable[Station]) -> Path:
    """Створює або безпечно оновлює SQLite-базу та довідник постів."""

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)

    try:
        connection.executescript(BASE_SCHEMA_SQL)
        _ensure_import_columns(connection)
        _ensure_observations_schema(connection)
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
            ("schema_version", SCHEMA_VERSION),
        )
        connection.executemany(
            """
            INSERT OR REPLACE INTO stations(station_index, station_name)
            VALUES (?, ?)
            """,
            ((station.index, station.name) for station in stations),
        )
        connection.commit()
    finally:
        connection.close()

    return db_path


def archive_raw_text(
    raw_root: Path,
    bulletin_date: str,
    message_type: str,
    raw_text: str,
) -> Path:
    """Зберігає отримане повідомлення у ``рік/місяць/дата_тип.txt``.

    Повторне збереження того самого вмісту повертає наявний файл. Якщо для
    тієї самої дати й типу прийшов інший вміст, попередній файл не
    перезаписується: нова версія отримує числовий суфікс.
    """

    try:
        observed_date = datetime.strptime(bulletin_date, "%d.%m.%Y")
    except ValueError as exc:
        raise ValueError("Дата має бути у форматі ДД.ММ.РРРР.") from exc

    normalized_type = message_type.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]+", normalized_type):
        raise ValueError(f"Некоректний тип повідомлення: {message_type}")

    folder = Path(raw_root) / f"{observed_date.year:04d}" / f"{observed_date.month:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{observed_date:%Y-%m-%d}_{normalized_type}"
    payload = raw_text.encode("utf-8")

    candidate = folder / f"{stem}.txt"
    suffix = 1
    while candidate.exists():
        if candidate.read_bytes() == payload:
            return candidate
        suffix += 1
        candidate = folder / f"{stem}_{suffix}.txt"

    candidate.write_bytes(payload)
    return candidate


def source_hash(raw_text: str) -> str:
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def import_observations(
    db_path: Path,
    observations: Sequence[HydroObservation],
    *,
    source_name: str,
    source_type: str,
    message_type: str,
    bulletin_date: str,
    raw_path: str,
    raw_text: str,
) -> ImportResult:
    """Записує імпорт і вимірювання однією транзакцією з дедуплікацією."""

    digest = source_hash(raw_text)
    connection = sqlite3.connect(Path(db_path))

    try:
        connection.execute("PRAGMA foreign_keys = ON")
        existing = connection.execute(
            "SELECT import_id FROM imports WHERE source_hash = ?",
            (digest,),
        ).fetchone()

        duplicate_file = existing is not None

        if existing is not None:
            # Повторна обробка може додати раніше відсутні параметри.
            import_id = int(existing[0])
        else:
            cursor = connection.execute(
                """
                INSERT INTO imports(
                    source_name, source_type, message_type, bulletin_date,
                    raw_path, source_hash
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    source_name,
                    source_type,
                    message_type.upper(),
                    bulletin_date,
                    raw_path,
                    digest,
                ),
            )

            if cursor.lastrowid is None:
                raise RuntimeError("Не вдалося отримати ID створеного імпорту.")

            import_id = int(cursor.lastrowid)
        inserted = 0
        duplicates = 0

        for observation in observations:
            for measurement in observation_measurements(observation):
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO observations(
                        station_index, observed_at, parameter_code, value, unit,
                        quality_status, source_type, source_file, raw_record, import_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        measurement.station_index,
                        measurement.observed_at.isoformat(timespec="seconds"),
                        measurement.parameter_code,
                        measurement.value,
                        measurement.unit,
                        measurement.quality_status,
                        measurement.source_type,
                        measurement.source_file,
                        measurement.raw_record,
                        import_id,
                    ),
                )
                if cursor.rowcount == 1:
                    inserted += 1
                else:
                    duplicates += 1

        connection.commit()
        return ImportResult(import_id, duplicate_file, inserted, duplicates)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def archive_summary(db_path: Path) -> dict[str, int]:
    """Повертає кількість записів у головних таблицях архіву."""

    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"База архіву не знайдена: {db_path}")

    result: dict[str, int] = {}
    connection = sqlite3.connect(db_path)
    try:
        for table_name in ("stations", "imports", "observations"):
            row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
            result[table_name] = int(row[0]) if row else 0
    finally:
        connection.close()
    return result


def read_observations(db_path: Path) -> list[dict[str, object]]:
    """Повертає нормалізовані записи для тестів і консольного демо."""

    connection = sqlite3.connect(Path(db_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT station_index, observed_at, parameter_code, value, unit,
                   quality_status, source_type, source_file, import_id
            FROM observations
            ORDER BY station_index, observed_at, parameter_code
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
