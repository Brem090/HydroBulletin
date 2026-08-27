"""Архівні графіки рівнів і витрат води з даних SQLite."""

from __future__ import annotations

import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Iterable, Sequence

os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path(tempfile.gettempdir()) / "hydrobulletin-matplotlib"),
)

import matplotlib

matplotlib.use("Agg")

from matplotlib import dates as mdates
from matplotlib.artist import Artist
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties, fontManager
from matplotlib.legend import Legend
from matplotlib.ticker import MaxNLocator

from .archive import (
    DatabaseRow,
    ProductResult,
    database_float,
    query_observations,
    register_product,
    required_database_int,
)


LEVEL_CHART = "LEVEL"
DISCHARGE_CHART = "DISCHARGE"
VALID_STATUSES = {"VALID", "OK"}
SERIES_COLOR = "#176FA6"
GAP_COLOR = "#8CA2AF"

MONTHS_UA_GENITIVE = {
    1: "січня",
    2: "лютого",
    3: "березня",
    4: "квітня",
    5: "травня",
    6: "червня",
    7: "липня",
    8: "серпня",
    9: "вересня",
    10: "жовтня",
    11: "листопада",
    12: "грудня",
}


@dataclass(frozen=True)
class ChartResult:
    """Створений графік та короткий підсумок використаних даних."""

    chart_type: str
    station_index: str
    station_name: str
    output_path: Path
    available_points: int
    missing_points: int
    flagged_points: int
    product: ProductResult


def chart_output_name(
    chart_type: str,
    station_index: str,
    start_date: str,
    end_date: str,
) -> str:
    """Повертає стабільну назву файлу без залежності від назви поста."""

    start, end = _parse_period(start_date, end_date)
    normalized_type = chart_type.upper()
    if normalized_type == LEVEL_CHART:
        prefix = "WaterLevel"
    elif normalized_type == DISCHARGE_CHART:
        prefix = "Discharge"
    else:
        raise ValueError(f"Невідомий тип графіка: {chart_type}")
    return f"{prefix}_{station_index}_{start:%Y-%m-%d}_{end:%Y-%m-%d}.png"


def _parse_period(start_date: str, end_date: str) -> tuple[datetime, datetime]:
    try:
        start = datetime.strptime(start_date, "%d.%m.%Y")
        end = datetime.strptime(end_date, "%d.%m.%Y")
    except ValueError as exc:
        raise ValueError("Дати періоду мають бути у форматі ДД.ММ.РРРР.") from exc
    if start > end:
        raise ValueError("Початкова дата не може бути пізнішою за кінцеву.")
    return start, end


def _days(start: datetime, end: datetime) -> tuple[datetime, ...]:
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _row_time(row: DatabaseRow) -> datetime:
    return datetime.fromisoformat(str(row["observed_at"]))


def _rows_by_timestamp(
    rows: Iterable[DatabaseRow],
) -> dict[datetime, DatabaseRow]:
    result: dict[datetime, DatabaseRow] = {}
    for row in rows:
        observed_at = _row_time(row)
        result[observed_at.replace(microsecond=0)] = row
    return result


def _regular_series(
    rows: Iterable[DatabaseRow],
    start: datetime,
    end: datetime,
    hours: Sequence[int],
) -> tuple[tuple[datetime, ...], tuple[float | None, ...]]:
    """Розкладає вимірювання в один хронологічний ряд із заданим кроком."""

    by_timestamp = _rows_by_timestamp(rows)
    timestamps = tuple(
        datetime.combine(day.date(), time(hour))
        for day in _days(start, end)
        for hour in hours
    )
    values = tuple(_number(by_timestamp.get(timestamp)) for timestamp in timestamps)
    return timestamps, values


def _number(row: DatabaseRow | None) -> float | None:
    return None if row is None else database_float(row["value"])


def _font_properties(font_path: Path | None) -> FontProperties:
    if font_path is not None:
        path = Path(font_path)
        if not path.exists():
            raise FileNotFoundError(f"Шрифт графіків не знайдено: {path}")
        try:
            fontManager.addfont(str(path))
            return FontProperties(fname=str(path))
        except (OSError, RuntimeError) as exc:
            raise RuntimeError(f"Не вдалося відкрити шрифт графіків: {path}") from exc
    return FontProperties(family="DejaVu Sans")


def _apply_font(axes: Axes, font: FontProperties) -> None:
    for label in (*axes.get_xticklabels(), *axes.get_yticklabels()):
        label.set_fontproperties(font)


def _quality_marker(status: str) -> tuple[str, str] | None:
    if status in VALID_STATUSES or status == "MISSING":
        return None
    if status == "OUT_OF_RANGE":
        return "#C0392B", "Поза допустимим діапазоном"
    return "#F39C12", "Потребує уваги (QC)"


def _date_numbers(values: Iterable[datetime]) -> list[float]:
    return [float(mdates.date2num(value)) for value in values]


def _plot_quality_points(axes: Axes, rows: Iterable[DatabaseRow]) -> int:
    labels_used: set[str] = set()
    flagged = 0
    for row in rows:
        value = _number(row)
        if value is None:
            continue
        marker = _quality_marker(str(row["quality_status"]))
        if marker is None:
            continue
        color, label = marker
        axes.scatter(
            _date_numbers((_row_time(row),)),
            [value],
            color=color,
            edgecolors="white",
            linewidths=0.6,
            s=36,
            zorder=5,
            label=label if label not in labels_used else None,
        )
        labels_used.add(label)
        flagged += 1
    return flagged


def _available_values(values: Iterable[float | None]) -> list[float]:
    available = [value for value in values if value is not None]
    if not available:
        raise ValueError("За вибраний період немає числових значень для графіка.")
    return available


def _plot_hydrograph_series(
    axes: Axes,
    timestamps: Sequence[datetime],
    values: Sequence[float | None],
) -> None:
    """Малює гідрологічну криву та позначає внутрішні пропуски даних."""

    available_indexes = [
        index for index, value in enumerate(values) if value is not None
    ]
    show_markers = len(available_indexes) <= 60

    gap_label_used = False
    for previous, current in zip(available_indexes, available_indexes[1:]):
        if current - previous <= 1:
            continue
        previous_value = values[previous]
        current_value = values[current]
        assert previous_value is not None
        assert current_value is not None
        axes.plot(
            _date_numbers((timestamps[previous], timestamps[current])),
            [previous_value, current_value],
            color=GAP_COLOR,
            linewidth=0.9,
            linestyle=(0, (3, 3)),
            label=(
                "Проміжок без спостережень"
                if not gap_label_used
                else None
            ),
            zorder=1,
        )
        gap_label_used = True

    segment_start: int | None = None
    for index in range(len(values) + 1):
        value = values[index] if index < len(values) else None
        if value is not None and segment_start is None:
            segment_start = index
        if value is not None or segment_start is None:
            continue

        segment_end = index
        segment_times = _date_numbers(timestamps[segment_start:segment_end])
        segment_values = [
            item
            for item in values[segment_start:segment_end]
            if item is not None
        ]
        marker = "o" if show_markers or len(segment_values) == 1 else None
        axes.plot(
            segment_times,
            segment_values,
            color=SERIES_COLOR,
            linewidth=1.45,
            marker=marker,
            markersize=2.8,
            markerfacecolor=SERIES_COLOR,
            markeredgewidth=0,
            solid_capstyle="round",
            solid_joinstyle="round",
            zorder=2,
        )
        segment_start = None


def _configure_y_axis(
    axes: Axes,
    values: Iterable[float | None],
    *,
    nonnegative: bool,
    integer_ticks: bool,
) -> None:
    available = _available_values(values)
    minimum = min(available)
    maximum = max(available)
    span = maximum - minimum
    margin = max(
        span * 0.08,
        abs(maximum) * 0.025,
        1.0 if integer_ticks else 0.0,
    )
    if margin == 0:
        margin = 0.02
    lower = minimum - margin
    if nonnegative and minimum >= 0:
        lower = max(0.0, lower)
    upper = maximum + margin
    if lower == upper:
        upper = lower + (1.0 if integer_ticks else 0.1)
    axes.set_ylim(lower, upper)
    axes.yaxis.set_major_locator(
        MaxNLocator(nbins=8, integer=integer_ticks, min_n_ticks=4)
    )


def _period_caption(start: datetime, end: datetime) -> str:
    if start.date() == end.date():
        return (
            f"{start.day} {MONTHS_UA_GENITIVE[start.month]} "
            f"{start.year} року"
        )
    if start.year == end.year and start.month == end.month:
        return (
            f"{start.day}–{end.day} {MONTHS_UA_GENITIVE[start.month]} "
            f"{start.year} року"
        )
    if start.year == end.year:
        return (
            f"{start.day} {MONTHS_UA_GENITIVE[start.month]} – "
            f"{end.day} {MONTHS_UA_GENITIVE[end.month]} {end.year} року"
        )
    return (
        f"{start.day} {MONTHS_UA_GENITIVE[start.month]} {start.year} року – "
        f"{end.day} {MONTHS_UA_GENITIVE[end.month]} {end.year} року"
    )


def _configure_axes(
    figure: Figure,
    axes: Axes,
    *,
    title: str,
    subtitle: str,
    y_label: str,
    start: datetime,
    end: datetime,
    timestamps: Sequence[datetime],
    font: FontProperties,
    show_hours: bool = False,
) -> None:
    figure.suptitle(
        title,
        fontsize=13,
        fontweight="semibold",
        y=0.965,
        fontproperties=font,
    )
    axes.set_title(
        subtitle,
        fontsize=9.5,
        color="#4E5960",
        pad=10,
        fontproperties=font,
    )
    axes.set_xlabel("Дата", fontsize=10, fontproperties=font, labelpad=8)
    axes.set_ylabel(y_label, fontsize=10, fontproperties=font, labelpad=8)
    axes.grid(
        True,
        which="major",
        axis="both",
        color="#D5DADD",
        linewidth=0.65,
        alpha=0.95,
    )
    axes.set_axisbelow(True)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color("#8C969B")
        axes.spines[side].set_linewidth(0.75)
        axes.spines[side].set_zorder(0)

    duration_days = (end - start).days + 1
    short_period = duration_days <= 7
    if short_period:
        max_ticks = 10
        step = max(1, (len(timestamps) + max_ticks - 1) // max_ticks)
        tick_times = list(timestamps[::step])
        if timestamps[-1] not in tick_times:
            tick_times.append(timestamps[-1])
        axes.set_xticks(_date_numbers(tick_times))
        has_multiple_hours = len({value.hour for value in timestamps}) > 1
        date_format = (
            "%d.%m\n%H:%M" if show_hours or has_multiple_hours else "%d.%m"
        )
        axes.xaxis.set_major_formatter(mdates.DateFormatter(date_format))
    else:
        locator = mdates.AutoDateLocator(
            minticks=5,
            maxticks=13,
            interval_multiples=True,
        )
        date_format = "%d.%m" if start.year == end.year else "%d.%m.%Y"
        axes.xaxis.set_major_locator(locator)
        axes.xaxis.set_major_formatter(mdates.DateFormatter(date_format))
    if duration_days <= 45:
        axes.xaxis.set_minor_locator(mdates.DayLocator(interval=1))
        axes.grid(
            True,
            which="minor",
            axis="x",
            color="#ECEFF1",
            linewidth=0.4,
            alpha=0.9,
        )
    axis_start = timestamps[0]
    axis_end = timestamps[-1]
    if axis_start == axis_end:
        padding = timedelta(hours=1 if show_hours else 12)
        left_limit = axis_start - padding
        right_limit = axis_end + padding
    else:
        padding = max(
            timedelta(hours=1),
            (axis_end - axis_start) / 25,
        )
        left_limit = axis_start - padding
        right_limit = axis_end + padding
    axes.set_xlim(
        float(mdates.date2num(left_limit)),
        float(mdates.date2num(right_limit)),
    )
    axes.tick_params(axis="both", which="major", labelsize=8.5, colors="#30383C")
    axes.tick_params(axis="x", which="minor", length=0)
    _apply_font(axes, font)
    for label in axes.get_xticklabels():
        label.set_rotation(0 if short_period else 45)
        label.set_horizontalalignment("center" if short_period else "right")
    figure.subplots_adjust(left=0.095, right=0.975, bottom=0.19, top=0.84)


def _deduplicated_legend(
    figure: Figure,
    axes: Axes,
    font: FontProperties,
) -> Legend | None:
    """Розміщує умовні позначення в окремому рядку під графіком."""

    handles, labels = axes.get_legend_handles_labels()
    unique: dict[str, Artist] = {}
    for handle, label in zip(handles, labels):
        if label and label not in unique:
            unique[label] = handle
    if unique:
        legend_font = font.copy()
        legend_font.set_size(8.5)
        return figure.legend(
            unique.values(),
            unique.keys(),
            loc="lower center",
            bbox_to_anchor=(0.5, 0.012),
            ncol=min(len(unique), 3),
            frameon=False,
            prop=legend_font,
            handlelength=2.5,
            handletextpad=0.6,
            columnspacing=1.5,
        )
    return None


def _save_figure(figure: Figure, output_path: Path, title: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    FigureCanvasAgg(figure)
    figure.savefig(
        output_path,
        format="png",
        dpi=150,
        facecolor="white",
        metadata={"Title": title, "Software": "HydroBulletin"},
    )


def _register_chart(
    db_path: Path,
    *,
    chart_type: str,
    station_index: str,
    station_name: str,
    start_date: str,
    end_date: str,
    output_path: Path,
    rows: list[DatabaseRow],
    available_points: int,
    missing_points: int,
    flagged_points: int,
) -> ProductResult:
    parameter = "WATER_LEVEL" if chart_type == LEVEL_CHART else "DISCHARGE"
    quality_counts = Counter(str(row["quality_status"]) for row in rows)
    return register_product(
        db_path,
        product_type=f"{chart_type}_CHART",
        region_key=station_index,
        bulletin_date=end_date,
        output_path=str(output_path),
        observation_ids=(
            required_database_int(row["observation_id"], "observation_id")
            for row in rows
        ),
        metadata={
            "station_index": station_index,
            "station_name": station_name,
            "parameter": parameter,
            "start_date": start_date,
            "end_date": end_date,
            "available_points": available_points,
            "missing_points": missing_points,
            "flagged_points": flagged_points,
            "series_mode": (
                "chronological_08_20"
                if chart_type == LEVEL_CHART
                else "daily_08"
            ),
            "gap_rendering": "dashed_between_available_points",
            "quality_counts": dict(quality_counts),
        },
    )


def create_level_chart(
    db_path: Path,
    *,
    station_index: str,
    start_date: str,
    end_date: str,
    output_path: Path,
    font_path: Path | None = None,
) -> ChartResult:
    """Будує один хронологічний ряд рівнів 08:00 → 20:00 → 08:00."""

    start, end = _parse_period(start_date, end_date)
    rows = query_observations(
        db_path,
        start_date=start_date,
        end_date=end_date,
        station_indexes=(station_index,),
        parameter_codes=("WATER_LEVEL",),
    )
    used_rows = [
        row
        for row in rows
        if _row_time(row).hour in {8, 20} and _row_time(row).minute == 0
    ]
    if not used_rows:
        raise ValueError(
            f"У SQLite немає рівнів води для поста {station_index} "
            f"за період {start_date}–{end_date}."
        )

    station_name = str(used_rows[0]["station_name"])
    timestamps, values = _regular_series(used_rows, start, end, (8, 20))
    _available_values(values)
    last_observed = max(
        index for index, value in enumerate(values) if value is not None
    )

    font = _font_properties(font_path)
    figure = Figure(figsize=(11.5, 6.3), dpi=150)
    axes = figure.subplots()
    _plot_hydrograph_series(axes, timestamps, values)
    flagged_points = _plot_quality_points(axes, used_rows)
    _configure_y_axis(
        axes,
        values,
        nonnegative=False,
        integer_ticks=True,
    )
    title = f"Хід рівнів води: {station_name} ({station_index})"
    _configure_axes(
        figure,
        axes,
        title=title,
        subtitle=_period_caption(start, end),
        y_label="Рівень води, см над нулем поста",
        start=start,
        end=end,
        timestamps=timestamps[:last_observed + 1],
        font=font,
        show_hours=True,
    )
    _deduplicated_legend(figure, axes, font)
    output_path = Path(output_path)
    _save_figure(figure, output_path, title)

    available_points = sum(value is not None for value in values)
    product = _register_chart(
        db_path,
        chart_type=LEVEL_CHART,
        station_index=station_index,
        station_name=station_name,
        start_date=start_date,
        end_date=end_date,
        output_path=output_path,
        rows=used_rows,
        available_points=available_points,
        missing_points=len(values) - available_points,
        flagged_points=flagged_points,
    )
    return ChartResult(
        LEVEL_CHART,
        station_index,
        station_name,
        output_path,
        available_points,
        len(values) - available_points,
        flagged_points,
        product,
    )


def create_discharge_chart(
    db_path: Path,
    *,
    station_index: str,
    start_date: str,
    end_date: str,
    output_path: Path,
    font_path: Path | None = None,
) -> ChartResult:
    """Будує добовий гідрограф витрат води."""

    start, end = _parse_period(start_date, end_date)
    rows = query_observations(
        db_path,
        start_date=start_date,
        end_date=end_date,
        station_indexes=(station_index,),
        parameter_codes=("DISCHARGE",),
    )
    used_rows = [
        row
        for row in rows
        if _row_time(row).hour == 8 and _row_time(row).minute == 0
    ]
    if not used_rows:
        raise ValueError(
            f"У SQLite немає витрат води для поста {station_index} "
            f"за період {start_date}–{end_date}."
        )

    station_name = str(used_rows[0]["station_name"])
    timestamps, values = _regular_series(used_rows, start, end, (8,))
    _available_values(values)

    font = _font_properties(font_path)
    figure = Figure(figsize=(11.5, 6.3), dpi=150)
    axes = figure.subplots()
    _plot_hydrograph_series(axes, timestamps, values)
    flagged_points = _plot_quality_points(axes, used_rows)
    _configure_y_axis(
        axes,
        values,
        nonnegative=True,
        integer_ticks=False,
    )
    title = f"Гідрограф витрат води: {station_name} ({station_index})"
    _configure_axes(
        figure,
        axes,
        title=title,
        subtitle=_period_caption(start, end),
        y_label="Витрата води, м³/с",
        start=start,
        end=end,
        timestamps=timestamps,
        font=font,
    )
    _deduplicated_legend(figure, axes, font)
    output_path = Path(output_path)
    _save_figure(figure, output_path, title)

    available_points = sum(value is not None for value in values)
    product = _register_chart(
        db_path,
        chart_type=DISCHARGE_CHART,
        station_index=station_index,
        station_name=station_name,
        start_date=start_date,
        end_date=end_date,
        output_path=output_path,
        rows=used_rows,
        available_points=available_points,
        missing_points=len(values) - available_points,
        flagged_points=flagged_points,
    )
    return ChartResult(
        DISCHARGE_CHART,
        station_index,
        station_name,
        output_path,
        available_points,
        len(values) - available_points,
        flagged_points,
        product,
    )


def create_charts(
    db_path: Path,
    *,
    station_index: str,
    start_date: str,
    end_date: str,
    output_dir: Path,
    font_path: Path | None = None,
    include_levels: bool = True,
    include_discharge: bool = True,
) -> tuple[ChartResult, ...]:
    """Створює вибрані графіки для одного поста й періоду."""

    if not include_levels and not include_discharge:
        return ()
    _parse_period(start_date, end_date)
    output_dir = Path(output_dir)
    results: list[ChartResult] = []
    if include_levels:
        results.append(
            create_level_chart(
                db_path,
                station_index=station_index,
                start_date=start_date,
                end_date=end_date,
                output_path=output_dir
                / chart_output_name(
                    LEVEL_CHART,
                    station_index,
                    start_date,
                    end_date,
                ),
                font_path=font_path,
            )
        )
    if include_discharge:
        results.append(
            create_discharge_chart(
                db_path,
                station_index=station_index,
                start_date=start_date,
                end_date=end_date,
                output_path=output_dir
                / chart_output_name(
                    DISCHARGE_CHART,
                    station_index,
                    start_date,
                    end_date,
                ),
                font_path=font_path,
            )
        )
    return tuple(results)
