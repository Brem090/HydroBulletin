"""Конфігурації трьох регіональних гідрологічних бюлетенів."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import Station
from .stations import IF_STATIONS, LEFT_DNISTER_STATIONS, LVIV_STATIONS


@dataclass(frozen=True)
class RegionConfig:
    """Описує змінну частину універсального генератора бюлетенів."""

    key: str
    title: str
    output_prefix: str
    template_file: str
    stations: tuple[Station, ...]

    def output_name(self, bulletin_date: str) -> str:
        day, month, year = bulletin_date.split(".")
        return f"{self.output_prefix}_{day}.{month}.{year}.docx"

    def template_path(self, templates_dir: Path) -> Path:
        return Path(templates_dir) / self.template_file


REGIONS: tuple[RegionConfig, ...] = (
    RegionConfig(
        key="lviv",
        title="Львівська область",
        output_prefix="Bulleten_Lviv",
        template_file="bulletin_lviv_template.docx",
        stations=LVIV_STATIONS,
    ),
    RegionConfig(
        key="if",
        title="Івано-Франківська область",
        output_prefix="Bulleten_IF",
        template_file="bulletin_if_template.docx",
        stations=IF_STATIONS,
    ),
    RegionConfig(
        key="left_dnister",
        title="Ліві притоки Дністра",
        output_prefix="Bulleten_Left_Dnister",
        template_file="bulletin_left_dnister_template.docx",
        stations=LEFT_DNISTER_STATIONS,
    ),
)

REGIONS_BY_KEY: dict[str, RegionConfig] = {
    region.key: region for region in REGIONS
}


def message_types_for_regions(
    region_keys: list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    """Повертає мінімальний набір ZRUR для вибраних матеріалів.

    Пости Івано-Франківської області та лівих приток Дністра надходять у
    ZRUR52. Для повного покриття Львівської області додатково потрібен ZRUR71.
    """

    selected = set(region_keys)
    message_types = ["ZRUR52"]
    if "lviv" in selected:
        message_types.append("ZRUR71")
    return tuple(message_types)


def resolve_regions(keys: list[str] | tuple[str, ...]) -> tuple[RegionConfig, ...]:
    """Повертає конфігурації у стабільному порядку й перевіряє ключі."""

    unknown = [key for key in keys if key not in REGIONS_BY_KEY]
    if unknown:
        raise ValueError(f"Невідомі регіони: {', '.join(unknown)}")
    selected = set(keys)
    return tuple(region for region in REGIONS if region.key in selected)
