"""Моделі даних HydroBulletin."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Station:
    """Гідрологічний пост, який підтримує демонстраційна версія."""

    index: str
    name: str


@dataclass(frozen=True)
class HydroObservation:
    """Нормалізований запис одного гідрологічного поста.

    На першому тижні модель зберігає ранковий рівень, добову зміну та
    рівень о 20:00 попередньої доби. Інші показники будуть додані далі.
    """

    index: str
    station_name: str
    level: Optional[int]
    change: Optional[int]
    evening_level: Optional[int]
    raw_record: str
    quality_status: str = "NOT_CHECKED"

    @property
    def level_text(self) -> str:
        return "немає даних" if self.level is None else f"{self.level} см"

    @property
    def change_text(self) -> str:
        if self.change is None:
            return "немає даних"
        if self.change > 0:
            return f"+{self.change} см"
        return f"{self.change} см"

    @property
    def evening_level_text(self) -> str:
        """Рівень о 20:00 попередньої доби у зручному для таблиці вигляді."""

        return "немає даних" if self.evening_level is None else f"{self.evening_level} см"
