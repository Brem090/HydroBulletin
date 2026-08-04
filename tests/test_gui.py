"""Контрактні тести GUI без потреби у графічному дисплеї."""

from __future__ import annotations

import io
import inspect
import sys
import unittest
from datetime import datetime
from pathlib import Path
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

    def test_gui_has_two_source_choices(self) -> None:
        self.assertEqual(
            gui.SOURCE_LABELS,
            {
                "Автоматично": "auto",
                "Локальний TXT-файл": "local",
            },
        )

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
