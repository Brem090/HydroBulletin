"""Перевірки календарної структури сформованих матеріалів."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hydrobulletin.output_paths import MATERIALS_DIR_NAME, dated_output_dir


class OutputPathTests(unittest.TestCase):
    def test_materials_are_grouped_by_year_and_ukrainian_month(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / MATERIALS_DIR_NAME
            result = dated_output_dir(root, "09.08.2026")

            self.assertEqual(result, root / "2026" / "Серпень")
            self.assertTrue(result.is_dir())

    def test_invalid_material_date_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaisesRegex(ValueError, "ДД.ММ.РРРР"):
                dated_output_dir(Path(tmp_dir), "2026-08-09")


if __name__ == "__main__":
    unittest.main()
