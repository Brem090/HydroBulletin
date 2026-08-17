"""Перевірки raw-архіву, SQLite та конвеєра імпорту."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from hydrobulletin.archive import (
    archive_raw_text,
    archive_summary,
    import_observations,
    initialize_archive,
    read_observations,
)
from hydrobulletin.models import HydroObservation
from hydrobulletin.pipeline import PipelineResult, run_import_pipeline
from hydrobulletin.sources import LocalFileSource
from hydrobulletin.stations import LVIV_STATIONS, STATIONS_BY_INDEX


SAMPLE = "81015 12081 10186 20031 30180 41900 81234 00081 =\n"


class RawArchiveTests(unittest.TestCase):
    def test_path_and_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "raw"
            path = archive_raw_text(root, "12.07.2026", "ZRUR52", SAMPLE)

            self.assertEqual(
                path.relative_to(root).as_posix(),
                "2026/07/2026-07-12_ZRUR52.txt",
            )
            self.assertEqual(path.read_text(encoding="utf-8"), SAMPLE)

    def test_same_content_reuses_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "raw"
            first = archive_raw_text(root, "12.07.2026", "ZRUR52", SAMPLE)
            second = archive_raw_text(root, "12.07.2026", "ZRUR52", SAMPLE)
            self.assertEqual(first, second)

    def test_changed_content_creates_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "raw"
            first = archive_raw_text(root, "12.07.2026", "ZRUR52", SAMPLE)
            second = archive_raw_text(
                root,
                "12.07.2026",
                "ZRUR52",
                SAMPLE + "оновлено\n",
            )

            self.assertNotEqual(first, second)
            self.assertTrue(second.name.endswith("_2.txt"))
            self.assertEqual(first.read_text(encoding="utf-8"), SAMPLE)


class SchemaTests(unittest.TestCase):
    def test_schema_v4(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "archive.sqlite"
            initialize_archive(db_path, LVIV_STATIONS)
            connection = sqlite3.connect(db_path)
            try:
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(observations)")
                }
                version = connection.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'"
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(version, "4")
            self.assertTrue(
                {
                    "observed_at",
                    "parameter_code",
                    "value",
                    "text_value",
                    "quality_status",
                    "quality_message",
                    "source_file",
                }
                <= columns
            )

    def test_migrates_v1_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "archive.sqlite"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE stations (
                    station_index TEXT PRIMARY KEY,
                    station_name TEXT NOT NULL
                );
                CREATE TABLE imports (
                    import_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_name TEXT NOT NULL,
                    source_hash TEXT NOT NULL UNIQUE,
                    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE observations (
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    station_index TEXT NOT NULL,
                    observation_at TEXT NOT NULL,
                    observation_kind TEXT NOT NULL,
                    level_cm INTEGER,
                    daily_change_cm INTEGER,
                    quality_status TEXT NOT NULL DEFAULT 'NOT_CHECKED',
                    import_id INTEGER,
                    UNIQUE (station_index, observation_at, observation_kind)
                );
                """
            )
            connection.commit()
            connection.close()

            initialize_archive(db_path, LVIV_STATIONS)

            connection = sqlite3.connect(db_path)
            try:
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(observations)")
                }
            finally:
                connection.close()
            self.assertIn("parameter_code", columns)
            self.assertNotIn("level_cm", columns)


class PipelineTests(unittest.TestCase):
    def _run_pipeline(self, folder: Path, text: str = SAMPLE) -> PipelineResult:
        source_path = folder / "source.txt"
        source_path.write_text(text, encoding="utf-8")
        return run_import_pipeline(
            LocalFileSource(source_path),
            bulletin_date="12.07.2026",
            message_type="ZRUR52",
            raw_root=folder / "archive" / "raw",
            db_path=folder / "archive" / "database" / "archive.sqlite",
            stations=LVIV_STATIONS,
            stations_by_index=STATIONS_BY_INDEX,
            source_type="local",
            source_name=str(source_path),
        )

    def test_end_to_end_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            folder = Path(tmp_dir)
            result = self._run_pipeline(folder)
            rows = read_observations(folder / "archive" / "database" / "archive.sqlite")

            self.assertTrue(result.raw_path.exists())
            self.assertEqual(len(result.observations), 1)
            self.assertEqual(result.import_result.inserted_observations, 6)
            self.assertEqual(len(rows), 6)
            self.assertTrue(all(row["source_type"] == "local" for row in rows))
            self.assertTrue(
                all(str(row["source_file"]).startswith("raw/2026/07/") for row in rows)
            )
            change = next(
                row for row in rows if row["parameter_code"] == "DAILY_CHANGE"
            )
            self.assertEqual(change["quality_status"], "SUSPICIOUS")
            self.assertIn("попередньої доби", change["quality_message"])
            level_times = {
                row["observed_at"]
                for row in rows
                if row["parameter_code"] == "WATER_LEVEL"
            }
            self.assertEqual(
                level_times,
                {"2026-07-11T20:00:00", "2026-07-12T08:00:00"},
            )

    def test_same_file_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            folder = Path(tmp_dir)
            first = self._run_pipeline(folder)
            second = self._run_pipeline(folder)
            db_path = folder / "archive" / "database" / "archive.sqlite"
            summary = archive_summary(db_path)

            self.assertFalse(first.import_result.duplicate_file)
            self.assertTrue(second.import_result.duplicate_file)
            self.assertEqual(second.import_result.inserted_observations, 0)
            self.assertEqual(second.import_result.duplicate_observations, 6)
            self.assertEqual(summary["imports"], 1)
            self.assertEqual(summary["observations"], 6)

    def test_duplicate_measurements_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            folder = Path(tmp_dir)
            self._run_pipeline(folder)
            second = self._run_pipeline(folder, "СЛУЖБОВИЙ ЗАГОЛОВОК\n" + SAMPLE)
            db_path = folder / "archive" / "database" / "archive.sqlite"
            summary = archive_summary(db_path)

            self.assertFalse(second.import_result.duplicate_file)
            self.assertEqual(second.import_result.inserted_observations, 0)
            self.assertEqual(second.import_result.duplicate_observations, 6)
            self.assertEqual(summary["imports"], 2)
            self.assertEqual(summary["observations"], 6)

    def test_reimport_can_add_missing_parameter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "archive.sqlite"
            initialize_archive(db_path, LVIV_STATIONS)
            base = HydroObservation(
                index="81015",
                station_name="Дністер — Стрілки",
                level=47,
                change=None,
                evening_level=None,
                raw_record=SAMPLE,
                quality_status="OK",
                observed_at=datetime(2026, 7, 19, 8),
                source_type="online",
                source_file="raw/2026/07/2026-07-19_ZRUR52.txt",
            )
            enriched = replace(base, water_temperature_c=20.0)

            first = import_observations(
                db_path,
                [base],
                source_name="ZRUR52",
                source_type="online",
                message_type="ZRUR52",
                bulletin_date="19.07.2026",
                raw_path=base.source_file,
                raw_text=SAMPLE,
            )
            second = import_observations(
                db_path,
                [enriched],
                source_name="ZRUR52",
                source_type="online",
                message_type="ZRUR52",
                bulletin_date="19.07.2026",
                raw_path=base.source_file,
                raw_text=SAMPLE,
            )

            self.assertFalse(first.duplicate_file)
            self.assertTrue(second.duplicate_file)
            self.assertEqual(second.inserted_observations, 1)
            self.assertEqual(archive_summary(db_path)["imports"], 1)
            self.assertEqual(archive_summary(db_path)["observations"], 2)

    def test_nil_record_is_stored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            folder = Path(tmp_dir)
            self._run_pipeline(folder, "81015 12081 NIL =\n")
            rows = read_observations(folder / "archive" / "database" / "archive.sqlite")

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["parameter_code"], "WATER_LEVEL")
            self.assertIsNone(rows[0]["value"])
            self.assertEqual(rows[0]["quality_status"], "MISSING")


if __name__ == "__main__":
    unittest.main()
