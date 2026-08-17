"""Моделі даних HydroBulletin."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Station:
    """Гідрологічний пост із довідника системи."""

    index: str
    name: str


@dataclass(frozen=True)
class HydroObservation:
    """Нормалізований запис одного гідрологічного поста.

    Модель об'єднує значення одного кодованого запису. Під час запису до
    SQLite вона розкладається на окремі вимірювання за схемою
    ``пост / параметр / час / значення / якість``.
    """

    index: str
    station_name: str
    level: int | None
    change: int | None
    evening_level: int | None
    raw_record: str
    quality_status: str = "NOT_CHECKED"
    water_temperature_c: float | None = None
    precipitation_mm: float | None = None
    discharge_m3_s: float | None = None
    ice_phenomena: str = ""
    ice_thickness_cm: float | None = None
    observed_at: datetime | None = None
    evening_observed_at: datetime | None = None
    source_type: str = "unknown"
    source_file: str = ""
    quality_message: str = ""

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

        if self.evening_level is None:
            return "немає даних"
        return f"{self.evening_level} см"

    @staticmethod
    def _number_text(value: float | None, digits: int = 3) -> str:
        if value is None:
            return "немає даних"
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.{digits}f}".rstrip("0").rstrip(".").replace(".", ",")

    @property
    def temperature_text(self) -> str:
        value = self._number_text(self.water_temperature_c, digits=1)
        return value if self.water_temperature_c is None else f"{value} °C"

    @property
    def precipitation_text(self) -> str:
        if self.precipitation_mm is None:
            return "немає даних"
        if 0.0 <= self.precipitation_mm < 1.0:
            value = f"{self.precipitation_mm:.1f}".replace(".", ",")
        else:
            value = self._number_text(self.precipitation_mm, digits=1)
        return f"{value} мм"

    @property
    def discharge_text(self) -> str:
        value = self._number_text(self.discharge_m3_s, digits=3)
        return value if self.discharge_m3_s is None else f"{value} м³/с"


@dataclass(frozen=True)
class HydroMeasurement:
    """Одне значення часового ряду для збереження у SQLite."""

    station_index: str
    station_name: str
    observed_at: datetime
    parameter_code: str
    value: float | None
    text_value: str
    unit: str
    quality_status: str
    source_type: str
    source_file: str
    raw_record: str
    quality_message: str = ""


def observation_measurements(
    observation: HydroObservation,
) -> tuple[HydroMeasurement, ...]:
    """Розкладає кодований запис поста на нормалізовані вимірювання."""

    if observation.observed_at is None:
        return ()

    common = {
        "station_index": observation.index,
        "station_name": observation.station_name,
        "quality_status": observation.quality_status,
        "source_type": observation.source_type,
        "source_file": observation.source_file,
        "raw_record": observation.raw_record,
        "quality_message": observation.quality_message,
    }
    measurements: list[HydroMeasurement] = []

    # NIL-запис зберігається як відсутнє значення рівня.
    if observation.level is not None or observation.quality_status == "MISSING":
        measurements.append(
            HydroMeasurement(
                observed_at=observation.observed_at,
                parameter_code="WATER_LEVEL",
                value=None if observation.level is None else float(observation.level),
                text_value="",
                unit="cm",
                **common,
            )
        )

    if observation.change is not None:
        measurements.append(
            HydroMeasurement(
                observed_at=observation.observed_at,
                parameter_code="DAILY_CHANGE",
                value=float(observation.change),
                text_value="",
                unit="cm",
                **common,
            )
        )

    if (
        observation.evening_level is not None
        and observation.evening_observed_at is not None
    ):
        measurements.append(
            HydroMeasurement(
                observed_at=observation.evening_observed_at,
                parameter_code="WATER_LEVEL",
                value=float(observation.evening_level),
                text_value="",
                unit="cm",
                **common,
            )
        )

    optional_values = (
        ("WATER_TEMPERATURE", observation.water_temperature_c, "degC"),
        ("PRECIPITATION", observation.precipitation_mm, "mm"),
        ("DISCHARGE", observation.discharge_m3_s, "m3/s"),
    )
    for parameter_code, value, unit in optional_values:
        if value is None:
            continue
        measurements.append(
            HydroMeasurement(
                observed_at=observation.observed_at,
                parameter_code=parameter_code,
                value=float(value),
                text_value="",
                unit=unit,
                **common,
            )
        )

    if observation.ice_phenomena.strip():
        measurements.append(
            HydroMeasurement(
                observed_at=observation.observed_at,
                parameter_code="ICE_PHENOMENA",
                value=None,
                text_value=observation.ice_phenomena.strip(),
                unit="code",
                **common,
            )
        )

    if observation.ice_thickness_cm is not None:
        measurements.append(
            HydroMeasurement(
                observed_at=observation.observed_at,
                parameter_code="ICE_THICKNESS",
                value=float(observation.ice_thickness_cm),
                text_value="",
                unit="cm",
                **common,
            )
        )

    return tuple(measurements)
