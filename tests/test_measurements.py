"""Tests for decoded measurements and observation times."""

from __future__ import annotations

import unittest
from datetime import datetime

from hydrobulletin.decoder import (
    decode_station_record,
    find_precipitation_group,
    parse_discharge,
    parse_observation_datetime,
    parse_precipitation,
    parse_temperature,
)
from hydrobulletin.models import Station, observation_measurements


class DischargeTests(unittest.TestCase):
    def test_three_decimal_places(self) -> None:
        result = parse_discharge("80038")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result, 0.038)

    def test_two_decimal_places(self) -> None:
        result = parse_discharge("81234")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result, 2.34)

    def test_one_decimal_place(self) -> None:
        result = parse_discharge("82125")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result, 12.5)

    def test_large_value(self) -> None:
        self.assertEqual(parse_discharge("84123"), 1230.0)

    def test_invalid_group(self) -> None:
        self.assertIsNone(parse_discharge("71234"))


class TemperatureTests(unittest.TestCase):
    def test_summer_whole_degrees(self) -> None:
        self.assertEqual(parse_temperature("41900", "12.07.2026"), 19.0)

    def test_low_value_in_tenths(self) -> None:
        self.assertEqual(parse_temperature("40800", "12.07.2026"), 0.8)

    def test_high_code_in_tenths(self) -> None:
        self.assertEqual(parse_temperature("43800", "12.07.2026"), 3.8)

    def test_winter_value_in_tenths(self) -> None:
        self.assertEqual(parse_temperature("41900", "12.01.2026"), 1.9)

    def test_group_with_slashes(self) -> None:
        self.assertEqual(parse_temperature("419//", "19.07.2026"), 19.0)


class PrecipitationTests(unittest.TestCase):
    def test_operational_examples(self) -> None:
        self.assertEqual(parse_precipitation("00081"), 8.0)
        self.assertEqual(parse_precipitation("09940"), 0.4)
        self.assertEqual(parse_precipitation("01361"), 136.0)

    def test_missing_and_invalid_values(self) -> None:
        self.assertIsNone(parse_precipitation("00000"))
        self.assertIsNone(parse_precipitation("0994X"))

    def test_group_after_discharge_has_priority(self) -> None:
        self.assertEqual(
            find_precipitation_group(["00081", "81234", "09940"]),
            "09940",
        )


class ObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.station = Station("81017", "Дністер — Самбір")

    def test_observation_time(self) -> None:
        result = parse_observation_datetime("12081", "12.07.2026")
        self.assertEqual(result, datetime(2026, 7, 12, 8, 0))

    def test_full_record(self) -> None:
        result = decode_station_record(
            "81017 12081 10214 20061 30208 419// 81234 09940",
            self.station,
            bulletin_date="12.07.2026",
            source_type="online",
            source_file="raw/2026/07/2026-07-12_ZRUR52.txt",
        )

        self.assertEqual(result.water_temperature_c, 19.0)
        self.assertEqual(result.precipitation_mm, 0.4)
        self.assertEqual(result.discharge_m3_s, 2.34)
        self.assertEqual(result.observed_at, datetime(2026, 7, 12, 8, 0))
        self.assertEqual(result.evening_observed_at, datetime(2026, 7, 11, 20, 0))
        self.assertEqual(result.source_type, "online")

    def test_ignores_extra_observation(self) -> None:
        result = decode_station_record(
            "81017 12081 10214 20061 30208 41900 81234 09940 "
            "90000 42500 89999 01361",
            self.station,
            bulletin_date="12.07.2026",
        )

        self.assertEqual(result.water_temperature_c, 19.0)
        self.assertEqual(result.discharge_m3_s, 2.34)
        self.assertEqual(result.precipitation_mm, 0.4)

    def test_normalized_measurements(self) -> None:
        result = decode_station_record(
            "81017 12081 10214 20061 30208 41900 81234 09940",
            self.station,
            bulletin_date="12.07.2026",
        )
        measurements = observation_measurements(result)

        self.assertEqual(len(measurements), 6)
        level_times = [
            item.observed_at
            for item in measurements
            if item.parameter_code == "WATER_LEVEL"
        ]
        self.assertEqual(
            level_times,
            [datetime(2026, 7, 12, 8, 0), datetime(2026, 7, 11, 20, 0)],
        )


if __name__ == "__main__":
    unittest.main()
