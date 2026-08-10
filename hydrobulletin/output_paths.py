"""Календарна структура папок для сформованих матеріалів."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


MATERIALS_DIR_NAME = "Готові матеріали"

MONTHS_UA_FOLDER = {
    1: "Січень",
    2: "Лютий",
    3: "Березень",
    4: "Квітень",
    5: "Травень",
    6: "Червень",
    7: "Липень",
    8: "Серпень",
    9: "Вересень",
    10: "Жовтень",
    11: "Листопад",
    12: "Грудень",
}


def dated_output_dir(root: Path, date_text: str) -> Path:
    """Створює й повертає папку ``корінь/рік/місяць``."""

    try:
        value = datetime.strptime(date_text, "%d.%m.%Y")
    except ValueError as exc:
        raise ValueError("Дата матеріалів має бути у форматі ДД.ММ.РРРР.") from exc

    path = Path(root) / f"{value.year:04d}" / MONTHS_UA_FOLDER[value.month]
    path.mkdir(parents=True, exist_ok=True)
    return path
