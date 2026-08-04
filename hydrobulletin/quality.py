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

QUALITY_STATUSES = (
    VALID,
    MISSING,
    SUSPICIOUS,
    OUT_OF_RANGE,
    INCONSISTENT_CHANGE,
)

QUALITY_PRIORITY = {
    "NOT_CHECKED": 0,
    "OK": 0,
    VALID: 1,
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
}

SUSPICIOUS_LIMITS: dict[str, tuple[float | None, float | None]] = {
    "WATER_LEVEL": (-100.0, 3000.0),
    "DAILY_CHANGE": (-300.0, 300.0),
    "WATER_TEMPERATURE": (-0.5, 30.0),
    "PRECIPITATION": (None, 100.0),
    "DISCHARGE": (None, 10000.0),
}


@dataclass(frozen=True)
class QualitySummary:
    """Підсумок одного запуску правил QC."""

    checked: int
    inconsistent_changes: int
    counts: dict[str, int]


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
    inconsistent = 0

    try:
        rows = connection.execute(
            """
            SELECT observation_id, station_index, observed_at,
                   parameter_code, value
            FROM observations
            WHERE date(observed_at) = date(?)
            ORDER BY observation_id
            """,
            (target_date,),
        ).fetchall()

        for row in rows:
            status, message = evaluate_value(
                str(row["parameter_code"]),
                None if row["value"] is None else float(row["value"]),
            )
            connection.execute(
                """
                UPDATE observations
                SET quality_status = ?, quality_message = ?
                WHERE observation_id = ?
                """,
                (status, message, row["observation_id"]),
            )

        change_rows = [row for row in rows if row["parameter_code"] == "DAILY_CHANGE"]
        for change_row in change_rows:
            if change_row["value"] is None:
                continue
            current_at = datetime.fromisoformat(str(change_row["observed_at"]))
            previous_at = current_at - timedelta(days=1)

            current_level = connection.execute(
                """
                SELECT value
                FROM observations
                WHERE station_index = ? AND observed_at = ?
                  AND parameter_code = 'WATER_LEVEL'
                """,
                (
                    change_row["station_index"],
                    current_at.isoformat(timespec="seconds"),
                ),
            ).fetchone()
            previous_level = connection.execute(
                """
                SELECT value
                FROM observations
                WHERE station_index = ? AND observed_at = ?
                  AND parameter_code = 'WATER_LEVEL'
                """,
                (
                    change_row["station_index"],
                    previous_at.isoformat(timespec="seconds"),
                ),
            ).fetchone()

            if (
                current_level is None
                or previous_level is None
                or current_level[0] is None
                or previous_level[0] is None
            ):
                missing_parts: list[str] = []
                if current_level is None or current_level[0] is None:
                    missing_parts.append("поточний рівень")
                if previous_level is None or previous_level[0] is None:
                    missing_parts.append("рівень попередньої доби")
                current_status = connection.execute(
                    """
                    SELECT quality_status
                    FROM observations
                    WHERE observation_id = ?
                    """,
                    (change_row["observation_id"],),
                ).fetchone()
                if current_status and current_status[0] not in {
                    MISSING,
                    OUT_OF_RANGE,
                }:
                    connection.execute(
                        """
                        UPDATE observations
                        SET quality_status = ?, quality_message = ?
                        WHERE observation_id = ?
                        """,
                        (
                            SUSPICIOUS,
                            "Добову зміну не вдалося звірити: відсутній "
                            + " та ".join(missing_parts)
                            + ".",
                            change_row["observation_id"],
                        ),
                    )
                continue

            expected = float(current_level[0]) - float(previous_level[0])
            actual = float(change_row["value"])
            if abs(expected - actual) <= 1e-9:
                continue

            current_status = connection.execute(
                """
                SELECT quality_status
                FROM observations
                WHERE observation_id = ?
                """,
                (change_row["observation_id"],),
            ).fetchone()
            if current_status and current_status[0] in {MISSING, OUT_OF_RANGE}:
                continue

            message = (
                f"Добова зміна у коді {actual:g} см, але різниця рівнів "
                f"становить {expected:g} см."
            )
            connection.execute(
                """
                UPDATE observations
                SET quality_status = ?, quality_message = ?
                WHERE observation_id = ?
                """,
                (
                    INCONSISTENT_CHANGE,
                    message,
                    change_row["observation_id"],
                ),
            )
            inconsistent += 1

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

    normalized = [status or "NOT_CHECKED" for status in statuses]
    if not normalized:
        return MISSING
    return max(normalized, key=lambda item: QUALITY_PRIORITY.get(item, 0))
