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
    synop_precipitation_indicator,
)
from hydrobulletin.stations import METEO_STATIONS
from hydrobulletin.timeutils import ukraine_local_to_utc, ukraine_utc_offset_hours


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEMO_SYNOP = (
    PROJECT_DIR / "demo_data" / "regression" / "12.07.2026_SYNOP.txt"
)


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

    def test_kyiv_timezone_conversion_for_winter_and_summer(self) -> None:
        winter = datetime(2026, 1, 15, 9)
        summer = datetime(2026, 7, 15, 9)

        self.assertEqual(ukraine_utc_offset_hours(winter), 2)
        self.assertEqual(ukraine_utc_offset_hours(summer), 3)
        self.assertEqual(ukraine_local_to_utc(winter), datetime(2026, 1, 15, 7))
        self.assertEqual(ukraine_local_to_utc(summer), datetime(2026, 7, 15, 6))

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

    def test_ir_three_supplies_zero_for_an_omitted_half_day_group(self) -> None:
        records = [
            SynopRecord(
                "SM",
                "Тест",
                datetime(2026, 8, 7, 18),
                "33288",
                (
                    "33288",
                    "12697",
                    "63303",
                    "10196",
                    "20161",
                    "40182",
                    "52014",
                    "60062",
                ),
                "33288 12697 63303 10196 20161 40182 52014 60062",
            ),
            SynopRecord(
                "SM",
                "Тест",
                datetime(2026, 8, 8, 6),
                "33288",
                ("33288", "32998", "50000"),
                "33288 32998 50000",
            ),
        ]

        self.assertEqual(synop_precipitation_indicator(records[1]), 3)
        self.assertEqual(daily_precipitation(records, "08.08.2026"), 6.0)

    def test_ir_four_keeps_an_incomplete_daily_total_missing(self) -> None:
        records = [
            SynopRecord(
                "SM",
                "Тест",
                datetime(2026, 8, 7, 18),
                "33288",
                (
                    "33288",
                    "12697",
                    "63303",
                    "10196",
                    "20161",
                    "40182",
                    "52014",
                    "60062",
                ),
                "33288 12697 63303 10196 20161 40182 52014 60062",
            ),
            SynopRecord(
                "SM",
                "Тест",
                datetime(2026, 8, 8, 6),
                "33288",
                ("33288", "42998", "50000"),
                "33288 42998 50000",
            ),
        ]

        self.assertEqual(synop_precipitation_indicator(records[1]), 4)
        self.assertIsNone(daily_precipitation(records, "08.08.2026"))

    def test_incomplete_six_hour_fallback_is_not_reported_as_daily(self) -> None:
        records = [
            SynopRecord(
                "SM",
                "Тест",
                datetime(2026, 8, 7, 12),
                "33288",
                ("33288", "11111", "60011"),
                "33288 11111 60011",
            ),
            SynopRecord(
                "SM",
                "Тест",
                datetime(2026, 8, 7, 18),
                "33288",
                ("33288", "41111", "50000"),
                "33288 41111 50000",
            ),
        ]

        self.assertIsNone(daily_precipitation(records, "08.08.2026"))

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
