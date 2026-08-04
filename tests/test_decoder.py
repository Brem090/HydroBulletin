"""Перевірки базового декодера та створення локального архіву."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hydrobulletin.archive import archive_summary, initialize_archive
from hydrobulletin.decoder import (
    decode_codes,
    decode_station_record,
    parse_change,
    parse_evening_level,
    parse_level,
)
from hydrobulletin.models import Station
from hydrobulletin.sources import LocalFileSource, OnlineSourceSettings
from hydrobulletin.stations import LVIV_STATIONS, STATIONS_BY_INDEX


class LevelTests(unittest.TestCase):
    def test_positive_level(self) -> None:
        self.assertEqual(parse_level("10214"), 214)

    def test_negative_level(self) -> None:
        self.assertEqual(parse_level("15003"), -3)

    def test_invalid_group(self) -> None:
        self.assertIsNone(parse_level("30214"))


class EveningLevelTests(unittest.TestCase):
    def test_positive_level(self) -> None:
        self.assertEqual(parse_evening_level("30208"), 208)

    def test_negative_level(self) -> None:
        self.assertEqual(parse_evening_level("35003"), -3)

    def test_invalid_group(self) -> None:
        self.assertIsNone(parse_evening_level("10208"))


class ChangeTests(unittest.TestCase):
    def test_rise(self) -> None:
        self.assertEqual(parse_change("20061"), 6)

    def test_fall(self) -> None:
        self.assertEqual(parse_change("20042"), -4)

    def test_no_change(self) -> None:
        self.assertEqual(parse_change("20000"), 0)

    def test_invalid_sign(self) -> None:
        self.assertIsNone(parse_change("20069"))


class StationRecordTests(unittest.TestCase):
    def test_full_record(self) -> None:
        station = Station("81017", "Дністер — Самбір")
        result = decode_station_record("81017 12081 10214 20061 30208", station)

        self.assertEqual(result.level, 214)
        self.assertEqual(result.change, 6)
        self.assertEqual(result.evening_level, 208)
        self.assertEqual(result.quality_status, "OK")

    def test_nil(self) -> None:
        station = Station("81080", "Стрв’яж — Луки")
        result = decode_station_record("81080 12081 NIL", station)

        self.assertIsNone(result.level)
        self.assertIsNone(result.change)
        self.assertIsNone(result.evening_level)
        self.assertEqual(result.quality_status, "MISSING")


class DecodeCodesTests(unittest.TestCase):
    def test_filters_date(self) -> None:
        raw_text = """
81015 12081 10186 20031 30180 =
81017 11081 10214 20061 30208 =
"""
        results = decode_codes(raw_text, "12.07.2026", STATIONS_BY_INDEX)
        self.assertEqual([item.index for item in results], ["81015"])

    def test_known_station(self) -> None:
        results = decode_codes(
            "81017 12081 10214 20061 30208 =",
            "12.07.2026",
            STATIONS_BY_INDEX,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].station_name, "Дністер — Самбір")
        self.assertEqual(results[0].level_text, "214 см")
        self.assertEqual(results[0].change_text, "+6 см")
        self.assertEqual(results[0].evening_level_text, "208 см")


class LocalFileSourceTests(unittest.TestCase):
    def test_reads_utf8_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sample.txt"
            path.write_text("тестові дані", encoding="utf-8")
            self.assertEqual(LocalFileSource(path).load_text(), "тестові дані")

    def test_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "missing.txt"
            with self.assertRaises(FileNotFoundError):
                LocalFileSource(path).load_text()

    def test_env_setting_names_are_safe(self) -> None:
        settings = OnlineSourceSettings()
        self.assertEqual(settings.url_variable, "HYDRO_SOURCE_URL")
        self.assertEqual(settings.password_variable, "HYDRO_SOURCE_PASSWORD")


class ArchiveTests(unittest.TestCase):
    def test_creates_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "hydro_archive.sqlite"
            initialize_archive(db_path, LVIV_STATIONS)
            summary = archive_summary(db_path)

            self.assertTrue(db_path.exists())
            self.assertEqual(summary["stations"], 31)
            self.assertEqual(summary["observations"], 0)

    def test_reinitialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "hydro_archive.sqlite"
            initialize_archive(db_path, LVIV_STATIONS)
            initialize_archive(db_path, LVIV_STATIONS)
            self.assertEqual(archive_summary(db_path)["stations"], 31)


if __name__ == "__main__":
    unittest.main()
