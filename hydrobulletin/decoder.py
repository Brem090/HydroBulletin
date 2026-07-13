"""Функції декодування базових груп гідрологічного коду КС-15"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from .models import HydroObservation, Station


def normalize_token(token: str) -> str:
    """Прибирає пробіли та службовий знак ``=`` навколо кодової групи."""

    return token.strip().replace("=", "")


def parse_date(date_text: str) -> datetime:
    """Перетворює дату формату ДД.ММ.РРРР на ``datetime``."""

    try:
        return datetime.strptime(date_text.strip(), "%d.%m.%Y")
    except ValueError as exc:
        raise ValueError("Дата має бути у форматі ДД.ММ.РРРР, наприклад 12.07.2026") from exc


def _decode_level_payload(payload: str) -> Optional[int]:
    """Декодує чотири цифри рівня без розпізнавальної цифри групи.

    Значення 0000-4999 трактуються як додатні рівні. Для від'ємного рівня
    до абсолютного значення додається 5000, тому ``5003`` означає -3 см.
    """

    if not (len(payload) == 4 and payload.isdigit()):
        return None

    encoded_value = int(payload)
    if encoded_value >= 5000:
        return -(encoded_value - 5000)
    return encoded_value


def parse_level(group: str) -> Optional[int]:
    """Декодує групу ``1HHHH`` — рівень у строк спостереження.

    Приклади:
    - ``10214`` -> 214 см;
    - ``15003`` -> -3 см;
    - некоректна група -> ``None``.
    """

    group = normalize_token(group)
    if not (len(group) == 5 and group.startswith("1")):
        return None
    return _decode_level_payload(group[1:])


def parse_evening_level(group: str) -> Optional[int]:
    """Декодує групу ``3HHHH`` — рівень о 20:00 попередньої доби.

    Значення ``HHHH`` кодується за тими самими правилами, що й група 1.
    """

    group = normalize_token(group)
    if not (len(group) == 5 and group.startswith("3")):
        return None
    return _decode_level_payload(group[1:])


def parse_change(group: str) -> Optional[int]:
    """Декодує добову зміну рівня води з групи ``2HHHK``.

    Остання цифра визначає знак:
    - 0 — без зміни;
    - 1 — підйом;
    - 2 — спад.
    """

    group = normalize_token(group)
    if not (len(group) == 5 and group.startswith("2") and group[1:].isdigit()):
        return None

    value = int(group[1:4])
    sign_code = group[4]

    if sign_code == "0":
        return 0
    if sign_code == "1":
        return value
    if sign_code == "2":
        return -value
    return None


def _groups_before_extra_observations(groups: Iterable[str]) -> List[str]:
    """Залишає основний строк спостереження до першої групи 9xxxx."""

    result: List[str] = []
    for group in groups:
        normalized = normalize_token(group)
        if len(normalized) == 5 and normalized.startswith("9"):
            break
        result.append(normalized)
    return result


def extract_records(raw_text: str, station_indexes: Iterable[str]) -> List[str]:
    """Виділяє записи відомих постів із текстового повідомлення."""

    indexes = sorted(set(station_indexes))
    if not indexes:
        return []

    prepared = raw_text.replace("\r", "\n")
    prepared = re.sub(r"\n(?=\d{4}/)", " ", prepared)

    station_pattern = "|".join(re.escape(code) for code in indexes)
    pattern = re.compile(
        rf"\b({station_pattern})\b[\s\S]*?(?=\n\b(?:{station_pattern})\b|\Z)",
        re.MULTILINE,
    )

    records: List[str] = []
    for match in pattern.finditer(prepared):
        record = match.group(0).strip()
        if "=" in record:
            record = record.split("=", 1)[0]
        records.append(record)
    return records


def decode_station_record(record: str, station: Station) -> HydroObservation:
    """Декодує один запис поста у нормалізовану структуру."""

    parts = [normalize_token(part) for part in record.split() if normalize_token(part)]
    if len(parts) < 2 or parts[0] != station.index:
        raise ValueError(f"Запис не належить посту {station.index}")

    if any(part.upper() in {"НИЛ", "NIL"} for part in parts):
        return HydroObservation(
            index=station.index,
            station_name=station.name,
            level=None,
            change=None,
            evening_level=None,
            raw_record=record,
            quality_status="MISSING",
        )

    data_groups = _groups_before_extra_observations(parts[2:])

    level: Optional[int] = None
    change: Optional[int] = None
    evening_level: Optional[int] = None

    for group in data_groups:
        if level is None and group.startswith("1"):
            level = parse_level(group)
        elif change is None and group.startswith("2"):
            change = parse_change(group)
        elif evening_level is None and group.startswith("3"):
            evening_level = parse_evening_level(group)

    quality_status = "OK" if level is not None else "INCOMPLETE"
    return HydroObservation(
        index=station.index,
        station_name=station.name,
        level=level,
        change=change,
        evening_level=evening_level,
        raw_record=record,
        quality_status=quality_status,
    )


def decode_codes(
    raw_text: str,
    bulletin_date: str,
    stations_by_index: Dict[str, Station],
) -> List[HydroObservation]:
    """Декодує записи за вибрану дату та повертає їх у порядку довідника."""

    expected_day = parse_date(bulletin_date).strftime("%d")
    records = extract_records(raw_text, stations_by_index.keys())
    decoded_by_index: Dict[str, HydroObservation] = {}

    for record in records:
        parts = record.split()
        if len(parts) < 2:
            continue

        index = normalize_token(parts[0])
        date_group = normalize_token(parts[1])
        station = stations_by_index.get(index)
        if station is None:
            continue

        # Перші дві цифри групи YYGGn — день місяця.
        if len(date_group) >= 2 and date_group[:2].isdigit() and date_group[:2] != expected_day:
            continue

        decoded_by_index[index] = decode_station_record(record, station)

    return [
        decoded_by_index[index]
        for index in stations_by_index
        if index in decoded_by_index
    ]
