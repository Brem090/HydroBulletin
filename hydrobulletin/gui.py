"""Графічний інтерфейс HydroBulletin."""

from __future__ import annotations

import calendar
import os
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable

from .archive import (
    DatabaseRow,
    cancel_correction,
    create_correction,
    initialize_archive,
    read_reference_extremes,
    upsert_reference_extreme,
)
from .extremes import seed_extremes_from_templates
from .levels import LevelPanelRow, build_level_panel_rows
from .quality import quality_status_label
from .regions import REGIONS, message_types_for_regions
from .output_paths import MATERIALS_DIR_NAME, dated_output_dir
from .sources import (
    GCST_MIRROR_BASE_URL,
    GCST_PRIMARY_BASE_URL,
    GCST_SOURCE_AUTO,
    GCST_SOURCE_LABELS,
    SUPPORTED_MESSAGE_TYPES,
)
from .stations import ALL_STATIONS, HYDRO_STATIONS
from .workflow import (
    DEFAULT_HYDROLOGIST,
    WorkflowRequest,
    WorkflowResult,
    execute_workflow,
)


BG_MAIN = "#EAF6FB"
BG_CARD = "#FFFFFF"
BLUE_DARK = "#0B4F6C"
BLUE = "#147CA8"
GREEN = "#1F7A4D"
TEXT_DARK = "#16323F"
SUBTLE_CARD = "#F4FAFC"
CARD_BORDER = "#BFDDE8"
RUN_BUTTON_TEXT = "✓ Створити вибрані матеріали"

SOURCE_LABELS = {
    "Автоматично": "auto",
    "Локальний TXT-файл": "local",
    "Папка TXT-файлів": "batch",
    "Архів SQLite": "database",
}
GCST_LABEL_TO_MODE = {
    label: source_mode for source_mode, label in GCST_SOURCE_LABELS.items()
}

CHART_STATION_LABELS = {
    f"{station.index} — {station.name}": station.index
    for station in HYDRO_STATIONS
}

MONTHS_UA = {
    1: "січень",
    2: "лютий",
    3: "березень",
    4: "квітень",
    5: "травень",
    6: "червень",
    7: "липень",
    8: "серпень",
    9: "вересень",
    10: "жовтень",
    11: "листопад",
    12: "грудень",
}


def format_date_for_output(value: datetime) -> str:
    """Повертає дату у робочому форматі ДД.ММ.РРРР."""

    return value.strftime("%d.%m.%Y")


def parse_date(value: str) -> datetime:
    """Перетворює дату інтерфейсу на ``datetime``."""

    return datetime.strptime(value.strip(), "%d.%m.%Y")


def message_type_from_file(path: Path) -> str:
    """Визначає тип повідомлення за назвою локального файла."""

    filename = Path(path).name.upper()
    for message_type in SUPPORTED_MESSAGE_TYPES:
        if message_type in filename:
            return message_type
    return "ZRUR52"


def station_index_from_label(value: str) -> str:
    """Повертає індекс поста з підпису поля вибору."""

    try:
        return CHART_STATION_LABELS[value]
    except KeyError as exc:
        raise ValueError("Потрібно вибрати гідрологічний пост для графіка.") from exc


def gcst_usage_summary(
    result: WorkflowResult,
    requested_mode: str,
) -> str | None:
    """Описує фактично використаний сервер за результатом імпорту."""

    source_names = [item.source_name for item in result.hydro_imports]
    if result.meteo_import is not None:
        source_names.append(result.meteo_import.source_name)

    used_primary = any(GCST_PRIMARY_BASE_URL in name for name in source_names)
    used_mirror = any(GCST_MIRROR_BASE_URL in name for name in source_names)
    if used_primary and used_mirror:
        return "Використано основний сервер і дзеркало ГЦСТ."
    if used_mirror:
        if requested_mode == GCST_SOURCE_AUTO:
            return "Використано резервне дзеркало ГЦСТ."
        return "Використано дзеркало ГЦСТ."
    if used_primary:
        return "Використано основний сервер ГЦСТ."
    return None


def enable_high_dpi_awareness() -> None:
    """Вимикає bitmap-масштабування Windows до створення першого вікна Tk."""

    if not sys.platform.startswith("win"):
        return

    try:
        import ctypes

        # Окреме масштабування для кожного монітора у Windows 10/11.
        context = ctypes.c_void_p(-4)
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(context):
            return
    except Exception:
        pass

    try:
        import ctypes

        # Варіант для Windows 8.1 та ранніх збірок Windows 10.
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass

    try:
        import ctypes

        # Системний DPI-aware режим для старих версій Windows.
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def configure_tk_scaling(root: tk.Tk) -> None:
    """Узгоджує масштаб Tk зі справжнім DPI активного монітора."""

    try:
        pixels_per_inch = float(root.winfo_fpixels("1i"))
        if 72.0 <= pixels_per_inch <= 384.0:
            root.tk.call("tk", "scaling", pixels_per_inch / 72.0)
    except (tk.TclError, TypeError, ValueError):
        pass


class CalendarPopup(tk.Toplevel):
    """Невеликий календар для вибору дати бюлетеня."""

    def __init__(
        self,
        master: tk.Misc,
        selected_date: datetime,
        callback: Callable[[str], None],
    ) -> None:
        super().__init__(master)
        self.title("Вибір дати")
        self.resizable(False, False)
        self.callback = callback
        self.current_year = selected_date.year
        self.current_month = selected_date.month

        self.header = tk.Frame(self)
        self.header.pack(padx=10, pady=8)

        tk.Button(
            self.header,
            text="‹",
            width=3,
            command=self._previous_month,
        ).pack(side=tk.LEFT)
        self.title_label = tk.Label(
            self.header,
            width=18,
            font=("Segoe UI", 11, "bold"),
        )
        self.title_label.pack(side=tk.LEFT)
        tk.Button(
            self.header,
            text="›",
            width=3,
            command=self._next_month,
        ).pack(side=tk.LEFT)

        self.days_frame = tk.Frame(self)
        self.days_frame.pack(padx=10, pady=5)
        self._draw_calendar()

    def _draw_calendar(self) -> None:
        for widget in self.days_frame.winfo_children():
            widget.destroy()

        self.title_label.configure(
            text=f"{MONTHS_UA[self.current_month]} {self.current_year}"
        )
        weekdays = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")
        for column, name in enumerate(weekdays):
            tk.Label(
                self.days_frame,
                text=name,
                width=4,
                font=("Segoe UI", 9, "bold"),
            ).grid(row=0, column=column)

        month = calendar.Calendar(firstweekday=0)
        for row, week in enumerate(
            month.monthdayscalendar(self.current_year, self.current_month),
            start=1,
        ):
            for column, day in enumerate(week):
                if day == 0:
                    tk.Label(
                        self.days_frame,
                        text="",
                        width=4,
                    ).grid(row=row, column=column)
                    continue
                tk.Button(
                    self.days_frame,
                    text=str(day),
                    width=4,
                    command=lambda selected=day: self._select_day(selected),
                ).grid(row=row, column=column, padx=1, pady=1)

    def _previous_month(self) -> None:
        if self.current_month == 1:
            self.current_month = 12
            self.current_year -= 1
        else:
            self.current_month -= 1
        self._draw_calendar()

    def _next_month(self) -> None:
        if self.current_month == 12:
            self.current_month = 1
            self.current_year += 1
        else:
            self.current_month += 1
        self._draw_calendar()

    def _select_day(self, day: int) -> None:
        selected = datetime(self.current_year, self.current_month, day)
        self.callback(format_date_for_output(selected))
        self.destroy()


class HydroBulletinApp:
    """Графічна форма запуску основного сценарію програми."""

    def __init__(
        self,
        root: tk.Tk,
        resource_dir: Path,
        data_dir: Path | None = None,
    ) -> None:
        self.root = root
        self.resource_dir = Path(resource_dir)
        self.data_dir = Path(data_dir) if data_dir is not None else self.resource_dir
        today = datetime.now()

        self.root.title("HydroBulletin")
        self.root.geometry("1060x780")
        self.root.minsize(980, 660)
        self.root.resizable(True, True)
        self.root.configure(bg=BG_MAIN)

        self.date_var = tk.StringVar(
            master=self.root,
            value=format_date_for_output(today),
        )
        self.source_var = tk.StringVar(
            master=self.root,
            value=next(iter(SOURCE_LABELS)),
        )
        self.gcst_source_var = tk.StringVar(
            master=self.root,
            value=GCST_SOURCE_LABELS[GCST_SOURCE_AUTO],
        )
        self.gcst_used_source_var = tk.StringVar(
            master=self.root,
            value="Сервер ще не перевірявся.",
        )
        self.file_var = tk.StringVar(
            master=self.root,
            value=str(
                self.resource_dir
                / "demo_data"
                / "regression"
                / "12.07.2026_ZRUR52.txt"
            ),
        )
        self.meteo_file_var = tk.StringVar(
            master=self.root,
            value=str(
                self.resource_dir
                / "demo_data"
                / "regression"
                / "12.07.2026_SYNOP.txt"
            ),
        )
        self.batch_folder_var = tk.StringVar(
            master=self.root,
            value=str(self.resource_dir / "demo_data" / "full_private"),
        )
        self.output_var = tk.StringVar(
            master=self.root,
            value=str(self.data_dir / MATERIALS_DIR_NAME),
        )
        self.region_vars = {
            region.key: tk.BooleanVar(master=self.root, value=False)
            for region in REGIONS
        }
        self.create_map_var = tk.BooleanVar(master=self.root, value=False)
        self.create_level_chart_var = tk.BooleanVar(master=self.root, value=False)
        self.create_discharge_chart_var = tk.BooleanVar(
            master=self.root,
            value=False,
        )
        self.chart_station_var = tk.StringVar(
            master=self.root,
            value=next(iter(CHART_STATION_LABELS)),
        )
        self.chart_start_var = tk.StringVar(
            master=self.root,
            value=format_date_for_output(today - timedelta(days=14)),
        )
        self.chart_end_var = tk.StringVar(
            master=self.root,
            value=format_date_for_output(today),
        )
        self.status_var = tk.StringVar(
            master=self.root,
            value="Готово до запуску.",
        )
        self._main_scroll_after_id: str | None = None
        self._pending_main_scroll_fraction: float | None = None

        self._configure_ttk_style()
        self._build_ui()

    def _configure_ttk_style(self) -> None:
        style = ttk.Style(self.root)
        style.configure("Hydro.TCombobox", font=("Segoe UI", 10))

    def make_button(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], object],
        *,
        bg: str | None = None,
        fg: str = "white",
        width: int | None = None,
        height: int = 1,
    ) -> tk.Button:
        """Створює кнопку основного стилю інтерфейсу."""

        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg or BLUE,
            fg=fg,
            activebackground=BLUE_DARK,
            activeforeground="white",
            relief="flat",
            bd=0,
            cursor="hand2",
            font=("Segoe UI", 11, "bold"),
            disabledforeground="white",
            padx=14,
            pady=8,
            width=width,
            height=height,
        )

    def _build_ui(self) -> None:
        self.header = tk.Frame(self.root, bg=BLUE_DARK, height=74)
        self.header.pack(fill="x")
        self.header.pack_propagate(False)

        title_wrap = tk.Frame(self.header, bg=BLUE_DARK)
        title_wrap.pack(
            fill="both",
            expand=True,
            padx=28,
            pady=16,
        )

        self.header_title_label = tk.Label(
            title_wrap,
            text="HydroBulletin",
            bg=BLUE_DARK,
            fg="white",
            font=("Segoe UI", 18, "bold"),
        )
        self.header_title_label.pack(anchor="w")

        main_area = tk.Frame(self.root, bg=BG_MAIN)
        main_area.pack(fill="both", expand=True)

        self.main_canvas = tk.Canvas(
            main_area,
            bg=BG_MAIN,
            highlightthickness=0,
        )
        self.main_scrollbar = tk.Scrollbar(
            main_area,
            orient="vertical",
            command=self._on_main_scrollbar,
        )
        self.main_canvas.configure(yscrollcommand=self.main_scrollbar.set)
        self.main_canvas.pack(side=tk.LEFT, fill="both", expand=True)
        self.main_scrollbar.pack(side=tk.RIGHT, fill="y")

        self.scroll_content = tk.Frame(self.main_canvas, bg=BG_MAIN)
        self.scroll_window = self.main_canvas.create_window(
            (0, 0),
            window=self.scroll_content,
            anchor="nw",
        )
        self.scroll_content.bind(
            "<Configure>",
            self._refresh_scroll_region,
        )
        self.main_canvas.bind(
            "<Configure>",
            self._resize_scroll_content,
        )
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)

        self.card = tk.Frame(
            self.scroll_content,
            bg=BG_CARD,
            highlightbackground=CARD_BORDER,
            highlightthickness=1,
        )
        self.card.pack(fill="both", expand=True, padx=28, pady=18)

        self._build_date_section()
        self._build_regions_section()
        self._build_visuals_section()
        self._build_operational_tools_section()
        self._build_source_section()
        self._build_output_section()
        self._build_action_section()
        self._build_status_section()
        self._build_log_section()

    def _build_date_section(self) -> None:
        section = tk.Frame(self.card, bg=BG_CARD)
        section.pack(fill="x", padx=28, pady=(18, 10))

        tk.Label(
            section,
            text="Дата бюлетеня",
            bg=BG_CARD,
            fg=TEXT_DARK,
            font=("Segoe UI", 12, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 6), columnspan=3)

        self.date_entry = tk.Entry(
            section,
            textvariable=self.date_var,
            width=16,
            font=("Segoe UI", 13),
            relief="solid",
            bd=1,
            justify="center",
        )
        self.date_entry.grid(row=1, column=0, sticky="w", ipady=6)

        self.make_button(
            section,
            text="Вибрати в календарі",
            command=self.open_calendar,
            bg=BLUE,
        ).grid(row=1, column=1, sticky="w", padx=(12, 0))

    def _build_regions_section(self) -> None:
        section = tk.Frame(self.card, bg=BG_CARD)
        section.pack(fill="x", padx=28, pady=(4, 10))

        tk.Label(
            section,
            text="Бюлетені для створення",
            bg=BG_CARD,
            fg=TEXT_DARK,
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        region_card = tk.Frame(
            section,
            bg=SUBTLE_CARD,
            highlightbackground=CARD_BORDER,
            highlightthickness=1,
        )
        region_card.pack(fill="x")

        self.region_checkbuttons: list[tk.Checkbutton] = []
        for region in REGIONS:
            checkbutton = tk.Checkbutton(
                region_card,
                text=region.title,
                variable=self.region_vars[region.key],
                bg=SUBTLE_CARD,
                activebackground=SUBTLE_CARD,
                fg=TEXT_DARK,
                selectcolor="white",
                font=("Segoe UI", 11),
                anchor="w",
                padx=12,
                pady=5,
            )
            checkbutton.pack(fill="x", padx=8, pady=1)
            self.region_checkbuttons.append(checkbutton)

    def _build_operational_tools_section(self) -> None:
        section = tk.Frame(self.card, bg=BG_CARD)
        section.pack(fill="x", padx=28, pady=(4, 10))

        tk.Label(
            section,
            text="Оперативні інструменти",
            bg=BG_CARD,
            fg=TEXT_DARK,
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        tools_card = tk.Frame(
            section,
            bg=SUBTLE_CARD,
            highlightbackground=CARD_BORDER,
            highlightthickness=1,
        )
        tools_card.pack(fill="x")
        self.levels_button = tk.Button(
            tools_card,
            text="Панель рівнів",
            command=self.open_levels_panel,
            bg="#D1EAF4",
            fg=BLUE_DARK,
            activebackground="#B9DDEA",
            activeforeground=BLUE_DARK,
            relief="flat",
            bd=0,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=5,
            width=18,
        )
        self.levels_button.pack(side=tk.LEFT, padx=(14, 8), pady=10)
        self.extremes_button = tk.Button(
            tools_card,
            text="Екстремуми",
            command=self.open_extremes_manager,
            bg="#D1EAF4",
            fg=BLUE_DARK,
            activebackground="#B9DDEA",
            activeforeground=BLUE_DARK,
            relief="flat",
            bd=0,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=5,
            width=18,
        )
        self.extremes_button.pack(side=tk.LEFT, padx=8, pady=10)

    def _build_visuals_section(self) -> None:
        section = tk.Frame(self.card, bg=BG_CARD)
        section.pack(fill="x", padx=28, pady=(4, 10))

        tk.Label(
            section,
            text="Карта й архівні графіки",
            bg=BG_CARD,
            fg=TEXT_DARK,
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        visual_card = tk.Frame(
            section,
            bg=SUBTLE_CARD,
            highlightbackground=CARD_BORDER,
            highlightthickness=1,
        )
        visual_card.pack(fill="x")
        visual_card.columnconfigure(0, weight=1)

        tk.Checkbutton(
            visual_card,
            text="Гідрологічна карта Львівської області",
            variable=self.create_map_var,
            bg=SUBTLE_CARD,
            activebackground=SUBTLE_CARD,
            fg=TEXT_DARK,
            selectcolor="white",
            font=("Segoe UI", 11),
            anchor="w",
            padx=12,
            pady=5,
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 0))

        tk.Checkbutton(
            visual_card,
            text="Графік ходу рівнів води (08:00 і 20:00)",
            variable=self.create_level_chart_var,
            command=self._update_chart_fields,
            bg=SUBTLE_CARD,
            activebackground=SUBTLE_CARD,
            fg=TEXT_DARK,
            selectcolor="white",
            font=("Segoe UI", 11),
            anchor="w",
            padx=12,
            pady=5,
        ).grid(row=1, column=0, sticky="ew", padx=8)

        tk.Checkbutton(
            visual_card,
            text="Графік витрат води",
            variable=self.create_discharge_chart_var,
            command=self._update_chart_fields,
            bg=SUBTLE_CARD,
            activebackground=SUBTLE_CARD,
            fg=TEXT_DARK,
            selectcolor="white",
            font=("Segoe UI", 11),
            anchor="w",
            padx=12,
            pady=5,
        ).grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 4))

        self.chart_fields_frame = tk.Frame(visual_card, bg="#E9F4F8")
        self.chart_fields_frame.columnconfigure(1, weight=1)
        self.chart_fields_frame.grid(
            row=3,
            column=0,
            sticky="ew",
            padx=12,
            pady=(2, 12),
        )

        self._source_label(self.chart_fields_frame, "Гідропост", 0)
        self.chart_station_combo = ttk.Combobox(
            self.chart_fields_frame,
            textvariable=self.chart_station_var,
            values=tuple(CHART_STATION_LABELS),
            state="readonly",
            style="Hydro.TCombobox",
        )
        self.chart_station_combo.grid(
            row=0,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(12, 14),
            pady=(10, 5),
        )

        def add_date_row(
            row: int,
            label: str,
            variable: tk.StringVar,
        ) -> None:
            self._source_label(self.chart_fields_frame, label, row)
            entry = tk.Entry(
                self.chart_fields_frame,
                textvariable=variable,
                width=16,
                font=("Segoe UI", 10),
                relief="solid",
                bd=1,
                justify="center",
            )
            entry.grid(
                row=row,
                column=1,
                sticky="ew",
                padx=(12, 8),
                pady=5,
                ipady=4,
            )
            tk.Button(
                self.chart_fields_frame,
                text="Календар…",
                command=lambda: self.open_calendar(variable),
                bg="#D1EAF4",
                fg=BLUE_DARK,
                activebackground="#B9DDEA",
                activeforeground=BLUE_DARK,
                relief="flat",
                cursor="hand2",
                font=("Segoe UI", 9, "bold"),
                padx=10,
                pady=4,
                width=12,
            ).grid(
                row=row,
                column=2,
                sticky="e",
                padx=(0, 14),
                pady=5,
            )

        add_date_row(1, "Початок періоду", self.chart_start_var)
        add_date_row(2, "Кінець періоду", self.chart_end_var)
        self._update_chart_fields()

    def _build_source_section(self) -> None:
        section = tk.Frame(self.card, bg=BG_CARD)
        section.pack(fill="x", padx=28, pady=(4, 10))

        tk.Label(
            section,
            text="Джерело даних",
            bg=BG_CARD,
            fg=TEXT_DARK,
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        source_card = tk.Frame(
            section,
            bg=SUBTLE_CARD,
            highlightbackground=CARD_BORDER,
            highlightthickness=1,
        )
        source_card.pack(fill="x")
        source_card.columnconfigure(1, weight=1)

        self._source_label(source_card, "Спосіб отримання", 0)
        self.source_combo = ttk.Combobox(
            source_card,
            textvariable=self.source_var,
            values=tuple(SOURCE_LABELS),
            state="readonly",
            style="Hydro.TCombobox",
        )
        self.source_combo.grid(
            row=0,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(12, 14),
            pady=(12, 5),
        )
        self.source_combo.bind(
            "<<ComboboxSelected>>",
            self._update_source_fields,
        )

        self.gcst_frame = tk.Frame(
            source_card,
            bg=SUBTLE_CARD,
        )
        self.gcst_frame.columnconfigure(1, weight=1)
        self.gcst_frame.grid(
            row=1,
            column=0,
            columnspan=3,
            sticky="ew",
        )

        self._source_label(self.gcst_frame, "Сервер ГЦСТ", 0)
        self.gcst_source_combo = ttk.Combobox(
            self.gcst_frame,
            textvariable=self.gcst_source_var,
            values=tuple(GCST_LABEL_TO_MODE),
            state="readonly",
            style="Hydro.TCombobox",
        )
        self.gcst_source_combo.grid(
            row=0,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(12, 14),
            pady=(12, 5),
        )
        self.gcst_source_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.gcst_used_source_var.set(
                "Сервер ще не перевірявся."
            ),
        )
        tk.Label(
            self.gcst_frame,
            text=(
                "Автоматичний режим спочатку перевіряє основний сервер, "
                "а за недоступності або відсутності даних — дзеркало."
            ),
            bg=SUBTLE_CARD,
            fg="#667B85",
            font=("Segoe UI", 9),
            wraplength=640,
            justify="left",
            anchor="w",
        ).grid(
            row=1,
            column=0,
            columnspan=3,
            sticky="ew",
            padx=14,
            pady=(2, 3),
        )
        tk.Label(
            self.gcst_frame,
            textvariable=self.gcst_used_source_var,
            bg=SUBTLE_CARD,
            fg=BLUE_DARK,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).grid(
            row=2,
            column=0,
            columnspan=3,
            sticky="ew",
            padx=14,
            pady=(0, 8),
        )

        self.local_files_frame = tk.Frame(
            source_card,
            bg=SUBTLE_CARD,
        )
        self.local_files_frame.columnconfigure(1, weight=1)
        self.local_files_frame.grid(
            row=2,
            column=0,
            columnspan=3,
            sticky="ew",
        )

        self._path_row(
            self.local_files_frame,
            row=0,
            label="Гідрологічний TXT",
            variable=self.file_var,
            command=lambda: self._choose_file(self.file_var),
        )
        self._path_row(
            self.local_files_frame,
            row=1,
            label="SYNOP TXT (необов'язково)",
            variable=self.meteo_file_var,
            command=lambda: self._choose_file(self.meteo_file_var),
        )

        self.batch_folder_frame = tk.Frame(
            source_card,
            bg=SUBTLE_CARD,
        )
        self.batch_folder_frame.columnconfigure(1, weight=1)
        self.batch_folder_frame.grid(
            row=3,
            column=0,
            columnspan=3,
            sticky="ew",
        )
        self._path_row(
            self.batch_folder_frame,
            row=0,
            label="Папка з ZRUR/SYNOP",
            variable=self.batch_folder_var,
            command=lambda: self._choose_folder(
                self.batch_folder_var,
                "Папка з TXT-файлами за одну дату",
            ),
        )

        self._update_source_fields()

    def _build_output_section(self) -> None:
        section = tk.Frame(self.card, bg=BG_CARD)
        section.pack(fill="x", padx=28, pady=(4, 10))

        tk.Label(
            section,
            text="Збереження матеріалів",
            bg=BG_CARD,
            fg=TEXT_DARK,
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        output_card = tk.Frame(
            section,
            bg=SUBTLE_CARD,
            highlightbackground=CARD_BORDER,
            highlightthickness=1,
        )
        output_card.pack(fill="x")
        output_card.columnconfigure(1, weight=1)
        self._path_row(
            output_card,
            row=0,
            label="Коренева папка",
            variable=self.output_var,
            command=self._choose_output,
        )
        tk.Label(
            output_card,
            text="Підпапки року й місяця програма створює автоматично.",
            bg=SUBTLE_CARD,
            fg="#526B76",
            font=("Segoe UI", 9),
            anchor="w",
        ).grid(
            row=1,
            column=1,
            columnspan=2,
            sticky="w",
            padx=(12, 14),
            pady=(0, 8),
        )

    def _update_source_fields(self, _event: Any = None) -> None:
        source_mode = SOURCE_LABELS.get(self.source_var.get())
        if source_mode == "auto":
            self.gcst_frame.grid()
        else:
            self.gcst_frame.grid_remove()
        if source_mode == "local":
            self.local_files_frame.grid()
        else:
            self.local_files_frame.grid_remove()
        if source_mode == "batch":
            self.batch_folder_frame.grid()
        else:
            self.batch_folder_frame.grid_remove()
        self.root.after_idle(self._refresh_scroll_region)

    def _update_chart_fields(self) -> None:
        charts_selected = (
            self.create_level_chart_var.get()
            or self.create_discharge_chart_var.get()
        )
        if charts_selected:
            self.chart_fields_frame.grid()
        else:
            self.chart_fields_frame.grid_remove()
        self.root.after_idle(self._refresh_scroll_region)

    @staticmethod
    def _source_label(parent: tk.Misc, text: str, row: int) -> None:
        background = parent.cget("bg")
        tk.Label(
            parent,
            text=text,
            bg=background,
            fg=TEXT_DARK,
            font=("Segoe UI", 10),
            anchor="w",
        ).grid(
            row=row,
            column=0,
            sticky="w",
            padx=(14, 0),
            pady=(12, 5) if row == 0 else 5,
        )

    def _path_row(
        self,
        parent: tk.Misc,
        *,
        row: int,
        label: str,
        variable: tk.StringVar,
        command: Callable[[], object],
    ) -> None:
        self._source_label(parent, label, row)
        entry = tk.Entry(
            parent,
            textvariable=variable,
            font=("Segoe UI", 10),
            relief="solid",
            bd=1,
        )
        entry.grid(
            row=row,
            column=1,
            sticky="ew",
            padx=(12, 8),
            pady=5,
            ipady=4,
        )
        tk.Button(
            parent,
            text="Огляд…",
            command=command,
            bg="#D1EAF4",
            fg=BLUE_DARK,
            activebackground="#B9DDEA",
            activeforeground=BLUE_DARK,
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=4,
            width=12,
        ).grid(
            row=row,
            column=2,
            sticky="e",
            padx=(0, 14),
            pady=5,
        )

    def _build_action_section(self) -> None:
        section = tk.Frame(self.card, bg=BG_CARD)
        section.pack(fill="x", padx=28, pady=(8, 10))
        self.run_button = self.make_button(
            section,
            text=RUN_BUTTON_TEXT,
            command=self._start,
            bg=GREEN,
            width=30,
            height=2,
        )
        self.run_button.pack(anchor="center")

    def _build_status_section(self) -> None:
        status_box = tk.Frame(
            self.card,
            bg=SUBTLE_CARD,
            highlightbackground=CARD_BORDER,
            highlightthickness=1,
        )
        status_box.pack(fill="x", padx=28, pady=(4, 14))

        tk.Label(
            status_box,
            text="Статус",
            bg=SUBTLE_CARD,
            fg=BLUE_DARK,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(8, 0))

        status_row = tk.Frame(status_box, bg=SUBTLE_CARD)
        status_row.pack(fill="x", padx=12, pady=(4, 10))
        self.status_label = tk.Label(
            status_row,
            textvariable=self.status_var,
            bg=SUBTLE_CARD,
            fg=TEXT_DARK,
            wraplength=680,
            justify="left",
            anchor="w",
            font=("Segoe UI", 10),
        )
        self.status_label.pack(side=tk.LEFT, fill="x", expand=True)

    def _build_log_section(self) -> None:
        log_box = tk.Frame(
            self.card,
            bg=SUBTLE_CARD,
            highlightbackground=CARD_BORDER,
            highlightthickness=1,
        )
        log_box.pack(fill="both", expand=True, padx=28, pady=(0, 12))

        log_header = tk.Frame(log_box, bg=SUBTLE_CARD)
        log_header.pack(fill="x", padx=12, pady=(8, 2))
        tk.Label(
            log_header,
            text="Хід виконання",
            bg=SUBTLE_CARD,
            fg=BLUE_DARK,
            font=("Segoe UI", 10, "bold"),
        ).pack(side=tk.LEFT)

        tk.Button(
            log_header,
            text="📁 Матеріали",
            command=self.open_output_folder,
            bg=BLUE,
            fg="white",
            activebackground=BLUE_DARK,
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=4,
            width=18,
        ).pack(side=tk.RIGHT)

        log_area = tk.Frame(log_box, bg=SUBTLE_CARD)
        log_area.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.log_text = tk.Text(
            log_area,
            height=9,
            bg="white",
            fg=TEXT_DARK,
            relief="flat",
            wrap="word",
            font=("Segoe UI", 9),
            cursor="arrow",
            takefocus=0,
            exportselection=False,
            selectbackground="white",
            selectforeground=TEXT_DARK,
            inactiveselectbackground="white",
        )
        log_scroll = tk.Scrollbar(
            log_area,
            orient="vertical",
            command=self.log_text.yview,
        )
        self.log_text.configure(
            yscrollcommand=log_scroll.set,
            state="disabled",
        )
        self.log_text.pack(side=tk.LEFT, fill="both", expand=True)
        log_scroll.pack(side=tk.RIGHT, fill="y")

        for event_name in (
            "<Button-1>",
            "<B1-Motion>",
            "<Double-Button-1>",
            "<Triple-Button-1>",
        ):
            self.log_text.bind(event_name, lambda _event: "break")

        self._write_log(
            "Виберіть дату та матеріали для створення.",
            clear=True,
        )

    def _refresh_scroll_region(self, _event: Any = None) -> None:
        self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))

    def _resize_scroll_content(self, event: Any) -> None:
        self.main_canvas.itemconfigure(self.scroll_window, width=event.width)

    @staticmethod
    def _mousewheel_units(event: Any) -> int:
        delta = getattr(event, "delta", 0)
        if not delta:
            return 0
        units = int(-delta / 120)
        if units == 0:
            units = -1 if delta > 0 else 1
        return max(-4, min(4, units))

    def _on_mousewheel(self, event: Any) -> str | None:
        try:
            if event.widget.winfo_toplevel() is not self.root:
                return None
        except tk.TclError:
            return None

        units = self._mousewheel_units(event)
        if units:
            self.main_canvas.yview_scroll(units, "units")
        return "break"

    def _on_main_scrollbar(self, *args: str) -> None:
        if not args:
            return
        command = args[0]
        if command == "moveto" and len(args) > 1:
            try:
                fraction = max(0.0, min(1.0, float(args[1])))
            except (TypeError, ValueError):
                return
            self._pending_main_scroll_fraction = fraction
            if self._main_scroll_after_id is None:
                self._main_scroll_after_id = self.root.after(
                    16,
                    self._flush_main_scrollbar,
                )
            return

        if self._main_scroll_after_id is not None:
            try:
                self.root.after_cancel(self._main_scroll_after_id)
            except tk.TclError:
                pass
            self._main_scroll_after_id = None
        self.main_canvas.yview(*args)

    def _flush_main_scrollbar(self) -> None:
        self._main_scroll_after_id = None
        fraction = self._pending_main_scroll_fraction
        if fraction is None:
            return
        try:
            self.main_canvas.yview_moveto(fraction)
        except tk.TclError:
            pass

    def open_calendar(self, variable: tk.StringVar | None = None) -> None:
        target = variable or self.date_var
        try:
            selected = parse_date(target.get())
        except ValueError:
            selected = datetime.now()
        popup = CalendarPopup(self.root, selected, target.set)
        popup.transient(self.root)
        popup.grab_set()
        popup.focus_force()

    def _choose_file(self, variable: tk.StringVar) -> None:
        selected = filedialog.askopenfilename(
            title="Виберіть TXT-файл",
            filetypes=(("Текстові файли", "*.txt"), ("Усі файли", "*.*")),
            parent=self.root,
        )
        if selected:
            variable.set(selected)

    def _choose_folder(self, variable: tk.StringVar, title: str) -> None:
        selected = filedialog.askdirectory(title=title, parent=self.root)
        if selected:
            variable.set(selected)

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(
            title="Папка для створених матеріалів",
            parent=self.root,
        )
        if selected:
            self.output_var.set(selected)

    def open_output_folder(self) -> None:
        output_text = self.output_var.get().strip()
        if not output_text:
            messagebox.showwarning(
                "Папка результатів",
                "Спочатку виберіть папку результатів.",
                parent=self.root,
            )
            return
        folder = Path(output_text)
        try:
            chart_only = (
                not any(variable.get() for variable in self.region_vars.values())
                and not self.create_map_var.get()
                and (
                    self.create_level_chart_var.get()
                    or self.create_discharge_chart_var.get()
                )
            )
            folder_date = (
                self.chart_end_var.get().strip()
                if chart_only
                else self.date_var.get().strip()
            )
            folder = dated_output_dir(folder, folder_date)
        except ValueError:
            folder.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(folder))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:
            messagebox.showerror(
                "Помилка",
                f"Не вдалося відкрити папку:\n{exc}",
                parent=self.root,
            )

    def _prepare_operational_archive(self) -> Path:
        db_path = (
            self.data_dir
            / "archive"
            / "database"
            / "hydro_archive.sqlite"
        )
        initialize_archive(db_path, ALL_STATIONS)
        seed_extremes_from_templates(
            db_path,
            REGIONS,
            self.resource_dir / "templates" / "bulletins",
        )
        return db_path

    @staticmethod
    def _panel_number(value: float | None, *, signed: bool = False) -> str:
        if value is None:
            return "—"
        number = float(value)
        text = str(int(number)) if number.is_integer() else f"{number:g}"
        return f"+{text}" if signed and number > 0 else text

    def open_levels_panel(self) -> None:
        """Відкриває архівні рівні та контрольовані ручні правки."""

        date_text = self.date_var.get().strip()
        try:
            parse_date(date_text)
            db_path = self._prepare_operational_archive()
            initial_rows = build_level_panel_rows(
                db_path,
                date_text,
                HYDRO_STATIONS,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Панель рівнів", str(exc), parent=self.root)
            return

        window = tk.Toplevel(self.root)
        window.title(f"Панель рівнів — {date_text}")
        window.geometry("1180x690")
        window.minsize(980, 560)
        window.configure(bg=BG_MAIN)
        window.transient(self.root)

        tk.Label(
            window,
            text=f"Рівні на 08:00 {date_text} і 20:00 попередньої доби",
            bg=BG_MAIN,
            fg=BLUE_DARK,
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor="w", padx=20, pady=(18, 4))
        tk.Label(
            window,
            text=(
                "Активна правка застосовується до бюлетенів, карти й графіків; "
                "початкове значення залишається незмінним."
            ),
            bg=BG_MAIN,
            fg=TEXT_DARK,
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=20, pady=(0, 10))

        table_frame = tk.Frame(window, bg=BG_MAIN)
        table_frame.pack(fill="both", expand=True, padx=20)
        columns = (
            "index",
            "station",
            "morning",
            "evening",
            "change",
            "quality",
            "correction",
        )
        tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        headings = {
            "index": "Індекс",
            "station": "Річка — пост",
            "morning": "08:00",
            "evening": "20:00",
            "change": "ΔH",
            "quality": "Статус якості",
            "correction": "Активна правка",
        }
        widths = {
            "index": 72,
            "station": 290,
            "morning": 80,
            "evening": 80,
            "change": 80,
            "quality": 180,
            "correction": 170,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(
                column,
                width=widths[column],
                minwidth=60,
                anchor="w" if column == "station" else "center",
            )
        tree.tag_configure("corrected", background="#E2F0D9")
        tree.tag_configure("warning", background="#FCE8E6")
        tree.tag_configure("missing", foreground="#6B7280")
        vertical = tk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        horizontal = tk.Scrollbar(
            table_frame,
            orient="horizontal",
            command=tree.xview,
        )
        tree.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        details_var = tk.StringVar(master=window, value="Виберіть рядок поста.")
        details = tk.Label(
            window,
            textvariable=details_var,
            bg=SUBTLE_CARD,
            fg=TEXT_DARK,
            justify="left",
            anchor="w",
            wraplength=1080,
            font=("Segoe UI", 9),
            padx=12,
            pady=8,
        )
        details.pack(fill="x", padx=20, pady=(10, 8))

        rows_by_index: dict[str, LevelPanelRow] = {}

        def refresh(rows: tuple[LevelPanelRow, ...] | None = None) -> None:
            current_rows = rows or build_level_panel_rows(
                db_path,
                date_text,
                HYDRO_STATIONS,
            )
            rows_by_index.clear()
            tree.delete(*tree.get_children())
            for item in current_rows:
                rows_by_index[item.station_index] = item
                corrections: list[str] = []
                if item.level_correction_id is not None:
                    corrections.append("рівень")
                if item.change_correction_id is not None:
                    corrections.append("зміна")
                if corrections:
                    tag = "corrected"
                elif item.quality_status == "MISSING":
                    tag = "missing"
                elif item.quality_status not in {"VALID", "OK", "NOT_CHECKED"}:
                    tag = "warning"
                else:
                    tag = ""
                tree.insert(
                    "",
                    "end",
                    iid=item.station_index,
                    values=(
                        item.station_index,
                        item.station_name,
                        self._panel_number(item.morning_level),
                        self._panel_number(item.previous_evening_level),
                        self._panel_number(item.daily_change, signed=True),
                        quality_status_label(item.quality_status),
                        ", ".join(corrections) or "—",
                    ),
                    tags=(tag,) if tag else (),
                )

        def selected_row() -> LevelPanelRow | None:
            selection = tree.selection()
            if not selection:
                messagebox.showwarning(
                    "Панель рівнів",
                    "Спочатку виберіть гідропост у таблиці.",
                    parent=window,
                )
                return None
            return rows_by_index.get(selection[0])

        def show_details(_event: Any = None) -> None:
            selection = tree.selection()
            if not selection:
                return
            item = rows_by_index[selection[0]]
            details_var.set(
                item.quality_message
                or "Зауважень контролю якості для вибраних значень немає."
            )

        def apply_correction(parameter_code: str) -> None:
            item = selected_row()
            if item is None:
                return
            if parameter_code == "WATER_LEVEL":
                observation_id = item.level_observation_id
                current_value = item.morning_level
                label = "рівня на 08:00"
            else:
                observation_id = item.change_observation_id
                current_value = item.daily_change
                label = "добової зміни"
            if observation_id is None or current_value is None:
                messagebox.showerror(
                    "Ручна правка",
                    f"Для {label} немає первинного значення.",
                    parent=window,
                )
                return
            new_value = simpledialog.askinteger(
                "Ручна правка",
                f"Нове значення {label}, см:",
                initialvalue=int(current_value),
                parent=window,
            )
            if new_value is None:
                return
            reason = simpledialog.askstring(
                "Причина правки",
                "Обов'язково вкажіть причину:",
                parent=window,
            )
            if reason is None:
                return
            try:
                create_correction(
                    db_path,
                    observation_id,
                    new_value,
                    reason=reason,
                    hydrologist=DEFAULT_HYDROLOGIST,
                )
                refresh()
                tree.selection_set(item.station_index)
                tree.see(item.station_index)
                show_details()
                self._write_log(
                    f"Створено ручну правку: {item.station_name}, {label}."
                )
            except (OSError, ValueError) as exc:
                messagebox.showerror("Ручна правка", str(exc), parent=window)

        def undo_correction(parameter_code: str) -> None:
            item = selected_row()
            if item is None:
                return
            correction_id = (
                item.level_correction_id
                if parameter_code == "WATER_LEVEL"
                else item.change_correction_id
            )
            if correction_id is None:
                messagebox.showinfo(
                    "Скасування правки",
                    "Для вибраного параметра немає активної правки.",
                    parent=window,
                )
                return
            reason = simpledialog.askstring(
                "Скасування правки",
                "Вкажіть причину скасування:",
                initialvalue="Повторна перевірка первинного значення",
                parent=window,
            )
            if reason is None:
                return
            try:
                cancel_correction(
                    db_path,
                    correction_id,
                    hydrologist=DEFAULT_HYDROLOGIST,
                    reason=reason,
                )
                refresh()
                tree.selection_set(item.station_index)
                tree.see(item.station_index)
                show_details()
                self._write_log(
                    f"Скасовано ручну правку: {item.station_name}."
                )
            except (OSError, ValueError) as exc:
                messagebox.showerror("Скасування правки", str(exc), parent=window)

        tree.bind("<<TreeviewSelect>>", show_details)
        actions = tk.Frame(window, bg=BG_MAIN)
        actions.pack(fill="x", padx=20, pady=(0, 18))
        button_specs = (
            ("Виправити рівень", lambda: apply_correction("WATER_LEVEL"), GREEN),
            ("Виправити ΔH", lambda: apply_correction("DAILY_CHANGE"), GREEN),
            ("Скасувати правку рівня", lambda: undo_correction("WATER_LEVEL"), BLUE),
            ("Скасувати правку ΔH", lambda: undo_correction("DAILY_CHANGE"), BLUE),
        )
        for column, (label, command, color) in enumerate(button_specs):
            actions.columnconfigure(column, weight=1)
            self.make_button(
                actions,
                text=label,
                command=command,
                bg=color,
            ).grid(row=0, column=column, sticky="ew", padx=4)

        refresh(initial_rows)
        if tree.get_children():
            first = tree.get_children()[0]
            tree.selection_set(first)
            show_details()

    def open_extremes_manager(self) -> None:
        """Відкриває довідник максимальних, середніх і мінімальних рівнів."""

        try:
            db_path = self._prepare_operational_archive()
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Екстремуми", str(exc), parent=self.root)
            return

        window = tk.Toplevel(self.root)
        window.title("Екстремуми — багаторічні рівні")
        window.geometry("1040x690")
        window.minsize(900, 560)
        window.configure(bg=BG_MAIN)
        window.transient(self.root)

        tk.Label(
            window,
            text="Довідник багаторічних рівнів",
            bg=BG_MAIN,
            fg=BLUE_DARK,
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor="w", padx=20, pady=(18, 4))
        tk.Label(
            window,
            text=(
                "Значення зберігаються у SQLite й використовуються під час "
                "формування офіційних Word-бюлетенів."
            ),
            bg=BG_MAIN,
            fg=TEXT_DARK,
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=20, pady=(0, 10))

        body = tk.PanedWindow(window, orient=tk.HORIZONTAL, sashwidth=6, bg=BG_MAIN)
        body.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        list_frame = tk.Frame(body, bg=BG_CARD)
        form = tk.Frame(body, bg=BG_CARD, padx=16, pady=14)
        body.add(list_frame, stretch="always", minsize=520)
        body.add(form, stretch="always", minsize=330)

        columns = ("index", "station", "maximum", "average", "minimum")
        tree = ttk.Treeview(list_frame, columns=columns, show="headings")
        for column, title, width in (
            ("index", "Індекс", 70),
            ("station", "Річка — пост", 270),
            ("maximum", "Макс.", 70),
            ("average", "Сер.", 70),
            ("minimum", "Мін.", 70),
        ):
            tree.heading(column, text=title)
            tree.column(
                column,
                width=width,
                anchor="w" if column == "station" else "center",
            )
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill="both", expand=True)
        scrollbar.pack(side=tk.RIGHT, fill="y")

        variables = {
            "maximum_level": tk.StringVar(master=window),
            "maximum_date": tk.StringVar(master=window),
            "average_level": tk.StringVar(master=window),
            "minimum_level": tk.StringVar(master=window),
            "minimum_date": tk.StringVar(master=window),
        }
        selected_station_var = tk.StringVar(
            master=window,
            value="Виберіть гідропост",
        )
        tk.Label(
            form,
            textvariable=selected_station_var,
            bg=BG_CARD,
            fg=BLUE_DARK,
            justify="left",
            wraplength=330,
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        for label, key in (
            ("Максимальний рівень, см", "maximum_level"),
            ("Дата максимуму (необов'язково)", "maximum_date"),
            ("Середній рівень, см", "average_level"),
            ("Мінімальний рівень, см", "minimum_level"),
            ("Дата мінімуму (необов'язково)", "minimum_date"),
        ):
            tk.Label(
                form,
                text=label,
                bg=BG_CARD,
                fg=TEXT_DARK,
                font=("Segoe UI", 9, "bold"),
            ).pack(anchor="w", pady=(6, 3))
            tk.Entry(
                form,
                textvariable=variables[key],
                font=("Segoe UI", 10),
                relief="solid",
                bd=1,
            ).pack(fill="x", ipady=5)

        records: dict[str, DatabaseRow] = {}

        def refresh_extremes(select_index: str | None = None) -> None:
            records.clear()
            records.update(read_reference_extremes(db_path))
            tree.delete(*tree.get_children())
            for station in HYDRO_STATIONS:
                record = records.get(station.index, {})
                tree.insert(
                    "",
                    "end",
                    iid=station.index,
                    values=(
                        station.index,
                        station.name,
                        record.get("maximum_level", "—"),
                        record.get("average_level", "—"),
                        record.get("minimum_level", "—"),
                    ),
                )
            if select_index and tree.exists(select_index):
                tree.selection_set(select_index)
                tree.see(select_index)

        def load_selected(_event: Any = None) -> None:
            selection = tree.selection()
            if not selection:
                return
            station_index = selection[0]
            station = next(
                item for item in HYDRO_STATIONS if item.index == station_index
            )
            selected_station_var.set(f"{station.index} — {station.name}")
            record = records.get(station_index, {})
            for key, variable in variables.items():
                variable.set(str(record.get(key, "") or ""))

        def optional_date(value: str) -> str:
            text = value.strip()
            if not text:
                return ""
            try:
                return datetime.strptime(text, "%d.%m.%Y").strftime("%d.%m.%Y")
            except ValueError as exc:
                raise ValueError("Дати екстремумів мають формат ДД.ММ.РРРР.") from exc

        def save_extreme() -> None:
            selection = tree.selection()
            if not selection:
                messagebox.showwarning(
                    "Екстремуми",
                    "Спочатку виберіть гідропост.",
                    parent=window,
                )
                return
            station_index = selection[0]
            try:
                maximum = int(variables["maximum_level"].get().strip())
                average = int(variables["average_level"].get().strip())
                minimum = int(variables["minimum_level"].get().strip())
                upsert_reference_extreme(
                    db_path,
                    station_index=station_index,
                    maximum_level=maximum,
                    average_level=average,
                    minimum_level=minimum,
                    maximum_date=optional_date(variables["maximum_date"].get()),
                    minimum_date=optional_date(variables["minimum_date"].get()),
                    updated_by=DEFAULT_HYDROLOGIST,
                )
                refresh_extremes(station_index)
                load_selected()
                self._write_log(
                    f"Оновлено екстремуми для гідропоста {station_index}."
                )
                messagebox.showinfo(
                    "Екстремуми",
                    "Довідникові значення збережено.",
                    parent=window,
                )
            except (OSError, ValueError) as exc:
                messagebox.showerror("Екстремуми", str(exc), parent=window)

        tree.bind("<<TreeviewSelect>>", load_selected)
        self.make_button(
            form,
            text="Зберегти екстремуми",
            command=save_extreme,
            bg=GREEN,
        ).pack(fill="x", pady=(18, 0))

        refresh_extremes()
        if tree.get_children():
            first = tree.get_children()[0]
            tree.selection_set(first)
            load_selected()

    def _request(self) -> WorkflowRequest:
        region_keys = tuple(
            region.key
            for region in REGIONS
            if self.region_vars[region.key].get()
        )
        create_map = self.create_map_var.get()
        create_level_chart = self.create_level_chart_var.get()
        create_discharge_chart = self.create_discharge_chart_var.get()
        if not (
            region_keys
            or create_map
            or create_level_chart
            or create_discharge_chart
        ):
            raise ValueError("Потрібно вибрати хоча б один матеріал для створення.")

        def optional_path(value: str) -> Path | None:
            text = value.strip()
            return Path(text) if text else None

        output_text = self.output_var.get().strip()
        if not output_text:
            raise ValueError("Потрібно вибрати папку результатів.")

        source_mode = SOURCE_LABELS[self.source_var.get()]
        local_file = (
            optional_path(self.file_var.get())
            if source_mode == "local"
            else None
        )
        meteo_file = (
            optional_path(self.meteo_file_var.get())
            if source_mode == "local"
            else None
        )
        batch_folder = (
            optional_path(self.batch_folder_var.get())
            if source_mode == "batch"
            else None
        )
        if source_mode == "local" and local_file is None:
            raise ValueError("Потрібно вибрати гідрологічний TXT-файл.")
        if source_mode == "batch" and batch_folder is None:
            raise ValueError("Потрібно вибрати папку з TXT-файлами.")

        message_regions = region_keys
        if create_map and "lviv" not in message_regions:
            message_regions += ("lviv",)
        message_types = (
            (message_type_from_file(local_file),)
            if local_file is not None
            else message_types_for_regions(message_regions)
        )
        chart_station_index = (
            station_index_from_label(self.chart_station_var.get())
            if create_level_chart or create_discharge_chart
            else None
        )

        return WorkflowRequest(
            bulletin_date=self.date_var.get().strip(),
            source_mode=source_mode,
            message_types=message_types,
            local_file=local_file,
            meteo_file=meteo_file,
            batch_folder=batch_folder,
            db_path=(
                self.data_dir
                / "archive"
                / "database"
                / "hydro_archive.sqlite"
            ),
            raw_root=self.data_dir / "archive" / "raw",
            templates_dir=self.resource_dir / "templates" / "bulletins",
            output_dir=Path(output_text),
            mapping_path=(
                self.resource_dir
                / "config"
                / "precipitation_mapping.json"
            ),
            env_path=self.data_dir / ".env",
            gcst_source_mode=GCST_LABEL_TO_MODE[self.gcst_source_var.get()],
            region_keys=region_keys,
            hydrologist=DEFAULT_HYDROLOGIST,
            include_meteo=bool(region_keys),
            create_bulletins=bool(region_keys),
            create_map=create_map,
            map_template_path=(
                self.resource_dir
                / "templates"
                / "maps"
                / "HydroMap_UHMC_Lviv_template_clean.png"
            ),
            font_path=(
                self.resource_dir
                / "resources"
                / "fonts"
                / "e-Ukraine-Regular.otf"
            ),
            chart_station_index=chart_station_index,
            chart_start_date=self.chart_start_var.get().strip(),
            chart_end_date=self.chart_end_var.get().strip(),
            create_level_chart=create_level_chart,
            create_discharge_chart=create_discharge_chart,
        )

    def _start(self) -> None:
        try:
            request = self._request()
        except (KeyError, ValueError) as exc:
            messagebox.showerror(
                "HydroBulletin",
                str(exc),
                parent=self.root,
            )
            return

        self.run_button.configure(state="disabled", text="Створюю…")
        self.status_var.set("Створюю вибрані матеріали…")
        self._write_log("Запуск: перевіряю вибрані параметри.", clear=True)
        self.root.update_idletasks()
        threading.Thread(
            target=self._worker,
            args=(request,),
            daemon=True,
        ).start()

    def _worker(self, request: WorkflowRequest) -> None:
        try:
            result = execute_workflow(
                request,
                progress=lambda message: self.root.after(
                    0,
                    self._write_log,
                    message,
                ),
            )
        except Exception as exc:
            self.root.after(0, self._finish_error, exc)
            return
        self.root.after(0, self._finish_success, result)

    def _finish_error(self, exc: Exception) -> None:
        self.run_button.configure(state="normal", text=RUN_BUTTON_TEXT)
        self.status_var.set("Помилка. Матеріали не створено.")
        self._write_log(f"Помилка: {exc}")
        messagebox.showerror(
            "HydroBulletin",
            str(exc),
            parent=self.root,
        )

    def _finish_success(self, result: WorkflowResult) -> None:
        self.run_button.configure(state="normal", text=RUN_BUTTON_TEXT)
        self.status_var.set("Створено.")

        gcst_summary = gcst_usage_summary(
            result,
            GCST_LABEL_TO_MODE[self.gcst_source_var.get()],
        )
        if gcst_summary is not None:
            self.gcst_used_source_var.set(gcst_summary)
        else:
            self.gcst_used_source_var.set(
                "Онлайн-сервер ГЦСТ не використовувався."
            )

        lines = [
            f"Гідрологічних імпортів: {len(result.hydro_imports)}",
            f"SYNOP імпортовано: {'так' if result.meteo_import else 'ні'}",
            (
                "Контролем якості перевірено значень: "
                f"{result.quality_summary.checked}"
            ),
            f"Створено бюлетенів: {len(result.bulletins)}",
            f"Створено карту: {'так' if result.map_result else 'ні'}",
            f"Створено графіків: {len(result.charts)}",
        ]
        if gcst_summary is not None:
            lines.append(gcst_summary)
        lines.extend(f"• {item.output_path}" for item in result.bulletins)
        if result.map_result is not None:
            lines.append(f"• {result.map_result.output_path}")
        lines.extend(f"• {item.output_path}" for item in result.charts)
        if result.warnings:
            lines.append("Зауваження:")
            lines.extend(f"• {warning}" for warning in result.warnings)
        self._write_log("\n".join(lines))

        created_paths = [item.output_path for item in result.bulletins]
        if result.map_result is not None:
            created_paths.append(result.map_result.output_path)
        created_paths.extend(item.output_path for item in result.charts)
        if created_paths:
            output_folder = created_paths[0].parent
            names = "\n".join(f"• {path.name}" for path in created_paths)
            messagebox.showinfo(
                "HydroBulletin — Створено",
                f"Створено матеріали:\n\n{names}\n\nПапка:\n{output_folder}",
                parent=self.root,
            )

    def _write_log(self, text: str, *, clear: bool = False) -> None:
        if not hasattr(self, "log_text"):
            return
        self.log_text.configure(state="normal")
        if clear:
            self.log_text.delete("1.0", tk.END)
        timestamp = datetime.now().strftime("%H:%M:%S")
        for line in text.splitlines() or ("",):
            self.log_text.insert(tk.END, f"{timestamp} — {line}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")


def launch_gui(resource_dir: Path, data_dir: Path | None = None) -> None:
    """Налаштовує масштабування Windows і запускає GUI."""

    enable_high_dpi_awareness()
    root = tk.Tk()
    configure_tk_scaling(root)
    HydroBulletinApp(root, resource_dir, data_dir)
    root.mainloop()
