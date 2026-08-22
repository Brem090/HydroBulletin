"""Наскрізна перевірка бюлетенів, карти та графіків."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from hydrobulletin.archive import archive_summary, read_product_provenance
from hydrobulletin.batch import run_batch_import
from hydrobulletin.stations import ALL_STATIONS, METEO_STATIONS, STATIONS_BY_INDEX
from hydrobulletin.workflow import WorkflowRequest, execute_workflow


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEMO_DIR = PROJECT_DIR / "demo_data" / "regression"


class ProductWorkflowTests(unittest.TestCase):
    def test_archive_to_bulletins_map_and_charts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            db_path = root / "archive.sqlite"
            raw_root = root / "raw"
            output_dir = root / "output"

            batch = run_batch_import(
                DEMO_DIR,
                raw_root=raw_root,
                db_path=db_path,
                all_stations=ALL_STATIONS,
                hydro_stations_by_index=STATIONS_BY_INDEX,
                meteo_stations_by_index={
                    station.index: station for station in METEO_STATIONS
                },
            )
            self.assertEqual(batch.errors, ())

            request = WorkflowRequest(
                bulletin_date="12.07.2026",
                source_mode="database",
                message_types=(),
                db_path=db_path,
                raw_root=raw_root,
                templates_dir=PROJECT_DIR / "templates" / "bulletins",
                output_dir=output_dir,
                mapping_path=PROJECT_DIR
                / "config"
                / "precipitation_mapping.json",
                region_keys=("lviv", "if", "left_dnister"),
                hydrologist="Тестовий гідролог",
                include_meteo=False,
                create_bulletins=True,
                create_map=True,
                map_template_path=PROJECT_DIR
                / "templates"
                / "maps"
                / "HydroMap_UHMC_Lviv_template_clean.png",
                font_path=PROJECT_DIR
                / "resources"
                / "fonts"
                / "e-Ukraine-Regular.otf",
                chart_station_index="79726",
                chart_start_date="11.07.2026",
                chart_end_date="12.07.2026",
                create_level_chart=True,
                create_discharge_chart=True,
            )
            result = execute_workflow(request)

            self.assertEqual(len(result.bulletins), 3)
            self.assertIsNotNone(result.map_result)
            map_result = result.map_result
            assert map_result is not None
            self.assertEqual(len(result.charts), 2)
            self.assertEqual(archive_summary(db_path)["products"], 6)

            output_paths = [item.output_path for item in result.bulletins]
            output_paths.append(map_result.output_path)
            output_paths.extend(item.output_path for item in result.charts)
            self.assertTrue(all(path.exists() for path in output_paths))
            expected_month_dir = output_dir / "2026" / "Липень"
            self.assertTrue(
                all(path.parent == expected_month_dir for path in output_paths)
            )

            with Image.open(map_result.output_path) as image:
                self.assertEqual(image.size, (1920, 1080))
            for chart in result.charts:
                with Image.open(chart.output_path) as image:
                    image.verify()

            map_provenance = read_product_provenance(
                db_path,
                map_result.product.product_id,
            )
            chart_provenance = read_product_provenance(
                db_path,
                result.charts[0].product.product_id,
            )
            self.assertTrue(map_provenance)
            self.assertTrue(chart_provenance)
            self.assertTrue(all(row["raw_path"] for row in map_provenance))
            self.assertTrue(all(row["source_hash"] for row in chart_provenance))

            second = execute_workflow(request)
            self.assertEqual(len(second.bulletins), 3)
            self.assertEqual(len(second.charts), 2)
            self.assertEqual(archive_summary(db_path)["products"], 6)


if __name__ == "__main__":
    unittest.main()
