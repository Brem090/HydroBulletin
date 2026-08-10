"""Перевірки карти, архівних графіків і provenance візуальних продуктів."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from matplotlib.backends.backend_agg import FigureCanvasAgg
from PIL import Image

from hydrobulletin.archive import (
    archive_summary,
    import_observations,
    initialize_archive,
    query_observations,
    read_product_provenance,
)
from hydrobulletin.charts import (
    DISCHARGE_CHART,
    LEVEL_CHART,
    _regular_series,
    chart_output_name,
    create_discharge_chart,
    create_level_chart,
)
from hydrobulletin.maps import create_lviv_map, map_output_name
from hydrobulletin.models import HydroObservation
from hydrobulletin.stations import LVIV_STATIONS


PROJECT_DIR = Path(__file__).resolve().parents[1]
MAP_TEMPLATE = (
    PROJECT_DIR
    / "templates"
    / "maps"
    / "HydroMap_UHMC_Lviv_template_clean.png"
)
FONT_PATH = PROJECT_DIR / "resources" / "fonts" / "e-Ukraine-Regular.otf"


def add_observation(
    db_path: Path,
    *,
    bulletin_date: str,
    raw_text: str,
    observation: HydroObservation,
) -> None:
    import_observations(
        db_path,
        (observation,),
        source_name=f"test-{bulletin_date}",
        source_type="local",
        message_type="ZRUR52",
        bulletin_date=bulletin_date,
        raw_path=observation.source_file,
        raw_text=raw_text,
    )


def create_test_archive(root: Path) -> Path:
    db_path = root / "archive.sqlite"
    initialize_archive(db_path, LVIV_STATIONS)

    add_observation(
        db_path,
        bulletin_date="01.07.2026",
        raw_text="day-1",
        observation=HydroObservation(
            index="81015",
            station_name="Дністер — Стрілки",
            level=100,
            change=2,
            evening_level=99,
            water_temperature_c=12.0,
            discharge_m3_s=1.2,
            raw_record="day-1",
            quality_status="OK",
            observed_at=datetime(2026, 7, 1, 8),
            evening_observed_at=datetime(2026, 6, 30, 20),
            source_type="local",
            source_file="raw/2026/07/day-1.txt",
        ),
    )
    add_observation(
        db_path,
        bulletin_date="02.07.2026",
        raw_text="day-2",
        observation=HydroObservation(
            index="81015",
            station_name="Дністер — Стрілки",
            level=None,
            change=None,
            evening_level=102,
            raw_record="day-2",
            quality_status="OK",
            observed_at=datetime(2026, 7, 2, 8),
            evening_observed_at=datetime(2026, 7, 1, 20),
            source_type="local",
            source_file="raw/2026/07/day-2.txt",
        ),
    )
    add_observation(
        db_path,
        bulletin_date="03.07.2026",
        raw_text="day-3",
        observation=HydroObservation(
            index="81015",
            station_name="Дністер — Стрілки",
            level=105,
            change=3,
            evening_level=103,
            water_temperature_c=14.0,
            discharge_m3_s=1.6,
            raw_record="day-3",
            quality_status="OK",
            observed_at=datetime(2026, 7, 3, 8),
            evening_observed_at=datetime(2026, 7, 2, 20),
            source_type="local",
            source_file="raw/2026/07/day-3.txt",
        ),
    )

    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        UPDATE observations
        SET quality_status = 'SUSPICIOUS', quality_message = 'Контрольна точка.'
        WHERE station_index = '81015'
          AND observed_at = '2026-07-03T08:00:00'
          AND parameter_code IN ('WATER_LEVEL', 'DISCHARGE')
        """
    )
    connection.commit()
    connection.close()
    return db_path


class VisualProductTests(unittest.TestCase):
    def test_level_series_is_one_morning_evening_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            db_path = create_test_archive(root)
            rows = query_observations(
                db_path,
                start_date="01.07.2026",
                end_date="03.07.2026",
                station_indexes=("81015",),
                parameter_codes=("WATER_LEVEL",),
            )

            timestamps, values = _regular_series(
                rows,
                datetime(2026, 7, 1),
                datetime(2026, 7, 3),
                (8, 20),
            )

            self.assertEqual(
                timestamps,
                (
                    datetime(2026, 7, 1, 8),
                    datetime(2026, 7, 1, 20),
                    datetime(2026, 7, 2, 8),
                    datetime(2026, 7, 2, 20),
                    datetime(2026, 7, 3, 8),
                    datetime(2026, 7, 3, 20),
                ),
            )
            self.assertEqual(values, (100.0, 102.0, None, 103.0, 105.0, None))

    def test_map_uses_sqlite_and_registers_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            db_path = create_test_archive(root)
            output_path = root / map_output_name("03.07.2026")

            result = create_lviv_map(
                db_path,
                bulletin_date="03.07.2026",
                template_path=MAP_TEMPLATE,
                font_path=FONT_PATH,
                output_path=output_path,
            )

            self.assertEqual(result.plotted_stations, 1)
            self.assertEqual(result.missing_stations, 20)
            self.assertEqual(result.product.linked_observations, 3)
            self.assertNotEqual(output_path.read_bytes(), MAP_TEMPLATE.read_bytes())
            with Image.open(output_path) as image:
                self.assertEqual(image.size, (1920, 1080))

            provenance = read_product_provenance(
                db_path,
                result.product.product_id,
            )
            self.assertEqual(
                {str(row["parameter_code"]) for row in provenance},
                {"WATER_LEVEL", "DAILY_CHANGE", "WATER_TEMPERATURE"},
            )
            self.assertTrue(all(row["source_hash"] for row in provenance))

    def test_map_rejects_date_without_morning_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            db_path = create_test_archive(root)
            with self.assertRaisesRegex(ValueError, "немає ранкових рівнів"):
                create_lviv_map(
                    db_path,
                    bulletin_date="02.07.2026",
                    template_path=MAP_TEMPLATE,
                    font_path=FONT_PATH,
                    output_path=root / "map.png",
                )

    def test_map_checks_template_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            db_path = create_test_archive(root)
            small_template = root / "small.png"
            Image.new("RGB", (640, 480), "white").save(small_template)

            with self.assertRaisesRegex(ValueError, "1920 x 1080"):
                create_lviv_map(
                    db_path,
                    bulletin_date="03.07.2026",
                    template_path=small_template,
                    font_path=FONT_PATH,
                    output_path=root / "map.png",
                )

    def test_level_chart_shows_gaps_and_flagged_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            db_path = create_test_archive(root)
            output_path = root / "levels.png"

            result = create_level_chart(
                db_path,
                station_index="81015",
                start_date="01.07.2026",
                end_date="03.07.2026",
                output_path=output_path,
                font_path=FONT_PATH,
            )

            self.assertEqual(result.available_points, 4)
            self.assertEqual(result.missing_points, 2)
            self.assertEqual(result.flagged_points, 1)
            self.assertEqual(result.product.linked_observations, 4)
            with Image.open(output_path) as image:
                self.assertGreater(image.width, 1200)
                self.assertGreater(image.height, 700)

    def test_chart_legend_is_outside_plot_area(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            db_path = create_test_archive(root)
            checked = False

            def inspect_figure(figure, _output_path, _title) -> None:
                nonlocal checked
                canvas = FigureCanvasAgg(figure)
                canvas.draw()
                self.assertEqual(len(figure.legends), 1)
                axes_box = figure.axes[0].get_window_extent(canvas.get_renderer())
                legend_box = figure.legends[0].get_window_extent(
                    canvas.get_renderer()
                )
                self.assertLessEqual(legend_box.y1, axes_box.y0)
                checked = True

            with patch(
                "hydrobulletin.charts._save_figure",
                side_effect=inspect_figure,
            ):
                create_level_chart(
                    db_path,
                    station_index="81015",
                    start_date="01.07.2026",
                    end_date="03.07.2026",
                    output_path=root / "levels.png",
                    font_path=FONT_PATH,
                )

            self.assertTrue(checked)

    def test_discharge_chart_shows_gap_and_flagged_point(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            db_path = create_test_archive(root)
            output_path = root / "discharge.png"

            result = create_discharge_chart(
                db_path,
                station_index="81015",
                start_date="01.07.2026",
                end_date="03.07.2026",
                output_path=output_path,
                font_path=FONT_PATH,
            )

            self.assertEqual(result.available_points, 2)
            self.assertEqual(result.missing_points, 1)
            self.assertEqual(result.flagged_points, 1)
            self.assertEqual(result.product.linked_observations, 2)
            self.assertTrue(output_path.exists())

    def test_invalid_chart_period_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Початкова дата"):
            chart_output_name(
                LEVEL_CHART,
                "81015",
                "03.07.2026",
                "01.07.2026",
            )

    def test_output_names_are_predictable(self) -> None:
        self.assertEqual(
            map_output_name("03.07.2026"),
            "HydroMap_Lviv_03.07.2026.png",
        )
        self.assertEqual(
            chart_output_name(
                DISCHARGE_CHART,
                "81015",
                "01.07.2026",
                "03.07.2026",
            ),
            "Discharge_81015_2026-07-01_2026-07-03.png",
        )

    def test_repeated_map_generation_updates_one_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            db_path = create_test_archive(root)
            output_path = root / "map.png"
            first = create_lviv_map(
                db_path,
                bulletin_date="03.07.2026",
                template_path=MAP_TEMPLATE,
                font_path=FONT_PATH,
                output_path=output_path,
            )
            second = create_lviv_map(
                db_path,
                bulletin_date="03.07.2026",
                template_path=MAP_TEMPLATE,
                font_path=FONT_PATH,
                output_path=output_path,
            )

            self.assertEqual(first.product.product_id, second.product.product_id)
            self.assertEqual(archive_summary(db_path)["products"], 1)


if __name__ == "__main__":
    unittest.main()
