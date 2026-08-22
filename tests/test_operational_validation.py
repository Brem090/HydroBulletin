"""Контрактні тести повного експлуатаційного сценарію."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.validate_operational_scenario import (
    MANUAL_COMPARABLE_TOTAL,
    performance_metrics,
    select_operational_inputs,
)


class OperationalScenarioTests(unittest.TestCase):
    def test_manual_baseline_matches_four_user_measurements(self) -> None:
        self.assertEqual(MANUAL_COMPARABLE_TOTAL, 1695)

    def test_performance_metrics_use_the_comparable_scope(self) -> None:
        metrics = performance_metrics(10.0)

        self.assertEqual(metrics["saved_seconds"], 1685.0)
        self.assertEqual(metrics["time_reduction_percent"], 99.41)
        self.assertEqual(metrics["speedup_times"], 169.5)

    def test_semantic_duplicates_with_suffix_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            folder = Path(tmp_dir)
            hydro_52 = "81015 08081 10045 20012 30046 =\n"
            hydro_71 = "79726 08081 10120 20001 30120 =\n"
            synop = (
                "SM Синоптичне зведення: Тест\n"
                "2026-08-08 00:00:00\n"
                "33288 11111 555 60011 =\n"
            )
            (folder / "2026-08-08_ZRUR52.txt").write_text(
                hydro_52,
                encoding="utf-8",
            )
            (folder / "2026-08-08_ZRUR52_2.txt").write_text(
                "службовий заголовок\n" + hydro_52,
                encoding="utf-8",
            )
            (folder / "2026-08-08_ZRUR71.txt").write_text(
                hydro_71,
                encoding="utf-8",
            )
            (folder / "2026-08-08_SYNOP.txt").write_text(
                synop,
                encoding="utf-8",
            )

            selected = select_operational_inputs(folder, "08.08.2026")
            zrur52 = next(
                item for item in selected if item.message_type == "ZRUR52"
            )

            self.assertEqual(zrur52.path.name, "2026-08-08_ZRUR52.txt")
            self.assertEqual(
                zrur52.duplicate_names,
                ("2026-08-08_ZRUR52_2.txt",),
            )

    def test_more_relevant_suffix_variant_wins_over_demo_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            folder = Path(tmp_dir)
            (folder / "2026-08-08_ZRUR52.txt").write_text(
                "81015 12081 10125 20051 41800 =\n",
                encoding="utf-8",
            )
            (folder / "2026-08-08_ZRUR52_2.txt").write_text(
                "81015 08081 10073 20281 30045 =\n",
                encoding="utf-8",
            )
            (folder / "2026-08-08_ZRUR71.txt").write_text(
                "79726 08081 10056 20011 30055 =\n",
                encoding="utf-8",
            )
            (folder / "2026-08-08_SYNOP.txt").write_text(
                "SM Синоптичне зведення: Тест\n"
                "2026-08-08 06:00:00\n"
                "33288 32998 50000 =\n",
                encoding="utf-8",
            )

            selected = select_operational_inputs(folder, "08.08.2026")
            zrur52 = next(
                item for item in selected if item.message_type == "ZRUR52"
            )

            self.assertEqual(zrur52.path.name, "2026-08-08_ZRUR52_2.txt")
            self.assertEqual(
                zrur52.ignored_variant_names,
                ("2026-08-08_ZRUR52.txt",),
            )


if __name__ == "__main__":
    unittest.main()
