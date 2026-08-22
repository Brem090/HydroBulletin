"""Шляхи до ресурсів і робочих даних для Python та зібраного EXE."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    """Корені незмінних ресурсів і створюваних користувачем даних."""

    resource_root: Path
    data_root: Path


def resolve_runtime_paths(
    entry_file: Path,
    *,
    frozen: bool | None = None,
    executable: Path | None = None,
    bundle_root: Path | None = None,
) -> RuntimePaths:
    """Визначає корені запуску без запису до тимчасової папки PyInstaller.

    У звичайному Python-запуску ресурси й робочі дані лежать біля ``main.py``.
    У зібраному застосунку ресурси читаються з каталогу пакета, а SQLite,
    raw-архів, ``.env`` і результати створюються біля ``HydroBulletin.exe``.
    """

    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    source_root = Path(entry_file).resolve().parent
    if not is_frozen:
        return RuntimePaths(source_root, source_root)

    executable_path = Path(executable or sys.executable).resolve()
    if bundle_root is not None:
        resource_root = Path(bundle_root).resolve()
    else:
        pyinstaller_root = getattr(sys, "_MEIPASS", None)
        resource_root = (
            Path(str(pyinstaller_root)).resolve()
            if pyinstaller_root is not None
            else executable_path.parent
        )
    return RuntimePaths(resource_root, executable_path.parent)
