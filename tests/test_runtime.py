"""Перевірки шляхів Python-запуску й зібраного Windows-застосунку."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from hydrobulletin.runtime import RuntimePaths, resolve_runtime_paths


PROJECT_DIR = Path(__file__).resolve().parents[1]
WINDOWS_LAUNCHERS = {
    "build_exe.bat": "PyInstaller",
    "measure_operational_scenario.bat": "--samples 5",
    "run_full_demo.bat": "--samples 1",
    "run_gui.bat": "main.py --gui",
    "run_release.bat": "HydroBulletin.exe",
    "run_tests.bat": "run_tests.py",
    "run_typecheck.bat": "python -m pyright",
}
WINDOWS_LAUNCHER_TARGETS = {
    "build_exe.bat": "HydroBulletin.spec",
    "measure_operational_scenario.bat": "scripts/validate_operational_scenario.py",
    "run_full_demo.bat": "scripts/validate_operational_scenario.py",
    "run_gui.bat": "main.py",
    "run_tests.bat": "run_tests.py",
    "run_typecheck.bat": "requirements-dev.txt",
}


class RuntimePathTests(unittest.TestCase):
    def test_source_run_uses_project_root_for_resources_and_data(self) -> None:
        project_root = Path("test-runtime/project").resolve()
        result = resolve_runtime_paths(
            project_root / "main.py",
            frozen=False,
        )

        self.assertEqual(result, RuntimePaths(project_root, project_root))

    def test_frozen_run_keeps_writable_data_beside_executable(self) -> None:
        release_root = Path("test-runtime/release/HydroBulletin").resolve()
        bundle_root = release_root / "_internal"
        result = resolve_runtime_paths(
            bundle_root / "launcher.py",
            frozen=True,
            executable=release_root / "HydroBulletin.exe",
            bundle_root=bundle_root,
        )

        self.assertEqual(result.resource_root, bundle_root)
        self.assertEqual(result.data_root, release_root)

        with patch(
            "hydrobulletin.runtime.sys._MEIPASS", str(bundle_root), create=True
        ):
            detected = resolve_runtime_paths(
                bundle_root / "launcher.py",
                frozen=True,
                executable=release_root / "HydroBulletin.exe",
            )
        self.assertEqual(detected, result)


class WindowsLauncherTests(unittest.TestCase):
    def test_only_supported_launchers_remain(self) -> None:
        actual = {path.name for path in PROJECT_DIR.glob("*.bat")}
        self.assertEqual(actual, set(WINDOWS_LAUNCHERS))

    def test_launchers_are_ascii_crlf_and_use_project_root(self) -> None:
        for name, required_fragment in WINDOWS_LAUNCHERS.items():
            with self.subTest(name=name):
                payload = (PROJECT_DIR / name).read_bytes()
                text = payload.decode("ascii")
                self.assertTrue(payload.endswith(b"\r\n"))
                self.assertNotIn(b"\n", payload.replace(b"\r\n", b""))
                self.assertIn('cd /d "%~dp0"', text)
                self.assertIn(required_fragment, text)
                target = WINDOWS_LAUNCHER_TARGETS.get(name)
                if target is not None:
                    self.assertTrue((PROJECT_DIR / target).is_file())

    def test_windows_bundle_excludes_private_operational_inputs(self) -> None:
        spec_text = (PROJECT_DIR / "HydroBulletin.spec").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '("demo_data/regression", "demo_data/regression")',
            spec_text,
        )
        self.assertNotIn('("demo_data", "demo_data")', spec_text)
        self.assertNotIn("full_private", spec_text)
        self.assertNotIn('"unittest"', spec_text)


if __name__ == "__main__":
    unittest.main()
