"""Перевірки окремого імпорту опадів SYNOP."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from hydrobulletin.meteorology import (
    SynopRecord,
    daily_precipitation,
    decode_meteo_precipitation,
    decode_synop_precip_amount,
    load_precipitation_mapping,
    parse_synop_records,
    synop_precip_period_hours,
)
from hydrobulletin.stations import METEO_STATIONS


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEMO_SYNOP = PROJECT_DIR / "demo_data" / "week3" / "12.07.2026_SYNOP.txt"


class SynopParserTests(unittest.TestCase):
    def test_parses_demo_records(self) -> None:
        records = parse_synop_records(DEMO_SYNOP.read_text(encoding="utf-8"))

        self.assertEqual(len(records), 20)
        self.assertEqual(records[0].station_index, "33288")
        self.assertEqual(records[0].observed_at_utc, datetime(2026, 7, 11, 18))
        self.assertIn("60012", records[0].groups)

    def test_amount_codes_and_period(self) -> None:
        self.assertEqual(decode_synop_precip_amount(990), 0.0)
        self.assertEqual(decode_synop_precip_amount(995), 0.5)
        self.assertEqual(decode_synop_precip_amount(12), 12.0)
        self.assertEqual(synop_precip_period_hours("60011"), 6)
        self.assertEqual(synop_precip_period_hours("60022"), 12)
        self.assertIsNone(synop_precip_period_hours("10022"))

    def test_daily_total_prefers_two_twelve_hour_values(self) -> None:
        records = [
            SynopRecord(
                "SM",
                "Тест",
                datetime(2026, 7, 11, 18),
                "33288",
                ("33288", "555", "60042"),
                "33288 555 60042",
            ),
            SynopRecord(
                "SM",
                "Тест",
                datetime(2026, 7, 12, 6),
                "33288",
                ("33288", "555", "60022"),
                "33288 555 60022",
            ),
        ]

        self.assertEqual(daily_precipitation(records, "12.07.2026"), 6.0)

    def test_daily_total_falls_back_to_six_hour_values(self) -> None:
        records = [
            SynopRecord(
                "SM",
                "Тест",
                observed_at,
                "33288",
                ("33288", "555", group),
                f"33288 555 {group}",
            )
            for observed_at, group in (
                (datetime(2026, 7, 11, 12), "60011"),
                (datetime(2026, 7, 11, 18), "60021"),
                (datetime(2026, 7, 12, 0), "60031"),
                (datetime(2026, 7, 12, 6), "60041"),
            )
        ]

        self.assertEqual(daily_precipitation(records, "12.07.2026"), 10.0)

    def test_decodes_all_demo_stations(self) -> None:
        observations = decode_meteo_precipitation(
            DEMO_SYNOP.read_text(encoding="utf-8"),
            "12.07.2026",
            {station.index: station for station in METEO_STATIONS},
            source_type="local",
            source_file="demo.txt",
        )
        by_index = {item.index: item for item in observations}

        self.assertEqual(len(by_index), 10)
        self.assertEqual(by_index["33288"].precipitation_mm, 3.0)
        self.assertEqual(by_index["33536"].precipitation_mm, 15.0)
        self.assertEqual(by_index["33557"].precipitation_mm, 0.0)


class MappingTests(unittest.TestCase):
    def test_loads_valid_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "mapping.json"
            path.write_text(
                json.dumps({"79726": "33288"}),
                encoding="utf-8",
            )
            self.assertEqual(
                load_precipitation_mapping(path),
                {"79726": "33288"},
            )

    def test_rejects_invalid_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "mapping.json"
            path.write_text('{"bad": "33288"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_precipitation_mapping(path)


if __name__ == "__main__":
    unittest.main()
