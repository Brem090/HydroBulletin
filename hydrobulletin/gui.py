"""Графічний інтерфейс HydroBulletin."""

from __future__ import annotations

import calendar
import os
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .regions import REGIONS
from .sources import SUPPORTED_MESSAGE_TYPES
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

SOURCE_LABELS = {
    "Автоматично": "auto",
    "Локальний TXT-файл": "local",
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


def message_types_for_regions(region_keys: tuple[str, ...]) -> tuple[str, ...]:
    """Визначає потрібні гідрологічні повідомлення для вибраних бюлетенів."""

    message_types = ["ZRUR52"]
    if "lviv" in region_keys:
        message_types.append("ZRUR71")
    return tuple(message_types)


def message_type_from_file(path: Path) -> str:
    """Визначає тип повідомлення за назвою локального файла."""

    filename = Path(path).name.upper()
    for message_type in SUPPORTED_MESSAGE_TYPES:
        if message_type in filename:
            return message_type
    return "ZRUR52"


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
        callback,
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

    def __init__(self, root: tk.Tk, project_dir: Path) -> None:
        self.root = root
        self.project_dir = Path(project_dir)

        self.root.title("HydroBulletin")
        self.root.geometry("1060x720")
        self.root.minsize(980, 620)
        self.root.resizable(True, True)
        self.root.configure(bg=BG_MAIN)

        self.date_var = tk.StringVar(
            master=self.root,
            value=format_date_for_output(datetime.now()),
        )
        self.source_var = tk.StringVar(
            master=self.root,
            value=next(iter(SOURCE_LABELS)),
        )
        self.file_var = tk.StringVar(
            master=self.root,
            value=str(
                self.project_dir
                / "demo_data"
                / "week3"
                / "12.07.2026_ZRUR52.txt"
            ),
        )
        self.meteo_file_var = tk.StringVar(
            master=self.root,
            value=str(
                self.project_dir
                / "demo_data"
                / "week3"
                / "12.07.2026_SYNOP.txt"
            ),
        )
        self.output_var = tk.StringVar(
            master=self.root,
            value=str(self.project_dir / "output" / "week3"),
        )
        self.region_vars = {
            region.key: tk.BooleanVar(master=self.root, value=False)
            for region in REGIONS
        }
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
        parent,
        text: str,
        command,
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

        self.local_files_frame = tk.Frame(
            source_card,
            bg=SUBTLE_CARD,
        )
        self.local_files_frame.columnconfigure(1, weight=1)
        self.local_files_frame.grid(
            row=1,
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
            label="Папка матеріалів",
            variable=self.output_var,
            command=self._choose_output,
        )

    def _update_source_fields(self, _event=None) -> None:
        if SOURCE_LABELS.get(self.source_var.get()) == "local":
            self.local_files_frame.grid()
        else:
            self.local_files_frame.grid_remove()
        self.root.after_idle(self._refresh_scroll_region)

    @staticmethod
    def _source_label(parent, text: str, row: int) -> None:
        tk.Label(
            parent,
            text=text,
            bg=SUBTLE_CARD,
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
        parent,
        *,
        row: int,
        label: str,
        variable: tk.StringVar,
        command,
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
            text="✓ Створити вибрані матеріали",
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
            "Виберіть дату та потрібні бюлетені.",
            clear=True,
        )

    def _refresh_scroll_region(self, _event=None) -> None:
        self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))

    def _resize_scroll_content(self, event) -> None:
        self.main_canvas.itemconfigure(self.scroll_window, width=event.width)

    @staticmethod
    def _mousewheel_units(event) -> int:
        delta = getattr(event, "delta", 0)
        if not delta:
            return 0
        units = int(-delta / 120)
        if units == 0:
            units = -1 if delta > 0 else 1
        return max(-4, min(4, units))

    def _on_mousewheel(self, event):
        try:
            if event.widget.winfo_toplevel() is not self.root:
                return None
        except tk.TclError:
            return None

        units = self._mousewheel_units(event)
        if units:
            self.main_canvas.yview_scroll(units, "units")
        return "break"

    def _on_main_scrollbar(self, *args) -> None:
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

    def open_calendar(self) -> None:
        try:
            selected = parse_date(self.date_var.get())
        except ValueError:
            selected = datetime.now()
        popup = CalendarPopup(self.root, selected, self.date_var.set)
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

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(
            title="Папка для Word-бюлетенів",
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

    def _request(self) -> WorkflowRequest:
        region_keys = tuple(
            region.key
            for region in REGIONS
            if self.region_vars[region.key].get()
        )

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
        if source_mode == "local" and local_file is None:
            raise ValueError("Потрібно вибрати гідрологічний TXT-файл.")

        message_types = (
            (message_type_from_file(local_file),)
            if local_file is not None
            else message_types_for_regions(region_keys)
        )

        return WorkflowRequest(
            bulletin_date=self.date_var.get().strip(),
            source_mode=source_mode,
            message_types=message_types,
            local_file=local_file,
            meteo_file=meteo_file,
            db_path=(
                self.project_dir
                / "archive"
                / "database"
                / "hydro_archive.sqlite"
            ),
            raw_root=self.project_dir / "archive" / "raw",
            templates_dir=self.project_dir / "templates" / "bulletins",
            output_dir=Path(output_text),
            mapping_path=(
                self.project_dir
                / "config"
                / "precipitation_mapping.json"
            ),
            env_path=self.project_dir / ".env",
            region_keys=region_keys,
            hydrologist=DEFAULT_HYDROLOGIST,
            include_meteo=True,
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

        self.run_button.configure(state="disabled")
        self.status_var.set("Виконується…")
        self._write_log("Запуск робочого сценарію…", clear=True)
        threading.Thread(
            target=self._worker,
            args=(request,),
            daemon=True,
        ).start()

    def _worker(self, request: WorkflowRequest) -> None:
        try:
            result = execute_workflow(request)
        except Exception as exc:
            self.root.after(0, self._finish_error, exc)
            return
        self.root.after(0, self._finish_success, result)

    def _finish_error(self, exc: Exception) -> None:
        self.run_button.configure(state="normal")
        self.status_var.set("Помилка.")
        self._write_log(f"ПОМИЛКА: {exc}")
        messagebox.showerror(
            "HydroBulletin",
            str(exc),
            parent=self.root,
        )

    def _finish_success(self, result: WorkflowResult) -> None:
        self.run_button.configure(state="normal")
        self.status_var.set("Завершено.")

        lines = [
            f"Гідрологічних імпортів: {len(result.hydro_imports)}",
            f"SYNOP імпортовано: {'так' if result.meteo_import else 'ні'}",
            f"QC перевірено значень: {result.quality_summary.checked}",
            f"Створено бюлетенів: {len(result.bulletins)}",
        ]
        lines.extend(f"• {item.output_path}" for item in result.bulletins)
        if result.warnings:
            lines.append("Зауваження:")
            lines.extend(f"• {warning}" for warning in result.warnings)
        self._write_log("\n".join(lines))

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


def launch_gui(project_dir: Path) -> None:
    """Налаштовує масштабування Windows і запускає GUI."""

    enable_high_dpi_awareness()
    root = tk.Tk()
    configure_tk_scaling(root)
    HydroBulletinApp(root, project_dir)
    root.mainloop()
