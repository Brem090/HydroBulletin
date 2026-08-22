"""Перевірки онлайн-налаштувань та отримання даних."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import TracebackType
from urllib.request import Request

from hydrobulletin.sources import (
    ArchiveDataSource,
    DataSourceError,
    FallbackDataSource,
    GCST_MIRROR_BASE_URL,
    GCST_PRIMARY_BASE_URL,
    GCST_SOURCE_AUTO,
    GCST_SOURCE_MIRROR,
    GCST_SOURCE_PRIMARY,
    LocalFileSource,
    OnlineConnection,
    OnlineDataSource,
    OnlineSourceSettings,
    gcst_source_keys,
    html_to_text,
    validate_downloaded_message,
)


class FakeResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def read(self) -> bytes:
        return self.payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class FakeOpener:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[Request, float]] = []

    def open(self, request: Request, timeout: float) -> FakeResponse:
        self.requests.append((request, timeout))
        return self.responses.pop(0)


class SettingsTests(unittest.TestCase):
    def test_reads_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                "HYDRO_SOURCE_URL=https://example.test/base\n"
                "HYDRO_SOURCE_USERNAME=user\n"
                "HYDRO_SOURCE_PASSWORD=secret\n"
                "HYDRO_SOURCE_TIMEOUT=15\n",
                encoding="utf-8",
            )
            connection = OnlineSourceSettings().load_connection(env_path, environ={})

        self.assertEqual(connection.base_url, "https://example.test/base")
        self.assertEqual(connection.username, "user")
        self.assertEqual(connection.password, "secret")
        self.assertEqual(connection.timeout_seconds, 15.0)

    def test_environment_has_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                "HYDRO_SOURCE_URL=https://old.test\n"
                "HYDRO_SOURCE_USERNAME=old\n"
                "HYDRO_SOURCE_PASSWORD=old\n",
                encoding="utf-8",
            )
            connection = OnlineSourceSettings().load_connection(
                env_path,
                environ={
                    "HYDRO_SOURCE_URL": "https://new.test",
                    "HYDRO_SOURCE_USERNAME": "new-user",
                    "HYDRO_SOURCE_PASSWORD": "new-pass",
                },
            )

        self.assertEqual(connection.base_url, "https://new.test")
        self.assertEqual(connection.username, "new-user")

    def test_missing_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(DataSourceError):
                OnlineSourceSettings().load_connection(
                    Path(tmp_dir) / ".env",
                    environ={},
                )

    def test_gcst_server_order(self) -> None:
        self.assertEqual(
            gcst_source_keys(GCST_SOURCE_AUTO),
            (GCST_SOURCE_PRIMARY, GCST_SOURCE_MIRROR),
        )
        self.assertEqual(
            gcst_source_keys(GCST_SOURCE_MIRROR),
            (GCST_SOURCE_MIRROR,),
        )

    def test_gcst_connections_have_built_in_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                "HYDRO_SOURCE_USERNAME=user\n"
                "HYDRO_SOURCE_PASSWORD=secret\n",
                encoding="utf-8",
            )
            connections = OnlineSourceSettings().load_gcst_connections(
                GCST_SOURCE_AUTO,
                env_path,
                environ={},
            )

        self.assertEqual(
            tuple(item.base_url for item in connections),
            (GCST_PRIMARY_BASE_URL, GCST_MIRROR_BASE_URL),
        )


class DownloadTests(unittest.TestCase):
    def test_converts_html_to_text(self) -> None:
        html = "<table><tr><td>81015 12081</td><td>10186 20031</td></tr></table>"
        text = html_to_text(html)
        self.assertIn("81015 12081", text)
        self.assertIn("10186 20031", text)

    def test_rejects_invalid_response(self) -> None:
        with self.assertRaises(DataSourceError):
            validate_downloaded_message("", "12.07.2026")
        with self.assertRaises(DataSourceError):
            validate_downloaded_message("81015 11081 10186", "12.07.2026")

    def test_online_request(self) -> None:
        html = (
            "<html><table><tr><td>81015 12081 10186 20031 30180 "
            "41900 81234 00081 =</td></tr></table></html>"
        )
        opener = FakeOpener(
            FakeResponse(b"index"),
            FakeResponse(html.encode("koi8-u")),
        )
        source = OnlineDataSource(
            OnlineConnection("https://example.test/armua", "user", "pass", 12),
            "12.07.2026",
            "ZRUR52",
            opener=opener,
        )

        result = source.load_text()

        self.assertIn("81015 12081", result)
        self.assertEqual(len(opener.requests), 2)
        self.assertTrue(opener.requests[0][0].full_url.endswith("/index.phtml"))
        self.assertTrue(opener.requests[1][0].full_url.endswith("/jornal/show.phtml"))
        self.assertEqual(opener.requests[1][0].method, "POST")
        request_data = opener.requests[1][0].data
        self.assertIsInstance(request_data, bytes)
        assert isinstance(request_data, bytes)
        self.assertIn(b"FIND=%DA%D2%D5%D252%2A", request_data.upper())

    def test_http_error(self) -> None:
        opener = FakeOpener(FakeResponse(b"error", status=500))
        source = OnlineDataSource(
            OnlineConnection(
                "https://example.test/armua",
                "user",
                "pass",
                max_attempts=1,
                retry_delay_seconds=0,
            ),
            "12.07.2026",
            "ZRUR52",
            opener=opener,
        )
        with self.assertRaises(DataSourceError):
            source.load_text()

    def test_retries_temporary_response(self) -> None:
        html = "<td>81015 12081 10186 20031 =</td>"
        opener = FakeOpener(
            FakeResponse(b"temporary", status=500),
            FakeResponse(b"index"),
            FakeResponse(html.encode("koi8-u")),
        )
        source = OnlineDataSource(
            OnlineConnection(
                "https://example.test/armua",
                "user",
                "pass",
                max_attempts=2,
                retry_delay_seconds=0,
            ),
            "12.07.2026",
            "ZRUR52",
            opener=opener,
        )

        self.assertIn("81015 12081", source.load_text())
        self.assertEqual(len(opener.requests), 3)


class FallbackAndArchiveTests(unittest.TestCase):
    def test_fallback_uses_next_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            folder = Path(tmp_dir)
            available = folder / "available.txt"
            available.write_text("дані", encoding="utf-8")
            source = FallbackDataSource(
                (
                    LocalFileSource(folder / "missing.txt"),
                    LocalFileSource(available),
                )
            )

            self.assertEqual(source.load_text(), "дані")
            self.assertEqual(source.source_type, "local")
            self.assertEqual(source.source_name, str(available))

    def test_gcst_fallback_uses_mirror_when_primary_has_no_data(self) -> None:
        wrong_day = "<td>81015 11081 10186 20031 =</td>"
        valid = "<td>81015 12081 10186 20031 =</td>"
        primary = OnlineDataSource(
            OnlineConnection(GCST_PRIMARY_BASE_URL, "user", "pass"),
            "12.07.2026",
            "ZRUR52",
            opener=FakeOpener(
                FakeResponse(b"index"),
                FakeResponse(wrong_day.encode("koi8-u")),
            ),
        )
        mirror = OnlineDataSource(
            OnlineConnection(GCST_MIRROR_BASE_URL, "user", "pass"),
            "12.07.2026",
            "ZRUR52",
            opener=FakeOpener(
                FakeResponse(b"index"),
                FakeResponse(valid.encode("koi8-u")),
            ),
        )
        source = FallbackDataSource((primary, mirror))

        self.assertIn("81015 12081", source.load_text())
        self.assertEqual(source.source_type, "online")
        self.assertEqual(
            source.source_name,
            f"ZRUR52@{GCST_MIRROR_BASE_URL}",
        )

    def test_archive_uses_latest_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            folder = Path(tmp_dir) / "2026" / "07"
            folder.mkdir(parents=True)
            (folder / "2026-07-12_ZRUR52.txt").write_text(
                "перша",
                encoding="utf-8",
            )
            newest = folder / "2026-07-12_ZRUR52_3.txt"
            newest.write_text("третя", encoding="utf-8")

            source = ArchiveDataSource(
                Path(tmp_dir),
                "12.07.2026",
                "ZRUR52",
            )

            self.assertEqual(source.load_text(), "третя")
            self.assertEqual(source.path, newest)


if __name__ == "__main__":
    unittest.main()
