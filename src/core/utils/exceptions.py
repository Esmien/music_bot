"""Кастомные исключения приложения.

Все доменные исключения наследуются от базовых классов Python,
но имеют семантически понятные имена для упрощения обработки ошибок.
"""


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


class GenerationAPIError(RuntimeError):
    """Ошибка API генерации."""


class GenerationStreamError(RuntimeError):
    """Ошибка потока генерации."""


class GenerationAudioMissingError(RuntimeError):
    """Аудио не получено."""


class FeedbackSaveError(RuntimeError):
    """Ошибка сохранения оценки или отзыва в БД."""
