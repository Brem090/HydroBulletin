"""Архів початкових файлів і нормалізоване SQLite-сховище."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence, TypeAlias

from .models import HydroObservation, Station, observation_measurements

SCHEMA_VERSION = "4"

# Значення, які SQLite приймає як параметри й повертає в рядках запиту.
DatabaseValue: TypeAlias = str | int | float | bytes | None
DatabaseRow: TypeAlias = dict[str, DatabaseValue]


def database_float(value: DatabaseValue) -> float | None:
    """Перетворює значення SQLite на ``float``, зберігаючи ``None``."""

    if value is None:
        return None
    return float(value)


def database_int(value: DatabaseValue) -> int | None:
    """Перетворює значення SQLite на ``int``, зберігаючи ``None``."""

    if value is None:
        return None
    return int(value)


def required_database_int(value: DatabaseValue, field_name: str) -> int:
    """Читає ціле значення та відхиляє відсутнє обов'язкове поле."""

    converted = database_int(value)
    if converted is None:
        raise ValueError(f"У рядку SQLite відсутнє обов'язкове поле {field_name}.")
    return converted

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
    source_hash TEXT NOT NULL,
    source_key TEXT NOT NULL DEFAULT '',
    raw_byte_count INTEGER NOT NULL DEFAULT 0,
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
    text_value TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL,
    quality_status TEXT NOT NULL DEFAULT 'NOT_CHECKED',
    quality_message TEXT NOT NULL DEFAULT '',
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

CORRECTIONS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS corrections (
    correction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id INTEGER NOT NULL,
    original_value REAL NOT NULL,
    corrected_value REAL NOT NULL,
    reason TEXT NOT NULL,
    hydrologist TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cancelled_at TEXT,
    cancelled_by TEXT NOT NULL DEFAULT '',
    cancellation_reason TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (observation_id) REFERENCES observations(observation_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_corrections_one_active
ON corrections(observation_id)
WHERE is_active = 1;

CREATE INDEX IF NOT EXISTS idx_corrections_observation
ON corrections(observation_id, created_at);

CREATE TABLE IF NOT EXISTS reference_extremes (
    station_index TEXT PRIMARY KEY,
    maximum_level INTEGER NOT NULL,
    maximum_date TEXT NOT NULL DEFAULT '',
    average_level INTEGER NOT NULL,
    minimum_level INTEGER NOT NULL,
    minimum_date TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (station_index) REFERENCES stations(station_index),
    CHECK (minimum_level <= average_level),
    CHECK (average_level <= maximum_level)
);
"""

PRODUCTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS products (
    product_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_type TEXT NOT NULL,
    region_key TEXT NOT NULL DEFAULT '',
    bulletin_date TEXT NOT NULL,
    output_path TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (product_type, region_key, bulletin_date, output_path)
);

CREATE TABLE IF NOT EXISTS product_observations (
    product_id INTEGER NOT NULL,
    observation_id INTEGER NOT NULL,
    correction_id INTEGER,
    effective_value REAL,
    effective_text_value TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (product_id, observation_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE,
    FOREIGN KEY (observation_id) REFERENCES observations(observation_id),
    FOREIGN KEY (correction_id) REFERENCES corrections(correction_id)
);

CREATE INDEX IF NOT EXISTS idx_products_date_region
ON products(bulletin_date, region_key);
"""


@dataclass(frozen=True)
class ImportResult:
    """Підсумок однієї спроби запису файлу до SQLite."""

    import_id: int
    duplicate_file: bool
    inserted_observations: int
    duplicate_observations: int


@dataclass(frozen=True)
class ProductResult:
    """Зареєстрований службовий продукт і кількість пов'язаних значень."""

    product_id: int
    linked_observations: int


@dataclass(frozen=True)
class CorrectionResult:
    """Підсумок створення або скасування контрольованої ручної правки."""

    correction_id: int
    observation_id: int
    active: bool


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _ensure_import_columns(connection: sqlite3.Connection) -> None:
    """Додає відсутні поля походження файлу до старої схеми."""

    columns = _table_columns(connection, "imports")
    additions = {
        "source_type": "TEXT NOT NULL DEFAULT 'local'",
        "message_type": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "bulletin_date": "TEXT NOT NULL DEFAULT ''",
        "raw_path": "TEXT NOT NULL DEFAULT ''",
        "source_key": "TEXT NOT NULL DEFAULT ''",
        "raw_byte_count": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE imports ADD COLUMN {name} {definition}")

    _remove_previous_source_hash_constraint(connection)

    rows = connection.execute(
        """
        SELECT import_id, source_type, message_type, bulletin_date, source_hash
        FROM imports
        WHERE source_key = ''
        """
    ).fetchall()
    for import_id, source_type, message_type, bulletin_date, digest in rows:
        key = _source_key_from_digest(
            str(digest),
            source_type=str(source_type),
            message_type=str(message_type),
            bulletin_date=str(bulletin_date),
        )
        connection.execute(
            "UPDATE imports SET source_key = ? WHERE import_id = ?",
            (key, import_id),
        )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_imports_source_key
        ON imports(source_key)
        """
    )


def _remove_previous_source_hash_constraint(
    connection: sqlite3.Connection,
) -> None:
    """Замінює стару унікальність лише за хешем на складений ключ імпорту.

    Однаковий текст може надійти в іншу дату або з іншого джерела. У схемі
    v3 його ідентичність визначає ``source_key``: джерело, тип, дата й хеш.
    Вбудований autoindex SQLite не можна видалити окремо, тому таблиця
    ``imports`` перебудовується зі збереженням її ідентифікаторів.
    """

    create_row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'imports'
        """
    ).fetchone()
    create_sql = str(create_row[0] or "") if create_row else ""
    normalized = re.sub(r"\s+", " ", create_sql.upper())
    if not re.search(r"SOURCE_HASH\s+TEXT\s+NOT\s+NULL\s+UNIQUE", normalized):
        return

    connection.execute("PRAGMA foreign_keys = OFF")
    connection.executescript(
        """
        CREATE TABLE imports_v3 (
            import_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'local',
            message_type TEXT NOT NULL DEFAULT 'UNKNOWN',
            bulletin_date TEXT NOT NULL DEFAULT '',
            raw_path TEXT NOT NULL DEFAULT '',
            source_hash TEXT NOT NULL,
            source_key TEXT NOT NULL DEFAULT '',
            raw_byte_count INTEGER NOT NULL DEFAULT 0,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        INSERT INTO imports_v3(
            import_id, source_name, source_type, message_type, bulletin_date,
            raw_path, source_hash, source_key, raw_byte_count, imported_at
        )
        SELECT
            import_id, source_name, source_type, message_type, bulletin_date,
            raw_path, source_hash, source_key, raw_byte_count, imported_at
        FROM imports;

        DROP TABLE imports;
        ALTER TABLE imports_v3 RENAME TO imports;
        """
    )
    connection.execute("PRAGMA foreign_keys = ON")


def _ensure_observations_schema(connection: sqlite3.Connection) -> None:
    """Оновлює таблицю спостережень і переносить записи старої схеми."""

    columns = _table_columns(connection, "observations")
    if not columns:
        connection.executescript(OBSERVATIONS_SCHEMA_SQL)
        return
    if "parameter_code" in columns:
        if "text_value" not in columns:
            connection.execute(
                """
                ALTER TABLE observations
                ADD COLUMN text_value TEXT NOT NULL DEFAULT ''
                """
            )
        if "quality_message" not in columns:
            connection.execute(
                """
                ALTER TABLE observations
                ADD COLUMN quality_message TEXT NOT NULL DEFAULT ''
                """
            )
        connection.executescript(OBSERVATIONS_SCHEMA_SQL)
        return

    connection.execute("ALTER TABLE observations RENAME TO observations_legacy")
    connection.executescript(OBSERVATIONS_SCHEMA_SQL)

    previous_rows = connection.execute(
        """
        SELECT station_index, observation_at, level_cm, daily_change_cm,
               quality_status, import_id
        FROM observations_legacy
        """
    ).fetchall()
    for (
        station_index,
        observed_at,
        level,
        change,
        quality_status,
        import_id,
    ) in previous_rows:
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
                    quality_status, quality_message, source_type, source_file,
                    raw_record, import_id
                ) VALUES (?, ?, ?, ?, 'cm', ?, '', 'migrated', '', '', ?)
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

    connection.execute("DROP TABLE observations_legacy")


def _ensure_product_observation_columns(
    connection: sqlite3.Connection,
) -> None:
    """Додає знімок активної правки до зв'язку продукту з даними."""

    columns = _table_columns(connection, "product_observations")
    additions = {
        "correction_id": "INTEGER",
        "effective_value": "REAL",
        "effective_text_value": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(
                f"ALTER TABLE product_observations ADD COLUMN {name} {definition}"
            )
    connection.execute(
        """
        UPDATE product_observations
        SET effective_value = (
                SELECT o.value
                FROM observations AS o
                WHERE o.observation_id = product_observations.observation_id
            ),
            effective_text_value = COALESCE((
                SELECT o.text_value
                FROM observations AS o
                WHERE o.observation_id = product_observations.observation_id
            ), '')
        WHERE correction_id IS NULL
        """
    )


def initialize_archive(db_path: Path, stations: Iterable[Station]) -> Path:
    """Створює або оновлює SQLite-базу та довідник постів."""

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)

    try:
        connection.executescript(BASE_SCHEMA_SQL)
        _ensure_import_columns(connection)
        _ensure_observations_schema(connection)
        connection.executescript(CORRECTIONS_SCHEMA_SQL)
        connection.executescript(PRODUCTS_SCHEMA_SQL)
        _ensure_product_observation_columns(connection)
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


def _source_key_from_digest(
    digest: str,
    *,
    source_type: str,
    message_type: str,
    bulletin_date: str,
) -> str:
    identity = "|".join(
        (
            source_type.strip().lower(),
            message_type.strip().upper(),
            bulletin_date.strip(),
            digest,
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def source_key(
    raw_text: str,
    *,
    source_type: str,
    message_type: str,
    bulletin_date: str,
) -> str:
    """Стабільний ключ імпорту з урахуванням типу, дати й джерела."""

    return _source_key_from_digest(
        source_hash(raw_text),
        source_type=source_type,
        message_type=message_type,
        bulletin_date=bulletin_date,
    )


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
    import_key = _source_key_from_digest(
        digest,
        source_type=source_type,
        message_type=message_type,
        bulletin_date=bulletin_date,
    )
    connection = sqlite3.connect(Path(db_path))

    try:
        connection.execute("PRAGMA foreign_keys = ON")
        existing = connection.execute(
            """
            SELECT import_id
            FROM imports
            WHERE source_key = ?
            ORDER BY import_id
            LIMIT 1
            """,
            (import_key,),
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
                    raw_path, source_hash, source_key, raw_byte_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_name,
                    source_type,
                    message_type.upper(),
                    bulletin_date,
                    raw_path,
                    digest,
                    import_key,
                    len(raw_text.encode("utf-8")),
                ),
            )

            if cursor.lastrowid is None:
                raise RuntimeError("Не вдалося отримати ID створеного імпорту.")

            import_id = int(cursor.lastrowid)
        inserted = 0
        duplicates = 0

        for observation in observations:
            for measurement in observation_measurements(observation):
                observed_at_text = measurement.observed_at.isoformat(
                    timespec="seconds"
                )
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO observations(
                        station_index, observed_at, parameter_code, value,
                        text_value, unit, quality_status, quality_message,
                        source_type, source_file, raw_record, import_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        measurement.station_index,
                        observed_at_text,
                        measurement.parameter_code,
                        measurement.value,
                        measurement.text_value,
                        measurement.unit,
                        measurement.quality_status,
                        measurement.quality_message,
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
                    if (
                        measurement.parameter_code == "PRECIPITATION"
                        and measurement.text_value
                    ):
                        connection.execute(
                            """
                            UPDATE observations
                            SET text_value = ?
                            WHERE station_index = ?
                              AND observed_at = ?
                              AND parameter_code = 'PRECIPITATION'
                              AND text_value = ''
                            """,
                            (
                                measurement.text_value,
                                measurement.station_index,
                                observed_at_text,
                            ),
                        )

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
        for table_name in (
            "stations",
            "imports",
            "observations",
            "corrections",
            "reference_extremes",
            "products",
            "product_observations",
        ):
            row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
            result[table_name] = int(row[0]) if row else 0
    finally:
        connection.close()
    return result


def read_observations(db_path: Path) -> list[DatabaseRow]:
    """Повертає нормалізовані записи для тестів і консольної демонстрації."""

    connection = sqlite3.connect(Path(db_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT o.observation_id, o.station_index, o.observed_at,
                   o.parameter_code, o.value AS original_value,
                   o.text_value AS original_text_value,
                   COALESCE(c.corrected_value, o.value) AS value,
                   o.text_value, o.unit,
                   CASE WHEN c.correction_id IS NULL
                        THEN o.quality_status ELSE 'CORRECTED' END
                       AS quality_status,
                   CASE WHEN c.correction_id IS NULL
                        THEN o.quality_message
                        ELSE 'Ручна правка: ' || c.reason END
                       AS quality_message,
                   o.quality_status AS original_quality_status,
                   o.quality_message AS original_quality_message,
                   o.source_type, o.source_file, o.raw_record, o.import_id,
                   i.source_name, i.message_type, i.bulletin_date, i.raw_path,
                   c.correction_id, c.corrected_value, c.reason AS correction_reason,
                   c.hydrologist AS corrected_by, c.created_at AS corrected_at
            FROM observations AS o
            JOIN imports AS i ON i.import_id = o.import_id
            LEFT JOIN corrections AS c
              ON c.observation_id = o.observation_id AND c.is_active = 1
            ORDER BY o.station_index, o.observed_at, o.parameter_code
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def query_observations(
    db_path: Path,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    station_indexes: Sequence[str] = (),
    parameter_codes: Sequence[str] = (),
) -> list[DatabaseRow]:
    """Читає архівну вибірку за постом, датою, періодом і параметром."""

    clauses: list[str] = []
    values: list[DatabaseValue] = []

    if start_date:
        clauses.append("date(o.observed_at) >= date(?)")
        values.append(_iso_date(start_date))
    if end_date:
        clauses.append("date(o.observed_at) <= date(?)")
        values.append(_iso_date(end_date))
    if station_indexes:
        placeholders = ", ".join("?" for _ in station_indexes)
        clauses.append(f"o.station_index IN ({placeholders})")
        values.extend(station_indexes)
    if parameter_codes:
        placeholders = ", ".join("?" for _ in parameter_codes)
        clauses.append(f"o.parameter_code IN ({placeholders})")
        values.extend(code.upper() for code in parameter_codes)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    connection = sqlite3.connect(Path(db_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            f"""
            SELECT o.observation_id, o.station_index, s.station_name,
                   o.observed_at, o.parameter_code,
                   o.value AS original_value,
                   o.text_value AS original_text_value,
                   COALESCE(c.corrected_value, o.value) AS value,
                   o.text_value, o.unit,
                   CASE WHEN c.correction_id IS NULL
                        THEN o.quality_status ELSE 'CORRECTED' END
                       AS quality_status,
                   CASE WHEN c.correction_id IS NULL
                        THEN o.quality_message
                        ELSE 'Ручна правка: ' || c.reason END
                       AS quality_message,
                   o.quality_status AS original_quality_status,
                   o.quality_message AS original_quality_message,
                   o.source_type, o.source_file, o.raw_record, o.import_id,
                   i.source_name, i.message_type, i.bulletin_date, i.raw_path,
                   i.source_hash, c.correction_id, c.corrected_value,
                   c.reason AS correction_reason,
                   c.hydrologist AS corrected_by, c.created_at AS corrected_at
            FROM observations AS o
            JOIN stations AS s ON s.station_index = o.station_index
            JOIN imports AS i ON i.import_id = o.import_id
            LEFT JOIN corrections AS c
              ON c.observation_id = o.observation_id AND c.is_active = 1
            {where_sql}
            ORDER BY o.observed_at, o.station_index, o.parameter_code
            """,
            values,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


CORRECTABLE_PARAMETERS = {
    "WATER_LEVEL": (-1000.0, 10000.0),
    "DAILY_CHANGE": (-5000.0, 5000.0),
}


def create_correction(
    db_path: Path,
    observation_id: int,
    corrected_value: float,
    *,
    reason: str,
    hydrologist: str,
) -> CorrectionResult:
    """Створює одну активну правку, не змінюючи первинного значення."""

    clean_reason = reason.strip()
    clean_hydrologist = hydrologist.strip()
    value = float(corrected_value)
    if not clean_reason:
        raise ValueError("Для ручної правки потрібно вказати причину.")
    if not clean_hydrologist:
        raise ValueError("Для ручної правки потрібно вказати автора.")
    if not math.isfinite(value):
        raise ValueError("Виправлене значення має бути скінченним числом.")
    if not value.is_integer():
        raise ValueError("Рівень і добову зміну потрібно вказувати цілими сантиметрами.")

    connection = sqlite3.connect(Path(db_path))
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        row = connection.execute(
            """
            SELECT parameter_code, value
            FROM observations
            WHERE observation_id = ?
            """,
            (int(observation_id),),
        ).fetchone()
        if row is None:
            raise ValueError(f"Спостереження {observation_id} не знайдено.")

        parameter_code = str(row[0])
        original_value = row[1]
        if parameter_code not in CORRECTABLE_PARAMETERS:
            raise ValueError(
                "Ручні правки дозволено лише для рівня та добової зміни."
            )
        if original_value is None:
            raise ValueError("Відсутнє первинне значення не можна виправити.")

        minimum, maximum = CORRECTABLE_PARAMETERS[parameter_code]
        if value < minimum or value > maximum:
            raise ValueError(
                f"Виправлене значення виходить за допустимий інтервал "
                f"{minimum:g}…{maximum:g}."
            )
        if abs(value - float(original_value)) <= 1e-9:
            raise ValueError("Виправлене значення збігається з первинним.")

        active = connection.execute(
            """
            SELECT correction_id
            FROM corrections
            WHERE observation_id = ? AND is_active = 1
            """,
            (int(observation_id),),
        ).fetchone()
        if active is not None:
            raise ValueError(
                "Для цього спостереження вже є активна правка. "
                "Спочатку її потрібно скасувати."
            )

        cursor = connection.execute(
            """
            INSERT INTO corrections(
                observation_id, original_value, corrected_value,
                reason, hydrologist
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(observation_id),
                float(original_value),
                value,
                clean_reason,
                clean_hydrologist,
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Не вдалося отримати ID ручної правки.")
        connection.commit()
        return CorrectionResult(int(cursor.lastrowid), int(observation_id), True)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def cancel_correction(
    db_path: Path,
    correction_id: int,
    *,
    hydrologist: str,
    reason: str,
) -> CorrectionResult:
    """Скасовує активну правку, зберігаючи її повну історію."""

    clean_hydrologist = hydrologist.strip()
    clean_reason = reason.strip()
    if not clean_hydrologist:
        raise ValueError("Для скасування правки потрібно вказати автора.")
    if not clean_reason:
        raise ValueError("Для скасування правки потрібно вказати причину.")

    connection = sqlite3.connect(Path(db_path))
    try:
        row = connection.execute(
            """
            SELECT observation_id, is_active
            FROM corrections
            WHERE correction_id = ?
            """,
            (int(correction_id),),
        ).fetchone()
        if row is None:
            raise ValueError(f"Правку {correction_id} не знайдено.")
        if int(row[1]) != 1:
            raise ValueError("Цю правку вже скасовано.")

        connection.execute(
            """
            UPDATE corrections
            SET is_active = 0,
                cancelled_at = CURRENT_TIMESTAMP,
                cancelled_by = ?,
                cancellation_reason = ?
            WHERE correction_id = ? AND is_active = 1
            """,
            (clean_hydrologist, clean_reason, int(correction_id)),
        )
        connection.commit()
        return CorrectionResult(int(correction_id), int(row[0]), False)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def read_corrections(
    db_path: Path,
    *,
    observation_id: int | None = None,
    active_only: bool = False,
) -> list[DatabaseRow]:
    """Повертає історію правок для аудиту або Панелі рівнів."""

    clauses: list[str] = []
    values: list[DatabaseValue] = []
    if observation_id is not None:
        clauses.append("c.observation_id = ?")
        values.append(int(observation_id))
    if active_only:
        clauses.append("c.is_active = 1")
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    connection = sqlite3.connect(Path(db_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            f"""
            SELECT c.correction_id, c.observation_id, o.station_index,
                   s.station_name, o.observed_at, o.parameter_code,
                   c.original_value, c.corrected_value, c.reason,
                   c.hydrologist, c.is_active, c.created_at,
                   c.cancelled_at, c.cancelled_by, c.cancellation_reason
            FROM corrections AS c
            JOIN observations AS o ON o.observation_id = c.observation_id
            JOIN stations AS s ON s.station_index = o.station_index
            {where_sql}
            ORDER BY c.created_at, c.correction_id
            """,
            values,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _validated_extreme_levels(
    maximum_level: int,
    average_level: int,
    minimum_level: int,
) -> tuple[int, int, int]:
    values = (int(maximum_level), int(average_level), int(minimum_level))
    maximum, average, minimum = values
    if not minimum <= average <= maximum:
        raise ValueError(
            "Має виконуватися умова: мінімальний ≤ середній ≤ максимальний."
        )
    return maximum, average, minimum


def seed_reference_extreme(
    db_path: Path,
    *,
    station_index: str,
    maximum_level: int,
    average_level: int,
    minimum_level: int,
    source: str = "template",
) -> bool:
    """Додає відсутній довідниковий запис, не перезаписуючи правки."""

    maximum, average, minimum = _validated_extreme_levels(
        maximum_level,
        average_level,
        minimum_level,
    )
    connection = sqlite3.connect(Path(db_path))
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO reference_extremes(
                station_index, maximum_level, average_level,
                minimum_level, source
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (station_index, maximum, average, minimum, source.strip() or "template"),
        )
        connection.commit()
        return cursor.rowcount == 1
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def upsert_reference_extreme(
    db_path: Path,
    *,
    station_index: str,
    maximum_level: int,
    average_level: int,
    minimum_level: int,
    maximum_date: str = "",
    minimum_date: str = "",
    updated_by: str,
) -> None:
    """Перевіряє та зберігає багаторічні рівні одного поста."""

    author = updated_by.strip()
    if not author:
        raise ValueError("Потрібно вказати автора зміни довідника.")
    maximum, average, minimum = _validated_extreme_levels(
        maximum_level,
        average_level,
        minimum_level,
    )
    connection = sqlite3.connect(Path(db_path))
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO reference_extremes(
                station_index, maximum_level, maximum_date,
                average_level, minimum_level, minimum_date,
                source, updated_by
            ) VALUES (?, ?, ?, ?, ?, ?, 'manual', ?)
            ON CONFLICT(station_index) DO UPDATE SET
                maximum_level = excluded.maximum_level,
                maximum_date = excluded.maximum_date,
                average_level = excluded.average_level,
                minimum_level = excluded.minimum_level,
                minimum_date = excluded.minimum_date,
                source = 'manual',
                updated_by = excluded.updated_by,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                station_index,
                maximum,
                maximum_date.strip(),
                average,
                minimum,
                minimum_date.strip(),
                author,
            ),
        )
        connection.commit()
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise ValueError(f"Гідропост {station_index} відсутній у довіднику.") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def read_reference_extremes(
    db_path: Path,
    station_indexes: Sequence[str] = (),
) -> dict[str, DatabaseRow]:
    """Читає довідник екстремумів, за потреби лише для вибраних постів."""

    values: list[DatabaseValue] = []
    where_sql = ""
    if station_indexes:
        placeholders = ", ".join("?" for _ in station_indexes)
        where_sql = f"WHERE e.station_index IN ({placeholders})"
        values.extend(station_indexes)

    connection = sqlite3.connect(Path(db_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            f"""
            SELECT e.station_index, s.station_name, e.maximum_level,
                   e.maximum_date, e.average_level, e.minimum_level,
                   e.minimum_date, e.source, e.updated_by, e.updated_at
            FROM reference_extremes AS e
            JOIN stations AS s ON s.station_index = e.station_index
            {where_sql}
            ORDER BY e.station_index
            """,
            values,
        ).fetchall()
        return {str(row["station_index"]): dict(row) for row in rows}
    finally:
        connection.close()


def register_product(
    db_path: Path,
    *,
    product_type: str,
    region_key: str,
    bulletin_date: str,
    output_path: str,
    observation_ids: Iterable[int],
    metadata: Mapping[str, object] | None = None,
) -> ProductResult:
    """Фіксує продукт і зв'язки ``product → observation → import → raw``."""

    normalized_ids = sorted({int(item) for item in observation_ids})
    metadata_json = json.dumps(
        dict(metadata or {}),
        ensure_ascii=False,
        sort_keys=True,
    )
    connection = sqlite3.connect(Path(db_path))
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        cursor = connection.execute(
            """
            INSERT INTO products(
                product_type, region_key, bulletin_date, output_path,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(product_type, region_key, bulletin_date, output_path)
            DO UPDATE SET
                metadata_json = excluded.metadata_json,
                created_at = CURRENT_TIMESTAMP
            """,
            (
                product_type.upper(),
                region_key,
                bulletin_date,
                output_path,
                metadata_json,
            ),
        )
        if cursor.lastrowid:
            product_id = int(cursor.lastrowid)
        else:
            row = connection.execute(
                """
                SELECT product_id
                FROM products
                WHERE product_type = ? AND region_key = ?
                  AND bulletin_date = ? AND output_path = ?
                """,
                (
                    product_type.upper(),
                    region_key,
                    bulletin_date,
                    output_path,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("Не вдалося отримати ID створеного продукту.")
            product_id = int(row[0])

        connection.execute(
            "DELETE FROM product_observations WHERE product_id = ?",
            (product_id,),
        )
        for observation_id in normalized_ids:
            snapshot = connection.execute(
                """
                SELECT o.value, o.text_value, c.correction_id,
                       c.corrected_value
                FROM observations AS o
                LEFT JOIN corrections AS c
                  ON c.observation_id = o.observation_id AND c.is_active = 1
                WHERE o.observation_id = ?
                """,
                (observation_id,),
            ).fetchone()
            if snapshot is None:
                raise ValueError(
                    f"Спостереження {observation_id} для provenance не знайдено."
                )
            original_value, text_value, correction_id, corrected_value = snapshot
            effective_value = (
                corrected_value if correction_id is not None else original_value
            )
            connection.execute(
                """
                INSERT INTO product_observations(
                    product_id, observation_id, correction_id,
                    effective_value, effective_text_value
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    product_id,
                    observation_id,
                    correction_id,
                    effective_value,
                    str(text_value or ""),
                ),
            )
        connection.commit()
        return ProductResult(product_id, len(normalized_ids))
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def read_product_provenance(
    db_path: Path,
    product_id: int,
) -> list[DatabaseRow]:
    """Повертає повний ланцюг походження значень одного продукту."""

    connection = sqlite3.connect(Path(db_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT p.product_id, p.product_type, p.region_key,
                   p.bulletin_date, p.output_path, o.observation_id,
                   o.station_index, o.observed_at, o.parameter_code,
                   o.value AS original_value,
                   o.text_value AS original_text_value,
                   po.effective_value AS value,
                   po.effective_text_value AS text_value,
                   o.quality_status, o.quality_message,
                   i.import_id, i.source_name, i.source_type,
                   i.message_type, i.raw_path, i.source_hash,
                   po.correction_id, c.corrected_value,
                   c.reason AS correction_reason,
                   c.hydrologist AS corrected_by,
                   c.created_at AS corrected_at,
                   c.is_active AS correction_currently_active
            FROM products AS p
            JOIN product_observations AS po ON po.product_id = p.product_id
            JOIN observations AS o ON o.observation_id = po.observation_id
            JOIN imports AS i ON i.import_id = o.import_id
            LEFT JOIN corrections AS c ON c.correction_id = po.correction_id
            WHERE p.product_id = ?
            ORDER BY o.station_index, o.observed_at, o.parameter_code
            """,
            (product_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _iso_date(date_text: str) -> str:
    try:
        return datetime.strptime(date_text, "%d.%m.%Y").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Дата має бути у форматі ДД.ММ.РРРР.") from exc
