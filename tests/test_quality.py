"""Перевірки початкових статусів контролю якості."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from hydrobulletin.archive import (
    archive_summary,
    import_observations,
    initialize_archive,
    query_observations,
)
from hydrobulletin.models import HydroObservation, Station
from hydrobulletin.quality import (
    INCONSISTENT_CHANGE,
    MISSING,
    NOT_CHECKED,
    OUT_OF_RANGE,
    SUSPICIOUS,
    VALID,
    evaluate_value,
    quality_status_label,
    run_initial_quality_control,
    worst_quality_status,
)


STATION = Station("81015", "Дністер — Стрілки")


def observation(
    when: datetime,
    *,
    level: int | None,
    change: int | None,
    quality_status: str = "NOT_CHECKED",
) -> HydroObservation:
    return HydroObservation(
        index=STATION.index,
        station_name=STATION.name,
        level=level,
        change=change,
        evening_level=None,
        raw_record="test",
        quality_status=quality_status,
        observed_at=when,
        source_type="test",
        source_file="test.txt",
    )


class ValueRulesTests(unittest.TestCase):
    def test_quality_statuses_have_ukrainian_display_labels(self) -> None:
        expected = {
            VALID: "Без зауважень",
            MISSING: "Дані відсутні",
            SUSPICIOUS: "Підозріле значення",
            OUT_OF_RANGE: "Поза діапазоном",
            INCONSISTENT_CHANGE: "Неузгоджена зміна",
            "CORRECTED": "Виправлено",
            NOT_CHECKED: "Не перевірено",
        }
        for status, label in expected.items():
            with self.subTest(status=status):
                self.assertEqual(quality_status_label(status), label)

    def test_unknown_quality_status_is_not_lost(self) -> None:
        self.assertEqual(quality_status_label("NEW_STATUS"), "NEW_STATUS")

    def test_negative_level_can_be_valid(self) -> None:
        self.assertEqual(evaluate_value("WATER_LEVEL", -3.0), (VALID, ""))

    def test_suspicious_and_out_of_range(self) -> None:
        self.assertEqual(
            evaluate_value("PRECIPITATION", 150.0)[0],
            SUSPICIOUS,
        )
        self.assertEqual(
            evaluate_value("WATER_TEMPERATURE", 80.0)[0],
            OUT_OF_RANGE,
        )

    def test_missing(self) -> None:
        self.assertEqual(evaluate_value("WATER_LEVEL", None)[0], MISSING)

    def test_worst_status(self) -> None:
        self.assertEqual(
            worst_quality_status((VALID, SUSPICIOUS, INCONSISTENT_CHANGE)),
            INCONSISTENT_CHANGE,
        )


class QualityIntegrationTests(unittest.TestCase):
    def test_detects_inconsistent_daily_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "archive.sqlite"
            initialize_archive(db_path, (STATION,))

            first = observation(
                datetime(2026, 7, 11, 8),
                level=100,
                change=None,
            )
            second = observation(
                datetime(2026, 7, 12, 8),
                level=106,
                change=5,
            )
            for item, date in (
                (first, "11.07.2026"),
                (second, "12.07.2026"),
            ):
                import_observations(
                    db_path,
                    (item,),
                    source_name="same-source",
                    source_type="test",
                    message_type="ZRUR52",
                    bulletin_date=date,
                    raw_path=f"{date}.txt",
                    raw_text="однаковий вміст",
                )

            summary = run_initial_quality_control(db_path, "12.07.2026")
            rows = query_observations(
                db_path,
                start_date="12.07.2026",
                end_date="12.07.2026",
                parameter_codes=("DAILY_CHANGE",),
            )

            self.assertEqual(summary.inconsistent_changes, 1)
            self.assertEqual(rows[0]["quality_status"], INCONSISTENT_CHANGE)
            quality_message = rows[0]["quality_message"]
            self.assertIsInstance(quality_message, str)
            assert isinstance(quality_message, str)
            self.assertIn("різниця рівнів", quality_message)
            self.assertEqual(archive_summary(db_path)["imports"], 2)

    def test_nil_level_remains_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "archive.sqlite"
            initialize_archive(db_path, (STATION,))
            item = observation(
                datetime(2026, 7, 12, 8),
                level=None,
                change=None,
                quality_status=MISSING,
            )
            import_observations(
                db_path,
                (item,),
                source_name="nil",
                source_type="test",
                message_type="ZRUR52",
                bulletin_date="12.07.2026",
                raw_path="nil.txt",
                raw_text="NIL",
            )

            summary = run_initial_quality_control(db_path, "12.07.2026")

            self.assertEqual(summary.counts[MISSING], 1)

    def test_change_without_previous_level_is_not_marked_suspicious(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "archive.sqlite"
            initialize_archive(db_path, (STATION,))
            item = observation(
                datetime(2026, 7, 12, 8),
                level=106,
                change=5,
            )
            import_observations(
                db_path,
                (item,),
                source_name="one-day",
                source_type="test",
                message_type="ZRUR52",
                bulletin_date="12.07.2026",
                raw_path="one-day.txt",
                raw_text="однодобовий запис",
            )

            summary = run_initial_quality_control(db_path, "12.07.2026")
            rows = query_observations(
                db_path,
                start_date="12.07.2026",
                end_date="12.07.2026",
                parameter_codes=("DAILY_CHANGE",),
            )

            self.assertEqual(summary.counts[NOT_CHECKED], 1)
            self.assertEqual(rows[0]["quality_status"], NOT_CHECKED)
            quality_message = rows[0]["quality_message"]
            self.assertIsInstance(quality_message, str)
            assert isinstance(quality_message, str)
            self.assertIn("не звірено", quality_message)


if __name__ == "__main__":
    unittest.main()
