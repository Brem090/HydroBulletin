"""Початкова SQLite-схема майбутнього локального архіву HydroBulletin."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from .models import Station

SCHEMA_VERSION = "1"

SCHEMA_SQL = """
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
    source_hash TEXT NOT NULL UNIQUE,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_index TEXT NOT NULL,
    observation_at TEXT NOT NULL,
    observation_kind TEXT NOT NULL,
    level_cm INTEGER,
    daily_change_cm INTEGER,
    quality_status TEXT NOT NULL DEFAULT 'NOT_CHECKED',
    import_id INTEGER,
    FOREIGN KEY (station_index) REFERENCES stations(station_index),
    FOREIGN KEY (import_id) REFERENCES imports(import_id),
    UNIQUE (station_index, observation_at, observation_kind)
);
"""


def initialize_archive(db_path: Path, stations: Iterable[Station]) -> Path:
    """Створює порожню SQLite-базу та записує довідник постів."""

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(db_path)

    try:
        connection.executescript(SCHEMA_SQL)
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
            ("schema_version", SCHEMA_VERSION),
        )
        connection.executemany(
            "INSERT OR REPLACE INTO stations(station_index, station_name) VALUES (?, ?)",
            ((station.index, station.name) for station in stations),
        )
        connection.commit()
    finally:
        connection.close()

    return db_path


def archive_summary(db_path: Path) -> dict[str, int]:
    """Повертає кількість записів у головних таблицях архіву."""

    db_path = Path(db_path)

    if not db_path.exists():
        raise FileNotFoundError(f"База архіву не знайдена: {db_path}")

    result: dict[str, int] = {}
    connection = sqlite3.connect(db_path)

    try:
        for table_name in ("stations", "imports", "observations"):
            row = connection.execute(
                f"SELECT COUNT(*) FROM {table_name}"
            ).fetchone()
            result[table_name] = int(row[0]) if row else 0
    finally:
        connection.close()

    return result
