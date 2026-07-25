"""Tests for online settings and controlled downloads."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hydrobulletin.sources import (
    DataSourceError,
    OnlineConnection,
    OnlineDataSource,
    OnlineSourceSettings,
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

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class FakeOpener:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout: float):
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
        self.assertIn(b"FIND=%DA%D2%D5%D252%2A", opener.requests[1][0].data.upper())

    def test_http_error(self) -> None:
        opener = FakeOpener(FakeResponse(b"error", status=500))
        source = OnlineDataSource(
            OnlineConnection("https://example.test/armua", "user", "pass"),
            "12.07.2026",
            "ZRUR52",
            opener=opener,
        )
        with self.assertRaises(DataSourceError):
            source.load_text()


if __name__ == "__main__":
    unittest.main()
