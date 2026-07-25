"""Функції декодування основних груп гідрологічного коду КС-15."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Iterable, Mapping

from .models import HydroObservation, Station


def normalize_token(token: str) -> str:
    """Прибирає пробіли та службовий знак ``=`` навколо кодової групи."""

    return token.strip().replace("=", "")


def parse_date(date_text: str) -> datetime:
    """Перетворює дату формату ДД.ММ.РРРР на ``datetime``."""

    try:
        return datetime.strptime(date_text.strip(), "%d.%m.%Y")
    except ValueError as exc:
        raise ValueError(
            "Дата має бути у форматі ДД.ММ.РРРР, наприклад 12.07.2026"
        ) from exc


def parse_observation_datetime(date_group: str, bulletin_date: str) -> datetime:
    """Повертає локальний час спостереження з групи ``YYGGn``."""

    date_group = normalize_token(date_group)
    if not (len(date_group) == 5 and date_group.isdigit()):
        raise ValueError(f"Некоректна група дати/строку: {date_group}")

    bulletin = parse_date(bulletin_date)
    day = int(date_group[:2])
    hour = int(date_group[2:4])
    if day != bulletin.day or not 0 <= hour <= 23:
        raise ValueError(f"Група {date_group} не відповідає даті {bulletin_date}")

    return bulletin.replace(hour=hour, minute=0, second=0, microsecond=0)


def _decode_level_payload(payload: str) -> int | None:
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


def parse_level(group: str) -> int | None:
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


def parse_evening_level(group: str) -> int | None:
    """Декодує групу ``3HHHH`` — рівень о 20:00 попередньої доби.

    Значення ``HHHH`` кодується за тими самими правилами, що й група 1.
    """

    group = normalize_token(group)
    if not (len(group) == 5 and group.startswith("3")):
        return None
    return _decode_level_payload(group[1:])


def parse_change(group: str) -> int | None:
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


def parse_discharge(group: str) -> float | None:
    """Декодує витрату води з групи ``8nQQQ`` у м³/с."""

    group = normalize_token(group)
    if not (len(group) == 5 and group.startswith("8") and group[1:].isdigit()):
        return None

    integer_digits = int(group[1])
    q_digits = int(group[2:5])
    if integer_digits >= 3:
        return float(q_digits * (10 ** (integer_digits - 3)))

    text = str(q_digits).zfill(3)
    whole = text[:integer_digits] if integer_digits else "0"
    fraction = text[integer_digits:]
    return float(f"{whole}.{fraction}")


def parse_temperature(
    group: str,
    bulletin_date: str = "",
    force_decimal_low_values: bool = False,
) -> float | None:
    """Декодує температуру води з групи 4."""

    group = normalize_token(group)
    if not (len(group) == 5 and group.startswith("4")):
        return None

    # Для температури достатньо двох цифр після номера групи.
    if not group[1:3].isdigit():
        return None

    value = int(group[1:3])
    if value <= 9 or value >= 30:
        return value / 10.0

    winter_month = False
    if bulletin_date:
        winter_month = parse_date(bulletin_date).month in (11, 12, 1, 2, 3, 4)

    if force_decimal_low_values or winter_month:
        return value / 10.0
    return float(value)


def parse_precipitation(group: str) -> float | None:
    """Декодує гідрологічні опади з нульової групи ``0RRRt``."""

    group = normalize_token(group)
    if group == "00000":
        return None
    if not (len(group) == 5 and group.startswith("0") and group[1:4].isdigit()):
        return None
    if not (group[4].isdigit() or group[4] == "/"):
        return None

    code = int(group[1:4])
    if code == 0 and group[4] == "/":
        return None
    if 990 <= code <= 999:
        return (code - 990) / 10.0
    return float(code)


def find_precipitation_group(groups: Iterable[str]) -> str | None:
    """Знаходить групу опадів, надаючи перевагу групі після витрати."""

    found_discharge = False
    fallback_group: str | None = None

    for group in groups:
        normalized = normalize_token(group)
        if len(normalized) == 5 and normalized.startswith("8"):
            found_discharge = True
            continue
        if (
            len(normalized) == 5
            and normalized.startswith("0")
            and parse_precipitation(normalized) is not None
        ):
            if found_discharge:
                return normalized
            if fallback_group is None:
                fallback_group = normalized

    return fallback_group


def _primary_observation_groups(groups: Iterable[str]) -> list[str]:
    """Залишає основний строк спостереження до першої групи 9xxxx."""

    result: list[str] = []
    for group in groups:
        normalized = normalize_token(group)
        if len(normalized) == 5 and normalized.startswith("9"):
            break
        result.append(normalized)
    return result


def extract_records(raw_text: str, station_indexes: Iterable[str]) -> list[str]:
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

    records: list[str] = []
    for match in pattern.finditer(prepared):
        record = match.group(0).strip()
        if "=" in record:
            record = record.split("=", 1)[0]
        records.append(record)
    return records


def decode_station_record(
    record: str,
    station: Station,
    bulletin_date: str = "",
    source_type: str = "unknown",
    source_file: str = "",
) -> HydroObservation:
    """Декодує один запис поста у нормалізовану структуру."""

    parts = [normalize_token(part) for part in record.split()]
    parts = [part for part in parts if part]
    if len(parts) < 2 or parts[0] != station.index:
        raise ValueError(f"Запис не належить посту {station.index}")

    observed_at = (
        parse_observation_datetime(parts[1], bulletin_date)
        if bulletin_date
        else None
    )
    evening_observed_at = (
        observed_at - timedelta(hours=12) if observed_at is not None else None
    )

    if any(part.upper() in {"НИЛ", "NIL"} for part in parts):
        return HydroObservation(
            index=station.index,
            station_name=station.name,
            level=None,
            change=None,
            evening_level=None,
            raw_record=record,
            quality_status="MISSING",
            observed_at=observed_at,
            evening_observed_at=evening_observed_at,
            source_type=source_type,
            source_file=source_file,
        )

    data_groups = _primary_observation_groups(parts[2:])

    level: int | None = None
    change: int | None = None
    evening_level: int | None = None
    temperature: float | None = None
    discharge: float | None = None

    has_ice_context = any(
        len(group) == 5 and (group.startswith("5") or group.startswith("7"))
        for group in data_groups
    )

    for group in data_groups:
        if level is None and group.startswith("1"):
            level = parse_level(group)
        elif change is None and group.startswith("2"):
            change = parse_change(group)
        elif evening_level is None and group.startswith("3"):
            evening_level = parse_evening_level(group)
        elif temperature is None and group.startswith("4"):
            temperature = parse_temperature(
                group,
                bulletin_date=bulletin_date,
                force_decimal_low_values=has_ice_context,
            )
        elif discharge is None and group.startswith("8"):
            discharge = parse_discharge(group)

    precip_group = find_precipitation_group(data_groups)
    precipitation = parse_precipitation(precip_group) if precip_group else None

    quality_status = "OK" if level is not None else "INCOMPLETE"
    return HydroObservation(
        index=station.index,
        station_name=station.name,
        level=level,
        change=change,
        evening_level=evening_level,
        raw_record=record,
        quality_status=quality_status,
        water_temperature_c=temperature,
        precipitation_mm=precipitation,
        discharge_m3_s=discharge,
        observed_at=observed_at,
        evening_observed_at=evening_observed_at,
        source_type=source_type,
        source_file=source_file,
    )


def decode_codes(
    raw_text: str,
    bulletin_date: str,
    stations_by_index: Mapping[str, Station],
    source_type: str = "unknown",
    source_file: str = "",
) -> list[HydroObservation]:
    """Декодує записи за вибрану дату та повертає їх у порядку довідника."""

    expected_day = parse_date(bulletin_date).strftime("%d")
    records = extract_records(raw_text, stations_by_index.keys())
    decoded_by_index: dict[str, HydroObservation] = {}

    for record in records:
        parts = record.split()
        if len(parts) < 2:
            continue

        index = normalize_token(parts[0])
        date_group = normalize_token(parts[1])
        station = stations_by_index.get(index)
        if station is None:
            continue

        if (
            len(date_group) >= 2
            and date_group[:2].isdigit()
            and date_group[:2] != expected_day
        ):
            continue

        try:
            decoded_by_index[index] = decode_station_record(
                record,
                station,
                bulletin_date=bulletin_date,
                source_type=source_type,
                source_file=source_file,
            )
        except ValueError:
            continue

    return [
        decoded_by_index[index]
        for index in stations_by_index
        if index in decoded_by_index
    ]
