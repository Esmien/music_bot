"""События, возвращаемые воркерами после выполнения команд.

События сигнализируют о завершении работы (успешном или с ошибкой).
Все поля должны быть JSON-сериализуемыми примитивами.
"""

from pydantic import BaseModel, Field


class EnrichmentCompleted(BaseModel):
    """Событие успешного обогащения промпта.

    Args:
        user_id: Telegram ID пользователя.
        chat_id: ID чата для отправки результата.
        initial_prompt: Исходный промпт от пользователя.
        enriched_prompt: Обогащённый промпт.
        status_message_id: ID сообщения для обновления.
    """

    user_id: int = Field(..., description="Telegram ID пользователя")
    chat_id: int = Field(..., description="ID чата для отправки результата")
    initial_prompt: str = Field(..., description="Исходный промпт от пользователя")
    enriched_prompt: str = Field(..., description="Обогащённый промпт")
    status_message_id: int | None = Field(default=None, description="ID сообщения для обновления")


class GenerationSucceeded(BaseModel):
    """Событие успешной генерации песни.

    Args:
        user_id: Telegram ID пользователя.
        chat_id: ID чата для отправки результата.
        gen_id: ID записи генерации в БД.
        title: Название песни.
        audio_file_path: Путь к сгенерированному аудио-файлу (временный).
        status_message_id: ID сообщения прогресса для удаления.
    """

    user_id: int = Field(..., description="Telegram ID пользователя")
    chat_id: int = Field(..., description="ID чата для отправки результата")
    gen_id: int = Field(..., description="ID записи генерации в БД")
    title: str = Field(..., description="Название песни")
    audio_file_path: str = Field(..., description="Путь к сгенерированному аудио-файлу")
    status_message_id: int | None = Field(default=None, description="ID сообщения прогресса для удаления")


class GenerationFailed(BaseModel):
    """Событие неудачной генерации песни.

    Args:
        user_id: Telegram ID пользователя.
        chat_id: ID чата для отправки сообщения об ошибке.
        gen_id: ID записи генерации в БД, если генерация уже создана.
        stage: Этап, на котором произошла ошибка.
        error_message: Сообщение об ошибке для логирования.
        user_error_message: Сообщение об ошибке для пользователя (опционально).
        status_message_id: ID сообщения прогресса для удаления.
    """

    user_id: int = Field(..., description="Telegram ID пользователя")
    chat_id: int = Field(..., description="ID чата для отправки сообщения об ошибке")
    gen_id: int | None = Field(default=None, description="ID записи генерации в БД, если генерация уже создана")
    error_message: str = Field(..., description="Сообщение об ошибке для логирования")
    stage: str = Field(..., description="Этап, на котором произошла ошибка")
    user_error_message: str | None = Field(
        default=None, description="Сообщение об ошибке для пользователя (если None, используется стандартное)"
    )
    status_message_id: int | None = Field(default=None, description="ID сообщения прогресса для удаления")
