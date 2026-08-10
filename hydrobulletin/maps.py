"""Формування гідрологічної карти Львівської області з SQLite."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .archive import ProductResult, query_observations, register_product


MAP_PARAMETERS = (
    "WATER_LEVEL",
    "DAILY_CHANGE",
    "WATER_TEMPERATURE",
)

MONTHS_UA = {
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

# Координати відповідають позначкам постів у шаблоні 1920 x 1080.
LVIV_MAP_POSITIONS = {
    "81015": {
        "temp": (1053.4, 704.6),
        "level": (1027.5, 693.6),
        "change": (1024.5, 713.5),
        "line": (1027.5, 703.5),
    },
    "81017": {
        "temp": (1139.3, 639.5),
        "level": (1112.6, 630.8),
        "change": (1113.3, 648.3),
        "line": (1112.6, 639.5),
    },
    "81028": {
        "temp": (1362.3, 651.1),
        "level": (1386.0, 642.2),
        "change": (1387.2, 661.9),
        "line": (1386.0, 652.0),
    },
    "81030": {
        "temp": (1402.0, 737.2),
        "level": (1429.5, 726.4),
        "change": (1425.0, 746.0),
        "line": (1429.5, 736.2),
    },
    "81078": {
        "temp": (949.3, 591.3),
        "level": (976.3, 581.5),
        "change": (971.8, 601.0),
        "line": (976.3, 591.2),
    },
    "81080": {
        "temp": (1135.6, 522.1),
        "level": (1161.2, 513.2),
        "change": (1159.6, 532.3),
        "line": (1161.2, 522.7),
    },
    "81087": {
        "temp": (1244.0, 645.4),
        "level": (1216.8, 638.3),
        "change": (1213.5, 655.9),
        "line": (1216.8, 647.1),
    },
    "81102": {
        "temp": (1042.9, 923.7),
        "level": (1066.4, 909.0),
        "change": (1067.8, 927.5),
        "line": (1066.4, 918.2),
    },
    "81103": {
        "temp": (1136.2, 810.5),
        "level": (1111.1, 802.1),
        "change": (1106.4, 820.6),
        "line": (1111.1, 811.4),
    },
    "81108": {
        "temp": (1204.3, 782.8),
        "level": (1230.3, 772.7),
        "change": (1230.2, 791.7),
        "line": (1230.3, 782.2),
    },
    "81109": {
        "temp": (1383.7, 766.7),
        "level": (1356.7, 757.0),
        "change": (1353.3, 775.6),
        "line": (1356.7, 766.2),
    },
    "81113": {
        "temp": (1011.4, 822.2),
        "level": (1038.9, 813.7),
        "change": (1037.6, 831.2),
        "line": (1038.9, 822.4),
    },
    "81120": {
        "temp": (1163.6, 843.1),
        "level": (1189.6, 832.7),
        "change": (1185.6, 851.4),
        "line": (1189.6, 842.0),
    },
    "81122": {
        "temp": (1118.0, 963.2),
        "level": (1140.8, 951.3),
        "change": (1142.0, 971.3),
        "line": (1140.8, 961.3),
    },
    "79720": {
        "temp": (1177.5, 471.3),
        "level": (1150.4, 464.9),
        "change": (1147.3, 482.3),
        "line": (1150.4, 473.6),
    },
    "79726": {
        "temp": (1456.1, 306.3),
        "level": (1480.2, 297.2),
        "change": (1477.7, 315.9),
        "line": (1480.2, 306.5),
    },
    "79747": {
        "temp": (1650.1, 353.0),
        "level": (1619.8, 344.1),
        "change": (1616.1, 362.5),
        "line": (1619.8, 353.2),
    },
    "79753": {
        "temp": (1296.1, 256.4),
        "level": (1318.0, 245.6),
        "change": (1315.1, 263.8),
        "line": (1318.0, 254.9),
    },
    "79755": {
        "temp": (1509.6, 213.2),
        "level": (1482.7, 204.1),
        "change": (1478.6, 222.7),
        "line": (1482.7, 213.4),
    },
    "79761": {
        "temp": (1409.4, 146.6),
        "level": (1434.4, 140.0),
        "change": (1433.0, 159.8),
        "line": (1434.4, 149.9),
    },
    "79473": {
        "temp": (1722.7, 252.9),
        "level": (1765.0, 226.0),
        "change": (1759.9, 244.6),
        "line": (1765.0, 235.2),
    },
}

LVIV_MAP_STATION_INDEXES = tuple(LVIV_MAP_POSITIONS)


@dataclass(frozen=True)
class MapResult:
    """Створена карта та запис про її походження."""

    output_path: Path
    plotted_stations: int
    missing_stations: int
    product: ProductResult


def map_output_name(bulletin_date: str) -> str:
    """Повертає передбачувану назву PNG для вибраної дати."""

    observed_date = _parse_date(bulletin_date)
    return f"HydroMap_Lviv_{observed_date:%d.%m.%Y}.png"


def _parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%d.%m.%Y")
    except ValueError as exc:
        raise ValueError("Дата має бути у форматі ДД.ММ.РРРР.") from exc


def _font(font_path: Path, size: float, scale: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(font_path), round(size * scale))
    except OSError as exc:
        raise RuntimeError(f"Не вдалося відкрити шрифт карти: {font_path}") from exc


def _format_change(value: object) -> str:
    if value is None:
        return ""
    number = int(round(float(value)))
    return f"+{number}" if number > 0 else str(number)


def _change_color(value: object) -> tuple[int, int, int, int]:
    if value is None:
        return (10, 10, 10, 255)
    number = float(value)
    if number > 0:
        return (255, 0, 0, 255)
    if number < 0:
        return (40, 56, 210, 255)
    return (10, 10, 10, 255)


def _format_temperature(value: object) -> str:
    if value is None:
        return ""
    number = float(value)
    rounded = math.floor(number + 0.5) if number >= 0 else math.ceil(number - 0.5)
    return f"{rounded}°"


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    scale: int,
) -> None:
    if not text:
        return
    x, y = position
    draw.text(
        (x * scale, y * scale),
        text,
        font=font,
        fill=fill,
        anchor="mm",
    )


def _is_map_pixel(base: Image.Image, x: float, y: float) -> bool:
    width, height = base.size
    pixel_x = round(x)
    pixel_y = round(y)
    if not (0 <= pixel_x < width and 0 <= pixel_y < height):
        return False
    red, green, blue, *_ = base.getpixel((pixel_x, pixel_y))
    return not (red < 90 and green < 115 and blue < 150)


def _circle_fits(base: Image.Image, x: float, y: float, radius: float) -> bool:
    if not _is_map_pixel(base, x, y):
        return False
    for angle in range(0, 360, 20):
        radians = math.radians(angle)
        point_x = x + math.cos(radians) * radius
        point_y = y + math.sin(radians) * radius
        if not _is_map_pixel(base, point_x, point_y):
            return False
    return True


def _temperature_position(
    base: Image.Image,
    center: tuple[float, float],
    level_position: tuple[float, float],
    radius: float,
) -> tuple[float, float]:
    center_x, center_y = center
    level_x, level_y = level_position
    delta_x = center_x - level_x
    delta_y = center_y - level_y
    distance = math.hypot(delta_x, delta_y)
    if distance <= 0.01:
        unit_x, unit_y = 1.0, 0.0
    else:
        unit_x, unit_y = delta_x / distance, delta_y / distance
    side_x, side_y = -unit_y, unit_x

    candidates = (
        (center_x + unit_x * 8.0, center_y + unit_y * 8.0),
        (center_x + unit_x * 5.0, center_y + unit_y * 5.0),
        (center_x, center_y),
        (
            center_x + unit_x * 4.0 + side_x * 7.0,
            center_y + unit_y * 4.0 + side_y * 7.0,
        ),
        (
            center_x + unit_x * 4.0 - side_x * 7.0,
            center_y + unit_y * 4.0 - side_y * 7.0,
        ),
    )
    return next(
        (candidate for candidate in candidates if _circle_fits(base, *candidate, radius)),
        center,
    )


def _draw_temperature(
    draw: ImageDraw.ImageDraw,
    base: Image.Image,
    position: tuple[float, float],
    level_position: tuple[float, float],
    value: object,
    font: ImageFont.FreeTypeFont,
    scale: int,
) -> None:
    text = _format_temperature(value)
    if not text:
        return
    radius = 11.9
    center_x, center_y = _temperature_position(
        base,
        position,
        level_position,
        radius,
    )
    color = (18, 22, 30, 255)
    draw.ellipse(
        (
            (center_x - radius) * scale,
            (center_y - radius) * scale,
            (center_x + radius) * scale,
            (center_y + radius) * scale,
        ),
        outline=color,
        width=max(1, round(1.8 * scale)),
    )
    _draw_centered_text(
        draw,
        (center_x, center_y - 0.2),
        text,
        font,
        color,
        scale,
    )


def _draw_value_block(
    draw: ImageDraw.ImageDraw,
    positions: dict[str, tuple[float, float]],
    level: object,
    change: object,
    font: ImageFont.FreeTypeFont,
    scale: int,
) -> None:
    if level is None:
        return
    line_x, line_y = positions["line"]
    color = (10, 10, 10, 255)
    _draw_centered_text(
        draw,
        (line_x, positions["level"][1]),
        str(int(round(float(level)))),
        font,
        color,
        scale,
    )
    draw.line(
        (
            (line_x - 16) * scale,
            line_y * scale,
            (line_x + 16) * scale,
            line_y * scale,
        ),
        fill=color,
        width=max(1, round(2.4 * scale)),
    )
    _draw_centered_text(
        draw,
        (line_x, positions["change"][1]),
        _format_change(change),
        font,
        _change_color(change),
        scale,
    )


def _draw_title(
    draw: ImageDraw.ImageDraw,
    bulletin_date: str,
    font: ImageFont.FreeTypeFont,
    scale: int,
) -> None:
    observed_date = _parse_date(bulletin_date)
    lines = (
        "Рівень води на 08:00 год",
        f"{observed_date:%d} {MONTHS_UA[observed_date.month]} та його зміна",
        "за добу на гідрологічних",
        "постах",
    )
    for line_number, line in enumerate(lines):
        draw.text(
            (452 * scale, (386 + line_number * 65) * scale),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            anchor="ma",
        )


def _is_morning_observation(row: dict[str, object]) -> bool:
    observed_at = datetime.fromisoformat(str(row["observed_at"]))
    return observed_at.hour == 8 and observed_at.minute == 0


def _morning_values(
    rows: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        if not _is_morning_observation(row):
            continue
        station_values = result.setdefault(str(row["station_index"]), {})
        station_values[str(row["parameter_code"])] = row["value"]
    return result


def create_lviv_map(
    db_path: Path,
    *,
    bulletin_date: str,
    template_path: Path,
    font_path: Path,
    output_path: Path,
) -> MapResult:
    """Створює карту з ранкових значень і реєструє її provenance."""

    _parse_date(bulletin_date)
    template_path = Path(template_path)
    font_path = Path(font_path)
    if not template_path.exists():
        raise FileNotFoundError(f"Шаблон карти не знайдено: {template_path}")
    if not font_path.exists():
        raise FileNotFoundError(f"Шрифт карти не знайдено: {font_path}")

    rows = query_observations(
        db_path,
        start_date=bulletin_date,
        end_date=bulletin_date,
        station_indexes=LVIV_MAP_STATION_INDEXES,
        parameter_codes=MAP_PARAMETERS,
    )
    morning_rows = [
        row
        for row in rows
        if _is_morning_observation(row)
    ]
    if not any(row["parameter_code"] == "WATER_LEVEL" for row in morning_rows):
        raise ValueError(
            f"У SQLite немає ранкових рівнів води за {bulletin_date}."
        )

    values_by_station = _morning_values(morning_rows)
    base = Image.open(template_path).convert("RGBA")
    if base.size != (1920, 1080):
        raise ValueError(
            "Шаблон карти повинен мати розмір 1920 x 1080 пікселів."
        )

    scale = 4
    overlay = Image.new(
        "RGBA",
        (base.width * scale, base.height * scale),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(overlay)
    value_font = _font(font_path, 16, scale)
    temperature_font = _font(font_path, 10.2, scale)
    title_font = _font(font_path, 47, scale)
    _draw_title(draw, bulletin_date, title_font, scale)

    plotted_stations = 0
    for station_index, positions in LVIV_MAP_POSITIONS.items():
        values = values_by_station.get(station_index)
        if not values:
            continue
        level = values.get("WATER_LEVEL")
        change = values.get("DAILY_CHANGE")
        temperature = values.get("WATER_TEMPERATURE")
        if any(value is not None for value in (level, change, temperature)):
            plotted_stations += 1
        _draw_temperature(
            draw,
            base,
            positions["temp"],
            positions["level"],
            temperature,
            temperature_font,
            scale,
        )
        _draw_value_block(
            draw,
            positions,
            level,
            change,
            value_font,
            scale,
        )

    overlay = overlay.resize(base.size, Image.Resampling.LANCZOS)
    result_image = Image.alpha_composite(base, overlay).convert("RGB")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = BytesIO()
    result_image.save(buffer, format="PNG", optimize=True)
    output_path.write_bytes(buffer.getvalue())

    quality_counts = Counter(str(row["quality_status"]) for row in morning_rows)
    product = register_product(
        db_path,
        product_type="HYDRO_MAP",
        region_key="lviv",
        bulletin_date=bulletin_date,
        output_path=str(output_path),
        observation_ids=(int(row["observation_id"]) for row in morning_rows),
        metadata={
            "template": str(template_path),
            "parameters": list(MAP_PARAMETERS),
            "map_stations": len(LVIV_MAP_STATION_INDEXES),
            "plotted_stations": plotted_stations,
            "missing_stations": len(LVIV_MAP_STATION_INDEXES) - plotted_stations,
            "quality_counts": dict(quality_counts),
        },
    )
    return MapResult(
        output_path,
        plotted_stations,
        len(LVIV_MAP_STATION_INDEXES) - plotted_stations,
        product,
    )
