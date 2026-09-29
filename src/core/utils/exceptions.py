"""Кастомные исключения приложения.

Все доменные исключения наследуются от базовых классов Python,
но имеют семантически понятные имена для упрощения обработки ошибок.
"""

from typing import Any


class APINotSet(Exception):
    """API-ключ не установлен в конфигурации."""


class AccessKeyNotSet(Exception):
    """Ключ доступа к боту не установлен в конфигурации."""


class EnricherNotConfiguredError(ValueError):
    """Обогатитель не сконфигурирован."""


class EnricherResponseInvalidError(ValueError):
    """Ответ обогатителя не соответствует контракту."""


class GenerationConfigurationError(RuntimeError):
    """Некорректная конфигурация генерации."""


class GenerationFileError(RuntimeError):
    """Некорректный mock-файл генерации."""


class CreditsAPIError(RuntimeError):
    """Ошибка API проверки кредитов."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        headers: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.headers = headers


class GenerationAPIError(RuntimeError):
    """Ошибка API генерации."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        headers: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.headers = headers


class GenerationStreamError(RuntimeError):
    """Ошибка потока генерации."""


class GenerationAudioMissingError(RuntimeError):
    """Аудио не получено."""


class GenerationLockTimeoutError(RuntimeError):
    """Превышено время ожидания захвата блокировки генерации."""


class FeedbackSaveError(RuntimeError):
    """Ошибка сохранения оценки или отзыва в БД."""
