"""Контрактні тести GUI без потреби у графічному дисплеї."""

from __future__ import annotations

import io
import inspect
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main as main_module
from hydrobulletin import gui


class GuiContractTests(unittest.TestCase):
    def test_visual_palette_is_stable(self) -> None:
        self.assertEqual(gui.BG_MAIN, "#EAF6FB")
        self.assertEqual(gui.BG_CARD, "#FFFFFF")
        self.assertEqual(gui.BLUE_DARK, "#0B4F6C")
        self.assertEqual(gui.BLUE, "#147CA8")
        self.assertEqual(gui.TEXT_DARK, "#16323F")

    def test_header_contains_only_hydrobulletin_title(self) -> None:
        source = inspect.getsource(gui.HydroBulletinApp._build_ui)
        self.assertIn('text="HydroBulletin"', source)
        self.assertNotIn("Львівський регіональний центр", source)
        self.assertNotIn("Про програму", source)

    def test_temporary_interface_sections_are_absent(self) -> None:
        source = inspect.getsource(gui.HydroBulletinApp)
        self.assertNotIn("Черговий гідролог", source)
        self.assertNotIn("Налаштування інтерфейсу", source)
        self.assertNotIn("Сектор гідрологічних прогнозів", source)
        self.assertNotIn("Пошук на ГЦСТ", source)
        self.assertNotIn("ttk.Progressbar", source)

    def test_hydrologist_is_filled_automatically(self) -> None:
        self.assertEqual(gui.DEFAULT_HYDROLOGIST, "Євген КОЗИРЄВ")

    def test_gui_has_operational_and_archive_source_choices(self) -> None:
        self.assertEqual(
            gui.SOURCE_LABELS,
            {
                "Автоматично": "auto",
                "Локальний TXT-файл": "local",
                "Папка TXT-файлів": "batch",
                "Архів SQLite": "database",
            },
        )

    def test_gui_batch_mode_uses_a_folder_picker(self) -> None:
        source = inspect.getsource(gui.HydroBulletinApp._build_source_section)
        request_source = inspect.getsource(gui.HydroBulletinApp._request)
        self.assertIn("Папка з ZRUR/SYNOP", source)
        self.assertIn("batch_folder=batch_folder", request_source)

    def test_gui_has_primary_mirror_and_automatic_gcst_choices(self) -> None:
        self.assertEqual(
            gui.GCST_LABEL_TO_MODE,
            {
                "Автоматично (основний → дзеркало)": "auto",
                "Основний ГЦСТ": "primary",
                "Дзеркало ГЦСТ": "mirror",
            },
        )

    def test_gcst_usage_summary_identifies_fallback(self) -> None:
        result = SimpleNamespace(
            hydro_imports=(
                SimpleNamespace(
                    source_name=(
                        "ZRUR52@http://rgcst.meteo.gov.ua/armua"
                    )
                ),
            ),
            meteo_import=None,
        )
        self.assertEqual(
            gui.gcst_usage_summary(result, "auto"),
            "Використано резервне дзеркало ГЦСТ.",
        )

    def test_cli_accepts_explicit_gcst_server(self) -> None:
        args = main_module.build_parser().parse_args(
            ["--gcst-source", "mirror"]
        )
        self.assertEqual(args.gcst_source, "mirror")

    def test_chart_station_label_resolves_to_station_index(self) -> None:
        label = next(
            item for item in gui.CHART_STATION_LABELS if item.startswith("81015 ")
        )
        self.assertEqual(gui.station_index_from_label(label), "81015")
        with self.assertRaisesRegex(ValueError, "вибрати гідрологічний пост"):
            gui.station_index_from_label("невідомий пост")

    def test_visual_products_are_available_without_manual_corrections(self) -> None:
        source = inspect.getsource(gui.HydroBulletinApp._build_visuals_section)
        self.assertIn("Гідрологічна карта Львівської області", source)
        self.assertIn("Графік ходу рівнів води", source)
        self.assertIn("Графік витрат води", source)
        self.assertNotIn("ручн", source.lower())

    def test_operational_tools_are_separate_from_product_selection(self) -> None:
        source = inspect.getsource(
            gui.HydroBulletinApp._build_operational_tools_section
        )
        self.assertIn("Панель рівнів", source)
        self.assertIn("Екстремуми", source)
        self.assertIn("open_levels_panel", source)
        self.assertIn("open_extremes_manager", source)
        self.assertNotIn("SQLite", source)
        self.assertNotIn("self.make_button", source)
        self.assertIn("SUBTLE_CARD", source)
        self.assertIn('bg="#D1EAF4"', source)
        self.assertIn("width=18", source)
        layout_source = inspect.getsource(gui.HydroBulletinApp._build_ui)
        visuals_position = layout_source.index("self._build_visuals_section()")
        tools_position = layout_source.index("self._build_operational_tools_section()")
        source_position = layout_source.index("self._build_source_section()")
        self.assertLess(visuals_position, tools_position)
        self.assertLess(tools_position, source_position)

    def test_level_panel_uses_audited_corrections(self) -> None:
        source = inspect.getsource(gui.HydroBulletinApp.open_levels_panel)
        self.assertIn("create_correction", source)
        self.assertIn("cancel_correction", source)
        self.assertIn("початкове значення залишається незмінним", source)

    def test_level_panel_displays_quality_statuses_in_ukrainian(self) -> None:
        source = inspect.getsource(gui.HydroBulletinApp.open_levels_panel)
        self.assertIn('"quality": "Статус якості"', source)
        self.assertIn("quality_status_label(item.quality_status)", source)
        self.assertNotIn("item.quality_status,", source)

    def test_product_creation_has_unified_progress_states(self) -> None:
        start_source = inspect.getsource(gui.HydroBulletinApp._start)
        finish_source = inspect.getsource(gui.HydroBulletinApp._finish_success)
        error_source = inspect.getsource(gui.HydroBulletinApp._finish_error)
        self.assertIn("Створюю…", start_source)
        self.assertIn('status_var.set("Створено.")', finish_source)
        self.assertIn("Матеріали не створено", error_source)
        self.assertIn("showinfo", finish_source)

    def test_message_types_are_selected_for_regions(self) -> None:
        self.assertEqual(
            gui.message_types_for_regions(("if", "left_dnister")),
            ("ZRUR52",),
        )
        self.assertEqual(
            gui.message_types_for_regions(("lviv",)),
            ("ZRUR52", "ZRUR71"),
        )

    def test_local_message_type_is_read_from_filename(self) -> None:
        self.assertEqual(
            gui.message_type_from_file(Path("30.07.2026_ZRUR53.txt")),
            "ZRUR53",
        )
        self.assertEqual(
            gui.message_type_from_file(Path("hydro_codes.txt")),
            "ZRUR52",
        )

    def test_dpi_awareness_is_enabled_before_tk_window(self) -> None:
        source = inspect.getsource(gui.launch_gui)
        awareness_position = source.index("enable_high_dpi_awareness()")
        window_position = source.index("tk.Tk()")
        self.assertLess(awareness_position, window_position)

    def test_windows_dpi_fallbacks_are_present(self) -> None:
        source = inspect.getsource(gui.enable_high_dpi_awareness)
        self.assertIn("SetProcessDpiAwarenessContext", source)
        self.assertIn("SetProcessDpiAwareness", source)
        self.assertIn("SetProcessDPIAware", source)

    def test_date_format_matches_operational_interface(self) -> None:
        value = datetime(2026, 7, 30)
        self.assertEqual(gui.format_date_for_output(value), "30.07.2026")

    def test_default_output_uses_calendar_folders(self) -> None:
        source = inspect.getsource(gui.HydroBulletinApp.__init__)
        self.assertIn("MATERIALS_DIR_NAME", source)
        self.assertNotIn("output/week", source)

    def test_keyboard_interrupt_closes_gui_without_traceback(self) -> None:
        stderr = io.StringIO()
        with (
            patch.object(sys, "argv", ["main.py", "--gui"]),
            patch.object(main_module, "configure_console"),
            patch.object(gui, "launch_gui", side_effect=KeyboardInterrupt),
            patch("sys.stderr", stderr),
        ):
            self.assertEqual(main_module.main(), 130)

        self.assertEqual(
            stderr.getvalue().strip(),
            "Роботу GUI зупинено користувачем.",
        )


if __name__ == "__main__":
    unittest.main()
