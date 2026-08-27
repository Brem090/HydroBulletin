"""Локальне та онлайн-джерела кодованих гідрологічних повідомлень."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (
    HTTPBasicAuthHandler,
    HTTPPasswordMgrWithDefaultRealm,
    OpenerDirector,
    Request,
    build_opener,
)

from .timeutils import ukraine_local_to_utc


SUPPORTED_MESSAGE_TYPES = ("ZRUR52", "ZRUR53", "ZRUR71")
MESSAGE_SEARCH_QUERIES = {
    "ZRUR52": "зрур52*",
    "ZRUR53": "зрур53*",
    "ZRUR71": "зрур71*",
}
GCST_PRIMARY_BASE_URL = "http://gcst.meteo.gov.ua/armua"
GCST_MIRROR_BASE_URL = "http://rgcst.meteo.gov.ua/armua"
GCST_SOURCE_AUTO = "auto"
GCST_SOURCE_PRIMARY = "primary"
GCST_SOURCE_MIRROR = "mirror"
GCST_SOURCE_MODES = (
    GCST_SOURCE_AUTO,
    GCST_SOURCE_PRIMARY,
    GCST_SOURCE_MIRROR,
)
GCST_SOURCE_LABELS = {
    GCST_SOURCE_AUTO: "Автоматично (основний → дзеркало)",
    GCST_SOURCE_PRIMARY: "Основний ГЦСТ",
    GCST_SOURCE_MIRROR: "Дзеркало ГЦСТ",
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
    """Помилка джерела даних із повідомленням для користувача."""


class TextDataSource(Protocol):
    """Спільний контракт для будь-якого джерела текстового повідомлення."""

    def load_text(self) -> str:
        """Повертає початковий текст повідомлення."""
        ...


@dataclass(frozen=True)
class LocalFileSource:
    """Читає кодоване повідомлення з локального файлу."""

    path: Path
    encoding: str = "utf-8"

    def load_text(self) -> str:
        if not self.path.exists():
            raise FileNotFoundError(f"Файл не знайдено: {self.path}")
        return self.path.read_text(encoding=self.encoding)

    @property
    def source_name(self) -> str:
        return str(self.path)

    @property
    def source_type(self) -> str:
        return "local"


@dataclass(frozen=True)
class OnlineConnection:
    """Адреса, облікові дані та параметри повторення онлайн-запиту."""

    base_url: str
    username: str
    password: str
    timeout_seconds: float = 40.0
    max_attempts: int = 3
    retry_delay_seconds: float = 1.5


@dataclass(frozen=True)
class OnlineSourceSettings:
    """Назви змінних середовища для онлайн-підключення."""

    url_variable: str = "HYDRO_SOURCE_URL"
    username_variable: str = "HYDRO_SOURCE_USERNAME"
    password_variable: str = "HYDRO_SOURCE_PASSWORD"
    timeout_variable: str = "HYDRO_SOURCE_TIMEOUT"
    attempts_variable: str = "HYDRO_SOURCE_ATTEMPTS"
    retry_delay_variable: str = "HYDRO_SOURCE_RETRY_DELAY"
    primary_url_variable: str = "HYDRO_SOURCE_PRIMARY_URL"
    mirror_url_variable: str = "HYDRO_SOURCE_MIRROR_URL"

    def load_connection(
        self,
        env_path: Path | None = None,
        environ: Mapping[str, str] | None = None,
        *,
        base_url: str | None = None,
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
            self.attempts_variable,
            self.retry_delay_variable,
        ):
            if environment.get(key):
                values[key] = environment[key]

        configured_url = values.get(self.url_variable, "").strip()
        selected_url = (base_url or configured_url).strip().rstrip("/")
        username = values.get(self.username_variable, "").strip()
        password = values.get(self.password_variable, "").strip()
        missing = []
        if not selected_url:
            missing.append(self.url_variable)
        if not username:
            missing.append(self.username_variable)
        if not password:
            missing.append(self.password_variable)
        if missing:
            raise DataSourceError(
                f"Не заповнено {', '.join(missing)} у .env чи змінних "
                "середовища."
            )

        try:
            timeout = float(values.get(self.timeout_variable, "40") or "40")
        except ValueError as exc:
            raise DataSourceError("HYDRO_SOURCE_TIMEOUT має бути числом.") from exc
        if timeout <= 0:
            raise DataSourceError("HYDRO_SOURCE_TIMEOUT має бути більшим за нуль.")

        try:
            attempts = int(values.get(self.attempts_variable, "3") or "3")
        except ValueError as exc:
            raise DataSourceError("HYDRO_SOURCE_ATTEMPTS має бути цілим числом.") from exc
        if attempts < 1 or attempts > 10:
            raise DataSourceError("HYDRO_SOURCE_ATTEMPTS має бути від 1 до 10.")

        try:
            retry_delay = float(
                values.get(self.retry_delay_variable, "1.5") or "1.5"
            )
        except ValueError as exc:
            raise DataSourceError(
                "HYDRO_SOURCE_RETRY_DELAY має бути числом."
            ) from exc
        if retry_delay < 0:
            raise DataSourceError(
                "HYDRO_SOURCE_RETRY_DELAY не може бути від'ємним."
            )

        return OnlineConnection(
            selected_url,
            username,
            password,
            timeout,
            attempts,
            retry_delay,
        )

    def load_gcst_connections(
        self,
        source_mode: str = GCST_SOURCE_AUTO,
        env_path: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> tuple[OnlineConnection, ...]:
        """Повертає налаштовані підключення у вибраному порядку."""

        if env_path is None:
            env_path = Path(__file__).resolve().parents[1] / ".env"
        values = _read_env_file(env_path)
        environment = os.environ if environ is None else environ
        for key in (self.primary_url_variable, self.mirror_url_variable):
            if environment.get(key):
                values[key] = environment[key]

        primary_url = (
            values.get(self.primary_url_variable, "").strip()
            or GCST_PRIMARY_BASE_URL
        )
        mirror_url = (
            values.get(self.mirror_url_variable, "").strip()
            or GCST_MIRROR_BASE_URL
        )
        urls = {
            GCST_SOURCE_PRIMARY: primary_url,
            GCST_SOURCE_MIRROR: mirror_url,
        }
        return tuple(
            self.load_connection(
                env_path,
                environ,
                base_url=urls[source_key],
            )
            for source_key in gcst_source_keys(source_mode)
        )


def normalize_gcst_source_mode(source_mode: str) -> str:
    """Перевіряє назву режиму вибору сервера ГЦСТ."""

    normalized = str(source_mode or GCST_SOURCE_AUTO).strip().lower()
    if normalized not in GCST_SOURCE_MODES:
        raise ValueError(
            f"Невідомий серверний режим {source_mode}. "
            f"Доступні: {', '.join(GCST_SOURCE_MODES)}."
        )
    return normalized


def gcst_source_keys(source_mode: str) -> tuple[str, ...]:
    """Визначає порядок серверів для одного онлайн-запиту."""

    normalized = normalize_gcst_source_mode(source_mode)
    if normalized == GCST_SOURCE_PRIMARY:
        return (GCST_SOURCE_PRIMARY,)
    if normalized == GCST_SOURCE_MIRROR:
        return (GCST_SOURCE_MIRROR,)
    return (GCST_SOURCE_PRIMARY, GCST_SOURCE_MIRROR)


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
    def open(self, request: Request, /, *, timeout: float) -> Any:
        ...


def _response_status(response: Any) -> int:
    status = getattr(response, "status", 200)
    return 200 if status is None else int(status)


def _request_bytes_with_retries(
    opener: OpenerProtocol,
    request: Request,
    connection: OnlineConnection,
    description: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """Виконує запит із повторенням лише тимчасових помилок."""

    last_error: Exception | None = None
    for attempt in range(1, connection.max_attempts + 1):
        try:
            with opener.open(
                request,
                timeout=connection.timeout_seconds,
            ) as response:
                status = _response_status(response)
                if 200 <= status < 300:
                    return response.read()
                if status in (401, 403):
                    raise DataSourceError(
                        f"{description}: доступ відхилено (HTTP {status}); "
                        "перевірте логін і пароль."
                    )
                if status != 429 and status < 500:
                    raise DataSourceError(
                        f"{description}: сайт повернув HTTP {status}."
                    )
                last_error = DataSourceError(
                    f"{description}: тимчасова відповідь HTTP {status}."
                )
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise DataSourceError(
                    f"{description}: доступ відхилено (HTTP {exc.code}); "
                    "перевірте логін і пароль."
                ) from exc
            if exc.code != 429 and exc.code < 500:
                raise DataSourceError(
                    f"{description}: сайт повернув HTTP {exc.code}."
                ) from exc
            last_error = exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc

        if attempt < connection.max_attempts:
            delay = connection.retry_delay_seconds * (2 ** (attempt - 1))
            if delay:
                sleep(delay)

    detail = f": {last_error}" if last_error else ""
    raise DataSourceError(
        f"{description}: не вдалося отримати дані після "
        f"{connection.max_attempts} спроб{detail}"
    ) from last_error


@dataclass
class OnlineDataSource:
    """Отримує один із файлів ZRUR52/ZRUR53/ZRUR71 із робочого сайту."""

    connection: OnlineConnection
    bulletin_date: str
    message_type: str
    opener: OpenerProtocol | None = None
    response_encoding: str = "koi8-u"
    sleep: Callable[[float], None] = time.sleep

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

    @property
    def source_type(self) -> str:
        return "online"

    def _build_opener(self) -> OpenerDirector:
        password_manager = HTTPPasswordMgrWithDefaultRealm()
        password_manager.add_password(
            None,
            self.connection.base_url,
            self.connection.username,
            self.connection.password,
        )
        return build_opener(HTTPBasicAuthHandler(password_manager))

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

        index_request = Request(index_url, headers={"User-Agent": "Mozilla/5.0"})
        _request_bytes_with_retries(
            opener,
            index_request,
            self.connection,
            "Підключення до ГЦСТ",
            sleep=self.sleep,
        )

        search_request = Request(
            show_url,
            data=body,
            headers=headers,
            method="POST",
        )
        payload = _request_bytes_with_retries(
            opener,
            search_request,
            self.connection,
            f"Завантаження {self.message_type}",
            sleep=self.sleep,
        )

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


@dataclass(frozen=True)
class ArchiveDataSource:
    """Читає останню збережену версію raw-повідомлення за датою і типом."""

    raw_root: Path
    bulletin_date: str
    message_type: str
    encoding: str = "utf-8"

    def _resolve_path(self) -> Path:
        try:
            observed_date = datetime.strptime(self.bulletin_date, "%d.%m.%Y")
        except ValueError as exc:
            raise ValueError("Дата має бути у форматі ДД.ММ.РРРР.") from exc

        normalized_type = self.message_type.upper()
        folder = (
            Path(self.raw_root)
            / f"{observed_date.year:04d}"
            / f"{observed_date.month:02d}"
        )
        stem = f"{observed_date:%Y-%m-%d}_{normalized_type}"
        candidates = list(folder.glob(f"{stem}*.txt"))
        if not candidates:
            raise FileNotFoundError(
                f"В архіві немає {normalized_type} за {self.bulletin_date}: "
                f"{folder}"
            )

        def version(path: Path) -> int:
            suffix = path.stem.removeprefix(stem)
            if not suffix:
                return 1
            match = re.fullmatch(r"_(\d+)", suffix)
            return int(match.group(1)) if match else 0

        return max(candidates, key=lambda path: (version(path), path.stat().st_mtime))

    @property
    def path(self) -> Path:
        return self._resolve_path()

    @property
    def source_name(self) -> str:
        return str(self.path)

    @property
    def source_type(self) -> str:
        return "archive"

    def load_text(self) -> str:
        return self.path.read_text(encoding=self.encoding)


@dataclass
class FallbackDataSource:
    """Послідовно пробує джерела й запам'ятовує фактично використане."""

    sources: Sequence[TextDataSource]
    resolved_source_type: str = ""
    resolved_source_name: str = ""

    @property
    def source_type(self) -> str:
        return self.resolved_source_type or "auto"

    @property
    def source_name(self) -> str:
        return self.resolved_source_name or "автоматичне джерело"

    def load_text(self) -> str:
        errors: list[str] = []
        for source in self.sources:
            try:
                text = source.load_text()
            except (DataSourceError, FileNotFoundError, OSError, ValueError) as exc:
                label = getattr(source, "source_type", source.__class__.__name__)
                errors.append(f"{label}: {exc}")
                continue
            if not text.strip():
                errors.append(f"{source.__class__.__name__}: порожній текст")
                continue
            self.resolved_source_type = str(
                getattr(source, "source_type", source.__class__.__name__)
            )
            self.resolved_source_name = str(
                getattr(source, "source_name", source.__class__.__name__)
            )
            return text

        detail = "; ".join(errors) if errors else "джерела не задані"
        raise DataSourceError(f"Жодне резервне джерело не спрацювало: {detail}")


@dataclass
class OnlineMeteoDataSource:
    """Завантажує SYNOP-повідомлення потрібних метеостанцій."""

    connection: OnlineConnection
    bulletin_date: str
    station_indexes: Sequence[str]
    opener: OpenerProtocol | None = None
    response_encoding: str = "koi8-u"
    message_count: int = 32
    sleep: Callable[[float], None] = time.sleep

    @property
    def source_type(self) -> str:
        return "online_meteo"

    @property
    def source_name(self) -> str:
        return f"SYNOP@{self.connection.base_url}"

    def _build_opener(self) -> OpenerDirector:
        password_manager = HTTPPasswordMgrWithDefaultRealm()
        password_manager.add_password(
            None,
            self.connection.base_url,
            self.connection.username,
            self.connection.password,
        )
        return build_opener(HTTPBasicAuthHandler(password_manager))

    def load_text(self) -> str:
        if not self.station_indexes:
            raise DataSourceError("Не задано метеостанції для завантаження SYNOP.")
        try:
            bulletin = datetime.strptime(self.bulletin_date, "%d.%m.%Y")
        except ValueError as exc:
            raise ValueError("Дата має бути у форматі ДД.ММ.РРРР.") from exc

        base_url = self.connection.base_url.rstrip("/")
        index_url = f"{base_url}/sino/index.phtml?NODEF=ON&SM=ON&SI=ON"
        blanks_url = f"{base_url}/sino/blanks.phtml"
        opener = self.opener or self._build_opener()

        index_request = Request(index_url, headers={"User-Agent": "Mozilla/5.0"})
        _request_bytes_with_retries(
            opener,
            index_request,
            self.connection,
            "Підключення до SYNOP-архіву",
            sleep=self.sleep,
        )

        local_end = bulletin.replace(hour=23, minute=59, second=59)
        utc_end = ukraine_local_to_utc(local_end)
        local_start = (bulletin - timedelta(days=2)).replace(
            hour=0,
            minute=0,
            second=0,
        )
        utc_start = ukraine_local_to_utc(local_start) - timedelta(seconds=1)

        def fetch(blank_value: str) -> str:
            form = {
                "T1": " ".join(dict.fromkeys(self.station_indexes)),
                "blank": blank_value,
                "nabors": "",
                "numb": str(self.message_count),
                "srok": utc_end.strftime("%Y-%m-%d %H:%M:%S"),
                "dosrok": utc_start.strftime("%Y-%m-%d %H:%M:%S"),
                "SM": "on",
                "SI": "on",
            }
            body = urlencode(form, encoding="koi8-u").encode("ascii")
            request = Request(
                blanks_url,
                data=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Mozilla/5.0",
                    "Referer": index_url,
                },
                method="POST",
            )
            payload = _request_bytes_with_retries(
                opener,
                request,
                self.connection,
                "Завантаження SYNOP",
                sleep=self.sleep,
            )
            try:
                return html_to_text(payload.decode(self.response_encoding))
            except UnicodeDecodeError as exc:
                raise DataSourceError(
                    "SYNOP-відповідь не вдалося декодувати як KOI8-U."
                ) from exc

        text = fetch("norm")
        if not any(index in text for index in self.station_indexes):
            text = fetch("zipfile")
        if not any(index in text for index in self.station_indexes):
            raise DataSourceError(
                "У SYNOP-відповіді не знайдено вибраних метеостанцій."
            )
        return text
