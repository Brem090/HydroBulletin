"""Імпорт багаторічних рівнів з офіційних шаблонів до SQLite."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from docx import Document

from .archive import seed_reference_extreme
from .regions import RegionConfig


def _iter_tables(container):
    seen: set[object] = set()

    def walk(owner):
        for table in owner.tables:
            key = table._tbl
            if key in seen:
                continue
            seen.add(key)
            yield table
            seen_cells: set[object] = set()
            for row in table.rows:
                for cell in row.cells:
                    cell_key = cell._tc
                    if cell_key in seen_cells:
                        continue
                    seen_cells.add(cell_key)
                    yield from walk(cell)

    yield from walk(container)


def _official_table(document: Document):
    for table in _iter_tables(document):
        if not table.rows or len(table.columns) < 10:
            continue
        if "Річка-пункт" in table.rows[0].cells[0].text:
            return table
    raise RuntimeError("У шаблоні не знайдено офіційної таблиці бюлетеня.")


def _integer(text: str) -> int | None:
    match = re.search(r"-?\d+", text.replace("\u00a0", " "))
    return int(match.group(0)) if match else None


def seed_extremes_from_templates(
    db_path: Path,
    regions: Iterable[RegionConfig],
    templates_dir: Path,
) -> int:
    """Одноразово переносить відсутні екстремуми з Word-шаблонів."""

    inserted = 0
    for region in regions:
        template_path = region.template_path(templates_dir)
        if not template_path.exists():
            continue
        table = _official_table(Document(str(template_path)))
        if len(table.rows) < len(region.stations) + 2:
            raise RuntimeError(
                f"У шаблоні {template_path.name} недостатньо рядків "
                "для довідника екстремумів."
            )

        for position, station in enumerate(region.stations, start=2):
            row = table.rows[position]
            maximum = _integer(row.cells[6].text)
            average = _integer(row.cells[7].text)
            minimum = _integer(row.cells[8].text)
            if None in {maximum, average, minimum}:
                continue
            if seed_reference_extreme(
                db_path,
                station_index=station.index,
                maximum_level=int(maximum),
                average_level=int(average),
                minimum_level=int(minimum),
            ):
                inserted += 1
    return inserted
