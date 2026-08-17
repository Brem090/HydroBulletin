"""Перевірки схеми даних і оперативних інструментів п'ятого тижня."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from hydrobulletin.archive import (
    cancel_correction,
    create_correction,
    import_observations,
    initialize_archive,
    query_observations,
    read_corrections,
    read_product_provenance,
    read_reference_extremes,
    register_product,
    seed_reference_extreme,
    upsert_reference_extreme,
)
from hydrobulletin.decoder import (
    decode_station_record,
    parse_ice_group,
    parse_ice_thickness,
)
from hydrobulletin.levels import build_level_panel_rows
from hydrobulletin.models import HydroObservation, Station
from hydrobulletin.quality import CORRECTED, VALID, run_initial_quality_control


STATION = Station("81015", "Дністер — Стрілки")


def import_sample(db_path: Path) -> None:
    observation = HydroObservation(
        index=STATION.index,
        station_name=STATION.name,
        level=105,
        change=5,
        evening_level=103,
        raw_record="test",
        observed_at=datetime(2026, 7, 12, 8),
        evening_observed_at=datetime(2026, 7, 11, 20),
        source_type="test",
        source_file="test.txt",
    )
    import_observations(
        db_path,
        (observation,),
        source_name="test",
        source_type="test",
        message_type="ZRUR52",
        bulletin_date="12.07.2026",
        raw_path="test.txt",
        raw_text="test",
    )


class IceGroupsTests(unittest.TestCase):
    def test_decodes_groups_5_and_7(self) -> None:
        self.assertEqual(parse_ice_group("51605"), "Льодохід 50%")
        self.assertEqual(
            parse_ice_group("51316"),
            "Забереги, Льодохід",
        )
        self.assertEqual(parse_ice_thickness("70250"), 25)
        self.assertIsNone(parse_ice_group("59999"))
        self.assertIsNone(parse_ice_thickness("7////"))

    def test_ice_values_are_normalized_and_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "archive.sqlite"
            initialize_archive(db_path, (STATION,))
            observation = decode_station_record(
                "81015 12081 10105 20005 30103 40120 51605 70250 81234 00081",
                STATION,
                bulletin_date="12.07.2026",
                source_type="test",
                source_file="ice.txt",
            )
            import_observations(
                db_path,
                (observation,),
                source_name="ice",
                source_type="test",
                message_type="ZRUR52",
                bulletin_date="12.07.2026",
                raw_path="ice.txt",
                raw_text=observation.raw_record,
            )
            run_initial_quality_control(db_path, "12.07.2026")
            rows = query_observations(
                db_path,
                start_date="12.07.2026",
                end_date="12.07.2026",
                parameter_codes=("ICE_PHENOMENA", "ICE_THICKNESS"),
            )

            by_code = {str(row["parameter_code"]): row for row in rows}
            self.assertEqual(
                by_code["ICE_PHENOMENA"]["text_value"],
                "Льодохід 50%",
            )
            self.assertEqual(by_code["ICE_PHENOMENA"]["quality_status"], VALID)
            self.assertEqual(by_code["ICE_THICKNESS"]["value"], 25.0)


class CorrectionAuditTests(unittest.TestCase):
    def test_correction_is_effective_reversible_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "archive.sqlite"
            initialize_archive(db_path, (STATION,))
            import_sample(db_path)
            level = query_observations(
                db_path,
                start_date="12.07.2026",
                end_date="12.07.2026",
                parameter_codes=("WATER_LEVEL",),
            )[0]
            with self.assertRaisesRegex(ValueError, "цілими сантиметрами"):
                create_correction(
                    db_path,
                    int(level["observation_id"]),
                    108.5,
                    reason="Некоректна дробова правка",
                    hydrologist="Євген КОЗИРЄВ",
                )
            correction = create_correction(
                db_path,
                int(level["observation_id"]),
                109,
                reason="Звірено з журналом поста",
                hydrologist="Євген КОЗИРЄВ",
            )

            effective = query_observations(
                db_path,
                start_date="12.07.2026",
                end_date="12.07.2026",
                parameter_codes=("WATER_LEVEL",),
            )[0]
            self.assertEqual(effective["original_value"], 105.0)
            self.assertEqual(effective["value"], 109.0)
            self.assertEqual(effective["quality_status"], CORRECTED)
            self.assertEqual(effective["correction_id"], correction.correction_id)

            connection = sqlite3.connect(db_path)
            try:
                stored = connection.execute(
                    "SELECT value FROM observations WHERE observation_id = ?",
                    (level["observation_id"],),
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(stored, 105.0)

            with self.assertRaisesRegex(ValueError, "вже є активна правка"):
                create_correction(
                    db_path,
                    int(level["observation_id"]),
                    110,
                    reason="Повтор",
                    hydrologist="Євген КОЗИРЄВ",
                )

            product = register_product(
                db_path,
                product_type="TEST",
                region_key="lviv",
                bulletin_date="12.07.2026",
                output_path="test.docx",
                observation_ids=(int(level["observation_id"]),),
            )
            cancel_correction(
                db_path,
                correction.correction_id,
                hydrologist="Євген КОЗИРЄВ",
                reason="Правку відкликано після повторної перевірки",
            )

            restored = query_observations(
                db_path,
                start_date="12.07.2026",
                end_date="12.07.2026",
                parameter_codes=("WATER_LEVEL",),
            )[0]
            self.assertEqual(restored["value"], 105.0)
            history = read_corrections(
                db_path,
                observation_id=int(level["observation_id"]),
            )
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["is_active"], 0)

            provenance = read_product_provenance(db_path, product.product_id)
            self.assertEqual(provenance[0]["original_value"], 105.0)
            self.assertEqual(provenance[0]["value"], 109.0)
            self.assertEqual(provenance[0]["correction_id"], correction.correction_id)
            self.assertEqual(provenance[0]["correction_currently_active"], 0)


class ReferenceAndPanelTests(unittest.TestCase):
    def test_extremes_and_level_panel_read_from_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "archive.sqlite"
            initialize_archive(db_path, (STATION,))
            import_sample(db_path)
            self.assertTrue(
                seed_reference_extreme(
                    db_path,
                    station_index=STATION.index,
                    maximum_level=500,
                    average_level=150,
                    minimum_level=30,
                )
            )
            self.assertFalse(
                seed_reference_extreme(
                    db_path,
                    station_index=STATION.index,
                    maximum_level=600,
                    average_level=160,
                    minimum_level=40,
                )
            )
            upsert_reference_extreme(
                db_path,
                station_index=STATION.index,
                maximum_level=510,
                average_level=155,
                minimum_level=25,
                maximum_date="01.04.2023",
                minimum_date="02.07.2026",
                updated_by="Євген КОЗИРЄВ",
            )
            reference = read_reference_extremes(db_path)[STATION.index]
            self.assertEqual(reference["maximum_level"], 510)
            self.assertEqual(reference["minimum_level"], 25)
            self.assertEqual(reference["source"], "manual")

            panel = build_level_panel_rows(
                db_path,
                "12.07.2026",
                (STATION,),
            )[0]
            self.assertEqual(panel.morning_level, 105.0)
            self.assertEqual(panel.previous_evening_level, 103.0)
            self.assertEqual(panel.daily_change, 5.0)
            self.assertIsNotNone(panel.level_observation_id)

            correction = create_correction(
                db_path,
                int(panel.level_observation_id),
                108,
                reason="Контрольна правка",
                hydrologist="Євген КОЗИРЄВ",
            )
            corrected_panel = build_level_panel_rows(
                db_path,
                "12.07.2026",
                (STATION,),
            )[0]
            self.assertEqual(corrected_panel.morning_level, 108.0)
            self.assertEqual(
                corrected_panel.level_correction_id,
                correction.correction_id,
            )


if __name__ == "__main__":
    unittest.main()
