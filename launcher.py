"""Віконна точка входу для Windows-збірки HydroBulletin."""

from __future__ import annotations

from pathlib import Path

from hydrobulletin.gui import launch_gui
from hydrobulletin.runtime import resolve_runtime_paths


def main() -> int:
    """Запускає GUI з окремими коренями ресурсів і робочих даних."""

    paths = resolve_runtime_paths(Path(__file__))
    launch_gui(paths.resource_root, paths.data_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
