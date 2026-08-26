"""Запуск перевірок HydroBulletin у стислому або докладному режимі."""

from __future__ import annotations

import argparse
import io
import unittest
from collections import OrderedDict
from pathlib import Path
from typing import Iterable


GROUP_LABELS = {
    "test_archive_pipeline": "Архів і конвеєр",
    "test_decoder": "Базове декодування",
    "test_measurements": "Гідрологічні параметри",
    "test_meteorology": "SYNOP-опади та мапінг",
    "test_quality": "Первинний контроль якості",
    "test_sources": "Джерела даних та онлайн-завантаження",
    "test_gui": "Інтерфейс і масштабування",
    "test_output_paths": "Календарна структура матеріалів",
    "test_operational_validation": "Повний експлуатаційний сценарій",
    "test_runtime": "Windows-шляхи та файли запуску",
    "test_bulletin_workflow": "Пакетний імпорт і Word-бюлетені",
    "test_visual_products": "Карта, графіки та походження даних",
    "test_product_workflow": "Бюлетені, карта й графіки наскрізно",
    "test_operational_tools": "Правки, екстремуми, лід і Панель рівнів",
    "test_correction_workflow": "Правка, Word і provenance наскрізно",
}


def iter_tests(suite: unittest.TestSuite) -> Iterable[unittest.TestCase]:
    """Послідовно повертає окремі перевірки з набору unittest."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_tests(item)
        else:
            yield item


def load_test_groups(project_dir: Path) -> OrderedDict[str, unittest.TestSuite]:
    """Знаходить перевірки та групує їх за модулями."""
    discovered = unittest.defaultTestLoader.discover(
        str(project_dir / "tests"),
        pattern="test_*.py",
    )

    grouped_cases: OrderedDict[str, list[unittest.TestCase]] = OrderedDict()
    for test in iter_tests(discovered):
        module_name = test.__class__.__module__.split(".")[-1]
        grouped_cases.setdefault(module_name, []).append(test)

    return OrderedDict(
        (module_name, unittest.TestSuite(cases))
        for module_name, cases in grouped_cases.items()
    )


def print_failure_details(result: unittest.TestResult) -> None:
    """Виводить подробиці лише для невдалих перевірок."""
    problems = [
        ("ПОМИЛКА ТЕСТУ", test, traceback)
        for test, traceback in result.failures
    ]
    problems.extend(
        ("ПОМИЛКА ВИКОНАННЯ", test, traceback)
        for test, traceback in result.errors
    )

    for kind, test, traceback in problems:
        print(f"\n{kind}: {test.id()}")
        print(traceback.rstrip())


def run_compact(project_dir: Path) -> int:
    """Запускає перевірки зі стислим підсумком."""
    groups = load_test_groups(project_dir)
    total_run = 0
    total_passed = 0
    total_skipped = 0
    all_successful = True
    results: list[unittest.TestResult] = []

    print("HydroBulletin — автоматична перевірка")
    print("-" * 58)

    for module_name, suite in groups.items():
        runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
        result = runner.run(suite)
        results.append(result)

        failed = len(result.failures) + len(result.errors) + len(result.unexpectedSuccesses)
        skipped = len(result.skipped)
        passed = result.testsRun - failed - skipped
        default_label = module_name.replace("test_", "").replace("_", " ").title()
        label = GROUP_LABELS.get(module_name, default_label)
        marker = "OK" if result.wasSuccessful() else "FAIL"

        print(f"[{marker:4}] {label:<38} {passed}/{result.testsRun}")

        total_run += result.testsRun
        total_passed += passed
        total_skipped += skipped
        all_successful = all_successful and result.wasSuccessful()

    print("-" * 58)
    if all_successful:
        print(f"Результат: {total_passed}/{total_run} тестів пройдено успішно.")
        if total_skipped:
            print(f"Пропущено: {total_skipped}.")
        return 0

    print(f"Результат: {total_passed}/{total_run} тестів пройдено.")
    for result in results:
        print_failure_details(result)
    return 1


def run_verbose(project_dir: Path) -> int:
    """Запускає перевірки зі стандартним докладним виведенням unittest."""
    suite = unittest.defaultTestLoader.discover(
        str(project_dir / "tests"),
        pattern="test_*.py",
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the HydroBulletin test suite.")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show every individual test",
    )
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parent
    return run_verbose(project_dir) if args.verbose else run_compact(project_dir)


if __name__ == "__main__":
    raise SystemExit(main())
