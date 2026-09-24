class APINotSet(Exception): ...


class AccessKeyNotSet(Exception): ...


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
