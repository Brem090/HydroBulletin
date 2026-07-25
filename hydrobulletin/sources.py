"""Локальне та онлайн-джерела кодованих гідрологічних повідомлень."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (
    HTTPBasicAuthHandler,
    HTTPPasswordMgrWithDefaultRealm,
    Request,
    build_opener,
)


SUPPORTED_MESSAGE_TYPES = ("ZRUR52", "ZRUR53", "ZRUR71")
MESSAGE_SEARCH_QUERIES = {
    "ZRUR52": "зрур52*",
    "ZRUR53": "зрур53*",
    "ZRUR71": "зрур71*",
}
_HTML_BLOCK_TAGS = {
    "br",
    "div",
    "h1",
    "h2",
    "h3",
    "p",
    "table",
    "td",
    "th",
    "tr",
}


class DataSourceError(RuntimeError):
    """Зрозуміла помилка отримання або перевірки джерела даних."""


class TextDataSource(Protocol):
    """Спільний контракт для будь-якого джерела текстового повідомлення."""

    def load_text(self) -> str:
        """Повернути початковий текст повідомлення."""
        ...


@dataclass(frozen=True)
class LocalFileSource:
    """Читає кодоване повідомлення з локального файла."""

    path: Path
    encoding: str = "utf-8"

    def load_text(self) -> str:
        if not self.path.exists():
            raise FileNotFoundError(f"Файл не знайдено: {self.path}")
        return self.path.read_text(encoding=self.encoding)

    @property
    def source_name(self) -> str:
        return str(self.path)


@dataclass(frozen=True)
class OnlineConnection:
    """Перевірені значення підключення без збереження їх у коді."""

    base_url: str
    username: str
    password: str
    timeout_seconds: float = 40.0


@dataclass(frozen=True)
class OnlineSourceSettings:
    """Назви змінних середовища для безпечного онлайн-підключення."""

    url_variable: str = "HYDRO_SOURCE_URL"
    username_variable: str = "HYDRO_SOURCE_USERNAME"
    password_variable: str = "HYDRO_SOURCE_PASSWORD"
    timeout_variable: str = "HYDRO_SOURCE_TIMEOUT"

    def load_connection(
        self,
        env_path: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> OnlineConnection:
        """Завантажує адресу й доступи з ``.env`` та змінних середовища."""

        if env_path is None:
            env_path = Path(__file__).resolve().parents[1] / ".env"

        values = _read_env_file(env_path)
        environment = os.environ if environ is None else environ
        for key in (
            self.url_variable,
            self.username_variable,
            self.password_variable,
            self.timeout_variable,
        ):
            if environment.get(key):
                values[key] = environment[key]

        base_url = values.get(self.url_variable, "").strip().rstrip("/")
        username = values.get(self.username_variable, "").strip()
        password = values.get(self.password_variable, "").strip()
        if not base_url or not username or not password:
            raise DataSourceError(
                "Не заповнено HYDRO_SOURCE_URL, HYDRO_SOURCE_USERNAME або "
                "HYDRO_SOURCE_PASSWORD у .env чи змінних середовища."
            )

        try:
            timeout = float(values.get(self.timeout_variable, "40") or "40")
        except ValueError as exc:
            raise DataSourceError("HYDRO_SOURCE_TIMEOUT має бути числом.") from exc
        if timeout <= 0:
            raise DataSourceError("HYDRO_SOURCE_TIMEOUT має бути більшим за нуль.")

        return OnlineConnection(base_url, username, password, timeout)


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() in _HTML_BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def get_text(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.get_text()


def validate_downloaded_message(text: str, bulletin_date: str) -> None:
    """Відхиляє порожню, явно пошкоджену або недатовану відповідь."""

    if not text.strip():
        raise DataSourceError("Сайт повернув порожній файл.")

    try:
        expected_day = datetime.strptime(bulletin_date, "%d.%m.%Y").strftime("%d")
    except ValueError as exc:
        raise ValueError("Дата має бути у форматі ДД.ММ.РРРР.") from exc

    date_groups = re.findall(r"\b\d{5}\s+(\d{5})\b", text)
    if not date_groups:
        raise DataSourceError("У відповіді не знайдено кодованих записів гідропостів.")
    if not any(group.startswith(expected_day) for group in date_groups):
        raise DataSourceError(
            f"У відповіді немає записів за день {expected_day}; перевірте вибрану дату."
        )


class OpenerProtocol(Protocol):
    def open(self, request: Request, timeout: float) -> Any:
        ...

@dataclass
class OnlineDataSource:
    """Отримує один із файлів ZRUR52/ZRUR53/ZRUR71 із робочого сайту."""

    connection: OnlineConnection
    bulletin_date: str
    message_type: str
    opener: OpenerProtocol | None = None
    response_encoding: str = "koi8-u"

    def __post_init__(self) -> None:
        self.message_type = self.message_type.upper()
        if self.message_type not in SUPPORTED_MESSAGE_TYPES:
            raise ValueError(
                f"Непідтримуваний тип {self.message_type}. "
                f"Доступні: {', '.join(SUPPORTED_MESSAGE_TYPES)}."
            )

    @property
    def source_name(self) -> str:
        return f"{self.message_type}@{self.connection.base_url}"

    def _build_opener(self):
        password_manager = HTTPPasswordMgrWithDefaultRealm()
        password_manager.add_password(
            None,
            self.connection.base_url,
            self.connection.username,
            self.connection.password,
        )
        return build_opener(HTTPBasicAuthHandler(password_manager))

    @staticmethod
    def _check_status(response) -> None:
        status = getattr(response, "status", 200)
        if status is not None and not 200 <= int(status) < 300:
            raise DataSourceError(f"Сайт повернув HTTP-статус {status}.")

    def load_text(self) -> str:
        base_url = self.connection.base_url.rstrip("/")
        index_url = f"{base_url}/index.phtml"
        journal_url = f"{base_url}/jornal/index.phtml"
        show_url = f"{base_url}/jornal/show.phtml"
        bulletin = datetime.strptime(self.bulletin_date, "%d.%m.%Y")
        search_time = (bulletin - timedelta(days=1)).strftime("%Y-%m-%d 12:00:00")

        form = {
            "find": MESSAGE_SEARCH_QUERIES[self.message_type],
            "find1": "",
            "dip": "1",
            "srok": search_time,
            "sub": "       Знайти        ",
        }
        body = urlencode(form, encoding="koi8-u").encode("ascii")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0",
            "Referer": journal_url,
        }
        opener = self.opener or self._build_opener()

        try:
            index_request = Request(index_url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(
                index_request,
                timeout=self.connection.timeout_seconds,
            ) as response:
                self._check_status(response)
                response.read()

            search_request = Request(
                show_url,
                data=body,
                headers=headers,
                method="POST",
            )
            with opener.open(
                search_request,
                timeout=self.connection.timeout_seconds,
            ) as response:
                self._check_status(response)
                payload = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise DataSourceError(
                f"Не вдалося завантажити {self.message_type}: {exc}"
            ) from exc

        try:
            html = payload.decode(self.response_encoding)
        except UnicodeDecodeError as exc:
            raise DataSourceError(
                f"Відповідь {self.message_type} не вдалося декодувати як "
                f"{self.response_encoding}."
            ) from exc

        text = html_to_text(html)
        validate_downloaded_message(text, self.bulletin_date)
        return text
