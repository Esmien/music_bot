"""Команды для постановки задач в очередь.

Команды инициируют выполнение работы воркером.
Все поля должны быть JSON-сериализуемыми примитивами.
"""

from pydantic import BaseModel, Field


class StartEnrichment(BaseModel):
    """Команда на запуск обогащения промпта пользователя.

    Args:
        user_id: Telegram ID пользователя.
        chat_id: ID чата для отправки результата.
        initial_prompt: Исходный промпт от пользователя.
        history: История диалога с обогатителем (опционально).
        status_message_id: ID сообщения для обновления статуса.
    """

    user_id: int = Field(..., description="Telegram ID пользователя")
    chat_id: int = Field(..., description="ID чата для отправки результата")
    initial_prompt: str = Field(..., description="Исходный промпт от пользователя")
    history: list[dict[str, str]] | None = Field(
        default=None, description="История диалога с обогатителем (role, content)"
    )
    status_message_id: int | None = Field(default=None, description="ID сообщения для обновления статуса")


class RunGeneration(BaseModel):
    """Команда на запуск генерации песни.

    Args:
        user_id: Telegram ID пользователя.
        chat_id: ID чата для отправки результата.
        gen_id: ID записи генерации в БД.
        prompt: Обогащённый промпт для генерации.
        title: Название песни.
        status_message_id: ID сообщения для обновления прогресса.
    """

    user_id: int = Field(..., description="Telegram ID пользователя")
    chat_id: int = Field(..., description="ID чата для отправки результата")
    gen_id: int = Field(..., description="ID записи генерации в БД")
    prompt: str = Field(..., description="Обогащённый промпт для генерации")
    title: str = Field(..., description="Название песни")
    status_message_id: int | None = Field(default=None, description="ID сообщения для обновления прогресса")


class GetCredits(BaseModel):
    """Команда на получение баланса кредитов пользователя.

    Args:
        chat_id: ID чата для отправки результата.
    """

    chat_id: int = Field(..., description="ID чата для отправки результата")
