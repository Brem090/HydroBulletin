"""Первинний, консервативний контроль якості архівних вимірювань."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


VALID = "VALID"
MISSING = "MISSING"
SUSPICIOUS = "SUSPICIOUS"
OUT_OF_RANGE = "OUT_OF_RANGE"
INCONSISTENT_CHANGE = "INCONSISTENT_CHANGE"
CORRECTED = "CORRECTED"
NOT_CHECKED = "NOT_CHECKED"

QUALITY_STATUS_LABELS: dict[str, str] = {
    VALID: "Без зауважень",
    "OK": "Без зауважень",
    MISSING: "Дані відсутні",
    SUSPICIOUS: "Підозріле значення",
    OUT_OF_RANGE: "Поза діапазоном",
    INCONSISTENT_CHANGE: "Неузгоджена зміна",
    CORRECTED: "Виправлено",
    NOT_CHECKED: "Не перевірено",
    "INCOMPLETE": "Неповні дані",
}

QUALITY_STATUSES = (
    VALID,
    MISSING,
    SUSPICIOUS,
    OUT_OF_RANGE,
    INCONSISTENT_CHANGE,
    NOT_CHECKED,
)

QUALITY_PRIORITY = {
    NOT_CHECKED: 0,
    "OK": 0,
    VALID: 1,
    CORRECTED: 1.5,
    SUSPICIOUS: 2,
    INCONSISTENT_CHANGE: 3,
    OUT_OF_RANGE: 4,
    MISSING: 5,
}

# Межі навмисно широкі: система не виправляє значення автоматично, а лише
# позначає те, що потребує уваги гідролога.
PHYSICAL_RANGES: dict[str, tuple[float, float]] = {
    "WATER_LEVEL": (-1000.0, 10000.0),
    "DAILY_CHANGE": (-5000.0, 5000.0),
    "WATER_TEMPERATURE": (-2.0, 40.0),
    "PRECIPITATION": (0.0, 500.0),
    "DISCHARGE": (0.0, 100000.0),
    "ICE_THICKNESS": (0.0, 500.0),
}

SUSPICIOUS_LIMITS: dict[str, tuple[float | None, float | None]] = {
    "WATER_LEVEL": (-100.0, 3000.0),
    "DAILY_CHANGE": (-300.0, 300.0),
    "WATER_TEMPERATURE": (-0.5, 30.0),
    "PRECIPITATION": (None, 100.0),
    "DISCHARGE": (None, 10000.0),
    "ICE_THICKNESS": (None, 200.0),
}


@dataclass(frozen=True)
class QualitySummary:
    """Підсумок одного запуску правил QC."""

    checked: int
    inconsistent_changes: int
    counts: dict[str, int]


def quality_status_label(status: str) -> str:
    """Повертає український підпис внутрішнього статусу якості."""

    return QUALITY_STATUS_LABELS.get(status, status)


def _date_iso(date_text: str) -> str:
    try:
        return datetime.strptime(date_text, "%d.%m.%Y").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Дата має бути у форматі ДД.ММ.РРРР.") from exc


def evaluate_value(
    parameter_code: str,
    value: float | None,
) -> tuple[str, str]:
    """Повертає прапорець і зрозуміле пояснення для одного значення."""

    code = parameter_code.upper()
    if value is None:
        return MISSING, "Значення відсутнє у вихідному повідомленні."

    physical_range = PHYSICAL_RANGES.get(code)
    if physical_range is not None:
        minimum, maximum = physical_range
        if value < minimum or value > maximum:
            return (
                OUT_OF_RANGE,
                f"Значення {value:g} виходить за допустимий інтервал "
                f"{minimum:g}…{maximum:g}.",
            )

    suspicious_range = SUSPICIOUS_LIMITS.get(code)
    if suspicious_range is not None:
        minimum, maximum = suspicious_range
        if minimum is not None and value < minimum:
            return (
                SUSPICIOUS,
                f"Значення {value:g} нижче контрольного порога {minimum:g}.",
            )
        if maximum is not None and value > maximum:
            return (
                SUSPICIOUS,
                f"Значення {value:g} вище контрольного порога {maximum:g}.",
            )

    return VALID, ""


def _base_quality(row: sqlite3.Row) -> tuple[str, str]:
    if str(row["parameter_code"]) == "ICE_PHENOMENA":
        if str(row["text_value"]).strip():
            return VALID, ""
        return MISSING, "Льодове явище не декодовано."
    return evaluate_value(
        str(row["parameter_code"]),
        None if row["value"] is None else float(row["value"]),
    )


def _update_observation_quality(
    connection: sqlite3.Connection,
    observation_id: int,
    status: str,
    message: str,
) -> None:
    connection.execute(
        """
        UPDATE observations
        SET quality_status = ?, quality_message = ?
        WHERE observation_id = ?
        """,
        (status, message, observation_id),
    )


def _level_value(
    connection: sqlite3.Connection,
    station_index: str,
    observed_at: datetime,
) -> float | None:
    row = connection.execute(
        """
        SELECT value
        FROM observations
        WHERE station_index = ? AND observed_at = ?
          AND parameter_code = 'WATER_LEVEL'
        """,
        (station_index, observed_at.isoformat(timespec="seconds")),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0])


def _unavailable_level_message(
    current_level: float | None,
    previous_level: float | None,
) -> str:
    missing_parts: list[str] = []
    if current_level is None:
        missing_parts.append("поточний рівень")
    if previous_level is None:
        missing_parts.append("рівень попередньої доби")
    if len(missing_parts) == 1:
        return f"Добову зміну не звірено: відсутній {missing_parts[0]}."
    return "Добову зміну не звірено: відсутні " + " і ".join(missing_parts) + "."


def _check_daily_change(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> bool:
    """Перевіряє одну зміну; повертає ``True`` за неузгодженості."""

    if row["value"] is None:
        return False

    actual = float(row["value"])
    base_status, _base_message = evaluate_value("DAILY_CHANGE", actual)
    current_at = datetime.fromisoformat(str(row["observed_at"]))
    previous_at = current_at - timedelta(days=1)
    station_index = str(row["station_index"])
    current_level = _level_value(connection, station_index, current_at)
    previous_level = _level_value(connection, station_index, previous_at)

    if current_level is None or previous_level is None:
        # Відсутність бази для міждобового зіставлення не робить саме
        # значення підозрілим. Статус фіксує, що це правило не виконано.
        if base_status == VALID:
            _update_observation_quality(
                connection,
                int(row["observation_id"]),
                NOT_CHECKED,
                _unavailable_level_message(current_level, previous_level),
            )
        return False

    expected = current_level - previous_level
    if abs(expected - actual) <= 1e-9:
        return False
    if base_status in {MISSING, OUT_OF_RANGE}:
        return False

    _update_observation_quality(
        connection,
        int(row["observation_id"]),
        INCONSISTENT_CHANGE,
        (
            f"Добова зміна у коді {actual:g} см, але різниця рівнів "
            f"становить {expected:g} см."
        ),
    )
    return True


def run_initial_quality_control(
    db_path: Path,
    bulletin_date: str,
) -> QualitySummary:
    """Застосовує діапазони й перевірку добової зміни до вибраної дати.

    Правила лише оновлюють прапорець і пояснення. Початкове значення та
    зв'язок із raw-файлом залишаються незмінними.
    """

    target_date = _date_iso(bulletin_date)
    connection = sqlite3.connect(Path(db_path))
    connection.row_factory = sqlite3.Row

    try:
        rows = connection.execute(
            """
            SELECT observation_id, station_index, observed_at,
                   parameter_code, value, text_value
            FROM observations
            WHERE date(observed_at) = date(?)
            ORDER BY observation_id
            """,
            (target_date,),
        ).fetchall()

        for row in rows:
            status, message = _base_quality(row)
            _update_observation_quality(
                connection,
                int(row["observation_id"]),
                status,
                message,
            )

        inconsistent = sum(
            _check_daily_change(connection, row)
            for row in rows
            if row["parameter_code"] == "DAILY_CHANGE"
        )

        connection.commit()
        status_rows = connection.execute(
            """
            SELECT quality_status, COUNT(*)
            FROM observations
            WHERE date(observed_at) = date(?)
            GROUP BY quality_status
            """,
            (target_date,),
        ).fetchall()
        counts = Counter({str(status): int(count) for status, count in status_rows})
        return QualitySummary(len(rows), inconsistent, dict(counts))
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def worst_quality_status(statuses: list[str] | tuple[str, ...]) -> str:
    """Повертає найсерйозніший прапорець для рядка бюлетеня."""

    normalized = [status or NOT_CHECKED for status in statuses]
    if not normalized:
        return MISSING
    return max(normalized, key=lambda item: QUALITY_PRIORITY.get(item, 0))
