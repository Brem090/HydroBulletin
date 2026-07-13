"""Підготовча архітектура джерел даних.

На першому тижні реально працює локальне джерело. Онлайн-джерело буде
реалізовано на другому тижні, але решта програми вже не залежить від способу
отримання тексту.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class TextDataSource(Protocol):
    """Спільний контракт для будь-якого джерела текстового повідомлення."""

    def load_text(self) -> str:
        """Повернути початковий текст повідомлення."""
        ...


@dataclass(frozen=True)
class LocalFileSource:
    """Читає демонстраційне або архівне повідомлення з локального файла."""

    path: Path
    encoding: str = "utf-8"

    def load_text(self) -> str:
        if not self.path.exists():
            raise FileNotFoundError(f"Файл не знайдено: {self.path}")
        return self.path.read_text(encoding=self.encoding)


@dataclass(frozen=True)
class OnlineSourceSettings:
    """Назви змінних середовища для майбутнього онлайн-підключення.

    Тут немає логіна, пароля чи адреси. Клас лише фіксує, звідки програма
    повинна буде безпечно читати ці значення на другому тижні.
    """

    url_variable: str = "HYDRO_SOURCE_URL"
    username_variable: str = "HYDRO_SOURCE_USERNAME"
    password_variable: str = "HYDRO_SOURCE_PASSWORD"
